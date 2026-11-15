//go:build !linux || (!amd64 && !arm64)

package sidecar

func configureWebKitNetworkProxy(_ string) (bool, error) {
	return false, nil
}
