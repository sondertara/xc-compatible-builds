package sidecar

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os/exec"
	"strings"
	"sync"
	"time"
)

const maxLogBytes = 16 * 1024

// ringBuffer keeps the trailing N bytes written by the child process.
type ringBuffer struct {
	mu  sync.Mutex
	max int
	buf []byte
}

func newRingBuffer(maxBytes int) *ringBuffer {
	return &ringBuffer{max: maxBytes}
}

func (r *ringBuffer) Write(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()

	if len(p) >= r.max {
		r.buf = append(r.buf[:0], p[len(p)-r.max:]...)
		return len(p), nil
	}
	excess := len(r.buf) + len(p) - r.max
	if excess > 0 {
		copy(r.buf, r.buf[excess:])
		r.buf = r.buf[:len(r.buf)-excess]
	}
	r.buf = append(r.buf, p...)
	return len(p), nil
}

func (r *ringBuffer) String() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return strings.TrimSpace(string(r.buf))
}

// AcquireLoopbackPort reserves an ephemeral loopback TCP port and closes the probe listener.
func AcquireLoopbackPort() (int, error) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, fmt.Errorf("listen loopback ephemeral port: %w", err)
	}
	addr, ok := ln.Addr().(*net.TCPAddr)
	if !ok {
		_ = ln.Close()
		return 0, errors.New("unexpected non-TCP listener address")
	}
	port := addr.Port
	if err := ln.Close(); err != nil {
		return 0, fmt.Errorf("release loopback ephemeral port: %w", err)
	}
	return port, nil
}

// NewLoopbackHTTPClient returns an HTTP client whose transport bypasses system proxy settings.
func NewLoopbackHTTPClient(timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout: timeout,
		Transport: &http.Transport{
			Proxy:               nil,
			DisableKeepAlives:   false,
			MaxIdleConnsPerHost: 4,
			DialContext: (&net.Dialer{
				Timeout: 2 * time.Second,
			}).DialContext,
		},
	}
}

// ManagedProcess wraps a sidecar child process and its process group lifecycle.
type ManagedProcess struct {
	cmd      *exec.Cmd
	pid      int
	done     chan struct{}
	waitMu   sync.Mutex
	waitErr  error
	stopOnce sync.Once
	stopErr  error
	logs     *ringBuffer
}

// StartProcess launches the backend binary in its own process group.
func StartProcess(_ context.Context, spec *BackendSpec) (*ManagedProcess, error) {
	if spec == nil || strings.TrimSpace(spec.BinaryPath) == "" {
		return nil, errors.New("backend binary path is empty")
	}

	logs := newRingBuffer(maxLogBytes)
	cmd := exec.Command(spec.BinaryPath, spec.Args...)
	cmd.Dir = spec.WorkDir
	if len(spec.Env) > 0 {
		cmd.Env = spec.Env
	}
	cmd.Stdout = logs
	cmd.Stderr = logs
	configureCmdSysProcAttr(cmd)

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start backend process %q: %w", spec.BinaryPath, err)
	}

	proc := &ManagedProcess{
		cmd:  cmd,
		pid:  cmd.Process.Pid,
		done: make(chan struct{}),
		logs: logs,
	}

	go func() {
		err := cmd.Wait()
		proc.waitMu.Lock()
		proc.waitErr = err
		proc.waitMu.Unlock()
		close(proc.done)
	}()

	return proc, nil
}

// Done is closed once the direct child process has exited.
func (p *ManagedProcess) Done() <-chan struct{} {
	if p == nil {
		ch := make(chan struct{})
		close(ch)
		return ch
	}
	return p.done
}

// WaitErr returns the exit error recorded after Done is closed.
func (p *ManagedProcess) WaitErr() error {
	if p == nil {
		return nil
	}
	p.waitMu.Lock()
	defer p.waitMu.Unlock()
	return p.waitErr
}

// TailLogs returns the trailing combined stdout/stderr output captured from the child.
func (p *ManagedProcess) TailLogs() string {
	if p == nil || p.logs == nil {
		return ""
	}
	return p.logs.String()
}

// Stop terminates the sidecar process group idempotently with SIGTERM, escalating to SIGKILL on timeout.
func (p *ManagedProcess) Stop(timeout time.Duration) error {
	if p == nil || p.pid <= 0 {
		return nil
	}
	p.stopOnce.Do(func() {
		if timeout <= 0 {
			timeout = 3 * time.Second
		}

		// Always signal the process group so descendants (workers/plugins) terminate cleanly too.
		if err := terminateProcessGroup(p.pid); err != nil {
			p.stopErr = err
		}

		select {
		case <-p.done:
			p.stopErr = nil
			return
		case <-time.After(timeout):
		}

		if err := killProcessGroup(p.pid); err != nil && p.stopErr == nil {
			p.stopErr = err
		}

		select {
		case <-p.done:
			p.stopErr = nil
		case <-time.After(2 * time.Second):
		}
	})
	return p.stopErr
}

// WaitHTTPReady polls readyURL until an HTTP status in [200, 500) is observed or the process fails.
func WaitHTTPReady(ctx context.Context, client *http.Client, readyURL string, proc *ManagedProcess) error {
	if client == nil {
		client = NewLoopbackHTTPClient(2 * time.Second)
	}
	ticker := time.NewTicker(80 * time.Millisecond)
	defer ticker.Stop()

	var procDone <-chan struct{}
	if proc != nil {
		procDone = proc.Done()
	}

	var lastErr error
	for {
		if procDone != nil {
			select {
			case <-procDone:
				return formatEarlyExitError(proc)
			default:
			}
		}

		err := probeOnce(ctx, client, readyURL)
		if err == nil {
			return nil
		}
		lastErr = err

		select {
		case <-ctx.Done():
			tail := ""
			if proc != nil {
				tail = proc.TailLogs()
			}
			if tail != "" {
				return fmt.Errorf("wait ready %s: %w (last probe: %v)\n--- backend logs ---\n%s", readyURL, ctx.Err(), lastErr, tail)
			}
			return fmt.Errorf("wait ready %s: %w (last probe: %v)", readyURL, ctx.Err(), lastErr)
		case <-procDone:
			return formatEarlyExitError(proc)
		case <-ticker.C:
		}
	}
}

func probeOnce(ctx context.Context, client *http.Client, readyURL string) error {
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, readyURL, nil)
	if err != nil {
		return err
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()

	if resp.StatusCode >= 200 && resp.StatusCode < 500 {
		return nil
	}
	return fmt.Errorf("unexpected HTTP status %d", resp.StatusCode)
}

func formatEarlyExitError(proc *ManagedProcess) error {
	waitErr := proc.WaitErr()
	tail := proc.TailLogs()
	if tail != "" {
		return fmt.Errorf("backend process exited prematurely (%v)\n--- backend logs ---\n%s", waitErr, tail)
	}
	return fmt.Errorf("backend process exited prematurely (%v)", waitErr)
}
