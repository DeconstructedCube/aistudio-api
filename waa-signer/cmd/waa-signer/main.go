package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
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

type workerEntry struct {
	mu        sync.Mutex
	worker    *aistudio.NativeWorker
	statePath string
	authUser  int
	proxy     string
	createdAt time.Time
	lastUsed  time.Time
}

type WorkerPool struct {
	mu       sync.RWMutex
	entries  map[string]*workerEntry
	stateDir string
	proxy    string
	locale   string
	timezone string
}

func NewWorkerPool(stateDir, proxy, locale, timezone string) *WorkerPool {
	return &WorkerPool{
		entries:  make(map[string]*workerEntry),
		stateDir: stateDir,
		proxy:    proxy,
		locale:   locale,
		timezone: timezone,
	}
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
		if entry.worker != nil {
			_ = entry.worker.Close()
			entry.worker = nil
		}
		entry.mu.Unlock()
	}
}

func (p *WorkerPool) ActiveCount() int {
	p.mu.RLock()
	defer p.mu.RUnlock()
	count := 0
	for _, entry := range p.entries {
		if entry.worker != nil {
			st := entry.worker.State()
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

func (p *WorkerPool) AcquireWorker(
	ctx context.Context,
	rawCookies string,
	authUser int,
	model string,
	proxyOverride string,
	forceRecreate bool,
) (*aistudio.NativeWorker, string, error) {
	state, err := aistudio.ParseCookieText(rawCookies)
	if err != nil {
		return nil, "", fmt.Errorf("解析 Cookie 失败: %w", err)
	}

	key := accountKeyFromState(state, authUser)
	entry := p.getEntry(key)

	entry.mu.Lock()
	defer entry.mu.Unlock()

	effectiveProxy := strings.TrimSpace(proxyOverride)
	if effectiveProxy == "" {
		effectiveProxy = p.proxy
	}

	if !forceRecreate && entry.worker != nil && entry.proxy == effectiveProxy {
		st := entry.worker.State()
		if st.Phase == aistudio.WorkerReady || st.Phase == aistudio.WorkerBusy {
			entry.lastUsed = time.Now()
			return entry.worker, "hot", nil
		}
	}

	if entry.worker != nil {
		_ = entry.worker.Close()
		entry.worker = nil
	}

	accDir := filepath.Join(p.stateDir, key)
	if err := os.MkdirAll(accDir, 0o755); err != nil {
		return nil, "", fmt.Errorf("创建账户状态目录失败: %w", err)
	}
	statePath := filepath.Join(accDir, "storage-state.json")
	if err := aistudio.WriteStorageState(statePath, state); err != nil {
		return nil, "", fmt.Errorf("写入 storage-state.json 失败: %w", err)
	}

	cleanModel := strings.TrimPrefix(strings.TrimSpace(model), "models/")
	if cleanModel == "" {
		cleanModel = defaultModel
	}

	opts := camoufoxnative.Options{
		StorageStatePath: statePath,
		Model:            cleanModel,
		BootstrapPrompt:  camoufoxnative.DefaultBootstrapPrompt,
		Locale:           p.locale,
		Timezone:         p.timezone,
		Proxy:            effectiveProxy,
		AuthUser:         authUser,
	}

	slog.Info("正在初始化 SpiderMonkey WAA Worker", "account", key, "auth_user", authUser, "model", cleanModel, "proxy", effectiveProxy != "")
	t0 := time.Now()
	worker, err := aistudio.NewSpiderMonkeyWorker(ctx, key, opts)
	if err != nil {
		return nil, "", fmt.Errorf("初始化 SpiderMonkey WAA Worker 失败: %w", err)
	}
	slog.Info("SpiderMonkey WAA Worker 初始化就绪", "account", key, "elapsed_ms", time.Since(t0).Milliseconds())

	entry.worker = worker
	entry.statePath = statePath
	entry.authUser = authUser
	entry.proxy = effectiveProxy
	entry.createdAt = time.Now()
	entry.lastUsed = time.Now()
	return worker, "cold", nil
}

type MintRequest struct {
	Cookies      string          `json:"cookies"`
	AuthUser     any             `json:"authUser"`
	G1Tier       string          `json:"g1Tier"`
	Model        string          `json:"model"`
	Prompt       string          `json:"prompt"`
	PagePrompt   string          `json:"pagePrompt"`
	Digest       string          `json:"digest"`
	WireContents json.RawMessage `json:"wireContents"`
	Proxy        string          `json:"proxy"`
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

func buildSessionHeaders(ctx context.Context, worker *aistudio.NativeWorker, protoHeaders http.Header, g1Tier string) map[string]string {
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

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

type SendRPCRequest struct {
	Cookies    string            `json:"cookies"`
	AuthUser   any               `json:"authUser"`
	G1Tier     string            `json:"g1Tier"`
	Model      string            `json:"model"`
	URL        string            `json:"url"`
	Headers    map[string]string `json:"headers"`
	Body       string            `json:"body"`
	PagePrompt string            `json:"pagePrompt"`
	Proxy      string            `json:"proxy"`
}

func discoverProxy() string {
	for _, envName := range []string{"AISTUDIO_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"} {
		if val := strings.TrimSpace(os.Getenv(envName)); val != "" {
			return val
		}
	}
	for _, port := range []int{7890, 10808} {
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
	defaultListen := os.Getenv("LISTEN_ADDR")
	if defaultListen == "" {
		if port := os.Getenv("PORT"); port != "" {
			defaultListen = "0.0.0.0:" + port
		} else {
			defaultListen = "127.0.0.1:8081"
		}
	}
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

	listenAddr := flag.String("listen", defaultListen, "HTTP listen address")
	proxyURL := flag.String("proxy", discoverProxy(), "Upstream HTTP/HTTPS/SOCKS5 proxy URL")
	stateDir := flag.String("state-dir", defaultStateDir, "Directory for worker fingerprints and interpreter cache")
	locale := flag.String("locale", defaultLocale, "Browser profile locale")
	timezone := flag.String("timezone", defaultTZ, "Browser profile IANA timezone")
	flag.Parse()

	pool := NewWorkerPool(*stateDir, *proxyURL, *locale, *timezone)
	defer pool.CloseAll()

	mux := http.NewServeMux()

	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":      true,
			"status":  "ok",
			"engine":  "spidermonkey-152",
			"workers": pool.ActiveCount(),
		})
	})

	mux.HandleFunc("POST /v1/proof/mint", func(w http.ResponseWriter, r *http.Request) {
		var req MintRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, MintResponse{OK: false, Error: "invalid JSON body: " + err.Error()})
			return
		}
		if strings.TrimSpace(req.Cookies) == "" {
			writeJSON(w, http.StatusBadRequest, MintResponse{OK: false, Error: "cookies are required"})
			return
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

		ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
		defer cancel()

		worker, poolState, err := pool.AcquireWorker(ctx, req.Cookies, authUser, req.Model, req.Proxy, false)
		if err != nil {
			slog.Warn("获取 WAA Worker 失败", "auth_user", authUser, "error", err)
			writeJSON(w, http.StatusBadGateway, MintResponse{OK: false, Error: err.Error()})
			return
		}

		t0 := time.Now()
		proof, protoHeaders, err := worker.ProofByDigest(ctx, digest, pagePrompt)
		if err != nil && ctx.Err() == nil && poolState == "hot" {
			slog.Warn("热 Worker 签名失败，正在重建 Worker 重试", "auth_user", authUser, "error", err)
			worker, poolState, err = pool.AcquireWorker(ctx, req.Cookies, authUser, req.Model, req.Proxy, true)
			if err == nil {
				proof, protoHeaders, err = worker.ProofByDigest(ctx, digest, pagePrompt)
			}
		}
		if err != nil {
			slog.Warn("生成 WAA Proof 失败", "auth_user", authUser, "error", err)
			writeJSON(w, http.StatusBadGateway, MintResponse{OK: false, Error: err.Error()})
			return
		}

		headers := buildSessionHeaders(ctx, worker, protoHeaders, req.G1Tier)
		slog.Info(
			"生成 WAA Proof 成功",
			"auth_user", authUser,
			"pool_state", poolState,
			"digest", digest[:min(8, len(digest))],
			"page_prompt_len", len(pagePrompt),
			"proof_len", len(proof),
			"elapsed_ms", time.Since(t0).Milliseconds(),
		)

		writeJSON(w, http.StatusOK, MintResponse{
			OK:        true,
			Proof:     proof,
			Digest:    digest,
			PoolState: poolState,
			Headers:   headers,
		})
	})

	// POST /v1/rpc/send: 使用同一个 SpiderMonkey Worker 的 Firefox 152 uTLS/HTTP2/HTTP3 连接池直接发送受保护 RPC
	mux.HandleFunc("POST /v1/rpc/send", func(w http.ResponseWriter, r *http.Request) {
		var req SendRPCRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "invalid JSON body: " + err.Error()})
			return
		}
		if strings.TrimSpace(req.Cookies) == "" || strings.TrimSpace(req.Body) == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "cookies and body are required"})
			return
		}
		targetURL := strings.TrimSpace(req.URL)
		if targetURL == "" {
			targetURL = generateContentURL
		}
		authUser := parseAuthUser(req.AuthUser)

		worker, _, err := pool.AcquireWorker(r.Context(), req.Cookies, authUser, req.Model, req.Proxy, false)
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}

		protoHeaders, err := worker.ProtocolHeaders(r.Context(), "")
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		state, err := worker.BrowserStorageState(r.Context())
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		authHeader, err := aistudio.NewSigner().Authorization(state)
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}

		hdrs := protoHeaders.Clone()
		hdrs.Set("Content-Type", "application/json+protobuf")
		hdrs.Set("Authorization", authHeader)
		if tierVal := normalizeTierHeader(req.G1Tier); tierVal != "" {
			hdrs.Set("X-AIStudio-G1-Tier", tierVal)
		}
		for k, v := range req.Headers {
			if strings.EqualFold(k, "host") || strings.EqualFold(k, "content-length") || strings.EqualFold(k, "cookie") {
				continue
			}
			hdrs.Set(k, v)
		}

		resp, err := worker.SendProtected(r.Context(), aistudio.ProtectedRequest{
			URL:        targetURL,
			Headers:    hdrs,
			Body:       []byte(req.Body),
			PagePrompt: req.PagePrompt,
		})
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		defer resp.Body.Close()

		w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
		w.Header().Set("X-Upstream-Status", strconv.Itoa(resp.StatusCode))
		w.WriteHeader(resp.StatusCode)

		flusher, canFlush := w.(http.Flusher)
		buf := make([]byte, 16*1024)
		for {
			n, readErr := resp.Body.Read(buf)
			if n > 0 {
				_, _ = w.Write(buf[:n])
				if canFlush {
					flusher.Flush()
				}
			}
			if readErr != nil {
				break
			}
		}
	})

	server := &http.Server{
		Addr:              *listenAddr,
		Handler:           mux,
		ReadHeaderTimeout: 15 * time.Second,
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stop
		slog.Info("正在关闭 WAA Signer 服务...")
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(ctx)
		pool.CloseAll()
	}()

	slog.Info("WAA Signer 服务已启动 (Mag1cFall/AIStudio2API SpiderMonkey 152 内核)", "listen", *listenAddr, "proxy", *proxyURL, "state_dir", *stateDir)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("服务异常退出", "error", err)
		os.Exit(1)
	}
}
