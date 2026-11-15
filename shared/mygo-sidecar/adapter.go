package sidecar

import (
	"context"
	"net/http"
	"sort"
	"strings"
	"time"
)

// AppMeta describes the desktop window and tray identity of a wrapped web app.
type AppMeta struct {
	ID              string
	Name            string
	Width           int
	Height          int
	MinWidth        int
	MinHeight       int
	BackgroundColor string
	IconPNG         []byte
}

// BackendSpec describes the loopback sidecar process to launch and probe.
type BackendSpec struct {
	BinaryPath   string
	Args         []string
	WorkDir      string
	Env          []string
	ReadyPath    string
	ReadyTimeout time.Duration
}

// AuthCookie represents a session cookie injected into the WebView jar before navigation.
type AuthCookie struct {
	Name     string
	Value    string
	Domain   string
	Path     string
	HTTPOnly bool
}

// Adapter plugs an application-specific sidecar backend into the generic MyGO host runner.
type Adapter interface {
	Meta() AppMeta
	Prepare(ctx context.Context, port int) (*BackendSpec, error)
	Authenticate(ctx context.Context, client *http.Client, baseURL string) ([]AuthCookie, error)
}

// UISmokeReport captures browser-side readiness and error telemetry from a real WebView run.
type UISmokeReport struct {
	URL                string   `json:"url"`
	Pathname           string   `json:"pathname"`
	Title              string   `json:"title"`
	UserAgent          string   `json:"userAgent"`
	ReadyState         string   `json:"readyState"`
	ViewportWidth      int      `json:"viewportWidth"`
	ViewportHeight     int      `json:"viewportHeight"`
	ElementCount       int      `json:"elementCount"`
	TextLength         int      `json:"textLength"`
	PasswordInputCount int      `json:"passwordInputCount"`
	Errors             []string `json:"errors"`
	ConsoleErrors      []string `json:"consoleErrors"`
}

// UISmokeValidator may be implemented by an Adapter to enforce app-specific UI smoke assertions
// (for example, verifying cookie pre-injection bypassed a password login screen).
type UISmokeValidator interface {
	ValidateUISmoke(report UISmokeReport) error
}

// MergeEnv overrides keys in base with overrides (deduplicating by key name) and removes unsetKeys.
func MergeEnv(base []string, overrides map[string]string, unsetKeys ...string) []string {
	unset := make(map[string]struct{}, len(unsetKeys))
	for _, k := range unsetKeys {
		if k != "" {
			unset[k] = struct{}{}
		}
	}

	out := make([]string, 0, len(base)+len(overrides))
	seen := make(map[string]int, len(base)+len(overrides))

	for _, entry := range base {
		k, _, ok := strings.Cut(entry, "=")
		if !ok || k == "" {
			continue
		}
		if _, drop := unset[k]; drop {
			continue
		}
		if idx, exists := seen[k]; exists {
			out[idx] = entry
			continue
		}
		seen[k] = len(out)
		out = append(out, entry)
	}

	keys := make([]string, 0, len(overrides))
	for k := range overrides {
		if k != "" {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)

	for _, k := range keys {
		if _, drop := unset[k]; drop {
			continue
		}
		entry := k + "=" + overrides[k]
		if idx, exists := seen[k]; exists {
			out[idx] = entry
			continue
		}
		seen[k] = len(out)
		out = append(out, entry)
	}

	return out
}
