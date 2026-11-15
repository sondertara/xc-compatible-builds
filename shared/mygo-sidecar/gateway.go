package sidecar

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"time"
)

// VirtualHost derives a deterministic RFC 6761 `.localhost` hostname from an application ID
// (for example, "com.dbx.desktop" -> "com-dbx-desktop.localhost").
// Using a portless `.localhost` origin ensures WebKit preserves localStorage, IndexedDB,
// and secure-context Web Crypto across restarts even when loopback ports change every run.
func VirtualHost(appID string) string {
	raw := strings.ToLower(strings.TrimSpace(appID))
	var b strings.Builder
	prevDash := false
	for _, r := range raw {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
			prevDash = false
			continue
		}
		if !prevDash && b.Len() > 0 {
			b.WriteByte('-')
			prevDash = true
		}
	}
	label := strings.Trim(b.String(), "-")
	if len(label) > 63 {
		label = strings.TrimRight(label[:63], "-")
	}
	if label == "" {
		label = "sidecar-app"
	}
	return label + ".localhost"
}

// MapCookieDomain maps a default loopback cookie domain ("", "127.0.0.1", "localhost")
// onto the active virtual host when virtual-origin routing is enabled.
func MapCookieDomain(domain, virtualHost string) string {
	d := strings.TrimSpace(domain)
	if virtualHost == "" {
		if d == "" {
			return "127.0.0.1"
		}
		return d
	}
	if d == "" || d == "127.0.0.1" || strings.EqualFold(d, "localhost") {
		return virtualHost
	}
	return d
}

// LocalGateway is an in-process ephemeral loopback HTTP/WebSocket proxy that routes
// requests for a fixed virtual `.localhost` origin to the sidecar's ephemeral loopback port,
// while letting non-virtual-host traffic proceed normally.
type LocalGateway struct {
	virtualHost       string
	backendAddr       string
	listenAddr        string
	listener          net.Listener
	server            *http.Server
	backendTransport  *http.Transport
	externalTransport *http.Transport
	reverseProxy      *httputil.ReverseProxy
	closeOnce         sync.Once
}

// StartLocalGateway binds an ephemeral loopback listener (127.0.0.1:0) and starts serving.
func StartLocalGateway(virtualHost, backendAddr string) (*LocalGateway, error) {
	vhost := strings.ToLower(strings.TrimSpace(virtualHost))
	baddr := strings.TrimSpace(backendAddr)
	if vhost == "" {
		return nil, errors.New("virtualHost must not be empty")
	}
	if baddr == "" {
		return nil, errors.New("backendAddr must not be empty")
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen local gateway: %w", err)
	}

	backendTransport := &http.Transport{
		Proxy:               nil,
		DisableCompression:  true,
		MaxIdleConnsPerHost: 16,
		DialContext: (&net.Dialer{
			Timeout:   5 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
	}

	externalTransport := &http.Transport{
		Proxy:               http.ProxyFromEnvironment,
		DisableCompression:  true,
		MaxIdleConnsPerHost: 8,
		DialContext: (&net.Dialer{
			Timeout:   10 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
	}

	gw := &LocalGateway{
		virtualHost:       vhost,
		backendAddr:       baddr,
		listenAddr:        ln.Addr().String(),
		listener:          ln,
		backendTransport:  backendTransport,
		externalTransport: externalTransport,
	}

	gw.reverseProxy = &httputil.ReverseProxy{
		Transport:     backendTransport,
		FlushInterval: -1,
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL.Scheme = "http"
			pr.Out.URL.Host = gw.backendAddr
			pr.Out.Host = gw.backendAddr
			stripProxyHeaders(pr.Out.Header)

			if matchesVirtualOrigin(pr.In.Header.Get("Origin"), gw.virtualHost) {
				pr.Out.Header.Set("Origin", "http://"+gw.backendAddr)
			}
			if ref, ok := rewriteVirtualReferer(pr.In.Header.Get("Referer"), gw.virtualHost, gw.backendAddr); ok {
				pr.Out.Header.Set("Referer", ref)
			}
		},
		ModifyResponse: func(resp *http.Response) error {
			cookies := resp.Header.Values("Set-Cookie")
			if len(cookies) == 0 {
				return nil
			}
			updated := false
			rewritten := make([]string, len(cookies))
			for i, raw := range cookies {
				next := rewriteSetCookieDomain(raw, gw.virtualHost)
				if next != raw {
					updated = true
				}
				rewritten[i] = next
			}
			if updated {
				resp.Header.Del("Set-Cookie")
				for _, v := range rewritten {
					resp.Header.Add("Set-Cookie", v)
				}
			}
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, err error) {
			http.Error(w, fmt.Sprintf("sidecar gateway error: %v", err), http.StatusBadGateway)
		},
	}

	gw.server = &http.Server{
		Handler:           http.HandlerFunc(gw.serveHTTP),
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		_ = gw.server.Serve(ln)
	}()

	return gw, nil
}

// ProxyURI returns the loopback HTTP proxy URI (http://127.0.0.1:<port>) for WebKitNetworkProxySettings.
func (g *LocalGateway) ProxyURI() string {
	return "http://" + g.listenAddr
}

// OriginURL returns the permanent portless origin URL (http://<virtualHost>).
func (g *LocalGateway) OriginURL() string {
	return "http://" + g.virtualHost
}

// VirtualHost returns the normalized virtual hostname routed to the sidecar backend.
func (g *LocalGateway) VirtualHost() string {
	return g.virtualHost
}

// Close shuts down the gateway listener and idle connections idempotently.
func (g *LocalGateway) Close() error {
	if g == nil {
		return nil
	}
	var closeErr error
	g.closeOnce.Do(func() {
		if g.server != nil {
			closeErr = g.server.Close()
		} else if g.listener != nil {
			closeErr = g.listener.Close()
		}
		if g.backendTransport != nil {
			g.backendTransport.CloseIdleConnections()
		}
		if g.externalTransport != nil {
			g.externalTransport.CloseIdleConnections()
		}
	})
	return closeErr
}

func (g *LocalGateway) serveHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodConnect {
		g.handleConnect(w, r)
		return
	}

	targetHost := r.URL.Host
	if targetHost == "" {
		targetHost = r.Host
	}
	if g.isVirtualHost(targetHost) {
		g.reverseProxy.ServeHTTP(w, r)
		return
	}

	g.forwardExternalHTTP(w, r, targetHost)
}

func (g *LocalGateway) isVirtualHost(hostPort string) bool {
	h := strings.ToLower(strings.TrimSpace(hostPort))
	if h == "" {
		return false
	}
	if h == g.virtualHost || h == g.virtualHost+":80" {
		return true
	}
	host, port, err := net.SplitHostPort(h)
	if err != nil {
		return false
	}
	return host == g.virtualHost && (port == "" || port == "80")
}

func (g *LocalGateway) isSelfAddr(hostPort string) bool {
	hp := strings.TrimSpace(hostPort)
	if hp == g.listenAddr {
		return true
	}
	host, port, err := net.SplitHostPort(hp)
	if err != nil {
		return false
	}
	_, myPort, err := net.SplitHostPort(g.listenAddr)
	if err != nil || port != myPort {
		return false
	}
	return host == "127.0.0.1" || host == "::1" || strings.EqualFold(host, "localhost")
}

func (g *LocalGateway) handleConnect(w http.ResponseWriter, r *http.Request) {
	target := strings.TrimSpace(r.Host)
	if target == "" {
		target = strings.TrimSpace(r.URL.Host)
	}
	if target == "" {
		http.Error(w, "missing CONNECT target", http.StatusBadRequest)
		return
	}

	// Plaintext CONNECT to `<virtualHost>:80` (used by some soup2 WebSocket paths):
	// hijack and serve inner HTTP/1.1 + WebSocket requests through reverseProxy so
	// Host/Origin headers are normalized identically to direct proxy requests.
	if g.isVirtualHost(target) {
		hj, ok := w.(http.Hijacker)
		if !ok {
			http.Error(w, "hijacking unsupported", http.StatusInternalServerError)
			return
		}
		clientConn, brw, err := hj.Hijack()
		if err != nil {
			return
		}
		if _, err := io.WriteString(clientConn, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
			_ = clientConn.Close()
			return
		}
		wrapped := newBufferedConn(clientConn, brw.Reader)
		innerSrv := &http.Server{
			Handler:           g.reverseProxy,
			ReadHeaderTimeout: 10 * time.Second,
		}
		go func() {
			_ = innerSrv.Serve(&singleConnListener{conn: wrapped})
		}()
		return
	}

	if _, _, err := net.SplitHostPort(target); err != nil {
		target += ":443"
	}
	if g.isSelfAddr(target) {
		http.Error(w, "gateway self-connect refused", http.StatusLoopDetected)
		return
	}

	dialCtx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	upstreamConn, err := (&net.Dialer{}).DialContext(dialCtx, "tcp", target)
	if err != nil {
		http.Error(w, fmt.Sprintf("dial %s: %v", target, err), http.StatusBadGateway)
		return
	}

	hj, ok := w.(http.Hijacker)
	if !ok {
		_ = upstreamConn.Close()
		http.Error(w, "hijacking unsupported", http.StatusInternalServerError)
		return
	}
	clientConn, brw, err := hj.Hijack()
	if err != nil {
		_ = upstreamConn.Close()
		return
	}

	if _, err := io.WriteString(clientConn, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
		_ = clientConn.Close()
		_ = upstreamConn.Close()
		return
	}

	tunnelBidirectional(newBufferedConn(clientConn, brw.Reader), upstreamConn)
}

func (g *LocalGateway) forwardExternalHTTP(w http.ResponseWriter, r *http.Request, targetHost string) {
	if targetHost == "" || g.isSelfAddr(targetHost) {
		http.Error(w, "invalid gateway target", http.StatusBadRequest)
		return
	}
	scheme := strings.ToLower(r.URL.Scheme)
	if scheme == "" {
		scheme = "http"
	}
	if scheme != "http" && scheme != "https" {
		http.Error(w, "unsupported scheme", http.StatusBadRequest)
		return
	}

	outReq := r.Clone(r.Context())
	outReq.RequestURI = ""
	outReq.URL.Scheme = scheme
	outReq.URL.Host = targetHost
	stripProxyHeaders(outReq.Header)

	resp, err := g.externalTransport.RoundTrip(outReq)
	if err != nil {
		http.Error(w, fmt.Sprintf("upstream error: %v", err), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for k, vv := range resp.Header {
		for _, v := range vv {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

func stripProxyHeaders(h http.Header) {
	h.Del("Proxy-Connection")
	h.Del("Proxy-Authenticate")
	h.Del("Proxy-Authorization")
}

func matchesVirtualOrigin(origin, virtualHost string) bool {
	o := strings.ToLower(strings.TrimSpace(origin))
	return o == "http://"+virtualHost || o == "http://"+virtualHost+":80"
}

func rewriteVirtualReferer(referer, virtualHost, backendAddr string) (string, bool) {
	if referer == "" {
		return "", false
	}
	u, err := url.Parse(referer)
	if err != nil {
		return "", false
	}
	h := strings.ToLower(u.Host)
	if u.Scheme == "http" && (h == virtualHost || h == virtualHost+":80") {
		u.Host = backendAddr
		return u.String(), true
	}
	return "", false
}

func rewriteSetCookieDomain(rawCookie, virtualHost string) string {
	parts := strings.Split(rawCookie, ";")
	changed := false
	for i := 1; i < len(parts); i++ {
		attr := strings.TrimSpace(parts[i])
		k, v, ok := strings.Cut(attr, "=")
		if !ok || !strings.EqualFold(strings.TrimSpace(k), "Domain") {
			continue
		}
		dom := strings.TrimPrefix(strings.TrimSpace(v), ".")
		if dom == "127.0.0.1" || strings.EqualFold(dom, "localhost") {
			parts[i] = " Domain=" + virtualHost
			changed = true
		}
	}
	if !changed {
		return rawCookie
	}
	return strings.Join(parts, ";")
}

type bufferedConn struct {
	net.Conn
	r *bufio.Reader
}

func newBufferedConn(c net.Conn, r *bufio.Reader) net.Conn {
	if r == nil || r.Buffered() == 0 {
		return c
	}
	return &bufferedConn{Conn: c, r: r}
}

func (b *bufferedConn) Read(p []byte) (int, error) {
	return b.r.Read(p)
}

type singleConnListener struct {
	mu   sync.Mutex
	conn net.Conn
}

func (l *singleConnListener) Accept() (net.Conn, error) {
	l.mu.Lock()
	c := l.conn
	l.conn = nil
	l.mu.Unlock()
	if c == nil {
		return nil, io.EOF
	}
	return c, nil
}

func (l *singleConnListener) Close() error {
	l.mu.Lock()
	c := l.conn
	l.conn = nil
	l.mu.Unlock()
	if c != nil {
		return c.Close()
	}
	return nil
}

func (l *singleConnListener) Addr() net.Addr {
	return &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0}
}

func tunnelBidirectional(a, b net.Conn) {
	var wg sync.WaitGroup
	wg.Add(2)
	copyHalf := func(dst, src net.Conn) {
		defer wg.Done()
		_, _ = io.Copy(dst, src)
		if cw, ok := dst.(interface{ CloseWrite() error }); ok {
			_ = cw.CloseWrite()
		} else {
			_ = dst.Close()
		}
	}
	go copyHalf(a, b)
	go copyHalf(b, a)
	go func() {
		wg.Wait()
		_ = a.Close()
		_ = b.Close()
	}()
}
