package sidecar

import (
	"bufio"
	"crypto/sha1"
	"encoding/base64"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestVirtualHostAndMapCookieDomain(t *testing.T) {
	cases := map[string]string{
		"com.dbx.desktop":     "com-dbx-desktop.localhost",
		"  My_App..V2!! ":     "my-app-v2.localhost",
		"":                    "sidecar-app.localhost",
		"---":                 "sidecar-app.localhost",
		strings.Repeat("a", 80): strings.Repeat("a", 63) + ".localhost",
	}
	for in, want := range cases {
		if got := VirtualHost(in); got != want {
			t.Fatalf("VirtualHost(%q) = %q, want %q", in, got, want)
		}
	}

	if got := MapCookieDomain("", "com-dbx-desktop.localhost"); got != "com-dbx-desktop.localhost" {
		t.Fatalf("unexpected mapped empty domain: %q", got)
	}
	if got := MapCookieDomain("127.0.0.1", "com-dbx-desktop.localhost"); got != "com-dbx-desktop.localhost" {
		t.Fatalf("unexpected mapped loopback domain: %q", got)
	}
	if got := MapCookieDomain("custom.example", "com-dbx-desktop.localhost"); got != "custom.example" {
		t.Fatalf("unexpected mapped custom domain: %q", got)
	}
	if got := MapCookieDomain("", ""); got != "127.0.0.1" {
		t.Fatalf("unexpected fallback domain when virtualHost empty: %q", got)
	}
}

func startMockBackend(t *testing.T) (addr string) {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/echo", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Add("Set-Cookie", "sid=123; Path=/; Domain=127.0.0.1; HttpOnly")
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{
			"host":    r.Host,
			"origin":  r.Header.Get("Origin"),
			"referer": r.Header.Get("Referer"),
			"cookie":  r.Header.Get("Cookie"),
		})
	})
	mux.HandleFunc("/api/ws", func(w http.ResponseWriter, r *http.Request) {
		if !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
			http.Error(w, "expected websocket upgrade", http.StatusBadRequest)
			return
		}
		hj, ok := w.(http.Hijacker)
		if !ok {
			http.Error(w, "no hijacker", http.StatusInternalServerError)
			return
		}
		key := r.Header.Get("Sec-WebSocket-Key")
		sum := sha1.Sum([]byte(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
		accept := base64.StdEncoding.EncodeToString(sum[:])

		conn, brw, err := hj.Hijack()
		if err != nil {
			return
		}
		defer conn.Close()

		_, _ = brw.WriteString(
			"HTTP/1.1 101 Switching Protocols\r\n" +
				"Upgrade: websocket\r\n" +
				"Connection: Upgrade\r\n" +
				"X-Seen-Origin: " + r.Header.Get("Origin") + "\r\n" +
				"X-Seen-Host: " + r.Host + "\r\n" +
				"Sec-WebSocket-Accept: " + accept + "\r\n\r\n",
		)
		_ = brw.Flush()

		buf := make([]byte, 4)
		if _, err := io.ReadFull(brw, buf); err != nil {
			return
		}
		_, _ = conn.Write([]byte("PONG:" + string(buf)))
	})

	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	u, err := url.Parse(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	return u.Host
}

func TestLocalGatewayHTTPAndHeaderRewrite(t *testing.T) {
	backendAddr := startMockBackend(t)
	gw, err := StartLocalGateway("com-dbx-desktop.localhost", backendAddr)
	if err != nil {
		t.Fatalf("StartLocalGateway failed: %v", err)
	}
	defer gw.Close()

	proxyURL, err := url.Parse(gw.ProxyURI())
	if err != nil {
		t.Fatal(err)
	}
	client := &http.Client{
		Timeout:   5 * time.Second,
		Transport: &http.Transport{Proxy: http.ProxyURL(proxyURL)},
	}

	req, err := http.NewRequest(http.MethodGet, gw.OriginURL()+"/api/echo", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Origin", gw.OriginURL())
	req.Header.Set("Referer", gw.OriginURL()+"/workspace")
	req.Header.Set("Cookie", "dbx_session=ok")

	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("proxy GET failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}

	var got map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got["host"] != backendAddr {
		t.Fatalf("backend saw Host=%q, want %q", got["host"], backendAddr)
	}
	if got["origin"] != "http://"+backendAddr {
		t.Fatalf("backend saw Origin=%q, want %q", got["origin"], "http://"+backendAddr)
	}
	if got["referer"] != "http://"+backendAddr+"/workspace" {
		t.Fatalf("backend saw Referer=%q, want %q", got["referer"], "http://"+backendAddr+"/workspace")
	}
	if got["cookie"] != "dbx_session=ok" {
		t.Fatalf("backend saw Cookie=%q", got["cookie"])
	}
	setCookie := resp.Header.Get("Set-Cookie")
	if !strings.Contains(setCookie, "Domain=com-dbx-desktop.localhost") {
		t.Fatalf("expected Set-Cookie Domain rewrite, got %q", setCookie)
	}
}

func TestLocalGatewayWebSocketDirectAndConnect(t *testing.T) {
	backendAddr := startMockBackend(t)
	gw, err := StartLocalGateway("com-dbx-desktop.localhost", backendAddr)
	if err != nil {
		t.Fatal(err)
	}
	defer gw.Close()

	proxyURL, _ := url.Parse(gw.ProxyURI())

	// 1. Direct HTTP proxy `GET ws://...` (WebKitGTK libsoup-3.0 format).
	t.Run("direct_ws_upgrade", func(t *testing.T) {
		conn, err := net.DialTimeout("tcp", proxyURL.Host, 3*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		_ = conn.SetDeadline(time.Now().Add(5 * time.Second))

		rawReq := "GET ws://com-dbx-desktop.localhost/api/ws HTTP/1.1\r\n" +
			"Host: com-dbx-desktop.localhost\r\n" +
			"Origin: http://com-dbx-desktop.localhost\r\n" +
			"Connection: Upgrade\r\n" +
			"Upgrade: websocket\r\n" +
			"Sec-WebSocket-Version: 13\r\n" +
			"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
		if _, err := io.WriteString(conn, rawReq); err != nil {
			t.Fatal(err)
		}

		br := bufio.NewReader(conn)
		resp, err := http.ReadResponse(br, nil)
		if err != nil {
			t.Fatalf("read upgrade response: %v", err)
		}
		if resp.StatusCode != http.StatusSwitchingProtocols {
			t.Fatalf("expected 101, got %d", resp.StatusCode)
		}
		if resp.Header.Get("X-Seen-Origin") != "http://"+backendAddr {
			t.Fatalf("unexpected rewritten WS origin: %q", resp.Header.Get("X-Seen-Origin"))
		}

		if _, err := io.WriteString(conn, "PING"); err != nil {
			t.Fatal(err)
		}
		reply := make([]byte, 9)
		if _, err := io.ReadFull(br, reply); err != nil {
			t.Fatalf("read ws payload: %v", err)
		}
		if string(reply) != "PONG:PING" {
			t.Fatalf("unexpected ws reply %q", string(reply))
		}
	})

	// 2. Tunnel `CONNECT com-dbx-desktop.localhost:80` + inner WS upgrade (libsoup-2.4 fallback).
	t.Run("connect_tunnel_ws_upgrade", func(t *testing.T) {
		conn, err := net.DialTimeout("tcp", proxyURL.Host, 3*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		_ = conn.SetDeadline(time.Now().Add(5 * time.Second))

		if _, err := io.WriteString(conn, "CONNECT com-dbx-desktop.localhost:80 HTTP/1.1\r\nHost: com-dbx-desktop.localhost:80\r\n\r\n"); err != nil {
			t.Fatal(err)
		}
		br := bufio.NewReader(conn)
		connectResp, err := http.ReadResponse(br, nil)
		if err != nil {
			t.Fatalf("read CONNECT response: %v", err)
		}
		if connectResp.StatusCode != http.StatusOK {
			t.Fatalf("expected 200 on CONNECT, got %d", connectResp.StatusCode)
		}

		innerReq := "GET /api/ws HTTP/1.1\r\n" +
			"Host: com-dbx-desktop.localhost\r\n" +
			"Origin: http://com-dbx-desktop.localhost\r\n" +
			"Connection: Upgrade\r\n" +
			"Upgrade: websocket\r\n" +
			"Sec-WebSocket-Version: 13\r\n" +
			"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
		if _, err := io.WriteString(conn, innerReq); err != nil {
			t.Fatal(err)
		}
		wsResp, err := http.ReadResponse(br, nil)
		if err != nil {
			t.Fatalf("read inner WS response: %v", err)
		}
		if wsResp.StatusCode != http.StatusSwitchingProtocols {
			t.Fatalf("expected 101 inside CONNECT tunnel, got %d", wsResp.StatusCode)
		}
		if wsResp.Header.Get("X-Seen-Host") != backendAddr {
			t.Fatalf("unexpected inner WS host: %q", wsResp.Header.Get("X-Seen-Host"))
		}

		if _, err := io.WriteString(conn, "DATA"); err != nil {
			t.Fatal(err)
		}
		reply := make([]byte, 9)
		if _, err := io.ReadFull(br, reply); err != nil {
			t.Fatalf("read inner ws payload: %v", err)
		}
		if string(reply) != "PONG:DATA" {
			t.Fatalf("unexpected inner ws reply %q", string(reply))
		}
	})
}

func TestLocalGatewayExternalPassThroughAndLoopGuard(t *testing.T) {
	backendAddr := startMockBackend(t)
	gw, err := StartLocalGateway("com-dbx-desktop.localhost", backendAddr)
	if err != nil {
		t.Fatal(err)
	}
	defer gw.Close()

	extSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "external-ok")
	}))
	defer extSrv.Close()

	proxyURL, _ := url.Parse(gw.ProxyURI())
	client := &http.Client{
		Timeout:   5 * time.Second,
		Transport: &http.Transport{Proxy: http.ProxyURL(proxyURL)},
	}

	// Non-virtual host routes to external server, not backend.
	extResp, err := client.Get(extSrv.URL + "/check")
	if err != nil {
		t.Fatalf("external GET via gateway failed: %v", err)
	}
	body, _ := io.ReadAll(extResp.Body)
	_ = extResp.Body.Close()
	if string(body) != "external-ok" {
		t.Fatalf("unexpected external body %q", string(body))
	}

	// Self-loop request is rejected instead of recursing forever.
	loopResp, err := client.Get(gw.ProxyURI() + "/loop")
	if err != nil {
		t.Fatalf("self-loop GET transport error: %v", err)
	}
	_ = loopResp.Body.Close()
	if loopResp.StatusCode != http.StatusBadRequest {
		t.Fatalf("expected 400 for self-loop GET, got %d", loopResp.StatusCode)
	}
}
