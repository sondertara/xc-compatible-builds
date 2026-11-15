package dbx

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	sidecar "github.com/sondertara/xc-compatible-builds/shared/mygo-sidecar"
)

func TestAuthenticateSuccess(t *testing.T) {
	const expectedToken = "sess-abc-123"
	adapter := NewAdapter(Options{})
	adapter.password = "secret-pass"

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/auth/login":
			if r.Method != http.MethodPost {
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			var body struct {
				Password string `json:"password"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Password != "secret-pass" {
				w.WriteHeader(http.StatusUnauthorized)
				return
			}
			http.SetCookie(w, &http.Cookie{
				Name:     "dbx_session",
				Value:    expectedToken,
				Path:     "/",
				HttpOnly: true,
				SameSite: http.SameSiteLaxMode,
			})
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"ok":true}`))
		case "/api/auth/check":
			cookie, err := r.Cookie("dbx_session")
			authed := err == nil && cookie.Value == expectedToken
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"authenticated":  authed,
				"required":       true,
				"setup_required": false,
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	cookies, err := adapter.Authenticate(context.Background(), srv.Client(), srv.URL)
	if err != nil {
		t.Fatalf("Authenticate failed: %v", err)
	}
	if len(cookies) != 1 || cookies[0].Name != "dbx_session" || cookies[0].Value != expectedToken {
		t.Fatalf("unexpected cookies: %+v", cookies)
	}
	if !cookies[0].HTTPOnly || cookies[0].Domain != "127.0.0.1" || cookies[0].Path != "/" {
		t.Fatalf("unexpected cookie attributes: %+v", cookies[0])
	}
}

func TestAuthenticateRejectsWrongPassword(t *testing.T) {
	adapter := NewAdapter(Options{})
	adapter.password = "wrong-pass"

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()

	_, err := adapter.Authenticate(context.Background(), srv.Client(), srv.URL)
	if err == nil || !strings.Contains(err.Error(), "401") {
		t.Fatalf("expected HTTP 401 error, got: %v", err)
	}
}

func TestAuthenticateRejectsUnauthenticatedCheck(t *testing.T) {
	adapter := NewAdapter(Options{})
	adapter.password = "secret-pass"

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/auth/login":
			http.SetCookie(w, &http.Cookie{Name: "dbx_session", Value: "bad-token"})
			_, _ = w.Write([]byte(`{"ok":true}`))
		case "/api/auth/check":
			_, _ = w.Write([]byte(`{"authenticated":false,"required":true,"setup_required":false}`))
		}
	}))
	defer srv.Close()

	_, err := adapter.Authenticate(context.Background(), srv.Client(), srv.URL)
	if err == nil || !strings.Contains(err.Error(), "failed authentication check") {
		t.Fatalf("expected failed authentication check error, got: %v", err)
	}
}

func TestResolvePathsAndPrepareEnv(t *testing.T) {
	root := t.TempDir()
	binDir := filepath.Join(root, "app", "bin")
	libexecDir := filepath.Join(root, "app", "libexec")
	staticDir := filepath.Join(root, "app", "share", "dbx", "dist")
	dataDir := filepath.Join(root, "data")

	for _, d := range []string{binDir, libexecDir, staticDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}

	fakeBackend := filepath.Join(libexecDir, "dbx-web-bin")
	if err := os.WriteFile(fakeBackend, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(staticDir, "index.html"), []byte("<html></html>"), 0o644); err != nil {
		t.Fatal(err)
	}

	t.Setenv("DBX_BACKEND_BIN", "")
	t.Setenv("DBX_STATIC_DIR", "")
	t.Setenv("DBX_PUBLIC_BASE_PATH", "/stale-prefix")

	adapter := NewAdapter(Options{DataDir: dataDir})
	adapter.exeDirFn = func() (string, error) { return binDir, nil }

	spec, err := adapter.Prepare(context.Background(), 45678)
	if err != nil {
		t.Fatalf("Prepare failed: %v", err)
	}
	if spec.BinaryPath != fakeBackend {
		t.Fatalf("unexpected BinaryPath: got %q want %q", spec.BinaryPath, fakeBackend)
	}
	if spec.WorkDir != dataDir {
		t.Fatalf("unexpected WorkDir: got %q want %q", spec.WorkDir, dataDir)
	}
	if spec.ReadyPath != "/api/auth/check" {
		t.Fatalf("unexpected ReadyPath: %q", spec.ReadyPath)
	}

	envMap := map[string]string{}
	for _, entry := range spec.Env {
		k, v, ok := strings.Cut(entry, "=")
		if ok {
			envMap[k] = v
		}
	}
	if envMap["DBX_BIND_ADDR"] != "127.0.0.1" {
		t.Fatalf("unexpected DBX_BIND_ADDR: %q", envMap["DBX_BIND_ADDR"])
	}
	if envMap["DBX_PORT"] != "45678" {
		t.Fatalf("unexpected DBX_PORT: %q", envMap["DBX_PORT"])
	}
	if envMap["DBX_STATIC_DIR"] != staticDir {
		t.Fatalf("unexpected DBX_STATIC_DIR: got %q want %q", envMap["DBX_STATIC_DIR"], staticDir)
	}
	if envMap["DBX_DISABLE_PASSWORD"] != "0" || len(envMap["DBX_PASSWORD"]) != 64 {
		t.Fatalf("unexpected password settings: disable=%q len=%d", envMap["DBX_DISABLE_PASSWORD"], len(envMap["DBX_PASSWORD"]))
	}
	if _, exists := envMap["DBX_PUBLIC_BASE_PATH"]; exists {
		t.Fatal("DBX_PUBLIC_BASE_PATH should be unset in sidecar env")
	}

	meta := adapter.Meta()
	if meta.ID != "com.dbx.desktop" || len(meta.IconPNG) == 0 {
		t.Fatalf("unexpected Meta: id=%q icon_len=%d", meta.ID, len(meta.IconPNG))
	}
}

func TestResolveBackendFallbackAndMissing(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("DBX_BACKEND_BIN", "")

	sibling := filepath.Join(dir, "dbx-web-bin")
	if err := os.WriteFile(sibling, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	got, err := ResolveBackendBinary("", dir)
	if err != nil || got != sibling {
		t.Fatalf("expected sibling backend %q, got %q (err=%v)", sibling, got, err)
	}

	if _, err := ResolveBackendBinary(filepath.Join(dir, "nonexistent"), dir); err == nil {
		t.Fatal("expected error for missing explicit backend path")
	}
}

func TestValidateUISmoke(t *testing.T) {
	adapter := NewAdapter(Options{})

	if err := adapter.ValidateUISmoke(sidecar.UISmokeReport{
		URL:                "http://com-dbx-desktop.localhost/",
		Pathname:           "/",
		ElementCount:       85,
		PasswordInputCount: 0,
	}); err != nil {
		t.Fatalf("expected valid workspace UI to pass, got %v", err)
	}

	if err := adapter.ValidateUISmoke(sidecar.UISmokeReport{
		URL:                "http://127.0.0.1:45678/",
		Pathname:           "/",
		ElementCount:       85,
		PasswordInputCount: 0,
	}); err == nil {
		t.Fatal("expected raw loopback port origin to fail virtual-origin UI smoke check")
	}

	if err := adapter.ValidateUISmoke(sidecar.UISmokeReport{
		Pathname:           "/",
		ElementCount:       24,
		PasswordInputCount: 1,
	}); err == nil {
		t.Fatal("expected password prompt to fail UI smoke")
	}

	if err := adapter.ValidateUISmoke(sidecar.UISmokeReport{
		Pathname:           "/login",
		ElementCount:       24,
		PasswordInputCount: 0,
	}); err == nil {
		t.Fatal("expected /login route to fail UI smoke")
	}

	if err := adapter.ValidateUISmoke(sidecar.UISmokeReport{
		Pathname:     "/",
		ElementCount: 3,
	}); err == nil {
		t.Fatal("expected unmounted DOM to fail UI smoke")
	}
}
