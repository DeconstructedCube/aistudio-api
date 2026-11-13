package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Mag1cFall/AIStudio2API/internal/aistudio"
	"github.com/Mag1cFall/AIStudio2API/internal/camoufoxnative"
)

// 全部凭据均为合成夹具；假工厂不写文件，不启动原生 VM，也不访问网络。
const offlineCookies = "SAPISID=fixture-anchor; SID=fixture-login; __Secure-1PSIDTS=fixture-old-ts; SIDCC=fixture-old-cc"

type fakePoolWorker struct {
	mu      sync.Mutex
	storage aistudio.StorageState
	phase   aistudio.WorkerPhase
	closes  int
}

func cloneOfflineState(state aistudio.StorageState) aistudio.StorageState {
	state.Cookies = append([]aistudio.StateCookie(nil), state.Cookies...)
	return state
}

func (worker *fakePoolWorker) State() aistudio.WorkerState {
	worker.mu.Lock()
	defer worker.mu.Unlock()
	return aistudio.WorkerState{Phase: worker.phase}
}

func (worker *fakePoolWorker) Close() error {
	worker.mu.Lock()
	defer worker.mu.Unlock()
	worker.closes++
	worker.phase = aistudio.WorkerClosed
	return nil
}

func (worker *fakePoolWorker) BrowserStorageState(context.Context) (aistudio.StorageState, error) {
	worker.mu.Lock()
	defer worker.mu.Unlock()
	return cloneOfflineState(worker.storage), nil
}

func (worker *fakePoolWorker) ProofByDigest(context.Context, string, string) (string, http.Header, error) {
	return "offline-proof", http.Header{"X-Offline-Protocol": {"fixture"}}, nil
}

func (worker *fakePoolWorker) ProtocolHeaders(context.Context, string) (http.Header, error) {
	return http.Header{"X-Offline-Protocol": {"fixture"}}, nil
}

func (worker *fakePoolWorker) SendProtected(context.Context, aistudio.ProtectedRequest) (*aistudio.RPCResponse, error) {
	return &aistudio.RPCResponse{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader("offline-stream"))}, nil
}

func (worker *fakePoolWorker) setStorage(state aistudio.StorageState) {
	worker.mu.Lock()
	defer worker.mu.Unlock()
	worker.storage = cloneOfflineState(state)
}

func (worker *fakePoolWorker) closeCount() int {
	worker.mu.Lock()
	defer worker.mu.Unlock()
	return worker.closes
}

type fakeWorkerFactory struct {
	mu       sync.Mutex
	inputs   []aistudio.StorageState
	failNext bool
}

func (factory *fakeWorkerFactory) create(ctx context.Context, _ string, _ camoufoxnative.Options, state aistudio.StorageState) (poolWorker, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	factory.mu.Lock()
	defer factory.mu.Unlock()
	factory.inputs = append(factory.inputs, cloneOfflineState(state))
	if factory.failNext {
		factory.failNext = false
		return nil, errors.New("offline initialization failure")
	}
	return &fakePoolWorker{storage: cloneOfflineState(state), phase: aistudio.WorkerReady}, nil
}

func (factory *fakeWorkerFactory) calls() int {
	factory.mu.Lock()
	defer factory.mu.Unlock()
	return len(factory.inputs)
}

func (factory *fakeWorkerFactory) input(index int) aistudio.StorageState {
	factory.mu.Lock()
	defer factory.mu.Unlock()
	return cloneOfflineState(factory.inputs[index])
}

func (factory *fakeWorkerFactory) failOnce() {
	factory.mu.Lock()
	defer factory.mu.Unlock()
	factory.failNext = true
}

func newOfflinePool() (*WorkerPool, *fakeWorkerFactory) {
	pool := NewWorkerPool("offline-worker-state-unused", "", "zh-CN", "Asia/Shanghai")
	factory := &fakeWorkerFactory{}
	pool.factory = factory.create
	return pool, factory
}

func offlineState(t *testing.T, cookies string) aistudio.StorageState {
	t.Helper()
	state, err := aistudio.ParseCookieText(cookies)
	if err != nil {
		t.Fatalf("parse synthetic cookies: %v", err)
	}
	return state
}

func acquireOffline(t *testing.T, pool *WorkerPool, cookies string) *workerLease {
	t.Helper()
	lease, err := pool.AcquireWorker(context.Background(), cookies, 0, defaultModel, "")
	if err != nil {
		t.Fatalf("acquire offline worker: %v", err)
	}
	t.Cleanup(lease.Release)
	return lease
}

func offlineEntryCount(pool *WorkerPool) int {
	pool.mu.RLock()
	defer pool.mu.RUnlock()
	return len(pool.entries)
}

func cookieFixtureValue(t *testing.T, state aistudio.StorageState, name string) string {
	t.Helper()
	value, ok := state.CookieValue(name, "https://aistudio.google.com/", time.Now())
	if !ok {
		t.Fatalf("missing synthetic cookie %s", name)
	}
	return value
}

func TestWorkerPoolNewShortCookieRebuildsSameEntry(t *testing.T) {
	pool, factory := newOfflinePool()
	first := acquireOffline(t, pool, offlineCookies)
	oldWorker := first.worker.(*fakePoolWorker)
	first.Release()
	updated := strings.Replace(offlineCookies, "fixture-old-ts", "fixture-new-ts", 1)
	second := acquireOffline(t, pool, updated)
	if second.poolState != "cold" || second.worker == oldWorker {
		t.Fatal("new short-lived cookie reused the stale worker")
	}
	if factory.calls() != 2 || oldWorker.closeCount() != 1 || offlineEntryCount(pool) != 1 {
		t.Fatal("replacement must close once and keep the stable account entry")
	}
	if got := cookieFixtureValue(t, factory.input(1), "__Secure-1PSIDTS"); got != "fixture-new-ts" {
		t.Fatal("replacement did not receive new synthetic credentials")
	}
}

func TestWorkerPoolReorderedCookiesReuse(t *testing.T) {
	pool, factory := newOfflinePool()
	first := acquireOffline(t, pool, offlineCookies)
	worker := first.worker
	first.Release()
	reordered := " SIDCC=fixture-old-cc; __Secure-1PSIDTS=fixture-old-ts ; SID=fixture-login; SAPISID=fixture-anchor "
	second := acquireOffline(t, pool, reordered)
	if second.poolState != "hot" || second.worker != worker || factory.calls() != 1 {
		t.Fatal("cookie order or whitespace caused a cold rebuild")
	}
}

func TestWorkerPoolInternalRotationRemainsHot(t *testing.T) {
	pool, factory := newOfflinePool()
	first := acquireOffline(t, pool, offlineCookies)
	worker := first.worker.(*fakePoolWorker)
	first.Release()
	rotated := strings.Replace(offlineCookies, "fixture-old-ts", "fixture-rotated-ts", 1)
	worker.setStorage(offlineState(t, rotated))
	original := acquireOffline(t, pool, offlineCookies)
	if original.poolState != "hot" || original.worker != worker {
		t.Fatal("unchanged caller input discarded internal rotation")
	}
	original.Release()
	reflected := acquireOffline(t, pool, rotated)
	if reflected.poolState != "hot" || reflected.worker != worker || factory.calls() != 1 {
		t.Fatal("reflecting current worker cookies caused a cold rebuild")
	}
}

func TestWorkerPoolRPCReflectionPreservesHostCookies(t *testing.T) {
	pool, factory := newOfflinePool()
	original := offlineCookies + "; __Host-fixture=fixture-host-original"
	first := acquireOffline(t, pool, original)
	worker := first.worker.(*fakePoolWorker)
	first.Release()
	var rpcHeader string
	for index := range 5 {
		current := strings.Replace(original, "fixture-old-ts", fmt.Sprintf("fixture-rotated-%d", index), 1)
		current = strings.Replace(current, "fixture-host-original", fmt.Sprintf("fixture-host-rotated-%d", index), 1)
		state := offlineState(t, current)
		worker.setStorage(state)
		var err error
		rpcHeader, err = state.CookieHeader(generateContentURL, time.Now())
		if err != nil {
			t.Fatal(err)
		}
		// RPC 回传头不含页面专属 Cookie；随后调用仍可能携带磁盘中原来的页面 Cookie。
		for _, input := range []string{rpcHeader, rpcHeader + "; __Host-fixture=fixture-host-original"} {
			lease := acquireOffline(t, pool, input)
			if lease.poolState != "hot" || lease.worker != worker {
				t.Fatal("RPC reflection or unchanged page cookie caused repeated rebuilds")
			}
			lease.Release()
		}
	}
	if factory.calls() != 1 || offlineEntryCount(pool) != 1 {
		t.Fatal("normal rotation grew or recreated the worker pool")
	}
	changedHost := acquireOffline(t, pool, rpcHeader+"; __Host-fixture=fixture-host-reimported")
	if changedHost.poolState != "cold" || factory.calls() != 2 || worker.closeCount() != 1 {
		t.Fatal("changed page-only credentials did not rebuild the existing entry")
	}
}

func TestWorkerPoolCookiePathChangeRebuilds(t *testing.T) {
	pool, factory := newOfflinePool()
	state := offlineState(t, offlineCookies)
	state.Cookies = append(state.Cookies, aistudio.StateCookie{
		Name: "fixture-page", Value: "fixture-page-value", Domain: "aistudio.google.com",
		Path: "/prompts", Expires: -1, Secure: true,
	})
	encoded, err := json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	first := acquireOffline(t, pool, string(encoded))
	worker := first.worker
	first.Release()
	state.Cookies[len(state.Cookies)-1].Path = "/prompts/new_chat"
	encoded, err = json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	second := acquireOffline(t, pool, string(encoded))
	if second.poolState != "cold" || second.worker == worker || factory.calls() != 2 {
		t.Fatal("changed cookie path was hidden by RPC-only normalization")
	}
}

func TestWorkerPoolForceRecreateKeepsCurrentRotation(t *testing.T) {
	pool, factory := newOfflinePool()
	first := acquireOffline(t, pool, offlineCookies)
	worker := first.worker.(*fakePoolWorker)
	first.Release()
	rotated := strings.Replace(offlineCookies, "fixture-old-ts", "fixture-current-ts", 1)
	worker.setStorage(offlineState(t, rotated))
	lease := acquireOffline(t, pool, offlineCookies)
	if err := lease.Recreate(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := cookieFixtureValue(t, factory.input(1), "__Secure-1PSIDTS"); got != "fixture-current-ts" {
		t.Fatal("forced rebuild restored stale caller cookies")
	}
	if lease.poolState != "cold" || worker.closeCount() != 1 || factory.calls() != 2 {
		t.Fatal("forced rebuild failed to replace exactly once")
	}
	lease.Release()
	again := acquireOffline(t, pool, offlineCookies)
	if again.poolState != "hot" || factory.calls() != 2 {
		t.Fatal("forced rebuild forgot the caller input fingerprint")
	}
}

func TestWorkerPoolInitializationFailureDoesNotPublish(t *testing.T) {
	pool, factory := newOfflinePool()
	first := acquireOffline(t, pool, offlineCookies)
	oldWorker := first.worker.(*fakePoolWorker)
	first.Release()
	updated := strings.Replace(offlineCookies, "fixture-old-ts", "fixture-failed-ts", 1)
	factory.failOnce()
	lease, err := pool.AcquireWorker(context.Background(), updated, 0, defaultModel, "")
	if err == nil || lease != nil {
		t.Fatal("failed initialization published a usable lease")
	}
	entry := pool.getEntry(accountKeyFromState(offlineState(t, updated), 0))
	entry.mu.Lock()
	published := entry.workerSnapshot() != nil || entry.inputFingerprint != "" || len(entry.inputCookies) != 0
	entry.mu.Unlock()
	if published || oldWorker.closeCount() != 1 {
		t.Fatal("failed replacement published credentials or left the stale worker live")
	}
	retry := acquireOffline(t, pool, updated)
	if retry.poolState != "cold" || factory.calls() != 3 || offlineEntryCount(pool) != 1 {
		t.Fatal("failed replacement prevented a clean retry on the same entry")
	}
}

func TestWorkerLeaseProtectsHeadersAndStream(t *testing.T) {
	pool, factory := newOfflinePool()
	first := acquireOffline(t, pool, offlineCookies)
	oldWorker := first.worker.(*fakePoolWorker)
	active := make(chan int, 1)
	go func() { active <- pool.ActiveCount() }()
	select {
	case count := <-active:
		if count != 1 {
			t.Fatal("health snapshot did not count the leased worker")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("health snapshot blocked behind the long account lease")
	}
	type acquireResult struct {
		lease *workerLease
		err   error
	}
	started := make(chan struct{})
	finished := make(chan acquireResult, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	go func() {
		close(started)
		lease, err := pool.AcquireWorker(ctx, strings.Replace(offlineCookies, "fixture-old-ts", "fixture-new-ts", 1), 0, defaultModel, "")
		finished <- acquireResult{lease: lease, err: err}
	}()
	<-started
	select {
	case result := <-finished:
		if result.lease != nil {
			result.lease.Release()
		}
		t.Fatal("replacement passed the active account lease")
	case <-time.After(50 * time.Millisecond):
	}
	_, protocol, err := first.worker.ProofByDigest(context.Background(), "offline-digest", "offline-prompt")
	if err != nil {
		t.Fatal(err)
	}
	headers := buildSessionHeaders(context.Background(), first.worker, protocol, "Pro")
	response, err := first.worker.SendProtected(context.Background(), aistudio.ProtectedRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(headers["Cookie"], "fixture-old-ts") || oldWorker.closeCount() != 0 || factory.calls() != 1 {
		t.Fatal("leased proof, headers, and stream used an already replaced worker")
	}
	if _, err := io.ReadAll(response.Body); err != nil {
		t.Fatal(err)
	}
	if err := response.Body.Close(); err != nil {
		t.Fatal(err)
	}
	first.Release()
	select {
	case result := <-finished:
		if result.err != nil || result.lease == nil {
			t.Fatalf("replacement after stream release: %v", result.err)
		}
		result.lease.Release()
	case <-time.After(2 * time.Second):
		t.Fatal("replacement remained blocked after lease release")
	}
	if oldWorker.closeCount() != 1 || factory.calls() != 2 {
		t.Fatal("replacement after stream release did not close exactly once")
	}
}

func TestWorkerPoolActiveCountConcurrentReplacement(t *testing.T) {
	pool, _ := newOfflinePool()
	var waiting sync.WaitGroup
	waiting.Add(1)
	go func() {
		defer waiting.Done()
		for range 500 {
			pool.ActiveCount()
		}
	}()
	for index := range 30 {
		input := strings.Replace(offlineCookies, "fixture-old-ts", fmt.Sprintf("fixture-race-%d", index), 1)
		lease := acquireOffline(t, pool, input)
		lease.Release()
	}
	waiting.Wait()
	if pool.ActiveCount() != 1 || offlineEntryCount(pool) != 1 {
		t.Fatal("concurrent health snapshots changed the stable pool entry")
	}
}

// Python 仅回传滚动字段，NID/AEC 的内部更新仍应由热 Worker 保留。
func TestWorkerPoolPartialCookieReflectionRemainsHot(t *testing.T) {
	pool, factory := newOfflinePool()
	original := offlineCookies + "; NID=fixture-nid-original; AEC=fixture-aec-original"
	first := acquireOffline(t, pool, original)
	worker := first.worker.(*fakePoolWorker)
	first.Release()
	input := original
	current := offlineState(t, original)
	for index := range 5 {
		// 真实合并上游响应，普通 SIDCC/NID 不带 Secure，避免夹具隐藏标志差异。
		err := current.MergeSetCookieHeaders([]string{
			fmt.Sprintf("__Secure-1PSIDTS=fixture-current-ts-%d; Domain=.google.com; Path=/; Secure; HttpOnly", index),
			fmt.Sprintf("SIDCC=fixture-current-cc-%d; Domain=.google.com; Path=/; HttpOnly", index),
			fmt.Sprintf("NID=fixture-nid-internal-%d; Domain=.google.com; Path=/; HttpOnly", index),
			fmt.Sprintf("AEC=fixture-aec-internal-%d; Domain=.google.com; Path=/; Secure; HttpOnly", index),
		}, generateContentURL, time.Now())
		if err != nil {
			t.Fatal(err)
		}
		worker.setStorage(current)
		input = strings.Replace(original, "fixture-old-ts", fmt.Sprintf("fixture-current-ts-%d", index), 1)
		input = strings.Replace(input, "fixture-old-cc", fmt.Sprintf("fixture-current-cc-%d", index), 1)
		lease := acquireOffline(t, pool, input)
		if lease.poolState != "hot" || lease.worker != worker {
			t.Fatal("partial cookie reflection discarded internal NID/AEC updates")
		}
		state, err := worker.BrowserStorageState(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if got := cookieFixtureValue(t, state, "NID"); got != fmt.Sprintf("fixture-nid-internal-%d", index) {
			t.Fatal("unchanged caller NID overwrote the current worker state")
		}
		lease.Release()
	}
	if factory.calls() != 1 || offlineEntryCount(pool) != 1 {
		t.Fatal("partial reflections caused repeated cold initialization")
	}
	external := strings.Replace(input, "fixture-nid-original", "fixture-nid-reimported", 1)
	changed := acquireOffline(t, pool, external)
	if changed.poolState != "cold" || factory.calls() != 2 || worker.closeCount() != 1 {
		t.Fatal("a new external NID value incorrectly reused the current worker")
	}
}

// 使用真实 Set-Cookie 合并路径复现 SIDCC 无 Secure，而原始请求头重新推断 Secure=true 的情况。
func TestWorkerPoolSetCookieWithoutSecureReflectionRemainsHot(t *testing.T) {
	pool, factory := newOfflinePool()
	original := offlineCookies + "; NID=fixture-nid-original"
	first := acquireOffline(t, pool, original)
	worker := first.worker.(*fakePoolWorker)
	first.Release()
	current := offlineState(t, original)
	for index := range 4 {
		cc := fmt.Sprintf("fixture-set-cookie-cc-%d", index)
		err := current.MergeSetCookieHeaders([]string{
			"SIDCC=" + cc + "; Domain=.google.com; Path=/; HttpOnly",
			fmt.Sprintf("NID=fixture-internal-nid-%d; Domain=.google.com; Path=/; HttpOnly", index),
		}, generateContentURL, time.Now())
		if err != nil {
			t.Fatal(err)
		}
		for _, cookie := range current.Cookies {
			if cookie.Name == "SIDCC" && cookie.Secure {
				t.Fatal("Set-Cookie fixture did not reproduce the missing Secure flag")
			}
		}
		worker.setStorage(current)
		input := strings.Replace(original, "fixture-old-cc", cc, 1)
		for _, cookie := range offlineState(t, input).Cookies {
			if cookie.Name == "SIDCC" && !cookie.Secure {
				t.Fatal("header fixture did not reproduce inferred Secure=true")
			}
		}
		lease := acquireOffline(t, pool, input)
		if lease.poolState != "hot" || lease.worker != worker {
			t.Fatal("inferred Secure metadata forced a cold rebuild of current credentials")
		}
		lease.Release()
	}
	if factory.calls() != 1 || worker.closeCount() != 0 || offlineEntryCount(pool) != 1 {
		t.Fatal("Set-Cookie/header round trips repeatedly recreated the worker")
	}
}

func TestWorkerPoolExplicitMetadataChangeRebuildsThenHeaderReuses(t *testing.T) {
	pool, factory := newOfflinePool()
	first := acquireOffline(t, pool, offlineCookies)
	oldWorker := first.worker.(*fakePoolWorker)
	first.Release()
	state := offlineState(t, offlineCookies)
	for index := range state.Cookies {
		if state.Cookies[index].Name == "SIDCC" {
			state.Cookies[index].Secure = false
		}
	}
	encoded, err := json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	explicit := acquireOffline(t, pool, string(encoded))
	worker := explicit.worker
	if explicit.poolState != "cold" || factory.calls() != 2 || oldWorker.closeCount() != 1 {
		t.Fatal("explicitly changed JSON metadata did not rebuild the old worker")
	}
	explicit.Release()
	reflected := acquireOffline(t, pool, offlineCookies)
	if reflected.poolState != "hot" || reflected.worker != worker || factory.calls() != 2 {
		t.Fatal("inferred header metadata reversed the explicit worker metadata")
	}
}
