package camoufoxnative

import (
	"context"
	"errors"
	"io"
	"net/http"
	"time"
)

// ErrAuthenticationRequired 表示隔离账户需要恢复登录
var ErrAuthenticationRequired = errors.New("隔离登录态失效")

// ErrRegionNotSupported 表示 AI Studio 不支持当前出口地区或账户
var ErrRegionNotSupported = errors.New("AI Studio 不支持当前出口地区或账户，为该账户或全局代理换用支持地区的出口")

// AvailableRegionsURL 是官网在地区或账户不受支持时跳转的页面
const AvailableRegionsURL = "https://aistudio.google.com/docs/available-regions"

// StartupStage 表示 WAA runtime 的启动阶段
type StartupStage string

const (
	StartupPreparingBrowser StartupStage = "preparing_browser"
	StartupLaunchingBrowser StartupStage = "launching_browser"
	StartupConnectingBiDi   StartupStage = "connecting_bidi"
	StartupLoadingAIStudio  StartupStage = "loading_ai_studio"
	StartupLocatingWAA      StartupStage = "locating_waa"
	StartupBootstrappingWAA StartupStage = "bootstrapping_waa"
)

// DefaultBootstrapPrompt 是 Options 未指定 BootstrapPrompt 时 WAA bootstrap 写入官网输入框并提交的提示词
const DefaultBootstrapPrompt = "Hello"

// LiveView 占位结构
type LiveView struct{}

// Options 定义单个 AI Studio 账户的 WAA runtime
type Options struct {
	ExecutablePath   string
	StorageStatePath string
	Model            string
	BootstrapPrompt  string
	Locale           string
	Timezone         string
	Proxy            string
	ProxyBypass      string
	Headless         bool
	TemporaryChat    bool
	AuthUser         int
	CacheDirectory   string
	ReadyTimeout     time.Duration
	Log              io.Writer
	StartupProgress  func(StartupStage)
	liveView         bool
}

func (options Options) reportStartup(stage StartupStage) {
	if options.StartupProgress != nil {
		options.StartupProgress(stage)
	}
}

// State 返回 runtime 的当前页面与 bootstrap 结果
type State struct {
	PID         int
	PageURL     string
	UserAgent   string
	SnapshotKey string
	Headers     http.Header
}

// ProtectedResponse 表示流式返回的 MakerSuite 响应
type ProtectedResponse struct {
	StatusCode int
	Header     http.Header
	Body       io.ReadCloser
}

// FollowUp 是受保护请求发出 Delay 后以相同请求头发出的伴随请求，响应被丢弃
type FollowUp struct {
	URL   string
	Body  []byte
	Delay time.Duration
}

type followUpKey struct{}

// WithFollowUp 返回携带伴随请求的上下文
func WithFollowUp(ctx context.Context, followUp FollowUp) context.Context {
	return context.WithValue(ctx, followUpKey{}, followUp)
}

// FollowUpFrom 返回上下文中的伴随请求
func FollowUpFrom(ctx context.Context) (FollowUp, bool) {
	followUp, ok := ctx.Value(followUpKey{}).(FollowUp)
	return followUp, ok
}

func number(value any) (float64, bool) {
	switch item := value.(type) {
	case float64:
		return item, true
	case int:
		return float64(item), true
	default:
		return 0, false
	}
}
