package aistudio

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

var fallbackDNSOnce sync.Once

func resolvConfHasNameserver(path string) bool {
	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "nameserver" {
			return true
		}
	}
	return false
}

func parseResolvNameservers(path string) []string {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var servers []string
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "nameserver" {
			ip := net.ParseIP(fields[1])
			if ip != nil && !ip.IsLoopback() {
				servers = append(servers, net.JoinHostPort(ip.String(), "53"))
			}
		}
	}
	return servers
}

// EnsureFallbackDNS configures net.DefaultResolver for environments like Android Termux
// where a GOOS=linux netgo binary lacks /etc/resolv.conf and would otherwise fail on [::1]:53.
func EnsureFallbackDNS() {
	if runtime.GOOS == "windows" || runtime.GOOS == "darwin" {
		return
	}
	fallbackDNSOnce.Do(func() {
		if resolvConfHasNameserver("/etc/resolv.conf") {
			return
		}

		var candidates []string
		if prefix := strings.TrimSpace(os.Getenv("PREFIX")); prefix != "" {
			candidates = append(candidates, parseResolvNameservers(filepath.Join(prefix, "etc", "resolv.conf"))...)
		}
		candidates = append(candidates, parseResolvNameservers("/data/data/com.termux/files/usr/etc/resolv.conf")...)
		candidates = append(candidates, "8.8.8.8:53", "1.1.1.1:53", "223.5.5.5:53")

		seen := map[string]struct{}{}
		servers := make([]string, 0, len(candidates))
		for _, srv := range candidates {
			if _, ok := seen[srv]; !ok {
				seen[srv] = struct{}{}
				servers = append(servers, srv)
			}
		}

		net.DefaultResolver = &net.Resolver{
			PreferGo: true,
			Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
				dialer := &net.Dialer{Timeout: 3 * time.Second}
				var lastErr error
				for _, srv := range servers {
					conn, err := dialer.DialContext(ctx, network, srv)
					if err == nil {
						return conn, nil
					}
					lastErr = err
				}
				if lastErr != nil {
					return nil, lastErr
				}
				return dialer.DialContext(ctx, network, "8.8.8.8:53")
			},
		}
	})
}
