package sidecar

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var pngSignature = []byte{0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'}

// UISmokeArtifactSummary is persisted as <outDir>/ui-smoke-report.json alongside screenshot.png.
type UISmokeArtifactSummary struct {
	Passed          bool           `json:"passed"`
	FailureReason   string         `json:"failureReason,omitempty"`
	ScreenshotFile  string         `json:"screenshotFile,omitempty"`
	ScreenshotBytes int            `json:"screenshotBytes"`
	Timestamp       string         `json:"timestamp"`
	Report          *UISmokeReport `json:"report,omitempty"`
}

type uiSmokeCollector struct {
	listener net.Listener
	server   *http.Server
	url      string
	token    string
	doneCh   chan struct{}

	once   sync.Once
	mu     sync.Mutex
	report *UISmokeReport
}

func startUISmokeCollector() (*uiSmokeCollector, error) {
	var tokBytes [16]byte
	if _, err := rand.Read(tokBytes[:]); err != nil {
		return nil, fmt.Errorf("generate ui smoke token: %w", err)
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen ui smoke collector: %w", err)
	}
	tcpAddr, ok := ln.Addr().(*net.TCPAddr)
	if !ok {
		_ = ln.Close()
		return nil, errors.New("unexpected non-TCP listener address")
	}

	c := &uiSmokeCollector{
		listener: ln,
		token:    hex.EncodeToString(tokBytes[:]),
		doneCh:   make(chan struct{}),
	}
	c.url = fmt.Sprintf("http://127.0.0.1:%d/report?token=%s", tcpAddr.Port, c.token)

	mux := http.NewServeMux()
	mux.HandleFunc("/report", c.handleReport)
	c.server = &http.Server{
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		_ = c.server.Serve(ln)
	}()

	return c, nil
}

func (c *uiSmokeCollector) handleReport(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "*")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.URL.Query().Get("token") != c.token {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var decoded UISmokeReport
	if err := json.NewDecoder(io.LimitReader(r.Body, 256<<10)).Decode(&decoded); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}

	c.once.Do(func() {
		c.mu.Lock()
		c.report = &decoded
		c.mu.Unlock()
		close(c.doneCh)
	})

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"ok":true}`))
}

func (c *uiSmokeCollector) Done() <-chan struct{} {
	return c.doneCh
}

func (c *uiSmokeCollector) Report() *UISmokeReport {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.report == nil {
		return nil
	}
	cp := *c.report
	cp.Errors = append([]string(nil), c.report.Errors...)
	cp.ConsoleErrors = append([]string(nil), c.report.ConsoleErrors...)
	return &cp
}

func (c *uiSmokeCollector) Close() {
	if c == nil || c.server == nil {
		return
	}
	_ = c.server.Close()
}

// PreloadScript returns an ES5-compatible probe script (usable without WebKitGTK 2.40's async eval)
// that captures runtime errors, waits for SPA settle, and POSTs telemetry to the loopback collector.
func (c *uiSmokeCollector) PreloadScript() string {
	endpointJSON, _ := json.Marshal(c.url)
	return `(function() {
  if (typeof window === 'undefined') return;
  if (window !== window.top) return;
  if (!window.location || (window.location.protocol !== 'http:' && window.location.protocol !== 'https:')) return;
  if (window.__mygoUISmokeInstalled) return;
  window.__mygoUISmokeInstalled = true;

  var endpoint = ` + string(endpointJSON) + `;
  var errors = [];
  var consoleErrors = [];
  var sent = false;
  var settledAt = 0;
  var startedAt = Date.now();

  function pushLimited(arr, text) {
    if (arr.length >= 30) return;
    var s = String(text || '');
    if (s.length > 500) s = s.slice(0, 500) + '...';
    for (var i = 0; i < arr.length; i++) {
      if (arr[i] === s) return;
    }
    arr.push(s);
  }

  window.addEventListener('error', function(ev) {
    if (!ev) return;
    if (ev.message) {
      var loc = (ev.filename || '') + (ev.lineno ? ':' + ev.lineno : '');
      pushLimited(errors, ev.message + (loc ? ' @ ' + loc : ''));
      return;
    }
    var target = ev.target || ev.srcElement;
    if (target && target !== window) {
      var src = target.src || target.href || '';
      if (src) pushLimited(errors, 'resource error: ' + src);
    }
  }, true);

  window.addEventListener('unhandledrejection', function(ev) {
    var reason = ev && ev.reason;
    var msg = reason ? (reason.message || String(reason)) : 'unhandledrejection';
    pushLimited(errors, 'unhandledrejection: ' + msg);
  });

  if (typeof console !== 'undefined' && typeof console.error === 'function') {
    var origError = console.error;
    console.error = function() {
      try {
        var parts = [];
        for (var i = 0; i < arguments.length; i++) {
          parts.push(String(arguments[i]));
        }
        pushLimited(consoleErrors, parts.join(' '));
      } catch (_) {}
      return origError.apply(console, arguments);
    };
  }

  function sendXHR(body) {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', endpoint, true);
      xhr.setRequestHeader('Content-Type', 'text/plain');
      xhr.send(body);
    } catch (_) {}
  }

  function sendReport() {
    if (sent) return;
    sent = true;
    var body = document.body;
    var elCount = body && body.querySelectorAll ? body.querySelectorAll('*').length : 0;
    var rawText = body && body.innerText ? body.innerText : '';
    var textLen = rawText.replace(/\s+/g, ' ').trim().length;
    var pwCount = document.querySelectorAll ? document.querySelectorAll('input[type="password"]').length : 0;
    var payload = JSON.stringify({
      url: String(window.location.href || ''),
      pathname: String(window.location.pathname || '/'),
      title: String(document.title || ''),
      userAgent: String((navigator && navigator.userAgent) || ''),
      readyState: String(document.readyState || ''),
      viewportWidth: window.innerWidth || 0,
      viewportHeight: window.innerHeight || 0,
      elementCount: elCount,
      textLength: textLen,
      passwordInputCount: pwCount,
      errors: errors,
      consoleErrors: consoleErrors
    });

    if (typeof fetch === 'function') {
      try {
        fetch(endpoint, {
          method: 'POST',
          mode: 'cors',
          headers: { 'Content-Type': 'text/plain' },
          body: payload
        })['catch'](function() { sendXHR(payload); });
        return;
      } catch (_) {}
    }
    sendXHR(payload);
  }

  var timer = setInterval(function() {
    if (sent) {
      clearInterval(timer);
      return;
    }
    var now = Date.now();
    var body = document.body;
    var elCount = body && body.querySelectorAll ? body.querySelectorAll('*').length : 0;
    if (document.readyState === 'complete' && elCount >= 10) {
      if (!settledAt) settledAt = now;
      if (now - settledAt >= 1500) {
        clearInterval(timer);
        if (typeof window.requestAnimationFrame === 'function') {
          window.requestAnimationFrame(function() { sendReport(); });
        } else {
          sendReport();
        }
        return;
      }
    }
    if (now - startedAt >= 12000) {
      clearInterval(timer);
      sendReport();
    }
  }, 200);
})();`
}

func validateGenericUISmoke(report *UISmokeReport, pngBytes []byte, adapter Adapter) error {
	if report == nil {
		return errors.New("ui smoke did not receive browser telemetry report")
	}
	if len(report.Errors) > 0 {
		return fmt.Errorf("ui smoke detected %d browser error(s); first: %s", len(report.Errors), report.Errors[0])
	}
	if report.ElementCount == 0 {
		return errors.New("ui smoke rendered empty DOM (elementCount=0)")
	}
	if len(pngBytes) < 1024 || !bytes.HasPrefix(pngBytes, pngSignature) {
		return fmt.Errorf("ui smoke captured invalid PNG snapshot (bytes=%d)", len(pngBytes))
	}
	if v, ok := adapter.(UISmokeValidator); ok {
		if err := v.ValidateUISmoke(*report); err != nil {
			return err
		}
	}
	return nil
}

func writeUISmokeArtifacts(outDir string, pngBytes []byte, report *UISmokeReport, runErr error) error {
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return fmt.Errorf("create ui smoke dir: %w", err)
	}

	summary := UISmokeArtifactSummary{
		Passed:          runErr == nil,
		ScreenshotBytes: len(pngBytes),
		Timestamp:       time.Now().UTC().Format(time.RFC3339),
		Report:          report,
	}
	if runErr != nil {
		summary.FailureReason = strings.TrimSpace(runErr.Error())
	}

	if len(pngBytes) > 0 {
		const screenshotName = "screenshot.png"
		if err := os.WriteFile(filepath.Join(outDir, screenshotName), pngBytes, 0o644); err != nil {
			return fmt.Errorf("write ui smoke screenshot: %w", err)
		}
		summary.ScreenshotFile = screenshotName
	}

	raw, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal ui smoke summary: %w", err)
	}
	raw = append(raw, '\n')
	if err := os.WriteFile(filepath.Join(outDir, "ui-smoke-report.json"), raw, 0o644); err != nil {
		return fmt.Errorf("write ui smoke report: %w", err)
	}
	return nil
}
