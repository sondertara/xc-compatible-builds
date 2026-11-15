//go:build linux && (amd64 || arm64)

package sidecar

import (
	"errors"
	"fmt"
	"runtime"
	"strings"
	"sync"

	"github.com/ebitengine/purego"
)

const webkitNetworkProxyModeCustom int32 = 2 // WEBKIT_NETWORK_PROXY_MODE_CUSTOM

var (
	webkitProxyOnce sync.Once
	webkitProxyErr  error

	wkWebContextGetDefault              func() uintptr
	wkWebContextGetWebsiteDataManager   func(ctx uintptr) uintptr
	wkNetworkProxySettingsNew           func(defaultProxyURI *byte, ignoreHosts **byte) uintptr
	wkNetworkProxySettingsFree          func(settings uintptr)
	wkDataManagerSetNetworkProxy        func(manager uintptr, mode int32, settings uintptr)
	wkWebContextSetNetworkProxyFallback func(ctx uintptr, mode int32, settings uintptr)
)

func loadWebKitProxySymbols() error {
	webkitProxyOnce.Do(func() {
		var lib uintptr
		var openErrs []string
		// Match MyGO's exact dlopen order (4.1 -> 4.0) so we attach to the same loaded SONAME.
		for _, name := range []string{"libwebkit2gtk-4.1.so.0", "libwebkit2gtk-4.0.so.37"} {
			h, err := purego.Dlopen(name, purego.RTLD_NOW|purego.RTLD_GLOBAL)
			if err == nil && h != 0 {
				lib = h
				break
			}
			if err != nil {
				openErrs = append(openErrs, err.Error())
			}
		}
		if lib == 0 {
			webkitProxyErr = fmt.Errorf("dlopen webkit2gtk failed: %s", strings.Join(openErrs, "; "))
			return
		}

		bindReq := func(fn any, sym string) bool {
			if webkitProxyErr != nil {
				return false
			}
			addr, err := purego.Dlsym(lib, sym)
			if err != nil || addr == 0 {
				webkitProxyErr = fmt.Errorf("missing WebKitGTK symbol %s", sym)
				return false
			}
			purego.RegisterFunc(fn, addr)
			return true
		}

		bindOpt := func(fn any, sym string) bool {
			addr, err := purego.Dlsym(lib, sym)
			if err != nil || addr == 0 {
				return false
			}
			purego.RegisterFunc(fn, addr)
			return true
		}

		bindReq(&wkWebContextGetDefault, "webkit_web_context_get_default")
		bindReq(&wkWebContextGetWebsiteDataManager, "webkit_web_context_get_website_data_manager")
		bindReq(&wkNetworkProxySettingsNew, "webkit_network_proxy_settings_new")
		bindReq(&wkNetworkProxySettingsFree, "webkit_network_proxy_settings_free")

		hasMgr := bindOpt(&wkDataManagerSetNetworkProxy, "webkit_website_data_manager_set_network_proxy_settings")
		hasCtx := bindOpt(&wkWebContextSetNetworkProxyFallback, "webkit_web_context_set_network_proxy_settings")
		if webkitProxyErr == nil && !hasMgr && !hasCtx {
			webkitProxyErr = errors.New("neither webkit_website_data_manager_set_network_proxy_settings nor webkit_web_context_set_network_proxy_settings found")
		}
	})
	return webkitProxyErr
}

// configureWebKitNetworkProxy sets a custom HTTP proxy on WebKitGTK's default website data
// manager. Must be invoked on the GTK main thread (inside mygo.App.WhenReady) before windows open.
func configureWebKitNetworkProxy(proxyURI string) (bool, error) {
	uri := strings.TrimSpace(proxyURI)
	if uri == "" {
		return false, errors.New("proxyURI must not be empty")
	}
	if err := loadWebKitProxySymbols(); err != nil {
		return false, err
	}

	ctx := wkWebContextGetDefault()
	if ctx == 0 {
		return false, errors.New("webkit_web_context_get_default returned NULL")
	}

	proxyCStr := cString(uri)
	ign1 := cString("127.0.0.1")
	ign2 := cString("::1")
	ignoreArr := [3]*byte{&ign1[0], &ign2[0], nil}

	settings := wkNetworkProxySettingsNew(&proxyCStr[0], &ignoreArr[0])
	runtime.KeepAlive(proxyCStr)
	runtime.KeepAlive(ign1)
	runtime.KeepAlive(ign2)
	runtime.KeepAlive(ignoreArr)
	if settings == 0 {
		return false, errors.New("webkit_network_proxy_settings_new returned NULL")
	}
	defer wkNetworkProxySettingsFree(settings)

	if wkDataManagerSetNetworkProxy != nil {
		mgr := wkWebContextGetWebsiteDataManager(ctx)
		if mgr == 0 {
			return false, errors.New("webkit_web_context_get_website_data_manager returned NULL")
		}
		wkDataManagerSetNetworkProxy(mgr, webkitNetworkProxyModeCustom, settings)
		return true, nil
	}

	wkWebContextSetNetworkProxyFallback(ctx, webkitNetworkProxyModeCustom, settings)
	return true, nil
}

func cString(s string) []byte {
	b := make([]byte, len(s)+1)
	copy(b, s)
	return b
}
