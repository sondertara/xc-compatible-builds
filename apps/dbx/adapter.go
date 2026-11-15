package dbx

import (
	"bytes"
	"context"
	"crypto/rand"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/sondertara/xc-compatible-builds/shared/mygo-sidecar"
)

//go:embed assets/icon.png
var embeddedIconPNG []byte

// Options configures how the DBX desktop adapter resolves paths and launches dbx-web-bin.
type Options struct {
	BackendPath string
	StaticDir   string
	DataDir     string
}

// Adapter wraps official static dbx-web-bin for execution under shared/mygo-sidecar.
type Adapter struct {
	opts     Options
	password string
	exeDirFn func() (string, error)
}

// NewAdapter constructs a DBX sidecar adapter with optional explicit path overrides.
func NewAdapter(opts Options) *Adapter {
	return &Adapter{
		opts:     opts,
		exeDirFn: defaultExecutableDir,
	}
}

// Meta implements sidecar.Adapter.
func (a *Adapter) Meta() sidecar.AppMeta {
	return sidecar.AppMeta{
		ID:              "com.dbx.desktop",
		Name:            "DBX",
		Width:           1400,
		Height:          880,
		MinWidth:        960,
		MinHeight:       620,
		BackgroundColor: "#0f172a",
		IconPNG:         embeddedIconPNG,
	}
}

// Prepare implements sidecar.Adapter by generating an ephemeral session secret and backend spec.
func (a *Adapter) Prepare(_ context.Context, port int) (*sidecar.BackendSpec, error) {
	if port <= 0 || port > 65535 {
		return nil, fmt.Errorf("invalid loopback port %d", port)
	}

	exeDir, err := a.executableDir()
	if err != nil {
		return nil, err
	}

	backendPath, err := ResolveBackendBinary(a.opts.BackendPath, exeDir)
	if err != nil {
		return nil, err
	}

	dataDir, err := ResolveDataDir(a.opts.DataDir)
	if err != nil {
		return nil, err
	}

	staticDir := ResolveStaticDir(a.opts.StaticDir, exeDir)

	pw, err := generateEphemeralPassword()
	if err != nil {
		return nil, fmt.Errorf("generate ephemeral password: %w", err)
	}
	a.password = pw

	overrides := map[string]string{
		"DBX_BIND_ADDR":        "127.0.0.1",
		"DBX_PORT":             strconv.Itoa(port),
		"DBX_DATA_DIR":         dataDir,
		"DBX_PASSWORD":         pw,
		"DBX_DISABLE_PASSWORD": "0",
		"DBX_WEB_CAPTCHA":      "off",
	}
	unsetKeys := []string{
		"DBX_PUBLIC_BASE_PATH",
		"DBX_WEB_COOKIE_SECURE",
	}
	if staticDir != "" {
		overrides["DBX_STATIC_DIR"] = staticDir
	} else {
		unsetKeys = append(unsetKeys, "DBX_STATIC_DIR")
	}

	return &sidecar.BackendSpec{
		BinaryPath:   backendPath,
		WorkDir:      dataDir,
		Env:          sidecar.MergeEnv(os.Environ(), overrides, unsetKeys...),
		ReadyPath:    "/api/auth/check",
		ReadyTimeout: 30 * time.Second,
	}, nil
}

// Authenticate implements sidecar.Adapter by exchanging the ephemeral password for a dbx_session cookie.
func (a *Adapter) Authenticate(ctx context.Context, client *http.Client, baseURL string) ([]sidecar.AuthCookie, error) {
	if a.password == "" {
		return nil, errors.New("ephemeral password was not initialized; Prepare must be called first")
	}
	if client == nil {
		client = sidecar.NewLoopbackHTTPClient(5 * time.Second)
	}
	baseURL = strings.TrimRight(baseURL, "/")

	payload, err := json.Marshal(struct {
		Password string `json:"password"`
	}{
		Password: a.password,
	})
	if err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/api/auth/login", bytes.NewReader(payload))
	if err != nil {
		return nil, fmt.Errorf("build login request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("post /api/auth/login: %w", err)
	}
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	_ = resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("login returned HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}

	var sessionToken string
	for _, c := range resp.Cookies() {
		if c.Name == "dbx_session" && c.Value != "" {
			sessionToken = c.Value
			break
		}
	}
	if sessionToken == "" {
		return nil, errors.New("login succeeded without returning dbx_session cookie")
	}

	checkReq, err := http.NewRequestWithContext(ctx, http.MethodGet, baseURL+"/api/auth/check", nil)
	if err != nil {
		return nil, fmt.Errorf("build auth check request: %w", err)
	}
	checkReq.AddCookie(&http.Cookie{Name: "dbx_session", Value: sessionToken})

	checkResp, err := client.Do(checkReq)
	if err != nil {
		return nil, fmt.Errorf("get /api/auth/check: %w", err)
	}
	checkBody, _ := io.ReadAll(io.LimitReader(checkResp.Body, 4096))
	_ = checkResp.Body.Close()

	if checkResp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("auth check returned HTTP %d: %s", checkResp.StatusCode, strings.TrimSpace(string(checkBody)))
	}

	var checkResult struct {
		Authenticated bool `json:"authenticated"`
	}
	if err := json.Unmarshal(checkBody, &checkResult); err != nil {
		return nil, fmt.Errorf("decode /api/auth/check response: %w", err)
	}
	if !checkResult.Authenticated {
		return nil, errors.New("dbx_session cookie failed authentication check")
	}

	return []sidecar.AuthCookie{
		{
			Name:     "dbx_session",
			Value:    sessionToken,
			Domain:   "127.0.0.1",
			Path:     "/",
			HTTPOnly: true,
		},
	}, nil
}

// ValidateUISmoke implements sidecar.UISmokeValidator, asserting that the WebView loaded
// via the permanent `.localhost` virtual origin and entered the authenticated DBX workspace.
func (a *Adapter) ValidateUISmoke(report sidecar.UISmokeReport) error {
	expectedPrefix := "http://" + sidecar.VirtualHost(a.Meta().ID) + "/"
	if strings.TrimSpace(report.URL) != "" && !strings.HasPrefix(report.URL, expectedPrefix) {
		return fmt.Errorf("DBX UI loaded unexpected origin %q (want prefix %q)", report.URL, expectedPrefix)
	}
	if report.PasswordInputCount > 0 || strings.Contains(strings.ToLower(report.Pathname), "login") {
		return fmt.Errorf(
			"DBX UI landed on password/login view (pathname=%q, passwordInputs=%d); session cookie injection likely failed",
			report.Pathname,
			report.PasswordInputCount,
		)
	}
	if report.ElementCount < 10 {
		return fmt.Errorf("DBX UI root appears unmounted (elementCount=%d)", report.ElementCount)
	}
	return nil
}

func (a *Adapter) executableDir() (string, error) {
	if a.exeDirFn != nil {
		return a.exeDirFn()
	}
	return defaultExecutableDir()
}

func defaultExecutableDir() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("resolve current executable: %w", err)
	}
	if resolved, err := filepath.EvalSymlinks(exe); err == nil {
		exe = resolved
	}
	return filepath.Dir(exe), nil
}

// ResolveBackendBinary locates dbx-web-bin from explicit override, DBX_BACKEND_BIN, or relative app layout.
func ResolveBackendBinary(explicit, exeDir string) (string, error) {
	if trimmed := strings.TrimSpace(explicit); trimmed != "" {
		abs, err := filepath.Abs(trimmed)
		if err != nil {
			return "", fmt.Errorf("resolve backend path %q: %w", trimmed, err)
		}
		if !isExecutableFile(abs) {
			return "", fmt.Errorf("specified backend binary is missing or not executable: %s", abs)
		}
		return abs, nil
	}

	if envBin := strings.TrimSpace(os.Getenv("DBX_BACKEND_BIN")); envBin != "" {
		abs, err := filepath.Abs(envBin)
		if err != nil {
			return "", fmt.Errorf("resolve DBX_BACKEND_BIN %q: %w", envBin, err)
		}
		if !isExecutableFile(abs) {
			return "", fmt.Errorf("DBX_BACKEND_BIN is missing or not executable: %s", abs)
		}
		return abs, nil
	}

	var candidates []string
	if exeDir != "" {
		candidates = []string{
			filepath.Clean(filepath.Join(exeDir, "..", "libexec", "dbx-web-bin")),
			filepath.Join(exeDir, "dbx-web-bin"),
			filepath.Join(exeDir, "bin", "dbx-web-bin"),
		}
	}
	for _, candidate := range candidates {
		if isExecutableFile(candidate) {
			return candidate, nil
		}
	}

	return "", fmt.Errorf("dbx-web-bin not found; checked %s", strings.Join(candidates, ", "))
}

// ResolveStaticDir returns an override static asset directory if one contains index.html, or "".
func ResolveStaticDir(explicit, exeDir string) string {
	candidates := []string{strings.TrimSpace(explicit), strings.TrimSpace(os.Getenv("DBX_STATIC_DIR"))}
	if exeDir != "" {
		candidates = append(candidates,
			filepath.Clean(filepath.Join(exeDir, "..", "share", "dbx", "dist")),
			filepath.Join(exeDir, "dist"),
		)
	}

	for _, dir := range candidates {
		if dir == "" {
			continue
		}
		abs, err := filepath.Abs(dir)
		if err != nil {
			continue
		}
		info, err := os.Stat(filepath.Join(abs, "index.html"))
		if err == nil && !info.IsDir() {
			return abs
		}
	}
	return ""
}

// ResolveDataDir resolves and creates the isolated desktop DBX data directory.
func ResolveDataDir(explicit string) (string, error) {
	dir := strings.TrimSpace(explicit)
	if dir == "" {
		dir = strings.TrimSpace(os.Getenv("DBX_DATA_DIR"))
	}
	if dir == "" {
		xdgData := strings.TrimSpace(os.Getenv("XDG_DATA_HOME"))
		if xdgData == "" {
			home, err := os.UserHomeDir()
			if err != nil || home == "" {
				return "", fmt.Errorf("resolve user home directory for DBX data dir: %w", err)
			}
			xdgData = filepath.Join(home, ".local", "share")
		}
		dir = filepath.Join(xdgData, "com.dbx.desktop", "data")
	}

	abs, err := filepath.Abs(dir)
	if err != nil {
		return "", fmt.Errorf("resolve DBX data dir %q: %w", dir, err)
	}
	if err := os.MkdirAll(abs, 0o700); err != nil {
		return "", fmt.Errorf("create DBX data dir %q: %w", abs, err)
	}
	return abs, nil
}

func isExecutableFile(path string) bool {
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return false
	}
	return info.Mode()&0o111 != 0
}

func generateEphemeralPassword() (string, error) {
	var raw [32]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw[:]), nil
}
