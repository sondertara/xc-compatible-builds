module github.com/sondertara/xc-compatible-builds/apps/dbx

go 1.27.3

require github.com/sondertara/xc-compatible-builds/shared/mygo-sidecar v0.0.0

require (
	github.com/ebitengine/purego v0.11.1 // indirect
	github.com/egoist/mygo v0.9.0 // indirect
	golang.org/x/net v0.59.0 // indirect
	golang.org/x/text v0.43.0 // indirect
)

replace github.com/sondertara/xc-compatible-builds/shared/mygo-sidecar => ../../shared/mygo-sidecar
