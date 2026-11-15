package sidecar

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/egoist/mygo"
)

// Options configures how the MyGO sidecar runner hosts an application adapter.
type Options struct {
	Port           int
	EnableGPU      bool
	DevTools       bool
	SmokeTest      bool
	UISmokeDir     string
	UISmokeTimeout time.Duration
	ExtraPreloadJS string
}

// Run launches the sidecar backend, performs readiness/auth handshakes, and starts the MyGO window.
func Run(ctx context.Context, adapter Adapter, opts Options) error {
	if adapter == nil {
		return fmt.Errorf("adapter must not be nil")
	}

	if !opts.SmokeTest && runtime.GOOS == "linux" && !opts.EnableGPU && os.Getenv("WEBKIT_ENABLE_GPU") != "1" {
		if os.Getenv("WEBKIT_DISABLE_COMPOSITING_MODE") == "" {
			_ = os.Setenv("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
		}
		if os.Getenv("WEBKIT_DISABLE_DMABUF_RENDERER") == "" {
			_ = os.Setenv("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
		}
	}

	port := opts.Port
	if port <= 0 {
		var err error
		port, err = AcquireLoopbackPort()
		if err != nil {
			return err
		}
	}

	sigCtx, stopSig := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer stopSig()

	spec, err := adapter.Prepare(sigCtx, port)
	if err != nil {
		return fmt.Errorf("prepare backend: %w", err)
	}

	proc, err := StartProcess(sigCtx, spec)
	if err != nil {
		return err
	}
	defer func() { _ = proc.Stop(5 * time.Second) }()

	baseURL := fmt.Sprintf("http://127.0.0.1:%d", port)
	readyPath := normalizePath(spec.ReadyPath)
	readyTimeout := spec.ReadyTimeout
	if readyTimeout <= 0 {
		readyTimeout = 30 * time.Second
	}

	client := NewLoopbackHTTPClient(5 * time.Second)
	readyCtx, cancelReady := context.WithTimeout(sigCtx, readyTimeout)
	defer cancelReady()

	if err := WaitHTTPReady(readyCtx, client, baseURL+readyPath, proc); err != nil {
		return err
	}

	cookies, err := adapter.Authenticate(sigCtx, client, baseURL)
	if err != nil {
		return fmt.Errorf("authenticate sidecar: %w", err)
	}

	if opts.SmokeTest {
		if err := verifySmokeEndpoints(sigCtx, client, baseURL, readyPath, cookies); err != nil {
			return err
		}
		if err := proc.Stop(5 * time.Second); err != nil {
			return fmt.Errorf("stop backend during smoke test: %w", err)
		}
		fmt.Printf("SMOKE_OK url=%s\n", baseURL)
		return nil
	}

	// Let MyGO manage SIGINT/SIGTERM once the event loop owns shutdown.
	stopSig()

	uiSmokeDir := strings.TrimSpace(opts.UISmokeDir)
	uiSmoke := uiSmokeDir != ""

	var collector *uiSmokeCollector
	var uiSmokeMu sync.Mutex
	var uiSmokeErr error
	var uiSmokePNGBytes int
	var uiSmokeReport *UISmokeReport
	setUISmokeResult := func(err error, pngLen int, rep *UISmokeReport) {
		uiSmokeMu.Lock()
		uiSmokeErr = err
		uiSmokePNGBytes = pngLen
		uiSmokeReport = rep
		uiSmokeMu.Unlock()
	}
	if uiSmoke {
		var cErr error
		collector, cErr = startUISmokeCollector()
		if cErr != nil {
			_ = writeUISmokeArtifacts(uiSmokeDir, nil, nil, cErr)
			return cErr
		}
		defer collector.Close()
	}

	meta := adapter.Meta()
	if meta.ID != "" {
		mygo.App.SetName(meta.ID)
	} else if meta.Name != "" {
		mygo.App.SetName(meta.Name)
	}

	mygo.App.OnQuit(func() {
		_ = proc.Stop(5 * time.Second)
	})

	preload := WebKit238PreloadJS
	if strings.TrimSpace(opts.ExtraPreloadJS) != "" {
		preload += "\n" + opts.ExtraPreloadJS
	}
	if collector != nil {
		preload += "\n" + collector.PreloadScript()
	}

	devTools := mygo.DevToolsDisabled
	if opts.DevTools {
		devTools = mygo.DevToolsEnabled
	}

	width := meta.Width
	if width <= 0 {
		width = 1280
	}
	height := meta.Height
	if height <= 0 {
		height = 820
	}
	minWidth := meta.MinWidth
	if minWidth <= 0 {
		minWidth = 800
	}
	minHeight := meta.MinHeight
	if minHeight <= 0 {
		minHeight = 600
	}
	title := meta.Name
	if title == "" {
		title = meta.ID
	}

	var quitting atomic.Bool
	go func() {
		<-ctx.Done()
		quitting.Store(true)
		mygo.App.Quit()
	}()

	mygo.App.WhenReady(func() {
		for _, c := range cookies {
			path := c.Path
			if path == "" {
				path = "/"
			}
			domain := c.Domain
			if domain == "" {
				domain = "127.0.0.1"
			}
			if err := mygo.Cookies.Set(mygo.Cookie{
				Name:     c.Name,
				Value:    c.Value,
				Domain:   domain,
				Path:     path,
				HTTPOnly: c.HTTPOnly,
				SameSite: mygo.CookieSameSiteLax,
			}); err != nil {
				if uiSmoke {
					injectErr := fmt.Errorf("inject cookie %q into WebKit cookie manager: %w", c.Name, err)
					_ = writeUISmokeArtifacts(uiSmokeDir, nil, nil, injectErr)
					setUISmokeResult(injectErr, 0, nil)
					go func() {
						quitting.Store(true)
						mygo.App.Quit()
					}()
					return
				}
				fmt.Fprintf(os.Stderr, "warn: inject cookie %q failed: %v\n", c.Name, err)
			}
		}

		stateKey := "main"
		if uiSmoke {
			stateKey = ""
		}

		win := mygo.NewWindow(mygo.WindowOptions{
			Title:           title,
			Width:           width,
			Height:          height,
			MinWidth:        minWidth,
			MinHeight:       minHeight,
			BackgroundColor: meta.BackgroundColor,
			StateKey:        stateKey,
			URL:             baseURL + "/",
			Page: mygo.PageOptions{
				PreloadScript:    preload,
				PreloadAllFrames: true,
				DevTools:         devTools,
			},
		})

		type uiSmokeFatal struct {
			err               error
			renderProcessGone bool
		}
		var fatalCh chan uiSmokeFatal
		if uiSmoke {
			fatalCh = make(chan uiSmokeFatal, 2)
		}

		if page := win.Page(); page != nil {
			page.OnWillDownload(func(e *mygo.DownloadEvent) {
				defaultPath := e.Path
				if defaultPath == "" {
					defaultPath = e.SuggestedName
					if home, err := os.UserHomeDir(); err == nil && home != "" {
						defaultPath = filepath.Join(home, e.SuggestedName)
					}
				}
				savePath, err := mygo.Dialog.Save(mygo.SaveDialogOptions{
					Parent:      win,
					Title:       "保存文件 - " + title,
					DefaultPath: defaultPath,
				})
				if err != nil || strings.TrimSpace(savePath) == "" {
					e.PreventDefault()
					return
				}
				e.Path = savePath
			})

			if uiSmoke {
				page.OnDidFailLoad(func(e *mygo.LoadError) {
					if e != nil && (strings.HasPrefix(e.URL, baseURL) || e.URL == "") {
						select {
						case fatalCh <- uiSmokeFatal{err: fmt.Errorf("webview failed to load %s: %s (%d)", e.URL, e.Description, e.Code)}:
						default:
						}
					}
				})
				page.OnRenderProcessGone(func(reason string) {
					select {
					case fatalCh <- uiSmokeFatal{err: fmt.Errorf("webview render process gone: %s", reason), renderProcessGone: true}:
					default:
					}
				})
			}
		}

		if uiSmoke {
			smokeTimeout := opts.UISmokeTimeout
			if smokeTimeout <= 0 {
				smokeTimeout = 35 * time.Second
			}
			go func() {
				var fatal uiSmokeFatal
				select {
				case <-collector.Done():
				case fatal = <-fatalCh:
				case <-time.After(smokeTimeout):
					fatal.err = fmt.Errorf("ui smoke timed out after %s waiting for page telemetry", smokeTimeout)
				case <-ctx.Done():
					fatal.err = ctx.Err()
				}

				var pngBytes []byte
				var capErr error
				if !fatal.renderProcessGone {
					pngBytes, capErr = capturePageWithTimeout(win, 8*time.Second)
				}

				report := collector.Report()
				smokeErr := fatal.err
				if smokeErr == nil {
					if capErr != nil {
						smokeErr = fmt.Errorf("capture webview screenshot: %w", capErr)
					} else {
						smokeErr = validateGenericUISmoke(report, pngBytes, adapter)
					}
				}
				if writeErr := writeUISmokeArtifacts(uiSmokeDir, pngBytes, report, smokeErr); writeErr != nil && smokeErr == nil {
					smokeErr = writeErr
				}
				setUISmokeResult(smokeErr, len(pngBytes), report)
				quitting.Store(true)
				mygo.App.Quit()
			}()
		}

		var tray *mygo.Tray
		if len(meta.IconPNG) > 0 && !uiSmoke {
			trayMenu := mygo.NewMenu([]*mygo.MenuItem{
				{
					Label: "显示主窗口",
					Click: func(_ *mygo.MenuItem, _ *mygo.Window) {
						win.Show()
						win.Focus()
					},
				},
				{Type: mygo.MenuItemSeparator},
				{
					Label: "退出 " + title,
					Click: func(_ *mygo.MenuItem, _ *mygo.Window) {
						quitting.Store(true)
						mygo.App.Quit()
					},
				},
			})

			var trayErr error
			tray, trayErr = mygo.NewTray(mygo.TrayOptions{
				Icon:    meta.IconPNG,
				ToolTip: title,
				Menu:    trayMenu,
			})
			if trayErr != nil {
				fmt.Fprintf(os.Stderr, "warn: tray init failed: %v\n", trayErr)
				tray = nil
			} else if tray != nil {
				tray.OnClick(func() {
					win.Show()
					win.Focus()
				})
			}
		}

		win.OnClose(func(e *mygo.CloseEvent) {
			if tray != nil && !quitting.Load() {
				e.PreventDefault()
				win.Hide()
			}
		})
	})

	if err := mygo.App.Run(); err != nil {
		runErr := fmt.Errorf("mygo app run: %w", err)
		if uiSmoke {
			var rep *UISmokeReport
			if collector != nil {
				rep = collector.Report()
			}
			_ = writeUISmokeArtifacts(uiSmokeDir, nil, rep, runErr)
		}
		return runErr
	}
	if uiSmoke {
		uiSmokeMu.Lock()
		err := uiSmokeErr
		pngLen := uiSmokePNGBytes
		rep := uiSmokeReport
		uiSmokeMu.Unlock()
		if err != nil {
			return err
		}
		ua := ""
		if rep != nil {
			ua = rep.UserAgent
		}
		fmt.Printf("UI_SMOKE_OK dir=%s png_bytes=%d ua=%q\n", uiSmokeDir, pngLen, ua)
	}
	return nil
}

func capturePageWithTimeout(win *mygo.Window, timeout time.Duration) ([]byte, error) {
	type res struct {
		png []byte
		err error
	}
	ch := make(chan res, 1)
	go func() {
		png, err := win.CapturePage()
		ch <- res{png: png, err: err}
	}()
	select {
	case r := <-ch:
		return r.png, r.err
	case <-time.After(timeout):
		return nil, fmt.Errorf("CapturePage timed out after %s", timeout)
	}
}

func verifySmokeEndpoints(ctx context.Context, client *http.Client, baseURL, readyPath string, cookies []AuthCookie) error {
	targets := []string{baseURL + "/"}
	if readyPath != "" && readyPath != "/" {
		targets = append([]string{baseURL + readyPath}, targets...)
	}

	for _, target := range targets {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
		if err != nil {
			return fmt.Errorf("smoke request %s: %w", target, err)
		}
		for _, c := range cookies {
			req.AddCookie(&http.Cookie{Name: c.Name, Value: c.Value})
		}
		resp, err := client.Do(req)
		if err != nil {
			return fmt.Errorf("smoke fetch %s: %w", target, err)
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			return fmt.Errorf("smoke endpoint %s returned HTTP %d", target, resp.StatusCode)
		}
	}
	return nil
}

func normalizePath(p string) string {
	p = strings.TrimSpace(p)
	if p == "" {
		return "/"
	}
	if !strings.HasPrefix(p, "/") {
		return "/" + p
	}
	return p
}
