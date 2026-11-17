package aistudio

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Mag1cFall/AIStudio2API/internal/camoufoxnative"
)

func newTestClientNoRedirect(srv *httptest.Server) *http.Client {
	client := srv.Client()
	client.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}
	return client
}

func TestLoadPageDiagnostics(t *testing.T) {
	t.Run("servicerestricted redirect", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "https://myaccount.google.com/restrictions/200?hl=en", http.StatusFound)
		}))
		defer srv.Close()

		rt := &goWAARuntime{client: newTestClientNoRedirect(srv), pageURL: srv.URL + "/prompts/new_chat"}
		_, err := rt.loadPage(context.Background())
		if err == nil || !strings.Contains(err.Error(), "servicerestricted") {
			t.Fatalf("expected servicerestricted error, got %v", err)
		}
	})

	t.Run("available regions redirect", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "https://aistudio.google.com/docs/available-regions", http.StatusFound)
		}))
		defer srv.Close()

		rt := &goWAARuntime{client: newTestClientNoRedirect(srv), pageURL: srv.URL + "/prompts/new_chat"}
		_, err := rt.loadPage(context.Background())
		if !errors.Is(err, camoufoxnative.ErrRegionNotSupported) {
			t.Fatalf("expected ErrRegionNotSupported, got %v", err)
		}
	})

	t.Run("missing WIu0Nc includes title", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("<html><head><title>Account Restricted</title></head><body>no key</body></html>"))
		}))
		defer srv.Close()

		rt := &goWAARuntime{client: srv.Client(), pageURL: srv.URL + "/prompts/new_chat"}
		_, err := rt.loadPage(context.Background())
		if err == nil || !strings.Contains(err.Error(), "Account Restricted") {
			t.Fatalf("expected title in error, got %v", err)
		}
	})
}
