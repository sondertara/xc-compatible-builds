package sidecar

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestAcquireLoopbackPort(t *testing.T) {
	port, err := AcquireLoopbackPort()
	if err != nil {
		t.Fatalf("AcquireLoopbackPort failed: %v", err)
	}
	if port <= 0 || port > 65535 {
		t.Fatalf("unexpected port %d", port)
	}
}

func TestMergeEnv(t *testing.T) {
	base := []string{
		"PATH=/usr/bin",
		"DBX_PORT=4224",
		"DBX_STATIC_DIR=/stale",
		"KEEP=1",
		"DUP=old",
		"DUP=newer",
	}
	got := MergeEnv(base, map[string]string{
		"DBX_PORT":      "51234",
		"DBX_BIND_ADDR": "127.0.0.1",
	}, "DBX_STATIC_DIR")

	want := []string{
		"PATH=/usr/bin",
		"DBX_PORT=51234",
		"KEEP=1",
		"DUP=newer",
		"DBX_BIND_ADDR=127.0.0.1",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("MergeEnv mismatch:\n got: %v\nwant: %v", got, want)
	}
}

func TestStartProcessEarlyExitCapturesStderr(t *testing.T) {
	proc, err := StartProcess(context.Background(), &BackendSpec{
		BinaryPath: "/bin/sh",
		Args:       []string{"-c", "echo 'boom startup error' >&2; exit 23"},
	})
	if err != nil {
		t.Fatalf("StartProcess failed: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	client := NewLoopbackHTTPClient(time.Second)
	err = WaitHTTPReady(ctx, client, "http://127.0.0.1:1/health", proc)
	if err == nil {
		t.Fatal("expected WaitHTTPReady error for exited child, got nil")
	}
	msg := err.Error()
	if !strings.Contains(msg, "exited prematurely") || !strings.Contains(msg, "boom startup error") {
		t.Fatalf("expected early exit error with stderr tail, got: %s", msg)
	}
}

func TestStopKillsProcessGroupDescendants(t *testing.T) {
	pidFile := filepath.Join(t.TempDir(), "grandchild.pid")
	script := fmt.Sprintf("sleep 60 & echo $! > %q; wait", pidFile)

	proc, err := StartProcess(context.Background(), &BackendSpec{
		BinaryPath: "/bin/sh",
		Args:       []string{"-c", script},
	})
	if err != nil {
		t.Fatalf("StartProcess failed: %v", err)
	}

	var grandchildPID int
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		raw, readErr := os.ReadFile(pidFile)
		if readErr == nil && strings.TrimSpace(string(raw)) != "" {
			parsed, convErr := strconv.Atoi(strings.TrimSpace(string(raw)))
			if convErr == nil && parsed > 0 {
				grandchildPID = parsed
				break
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	if grandchildPID == 0 {
		_ = proc.Stop(time.Second)
		t.Fatal("timed out waiting for grandchild pid file")
	}

	if err := proc.Stop(2 * time.Second); err != nil {
		t.Fatalf("Stop returned error: %v", err)
	}

	<-proc.Done()

	// Poll briefly to ensure the kernel reaped/terminated the grandchild in the group.
	dead := false
	reapDeadline := time.Now().Add(1 * time.Second)
	for time.Now().Before(reapDeadline) {
		err := syscall.Kill(grandchildPID, 0)
		if errors.Is(err, syscall.ESRCH) {
			dead = true
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !dead {
		_ = syscall.Kill(grandchildPID, syscall.SIGKILL)
		t.Fatalf("grandchild pid %d survived Stop", grandchildPID)
	}
}

func TestWaitHTTPReady(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ready" {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("ok"))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	proc, err := StartProcess(context.Background(), &BackendSpec{
		BinaryPath: "/bin/sh",
		Args:       []string{"-c", "sleep 30"},
	})
	if err != nil {
		t.Fatalf("StartProcess failed: %v", err)
	}
	defer func() { _ = proc.Stop(2 * time.Second) }()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	client := NewLoopbackHTTPClient(time.Second)
	if err := WaitHTTPReady(ctx, client, srv.URL+"/ready", proc); err != nil {
		t.Fatalf("WaitHTTPReady failed: %v", err)
	}
}
