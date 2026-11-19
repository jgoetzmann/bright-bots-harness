//go:build !linux

package cli

// hide does nothing where there is no /proc to hide from.
func hide() error { return nil }
