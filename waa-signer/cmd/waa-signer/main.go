package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	_ "time/tzdata"

	"github.com/Mag1cFall/AIStudio2API/internal/aistudio"
	"github.com/Mag1cFall/AIStudio2API/internal/camoufoxnative"
)

const (
	defaultModel       = "gemini-3.7-flash"
	generateContentURL = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent"
)

// maxWorkerAge 限定热 Worker 的最长复用时长：常驻数十小时后，官网构建号与 visitId 会老化，
// 继续复用只会签出必定 403 的旧快照，因此超龄后回收并重新冷启动加载最新页面与 VM。
const maxWorkerAge = 6 * time.Hour

// poolWorker 只暴露池和请求处理需要的操作，离线测试可使用内存 Worker。
type poolWorker interface {
	State() aistudio.WorkerState
	Close() error
	BrowserStorageState(context.Context) (aistudio.StorageState, error)
	ProofByDigest(context.Context, string, string) (string, http.Header, error)
	ProtocolHeaders(context.Context, string) (http.Header, error)
	SendProtected(context.Context, aistudio.ProtectedRequest) (*aistudio.RPCResponse, error)
}

type workerFactory func(context.Context, string, camoufoxnative.Options, aistudio.StorageState) (poolWorker, error)

type workerEntry struct {
	mu               sync.Mutex
	workerMu         sync.RWMutex
	worker           poolWorker
	inputFingerprint string
	inputCookies     map[cookieIdentity][]fingerprintCookie
	statePath        string
	authUser         int
	proxy            string
	createdAt        time.Time
	lastUsed         time.Time
}

// workerSnapshot 只持短锁，健康检查不会等待签名或整段响应流。
func (entry *workerEntry) workerSnapshot() poolWorker {
	entry.workerMu.RLock()
	defer entry.workerMu.RUnlock()
	return entry.worker
}

func (entry *workerEntry) setWorker(worker poolWorker) {
	entry.workerMu.Lock()
	defer entry.workerMu.Unlock()
	entry.worker = worker
}

type WorkerPool struct {
	mu       sync.RWMutex
	entries  map[string]*workerEntry
	stateDir string
	proxy    string
	locale   string
	timezone string
	factory  workerFactory
}

func NewWorkerPool(stateDir, proxy, locale, timezone string) *WorkerPool {
	return &WorkerPool{
		entries:  make(map[string]*workerEntry),
		stateDir: stateDir,
		proxy:    proxy,
		locale:   locale,
		timezone: timezone,
		factory:  createNativeWorker,
	}
}

// createNativeWorker 保留真实初始化的磁盘操作，假工厂不会创建或清理文件。
func createNativeWorker(ctx context.Context, key string, opts camoufoxnative.Options, state aistudio.StorageState) (poolWorker, error) {
	if err := os.MkdirAll(filepath.Dir(opts.StorageStatePath), 0o755); err != nil {
		return nil, fmt.Errorf("创建账户状态目录失败: %w", err)
	}
	if err := aistudio.WriteStorageState(opts.StorageStatePath, state); err != nil {
		return nil, fmt.Errorf("写入 storage-state.json 失败: %w", err)
	}
	worker, err := aistudio.NewSpiderMonkeyWorker(ctx, key, opts)
	if err != nil {
		return nil, err
	}
	return worker, nil
}

func (p *WorkerPool) CloseAll() {
	p.mu.Lock()
	entries := make([]*workerEntry, 0, len(p.entries))
	for _, entry := range p.entries {
		entries = append(entries, entry)
	}
	p.entries = make(map[string]*workerEntry)
	p.mu.Unlock()

	for _, entry := range entries {
		entry.mu.Lock()
		if worker := entry.workerSnapshot(); worker != nil {
			_ = worker.Close()
			entry.setWorker(nil)
		}
		entry.inputFingerprint = ""
		entry.inputCookies = nil
		entry.mu.Unlock()
	}
}

func (p *WorkerPool) ActiveCount() int {
	p.mu.RLock()
	entries := make([]*workerEntry, 0, len(p.entries))
	for _, entry := range p.entries {
		entries = append(entries, entry)
	}
	p.mu.RUnlock()
	count := 0
	for _, entry := range entries {
		if worker := entry.workerSnapshot(); worker != nil {
			st := worker.State()
			if st.Phase == aistudio.WorkerReady || st.Phase == aistudio.WorkerBusy {
				count++
			}
		}
	}
	return count
}

func accountKeyFromState(state aistudio.StorageState, authUser int) string {
	now := time.Now()
	seed, ok := state.CookieValue("SAPISID", "https://aistudio.google.com/", now)
	if !ok || seed == "" {
		seed, _ = state.CookieValue("__Secure-3PAPISID", "https://aistudio.google.com/", now)
	}
	if seed == "" {
		seed, _ = state.CookieValue("SID", "https://aistudio.google.com/", now)
	}
	sum := sha256.Sum256([]byte(seed))
	return fmt.Sprintf("acc_%s_u%d", hex.EncodeToString(sum[:6]), authUser)
}

type cookieIdentity struct {
	Domain string
	Path   string
	Name   string
}

type cookieFingerprints struct {
	input   string
	cookies map[cookieIdentity][]fingerprintCookie
}

type fingerprintCookie struct {
	Name     string
	Value    string
	Domain   string
	Path     string
	HTTPOnly bool
	Secure   bool
	RPC      bool
}

// effectiveCookieFingerprint 保留有效 Cookie 的域和路径，同名同范围的顺序也保留。
func effectiveCookieFingerprint(state aistudio.StorageState) (cookieFingerprints, error) {
	now := time.Now()
	header, err := state.CookieHeader(generateContentURL, now)
	if err != nil {
		return cookieFingerprints{}, err
	}
	if header == "" {
		return cookieFingerprints{}, fmt.Errorf("没有可用于上游 RPC 的有效 Cookie")
	}
	all := make([]fingerprintCookie, 0, len(state.Cookies))
	for _, cookie := range state.Cookies {
		if cookie.Value == "" || cookie.Expires >= 0 && cookie.Expires <= float64(now.UnixNano())/1e9 {
			continue
		}
		single := aistudio.StorageState{Cookies: []aistudio.StateCookie{cookie}}
		rpcHeader, headerErr := single.CookieHeader(generateContentURL, now)
		if headerErr != nil {
			return cookieFingerprints{}, headerErr
		}
		all = append(all, fingerprintCookie{
			Name: cookie.Name, Value: cookie.Value,
			Domain: strings.ToLower(strings.TrimPrefix(cookie.Domain, ".")), Path: cookie.Path,
			HTTPOnly: cookie.HTTPOnly, Secure: cookie.Secure, RPC: rpcHeader != "",
		})
	}
	sort.SliceStable(all, func(left, right int) bool {
		a, b := all[left], all[right]
		if a.Domain != b.Domain {
			return a.Domain < b.Domain
		}
		if a.Path != b.Path {
			return a.Path < b.Path
		}
		return a.Name < b.Name
	})
	encoded, err := json.Marshal(all)
	if err != nil {
		return cookieFingerprints{}, err
	}
	groups := make(map[cookieIdentity][]fingerprintCookie)
	for _, cookie := range all {
		key := cookieIdentity{Domain: cookie.Domain, Path: cookie.Path, Name: cookie.Name}
		groups[key] = append(groups[key], cookie)
	}
	sum := sha256.Sum256(encoded)
	return cookieFingerprints{input: hex.EncodeToString(sum[:]), cookies: groups}, nil
}

func sameCookieGroup(left, right []fingerprintCookie) bool {
	if len(left) != len(right) {
		return false
	}
	for index, cookie := range left {
		if cookie != right[index] {
			return false
		}
	}
	return true
}

// cookieMetadataExplicit 与解析器采用相同形态判断；原始 Cookie 头不携带 Secure/HTTPOnly 标志。
func cookieMetadataExplicit(rawCookies string) bool {
	text := strings.TrimSpace(strings.TrimPrefix(rawCookies, string(rune(0xFEFF))))
	return strings.HasPrefix(text, "[") || strings.HasPrefix(text, "{") || strings.Contains(text, "\t")
}

var perResponseRollingCookieNames = map[string]bool{
	"SIDCC":            true,
	"__Secure-1PSIDCC": true,
	"__Secure-3PSIDCC": true,
}

// matchesCurrentCredentials 比较真实凭据；只有明确改变的导出标志才约束当前 Worker 元数据。
func matchesCurrentCredentials(input, previous, current []fingerprintCookie, explicitMetadata bool) bool {
	if len(input) != len(current) {
		return false
	}
	for index, cookie := range input {
		active := current[index]
		if cookie.Name != active.Name || cookie.Domain != active.Domain || cookie.Path != active.Path {
			return false
		}
		if cookie.Value != active.Value && !perResponseRollingCookieNames[cookie.Name] {
			return false
		}
		if !explicitMetadata {
			continue
		}
		if len(previous) != len(input) {
			if cookie.HTTPOnly != active.HTTPOnly || cookie.Secure != active.Secure {
				return false
			}
			continue
		}
		old := previous[index]
		if cookie.HTTPOnly != old.HTTPOnly && cookie.HTTPOnly != active.HTTPOnly ||
			cookie.Secure != old.Secure && cookie.Secure != active.Secure {
			return false
		}
	}
	return true
}

// cookieUpdatesMatchCurrent 只核对调用者实际改变的字段，未回传的内部更新不回退。
func cookieUpdatesMatchCurrent(input, previous, current map[cookieIdentity][]fingerprintCookie, explicitMetadata bool) bool {
	for key, incoming := range input {
		if !sameCookieGroup(incoming, previous[key]) && !matchesCurrentCredentials(incoming, previous[key], current[key], explicitMetadata) {
			return false
		}
	}
	for key, old := range previous {
		if _, exists := input[key]; exists {
			continue
		}
		// RPC 头无法携带页面专属 Cookie，缺失这些字段不表示用户替换了凭据。
		if len(old) > 0 && !old[0].RPC {
			continue
		}
		if len(current[key]) > 0 {
			return false
		}
	}
	return true
}

// retainUnsentCookieInputs 记住 RPC 回传无法携带的旧输入，兼容下一次带页面 Cookie 的请求。
func retainUnsentCookieInputs(previous, input map[cookieIdentity][]fingerprintCookie) map[cookieIdentity][]fingerprintCookie {
	next := make(map[cookieIdentity][]fingerprintCookie, len(input))
	for key, cookies := range input {
		next[key] = append([]fingerprintCookie(nil), cookies...)
	}
	for key, cookies := range previous {
		if _, exists := next[key]; !exists && len(cookies) > 0 && !cookies[0].RPC {
			next[key] = append([]fingerprintCookie(nil), cookies...)
		}
	}
	return next
}

func (p *WorkerPool) getEntry(key string) *workerEntry {
	p.mu.Lock()
	defer p.mu.Unlock()
	entry, ok := p.entries[key]
	if !ok {
		entry = &workerEntry{}
		p.entries[key] = entry
	}
	return entry
}

func (p *WorkerPool) evictExpiredEntries(excludeKey string) {
	now := time.Now()
	p.mu.RLock()
	staleKeys := make([]string, 0, len(p.entries))
	staleEntries := make([]*workerEntry, 0, len(p.entries))
	for k, entry := range p.entries {
		if k == excludeKey {
			continue
		}
		staleKeys = append(staleKeys, k)
		staleEntries = append(staleEntries, entry)
	}
	p.mu.RUnlock()

	for i, entry := range staleEntries {
		if !entry.mu.TryLock() {
			continue
		}
		key := staleKeys[i]
		worker := entry.workerSnapshot()
		expired := (!entry.createdAt.IsZero() && now.Sub(entry.createdAt) >= maxWorkerAge) ||
			(worker == nil && !entry.lastUsed.IsZero() && now.Sub(entry.lastUsed) >= maxWorkerAge)
		if expired {
			if worker != nil {
				_ = worker.Close()
				entry.setWorker(nil)
			}
			entry.inputFingerprint = ""
			entry.inputCookies = nil
			p.mu.Lock()
			if p.entries[key] == entry && entry.workerSnapshot() == nil {
				delete(p.entries, key)
			}
			p.mu.Unlock()
		}
		entry.mu.Unlock()
	}
}

// workerLease 将获取、签名、取头和响应流放在同一账户租约内，避免中途关闭 Worker。
type workerLease struct {
	pool             *WorkerPool
	entry            *workerEntry
	worker           poolWorker
	poolState        string
	key              string
	options          camoufoxnative.Options
	inputState       aistudio.StorageState
	inputFingerprint cookieFingerprints
	releaseOnce      sync.Once
}

func (lease *workerLease) Release() {
	lease.releaseOnce.Do(func() { lease.entry.mu.Unlock() })
}

// Recreate 只能在持有租约时调用；当前 Worker 状态优先，保留其已轮换的凭据。
func (lease *workerLease) Recreate(ctx context.Context) error {
	if lease.entry.workerSnapshot() != lease.worker {
		return fmt.Errorf("Worker 已被替换，拒绝陈旧的重建请求")
	}
	state := lease.inputState
	if current, err := lease.worker.BrowserStorageState(ctx); err == nil {
		state = current
	}
	previousInputCookies := lease.entry.inputCookies
	worker, err := lease.pool.initializeWorker(ctx, lease.entry, lease.key, lease.options, state, lease.inputFingerprint)
	if err != nil {
		return err
	}
	lease.entry.inputCookies = retainUnsentCookieInputs(previousInputCookies, lease.inputFingerprint.cookies)
	lease.worker = worker
	lease.poolState = "cold"
	return nil
}

// initializeWorker 由账户租约保护，只在成功初始化后发布 Worker 和输入指纹。
func (p *WorkerPool) initializeWorker(ctx context.Context, entry *workerEntry, key string, opts camoufoxnative.Options, state aistudio.StorageState, inputFingerprint cookieFingerprints) (poolWorker, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	entry.inputFingerprint = ""
	entry.inputCookies = nil
	entry.statePath = ""
	if previous := entry.workerSnapshot(); previous != nil {
		err := previous.Close()
		entry.setWorker(nil)
		if err != nil {
			return nil, fmt.Errorf("关闭旧 Worker 失败: %w", err)
		}
	}

	slog.Info("正在初始化 SpiderMonkey WAA Worker", "account", key, "auth_user", opts.AuthUser, "model", opts.Model, "proxy", opts.Proxy != "")
	t0 := time.Now()
	worker, err := p.factory(ctx, key, opts, state)
	if err != nil {
		return nil, fmt.Errorf("初始化 SpiderMonkey WAA Worker 失败: %w", err)
	}
	if worker == nil {
		return nil, fmt.Errorf("初始化 SpiderMonkey WAA Worker 返回空实例")
	}
	slog.Info("SpiderMonkey WAA Worker 初始化就绪", "account", key, "elapsed_ms", time.Since(t0).Milliseconds())
	entry.setWorker(worker)
	entry.inputFingerprint = inputFingerprint.input
	entry.inputCookies = retainUnsentCookieInputs(nil, inputFingerprint.cookies)
	entry.statePath = opts.StorageStatePath
	entry.authUser = opts.AuthUser
	entry.proxy = opts.Proxy
	entry.createdAt = time.Now()
	entry.lastUsed = time.Now()
	return worker, nil
}

func (p *WorkerPool) AcquireWorker(ctx context.Context, rawCookies string, authUser int, model string, proxyOverride string) (*workerLease, error) {
	state, err := aistudio.ParseCookieText(rawCookies)
	if err != nil {
		return nil, fmt.Errorf("解析 Cookie 失败: %w", err)
	}
	inputFingerprint, err := effectiveCookieFingerprint(state)
	if err != nil {
		return nil, fmt.Errorf("规范化 Cookie 失败: %w", err)
	}
	key := accountKeyFromState(state, authUser)
	p.evictExpiredEntries(key)
	entry := p.getEntry(key)
	entry.mu.Lock()
	if err := ctx.Err(); err != nil {
		entry.mu.Unlock()
		return nil, err
	}

	effectiveProxy := strings.TrimSpace(proxyOverride)
	if effectiveProxy == "" {
		effectiveProxy = p.proxy
	}
	cleanModel := strings.TrimPrefix(strings.TrimSpace(model), "models/")
	if cleanModel == "" {
		cleanModel = defaultModel
	}
	lease := &workerLease{
		pool: p, entry: entry, key: key, inputState: state, inputFingerprint: inputFingerprint,
		options: camoufoxnative.Options{
			StorageStatePath: filepath.Join(p.stateDir, key, "storage-state.json"),
			Model:            cleanModel, BootstrapPrompt: camoufoxnative.DefaultBootstrapPrompt,
			Locale: p.locale, Timezone: p.timezone, Proxy: effectiveProxy, AuthUser: authUser,
		},
	}
	if worker := entry.workerSnapshot(); worker != nil && entry.proxy == effectiveProxy {
		phase := worker.State().Phase
		if phase == aistudio.WorkerReady || phase == aistudio.WorkerBusy {
			if time.Since(entry.createdAt) >= maxWorkerAge {
				slog.Info(
					"热 Worker 已超过最大复用时长，回收后冷启动加载最新页面",
					"account", key,
					"age", time.Since(entry.createdAt).Truncate(time.Minute).String(),
					"max_age", maxWorkerAge.String(),
				)
			} else {
				reuse := entry.inputFingerprint == inputFingerprint.input
				if !reuse {
					if current, stateErr := worker.BrowserStorageState(ctx); stateErr == nil {
						currentFingerprint, fingerprintErr := effectiveCookieFingerprint(current)
						reuse = fingerprintErr == nil && cookieUpdatesMatchCurrent(inputFingerprint.cookies, entry.inputCookies, currentFingerprint.cookies, cookieMetadataExplicit(rawCookies))
					}
				}
				if reuse {
					entry.inputFingerprint = inputFingerprint.input
					entry.inputCookies = retainUnsentCookieInputs(entry.inputCookies, inputFingerprint.cookies)
					entry.lastUsed = time.Now()
					lease.worker, lease.poolState = worker, "hot"
					return lease, nil
				}
			}
		}
	}
	worker, err := p.initializeWorker(ctx, entry, key, lease.options, state, inputFingerprint)
	if err != nil {
		entry.mu.Unlock()
		return nil, err
	}
	lease.worker, lease.poolState = worker, "cold"
	return lease, nil
}

type MintRequest struct {
	Cookies       string          `json:"cookies"`
	AuthUser      any             `json:"authUser"`
	G1Tier        string          `json:"g1Tier"`
	Model         string          `json:"model"`
	Prompt        string          `json:"prompt"`
	PagePrompt    string          `json:"pagePrompt"`
	Digest        string          `json:"digest"`
	WireContents  json.RawMessage `json:"wireContents"`
	Proxy         string          `json:"proxy"`
	ForceRecreate bool            `json:"forceRecreate,omitempty"`
}

type MintResponse struct {
	OK        bool              `json:"ok"`
	Proof     string            `json:"proof,omitempty"`
	Digest    string            `json:"digest,omitempty"`
	PoolState string            `json:"poolState,omitempty"`
	Headers   map[string]string `json:"headers,omitempty"`
	Error     string            `json:"error,omitempty"`
}

func parseAuthUser(val any) int {
	switch v := val.(type) {
	case float64:
		return int(v)
	case int:
		return v
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(v))
		if err == nil && n >= 0 {
			return n
		}
	}
	return 0
}

func normalizeTierHeader(tier string) string {
	switch strings.ToUpper(strings.TrimSpace(tier)) {
	case "TIER1", "PRO":
		return "TIER1"
	case "TIER2", "ULTRA", "ULTRA_20X":
		return "TIER2"
	case "TIER0", "PLUS":
		return "TIER0"
	default:
		return ""
	}
}

// extractPromptsFromWire 从 wireContents ([[parts, role], ...]) 提取 bindingPrompt 与最后一轮文本 pagePrompt
func extractPromptsFromWire(wireContents json.RawMessage, fallbackPrompt string) (string, string) {
	bindingPrompt := fallbackPrompt
	if len(wireContents) == 0 {
		return bindingPrompt, bindingPrompt
	}

	var turns [][]json.RawMessage
	if err := json.Unmarshal(wireContents, &turns); err != nil || len(turns) == 0 {
		return bindingPrompt, bindingPrompt
	}

	var bindingParts []string
	var lastTurnTextParts []string

	for turnIdx, turn := range turns {
		if len(turn) == 0 {
			continue
		}
		var parts [][]json.RawMessage
		if err := json.Unmarshal(turn[0], &parts); err != nil {
			continue
		}
		isLastTurn := turnIdx == len(turns)-1
		for _, part := range parts {
			// Check thought flags: index 0 (bool), index 10 (bool), index 12 (int 1 or bool true)
			isThought := false
			if len(part) > 0 {
				var b0 bool
				if json.Unmarshal(part[0], &b0) == nil && b0 {
					isThought = true
				}
			}
			if len(part) > 10 {
				var b10 bool
				if json.Unmarshal(part[10], &b10) == nil && b10 {
					isThought = true
				}
			}
			if len(part) > 12 {
				var b12 bool
				var n12 int
				if (json.Unmarshal(part[12], &b12) == nil && b12) || (json.Unmarshal(part[12], &n12) == nil && n12 == 1) {
					isThought = true
				}
			}

			// Text at index 1
			if len(part) > 1 && len(part[1]) > 0 && string(part[1]) != "null" {
				var textVal string
				if err := json.Unmarshal(part[1], &textVal); err == nil {
					bindingParts = append(bindingParts, textVal)
					if isLastTurn && !isThought && textVal != "" {
						lastTurnTextParts = append(lastTurnTextParts, textVal)
					}
					continue
				}
			}
			// InlineData at index 2 ([mime, base64])
			if len(part) > 2 && len(part[2]) > 0 && string(part[2]) != "null" {
				var inline []string
				if err := json.Unmarshal(part[2], &inline); err == nil && len(inline) >= 2 {
					bindingParts = append(bindingParts, inline[1])
					continue
				}
			}
			// FileData at index 5 ([file_id])
			if len(part) > 5 && len(part[5]) > 0 && string(part[5]) != "null" {
				var fileData []string
				if err := json.Unmarshal(part[5], &fileData); err == nil && len(fileData) >= 1 {
					bindingParts = append(bindingParts, fileData[0])
					continue
				}
			}
			bindingParts = append(bindingParts, "")
		}
	}

	if bindingPrompt == "" && len(bindingParts) > 0 {
		bindingPrompt = strings.Join(bindingParts, " ")
	}
	pagePrompt := strings.Join(lastTurnTextParts, " ")
	return bindingPrompt, pagePrompt
}

func buildSessionHeaders(ctx context.Context, worker poolWorker, protoHeaders http.Header, g1Tier string) map[string]string {
	out := make(map[string]string)
	for k, vals := range protoHeaders {
		if len(vals) > 0 && vals[0] != "" {
			out[http.CanonicalHeaderKey(k)] = vals[0]
		}
	}
	if tierVal := normalizeTierHeader(g1Tier); tierVal != "" {
		out["X-AIStudio-G1-Tier"] = tierVal
	}
	if state, err := worker.BrowserStorageState(ctx); err == nil {
		if auth, err := aistudio.NewSigner().Authorization(state); err == nil && auth != "" {
			out["Authorization"] = auth
		}
		if cookieHdr, err := state.CookieHeader(generateContentURL, time.Now()); err == nil && cookieHdr != "" {
			out["Cookie"] = cookieHdr
		}
	}
	return out
}

type ipcRequest struct {
	ID     uint64          `json:"id"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params,omitempty"`
}

type ipcResponse struct {
	ID        uint64            `json:"id"`
	Event     string            `json:"event,omitempty"`
	OK        bool              `json:"ok"`
	Engine    string            `json:"engine,omitempty"`
	Workers   int               `json:"workers,omitempty"`
	Proof     string            `json:"proof,omitempty"`
	Digest    string            `json:"digest,omitempty"`
	PoolState string            `json:"poolState,omitempty"`
	Headers   map[string]string `json:"headers,omitempty"`
	Error     string            `json:"error,omitempty"`
}

func handleMintProof(ctx context.Context, pool *WorkerPool, req MintRequest) ipcResponse {
	if strings.TrimSpace(req.Cookies) == "" {
		return ipcResponse{OK: false, Error: "cookies are required"}
	}

	authUser := parseAuthUser(req.AuthUser)
	bindingPrompt, pagePrompt := extractPromptsFromWire(req.WireContents, req.Prompt)
	if req.PagePrompt != "" {
		pagePrompt = req.PagePrompt
	}

	digest := strings.TrimSpace(req.Digest)
	if digest == "" {
		sum := sha256.Sum256([]byte(bindingPrompt))
		digest = hex.EncodeToString(sum[:])
	}

	reqCtx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()

	lease, err := pool.AcquireWorker(reqCtx, req.Cookies, authUser, req.Model, req.Proxy)
	if err != nil {
		slog.Warn("获取 WAA Worker 失败", "auth_user", authUser, "error", err)
		return ipcResponse{OK: false, Error: err.Error()}
	}
	defer lease.Release()

	worker, poolState := lease.worker, lease.poolState
	if req.ForceRecreate && poolState == "hot" {
		slog.Warn("收到 forceRecreate 请求，强制销毁热 Worker 并冷重建", "auth_user", authUser, "pool_state", poolState)
		if recreateErr := lease.Recreate(reqCtx); recreateErr != nil {
			slog.Warn("强制重建 WAA Worker 失败", "auth_user", authUser, "error", recreateErr)
			return ipcResponse{OK: false, Error: recreateErr.Error()}
		}
		worker, poolState = lease.worker, lease.poolState
	}

	t0 := time.Now()
	proof, protoHeaders, err := worker.ProofByDigest(reqCtx, digest, pagePrompt)
	if err != nil && reqCtx.Err() == nil && poolState == "hot" {
		slog.Warn("热 Worker 签名失败，正在重建 Worker 重试", "auth_user", authUser, "error", err)
		if err = lease.Recreate(reqCtx); err == nil {
			worker, poolState = lease.worker, lease.poolState
			proof, protoHeaders, err = worker.ProofByDigest(reqCtx, digest, pagePrompt)
		}
	}
	if err != nil {
		slog.Warn("生成 WAA Proof 失败", "auth_user", authUser, "error", err)
		return ipcResponse{OK: false, Error: err.Error()}
	}

	headers := buildSessionHeaders(reqCtx, worker, protoHeaders, req.G1Tier)
	lease.Release()
	slog.Info(
		"生成 WAA Proof 成功",
		"auth_user", authUser,
		"pool_state", poolState,
		"digest", digest[:min(8, len(digest))],
		"page_prompt_len", len(pagePrompt),
		"proof_len", len(proof),
		"elapsed_ms", time.Since(t0).Milliseconds(),
	)

	return ipcResponse{
		OK:        true,
		Proof:     proof,
		Digest:    digest,
		PoolState: poolState,
		Headers:   headers,
	}
}

func serveIPC(ctx context.Context, in io.Reader, out io.Writer, pool *WorkerPool) error {
	ipcCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	var writeMu sync.Mutex
	writeLine := func(resp ipcResponse) error {
		payload, err := json.Marshal(resp)
		if err != nil {
			return err
		}
		payload = append(payload, '\n')
		writeMu.Lock()
		defer writeMu.Unlock()
		_, err = out.Write(payload)
		return err
	}

	if err := writeLine(ipcResponse{
		ID:     0,
		Event:  "ready",
		OK:     true,
		Engine: "spidermonkey-152",
	}); err != nil {
		return err
	}

	reader := bufio.NewReaderSize(in, 64*1024)
	var wg sync.WaitGroup

	for {
		line, err := reader.ReadBytes('\n')
		trimmed := bytes.TrimSpace(line)
		if len(trimmed) > 0 {
			var req ipcRequest
			if unmarshalErr := json.Unmarshal(trimmed, &req); unmarshalErr != nil {
				_ = writeLine(ipcResponse{
					OK:    false,
					Error: "invalid IPC JSON request: " + unmarshalErr.Error(),
				})
			} else if req.Method == "shutdown" {
				_ = writeLine(ipcResponse{
					ID:     req.ID,
					OK:     true,
					Engine: "spidermonkey-152",
				})
				cancel()
				break
			} else {
				wg.Add(1)
				go func(r ipcRequest) {
					defer wg.Done()
					var resp ipcResponse
					switch r.Method {
					case "health":
						resp = ipcResponse{
							ID:      r.ID,
							OK:      true,
							Engine:  "spidermonkey-152",
							Workers: pool.ActiveCount(),
						}
					case "proof.mint":
						var mintReq MintRequest
						if len(r.Params) == 0 {
							resp = ipcResponse{ID: r.ID, OK: false, Error: "params are required"}
						} else if paramErr := json.Unmarshal(r.Params, &mintReq); paramErr != nil {
							resp = ipcResponse{ID: r.ID, OK: false, Error: "invalid mint params: " + paramErr.Error()}
						} else {
							resp = handleMintProof(ipcCtx, pool, mintReq)
							resp.ID = r.ID
						}
					default:
						resp = ipcResponse{
							ID:    r.ID,
							OK:    false,
							Error: "unknown IPC method: " + r.Method,
						}
					}
					if ipcCtx.Err() == nil {
						_ = writeLine(resp)
					}
				}(req)
			}
		}

		if err != nil {
			if errors.Is(err, io.EOF) {
				break
			}
			return err
		}
	}

	done := make(chan struct{})
	go func() {
		wg.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
	}
	pool.CloseAll()
	return nil
}

func discoverProxy() string {
	for _, envName := range []string{"AISTUDIO_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"} {
		if val := strings.TrimSpace(os.Getenv(envName)); val != "" {
			return val
		}
	}
	for _, port := range []int{7890, 7897, 10808, 10809, 2080} {
		addr := fmt.Sprintf("127.0.0.1:%d", port)
		conn, err := net.DialTimeout("tcp", addr, 50*time.Millisecond)
		if err == nil {
			_ = conn.Close()
			return "http://" + addr
		}
	}
	return ""
}

func main() {
	aistudio.EnsureFallbackDNS()
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})))

	defaultStateDir := os.Getenv("WAA_STATE_DIR")
	if defaultStateDir == "" {
		defaultStateDir = filepath.Join(os.TempDir(), "aistudio_waa_workers")
	}
	defaultLocale := os.Getenv("WAA_LOCALE")
	if defaultLocale == "" {
		defaultLocale = "en-US"
	}
	defaultTZ := os.Getenv("AISTUDIO_TIMEZONE")
	if defaultTZ == "" {
		defaultTZ = "America/Los_Angeles"
	}

	checkMode := flag.Bool("check", false, "Verify runtime binary and exit")
	proxyURL := flag.String("proxy", "", "Upstream HTTP/HTTPS/SOCKS5 proxy URL")
	stateDir := flag.String("state-dir", defaultStateDir, "Directory for worker fingerprints and interpreter cache")
	locale := flag.String("locale", defaultLocale, "Browser profile locale")
	timezone := flag.String("timezone", defaultTZ, "Browser profile IANA timezone")
	flag.Parse()

	if *checkMode {
		_ = json.NewEncoder(os.Stdout).Encode(map[string]any{
			"ok":     true,
			"engine": "spidermonkey-152",
			"mode":   "ipc",
		})
		return
	}

	effectiveProxy := strings.TrimSpace(*proxyURL)
	if effectiveProxy == "" {
		effectiveProxy = discoverProxy()
	}

	pool := NewWorkerPool(*stateDir, effectiveProxy, *locale, *timezone)
	defer pool.CloseAll()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	slog.Info("WAA Signer IPC 服务已启动 (SpiderMonkey 152 内核)", "proxy", effectiveProxy, "state_dir", *stateDir)
	if err := serveIPC(ctx, os.Stdin, os.Stdout, pool); err != nil && !errors.Is(err, context.Canceled) {
		slog.Error("IPC 服务异常退出", "error", err)
		os.Exit(1)
	}
}
