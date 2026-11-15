package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"

	dbx "github.com/sondertara/xc-compatible-builds/apps/dbx"
	sidecar "github.com/sondertara/xc-compatible-builds/shared/mygo-sidecar"
)

var version = "dev"

func formattedVersion() string {
	v := strings.TrimSpace(version)
	if v == "" || v == "dev" {
		return "dev"
	}
	if strings.HasPrefix(v, "v") {
		return v
	}
	return "v" + v
}

func main() {
	for _, arg := range os.Args[1:] {
		if arg == "--version" || arg == "-version" {
			fmt.Printf("dbx-desktop %s\n", formattedVersion())
			return
		}
	}

	backendFlag := flag.String("backend", "", "path to dbx-web-bin executable")
	staticDirFlag := flag.String("static-dir", "", "optional frontend dist directory override (DBX_STATIC_DIR)")
	dataDirFlag := flag.String("data-dir", "", "DBX data directory override (DBX_DATA_DIR)")
	portFlag := flag.Int("port", 0, "loopback listen port (default: ephemeral free port)")
	enableGPUFlag := flag.Bool("enable-gpu", false, "enable WebKitGTK GPU compositing")
	devToolsFlag := flag.Bool("devtools", false, "enable WebKitGTK Web Inspector")
	smokeTestFlag := flag.Bool("smoke-test", false, "run headless sidecar startup/auth smoke test and exit")
	uiSmokeFlag := flag.String("ui-smoke", "", "run real WebKitGTK window smoke test and write screenshot.png + ui-smoke-report.json to directory")
	versionFlag := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *versionFlag {
		fmt.Printf("dbx-desktop %s\n", formattedVersion())
		return
	}

	adapter := dbx.NewAdapter(dbx.Options{
		BackendPath: *backendFlag,
		StaticDir:   *staticDirFlag,
		DataDir:     *dataDirFlag,
	})

	err := sidecar.Run(context.Background(), adapter, sidecar.Options{
		Port:       *portFlag,
		EnableGPU:  *enableGPUFlag,
		DevTools:   *devToolsFlag,
		SmokeTest:  *smokeTestFlag,
		UISmokeDir: *uiSmokeFlag,
	})
	if err != nil {
		log.Fatalf("dbx-desktop: %v", err)
	}
}
