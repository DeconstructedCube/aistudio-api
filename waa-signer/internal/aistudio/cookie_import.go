package aistudio

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// ErrLoginCookieMissing 表示 Cookie 中没有登录 AI Studio 所需的 Cookie
var ErrLoginCookieMissing = errors.New("Cookie 缺少登录 Cookie")

var knownHTTPOnlyGoogleCookies = map[string]bool{
	"HSID":             true,
	"SSID":             true,
	"__Secure-1PSID":   true,
	"__Secure-3PSID":   true,
	"__Secure-1PSIDTS": true,
	"__Secure-3PSIDTS": true,
	"__Secure-1PSIDCC": true,
	"__Secure-3PSIDCC": true,
	"SEARCH_SAMESITE":  true,
	"NID":              true,
	"AEC":              true,
	"OSID":             true,
	"__Secure-OSID":    true,
	"LSID":             true,
}

// ParseCookieText 把 Cookie 请求头、浏览器扩展导出的 JSON 或 Netscape cookies.txt 转为 storage state，只保留 google.com 及其子域的 Cookie
func ParseCookieText(input string) (StorageState, error) {
	text := strings.TrimSpace(strings.TrimPrefix(input, string(rune(0xFEFF))))
	var state StorageState
	var err error
	switch {
	case strings.HasPrefix(text, "[") || strings.HasPrefix(text, "{"):
		state, err = parseCookieJSON(text)
	case strings.Contains(text, "\t"):
		state.Cookies, err = parseNetscapeCookies(text)
	default:
		state.Cookies, err = parseCookieHeader(text)
	}
	if err != nil {
		return StorageState{}, err
	}
	unique := make(map[[3]string]int, len(state.Cookies))
	cookies := make([]StateCookie, 0, len(state.Cookies))
	for _, cookie := range state.Cookies {
		key := [3]string{strings.ToLower(cookie.Domain), cookie.Path, cookie.Name}
		if index, exists := unique[key]; exists {
			cookies[index] = cookie
			continue
		}
		unique[key] = len(cookies)
		cookies = append(cookies, cookie)
	}
	state.Cookies = cookies
	now := time.Now()
	_, hasSAPISID := state.CookieValue("SAPISID", aiStudioOrigin+"/", now)
	_, has3P := state.CookieValue("__Secure-3PAPISID", aiStudioOrigin+"/", now)
	if !hasSAPISID && !has3P {
		return StorageState{}, fmt.Errorf("%w SAPISID，请在已登录 AI Studio 的浏览器中导出", ErrLoginCookieMissing)
	}
	return state, state.Validate()
}

// googleCookieDomain 报告 Cookie 域是否为 google.com 或其子域
func googleCookieDomain(domain string) bool {
	domain = strings.ToLower(strings.TrimPrefix(strings.TrimSpace(domain), "."))
	return domain == "google.com" || strings.HasSuffix(domain, ".google.com")
}

// exportedCookie 是 Cookie-Editor 等扩展与 Playwright storage state 导出的 Cookie
type exportedCookie struct {
	Name           string   `json:"name"`
	Value          string   `json:"value"`
	Domain         string   `json:"domain"`
	Path           string   `json:"path"`
	Expires        *float64 `json:"expires"`
	ExpirationDate *float64 `json:"expirationDate"`
	Session        bool     `json:"session"`
	HTTPOnly       *bool    `json:"httpOnly"`
	Secure         *bool    `json:"secure"`
	SameSite       string   `json:"sameSite"`
}

// parseCookieJSON 解析 Cookie 数组或带 cookies 与 origins 字段的 storage state
func parseCookieJSON(text string) (StorageState, error) {
	var exported []exportedCookie
	var origins []StorageOrigin
	if strings.HasPrefix(text, "{") {
		var document struct {
			Cookies []exportedCookie `json:"cookies"`
			Origins []StorageOrigin  `json:"origins"`
		}
		if err := json.Unmarshal([]byte(text), &document); err != nil {
			return StorageState{}, fmt.Errorf("Cookie JSON 无效: %w", err)
		}
		exported, origins = document.Cookies, document.Origins
	} else if err := json.Unmarshal([]byte(text), &exported); err != nil {
		return StorageState{}, fmt.Errorf("Cookie JSON 无效: %w", err)
	}
	cookies := make([]StateCookie, 0, len(exported))
	for _, item := range exported {
		if item.Name == "" || item.Value == "" {
			continue
		}
		domain := strings.TrimSpace(item.Domain)
		if domain == "" {
			if strings.HasPrefix(item.Name, "__Host-") {
				domain = "aistudio.google.com"
			} else {
				domain = ".google.com"
			}
		}
		if !googleCookieDomain(domain) {
			continue
		}
		expires := float64(-1)
		switch {
		case item.Session:
		case item.Expires != nil:
			expires = *item.Expires
		case item.ExpirationDate != nil:
			expires = *item.ExpirationDate
		}
		path := item.Path
		if path == "" {
			path = "/"
		}
		httpOnly := knownHTTPOnlyGoogleCookies[item.Name]
		if item.HTTPOnly != nil {
			httpOnly = *item.HTTPOnly
		}
		secure := true
		if item.Secure != nil {
			secure = *item.Secure
		}
		cookies = append(cookies, StateCookie{
			Name: item.Name, Value: item.Value, Domain: domain, Path: path, Expires: expires,
			HTTPOnly: httpOnly, Secure: secure, SameSite: cookieSameSite(item.SameSite),
		})
	}
	googleOrigins := make([]StorageOrigin, 0, len(origins))
	for _, origin := range origins {
		if host, _, _ := strings.Cut(strings.TrimPrefix(origin.Origin, "https://"), "/"); strings.HasPrefix(origin.Origin, "https://") && googleCookieDomain(host) {
			googleOrigins = append(googleOrigins, origin)
		}
	}
	return StorageState{Cookies: cookies, Origins: googleOrigins}, nil
}

// cookieSameSite 把扩展导出的 SameSite 取值转为 storage state 写法
func cookieSameSite(value string) string {
	switch strings.ToLower(value) {
	case "no_restriction", "none":
		return "None"
	case "lax":
		return "Lax"
	case "strict":
		return "Strict"
	}
	return ""
}

// parseNetscapeCookies 解析 Netscape cookies.txt，#HttpOnly_ 前缀表示 HttpOnly，过期时间 0 表示会话 Cookie
func parseNetscapeCookies(text string) ([]StateCookie, error) {
	var cookies []StateCookie
	for number, line := range strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		httpOnly := strings.HasPrefix(line, "#HttpOnly_")
		line = strings.TrimPrefix(line, "#HttpOnly_")
		if strings.TrimSpace(line) == "" || strings.HasPrefix(line, "#") {
			continue
		}
		fields := strings.Split(line, "\t")
		if len(fields) != 7 {
			return nil, fmt.Errorf("cookies.txt 第 %d 行不是 7 个制表符分隔的字段", number+1)
		}
		if !googleCookieDomain(fields[0]) || fields[6] == "" {
			continue
		}
		expires, err := strconv.ParseFloat(fields[4], 64)
		if err != nil {
			return nil, fmt.Errorf("cookies.txt 第 %d 行的过期时间无效", number+1)
		}
		if expires == 0 {
			expires = -1
		}
		cookies = append(cookies, StateCookie{
			Name: fields[5], Value: fields[6], Domain: fields[0], Path: fields[2], Expires: expires,
			HTTPOnly: httpOnly || knownHTTPOnlyGoogleCookies[fields[5]], Secure: strings.EqualFold(fields[3], "TRUE"),
		})
	}
	return cookies, nil
}

// parseCookieHeader 解析一行 Cookie 请求头，__Host- 前缀的 Cookie 归属 aistudio.google.com，其余归属 .google.com
func parseCookieHeader(header string) ([]StateCookie, error) {
	if len(header) >= len("Cookie:") && strings.EqualFold(header[:len("Cookie:")], "Cookie:") {
		header = strings.TrimSpace(header[len("Cookie:"):])
	}
	header = strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(header, "\r", ";"), "\n", ";"))
	if header == "" {
		return nil, fmt.Errorf("Cookie 请求头不能为空")
	}
	cookies := make([]StateCookie, 0)
	for _, item := range strings.Split(header, ";") {
		trimmed := strings.TrimSpace(item)
		if trimmed == "" {
			continue
		}
		name, value, found := strings.Cut(trimmed, "=")
		name = strings.TrimSpace(name)
		if !found || name == "" {
			continue
		}
		domain := ".google.com"
		if strings.HasPrefix(name, "__Host-") {
			domain = "aistudio.google.com"
		}
		cookies = append(cookies, StateCookie{
			Name:     name,
			Value:    strings.TrimSpace(value),
			Domain:   domain,
			Path:     "/",
			Expires:  -1,
			HTTPOnly: knownHTTPOnlyGoogleCookies[name],
			Secure:   true,
			SameSite: "None",
		})
	}
	if len(cookies) == 0 {
		return nil, fmt.Errorf("未解析到任何有效 Cookie")
	}
	return cookies, nil
}
