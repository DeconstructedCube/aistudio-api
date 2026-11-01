package aistudio

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"time"
	"unicode"

	"github.com/Mag1cFall/AIStudio2API/internal/camoufoxnative"
)

const publicDiscoveryUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"

var makerSuiteAPIKeyPattern = regexp.MustCompile(`"WIu0Nc":"([^"]+)"`)

// RPCResponse 描述认证传输层返回的响应头和实时正文
type RPCResponse struct {
	StatusCode int
	Header     http.Header
	Body       io.ReadCloser
}

// ProtectedPreparer 为一次请求写入 fresh WAA proof 并发送
type ProtectedPreparer interface {
	Prepare(context.Context, ProtectedRequest) (PreparedProtectedRequest, error)
	BrowserStorageState(context.Context) (StorageState, error)
	SendProtected(context.Context, ProtectedRequest) (*RPCResponse, error)
}

// ProtocolHeaderProvider 按账户提供官方运行时发现的动态公共头
type ProtocolHeaderProvider interface {
	ProtocolHeaders(context.Context, string) (http.Header, error)
}

func newVisitID() (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("生成 AI Studio visit ID: %w", err)
	}
	value[6] = value[6]&0x0f | 0x40
	value[8] = value[8]&0x3f | 0x80
	uuid := fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		value[0:4], value[4:6], value[6:8], value[8:10], value[10:16],
	)
	return "v1_" + base64.StdEncoding.EncodeToString([]byte(uuid)), nil
}

// NewProxyHTTPClient 创建普通 HTTP、HTTPS 或 SOCKS5 固定出口客户端
func NewProxyHTTPClient(proxyURL string) (*http.Client, error) {
	roundTripper, err := newBrowserRoundTripper(proxyURL)
	if err != nil {
		return nil, err
	}
	return &http.Client{Transport: roundTripper}, nil
}

// RPCError 保存上游状态和协议错误码
type RPCError struct {
	Method     string
	StatusCode int
	Code       int64
	Message    string
	Metadata   map[string]string
	RetryDelay time.Duration
}

func (e *RPCError) regionNotSupported() bool {
	return e.StatusCode == http.StatusForbidden && e.Code == 7 && (e.Message == "Region not supported." || e.Message == "Account not supported.")
}

func (e *RPCError) Error() string {
	if e.regionNotSupported() {
		return fmt.Sprintf("AI Studio %s 返回 HTTP %d、协议错误码 %d: %s（%v，官网跳转 %s）", e.Method, e.StatusCode, e.Code, e.Message, camoufoxnative.ErrRegionNotSupported, camoufoxnative.AvailableRegionsURL)
	}
	if e.Code != 0 {
		return fmt.Sprintf("AI Studio %s 返回 HTTP %d、协议错误码 %d: %s", e.Method, e.StatusCode, e.Code, e.Message)
	}
	return fmt.Sprintf("AI Studio %s 返回 HTTP %d: %s", e.Method, e.StatusCode, e.Message)
}

func (e *RPCError) Unwrap() error {
	if e.regionNotSupported() {
		return camoufoxnative.ErrRegionNotSupported
	}
	return nil
}

// DecodeRPCError 解析独立状态和流式封装中的上游错误
func DecodeRPCError(method string, statusCode int, raw []byte) *RPCError {
	rpcError := &RPCError{
		Method:     method,
		StatusCode: statusCode,
		Message:    http.StatusText(statusCode),
	}
	value, err := decodeJSONValue(raw)
	if err != nil {
		return rpcError
	}
	root, err := rawArray(value, "$", value)
	if err != nil || len(root) < 2 || isJSONNull(root[1]) {
		return rpcError
	}
	provider, providerPath := root, "$"
	if bytes.HasPrefix(bytes.TrimSpace(root[1]), []byte("[")) {
		provider, err = rawArray(root[1], "$[1]", value)
		if err != nil || len(provider) < 2 {
			return rpcError
		}
		providerPath = "$[1]"
	}
	if code, err := rawInt64(provider[0], providerPath+"[0]", value); err == nil {
		rpcError.Code = code
	}
	if message, err := rawString(provider[1], providerPath+"[1]", value); err == nil && message != "" {
		rpcError.Message = message
	}
	return rpcError
}

type ProtocolEvidenceError struct {
	Method string
	Path   string
	Detail string
	Raw    json.RawMessage
}

func (e *ProtocolEvidenceError) Error() string {
	if e.Method == "" {
		return fmt.Sprintf("协议位置 %s: %s", e.Path, e.Detail)
	}
	return fmt.Sprintf("AI Studio %s 协议位置 %s: %s", e.Method, e.Path, e.Detail)
}

type sparseJSONReader struct {
	source        *bufio.Reader
	pending       []byte
	inString      bool
	escaped       bool
	previousToken byte
}

func newSparseJSONReader(source io.Reader) io.Reader {
	return &sparseJSONReader{source: bufio.NewReader(source)}
}

func (r *sparseJSONReader) Read(destination []byte) (int, error) {
	written := 0
	for written < len(destination) {
		if len(r.pending) > 0 {
			count := copy(destination[written:], r.pending)
			r.pending = r.pending[count:]
			written += count
			continue
		}
		if written > 0 && r.source.Buffered() == 0 {
			return written, nil
		}
		value, err := r.source.ReadByte()
		if err != nil {
			if written > 0 {
				return written, nil
			}
			return 0, err
		}
		r.pending = r.normalize(value)
	}
	return written, nil
}

func (r *sparseJSONReader) normalize(value byte) []byte {
	if r.inString {
		if r.escaped {
			r.escaped = false
		} else if value == '\\' {
			r.escaped = true
		} else if value == '"' {
			r.inString = false
		}
		return []byte{value}
	}
	if value == '"' {
		r.inString = true
		r.previousToken = value
		return []byte{value}
	}
	if value == ',' && (r.previousToken == '[' || r.previousToken == ',') {
		r.previousToken = value
		return []byte("null,")
	}
	if value == ']' && r.previousToken == ',' {
		r.previousToken = value
		return []byte("null]")
	}
	if !unicode.IsSpace(rune(value)) {
		r.previousToken = value
	}
	return []byte{value}
}

func decodeJSONValue(raw []byte) (json.RawMessage, error) {
	decoder := json.NewDecoder(newSparseJSONReader(bytes.NewReader(raw)))
	decoder.UseNumber()
	var value json.RawMessage
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var extra json.RawMessage
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("JSON+protobuf 包含多个根值")
		}
		return nil, err
	}
	return value, nil
}

func rawArray(raw json.RawMessage, path string, evidence json.RawMessage) ([]json.RawMessage, error) {
	var values []json.RawMessage
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&values); err != nil {
		return nil, &ProtocolEvidenceError{Path: path, Detail: "期望数组", Raw: cloneEvidence(raw, evidence)}
	}
	return values, nil
}

func rawString(raw json.RawMessage, path string, evidence json.RawMessage) (string, error) {
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", &ProtocolEvidenceError{Path: path, Detail: "期望字符串", Raw: cloneEvidence(raw, evidence)}
	}
	return value, nil
}

func rawInt64(raw json.RawMessage, path string, evidence json.RawMessage) (int64, error) {
	var number json.Number
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&number); err != nil {
		return 0, &ProtocolEvidenceError{Path: path, Detail: "期望整数", Raw: cloneEvidence(raw, evidence)}
	}
	value, err := strconv.ParseInt(number.String(), 10, 64)
	if err != nil {
		return 0, &ProtocolEvidenceError{Path: path, Detail: "期望整数", Raw: cloneEvidence(raw, evidence)}
	}
	return value, nil
}

func isJSONNull(raw json.RawMessage) bool {
	trimmed := bytes.TrimSpace(raw)
	return len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null"))
}

func cloneEvidence(primary json.RawMessage, fallback json.RawMessage) json.RawMessage {
	if len(primary) > 0 {
		return append(json.RawMessage(nil), primary...)
	}
	return append(json.RawMessage(nil), fallback...)
}
