package sidecar

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type stubSmokeAdapter struct {
	validateErr error
}

func (s *stubSmokeAdapter) Meta() AppMeta {
	return AppMeta{ID: "com.example.stub", Name: "Stub"}
}

func (s *stubSmokeAdapter) Prepare(_ context.Context, _ int) (*BackendSpec, error) {
	return &BackendSpec{}, nil
}

func (s *stubSmokeAdapter) Authenticate(_ context.Context, _ *http.Client, _ string) ([]AuthCookie, error) {
	return nil, nil
}

func (s *stubSmokeAdapter) ValidateUISmoke(_ UISmokeReport) error {
	return s.validateErr
}

func validTestPNG() []byte {
	buf := make([]byte, 2048)
	copy(buf, pngSignature)
	return buf
}

func TestUISmokeCollectorRoundTripAndToken(t *testing.T) {
	c, err := startUISmokeCollector()
	if err != nil {
		t.Fatalf("startUISmokeCollector failed: %v", err)
	}
	defer c.Close()

	if !strings.Contains(c.PreloadScript(), "__mygoUISmokeInstalled") {
		t.Fatal("PreloadScript missing probe guard")
	}

	client := NewLoopbackHTTPClient(5 * time.Second)

	// Preflight CORS OPTIONS succeeds.
	optReq, _ := http.NewRequest(http.MethodOptions, c.url, nil)
	optResp, err := client.Do(optReq)
	if err != nil {
		t.Fatalf("OPTIONS failed: %v", err)
	}
	_ = optResp.Body.Close()
	if optResp.StatusCode != http.StatusNoContent {
		t.Fatalf("expected 204 for OPTIONS, got %d", optResp.StatusCode)
	}

	// Wrong token rejected.
	badURL := strings.Split(c.url, "?")[0] + "?token=wrong"
	badResp, err := client.Post(badURL, "text/plain", strings.NewReader(`{}`))
	if err != nil {
		t.Fatalf("POST bad token failed: %v", err)
	}
	_ = badResp.Body.Close()
	if badResp.StatusCode != http.StatusForbidden {
		t.Fatalf("expected 403 for bad token, got %d", badResp.StatusCode)
	}

	// Valid POST stores report and closes Done channel.
	payload := `{"url":"http://127.0.0.1:4224/","pathname":"/","title":"DBX","userAgent":"WebKit/605","readyState":"complete","viewportWidth":1400,"viewportHeight":880,"elementCount":42,"textLength":120,"passwordInputCount":0,"errors":[],"consoleErrors":[]}`
	resp, err := client.Post(c.url, "text/plain", strings.NewReader(payload))
	if err != nil {
		t.Fatalf("POST valid report failed: %v", err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200 for valid report, got %d", resp.StatusCode)
	}

	select {
	case <-c.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("collector Done channel did not close")
	}

	rep := c.Report()
	if rep == nil || rep.ElementCount != 42 || rep.Title != "DBX" {
		t.Fatalf("unexpected collected report: %+v", rep)
	}
}

func TestValidateGenericUISmoke(t *testing.T) {
	okRep := &UISmokeReport{
		URL:          "http://127.0.0.1:4224/",
		ElementCount: 25,
	}
	png := validTestPNG()

	if err := validateGenericUISmoke(okRep, png, &stubSmokeAdapter{}); err != nil {
		t.Fatalf("expected valid report to pass, got %v", err)
	}
	if err := validateGenericUISmoke(nil, png, &stubSmokeAdapter{}); err == nil {
		t.Fatal("expected nil report to fail")
	}
	if err := validateGenericUISmoke(&UISmokeReport{ElementCount: 5, Errors: []string{"SyntaxError"}}, png, &stubSmokeAdapter{}); err == nil {
		t.Fatal("expected JS error report to fail")
	}
	if err := validateGenericUISmoke(&UISmokeReport{ElementCount: 0}, png, &stubSmokeAdapter{}); err == nil {
		t.Fatal("expected empty DOM report to fail")
	}
	if err := validateGenericUISmoke(okRep, []byte("not-a-png"), &stubSmokeAdapter{}); err == nil {
		t.Fatal("expected invalid PNG to fail")
	}
	if err := validateGenericUISmoke(okRep, png, &stubSmokeAdapter{validateErr: errors.New("stuck on login")}); err == nil {
		t.Fatal("expected UISmokeValidator error to propagate")
	}
}

func TestWriteUISmokeArtifacts(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "ui-smoke")
	png := validTestPNG()
	rep := &UISmokeReport{URL: "http://127.0.0.1:4224/", ElementCount: 12}

	if err := writeUISmokeArtifacts(dir, png, rep, errors.New("boom")); err != nil {
		t.Fatalf("writeUISmokeArtifacts failed: %v", err)
	}

	gotPNG, err := os.ReadFile(filepath.Join(dir, "screenshot.png"))
	if err != nil || !bytes.Equal(gotPNG, png) {
		t.Fatalf("unexpected screenshot.png (err=%v, len=%d)", err, len(gotPNG))
	}

	rawSummary, err := os.ReadFile(filepath.Join(dir, "ui-smoke-report.json"))
	if err != nil {
		t.Fatalf("read summary failed: %v", err)
	}
	var summary UISmokeArtifactSummary
	if err := json.Unmarshal(rawSummary, &summary); err != nil {
		t.Fatalf("decode summary failed: %v", err)
	}
	if summary.Passed || summary.FailureReason != "boom" || summary.ScreenshotBytes != len(png) {
		t.Fatalf("unexpected summary: %+v", summary)
	}
}
