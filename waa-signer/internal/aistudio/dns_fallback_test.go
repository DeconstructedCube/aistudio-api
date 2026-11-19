package aistudio

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParseResolvNameserversAndDetection(t *testing.T) {
	tmp := t.TempDir()
	confPath := filepath.Join(tmp, "resolv.conf")
	content := "# comment\nnameserver 127.0.0.1\nnameserver ::1\nnameserver 8.8.8.8\nnameserver 1.1.1.1\n"
	if err := os.WriteFile(confPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	if !resolvConfHasNameserver(confPath) {
		t.Fatal("expected resolvConfHasNameserver to return true")
	}
	servers := parseResolvNameservers(confPath)
	if len(servers) != 2 || servers[0] != "8.8.8.8:53" || servers[1] != "1.1.1.1:53" {
		t.Fatalf("unexpected parsed nameservers (loopback must be excluded): %v", servers)
	}
	if resolvConfHasNameserver(filepath.Join(tmp, "missing.conf")) {
		t.Fatal("expected missing resolv.conf to return false")
	}
}
