//go:build !unix

package model

import "os/exec"

// killGroup kills only the CLI itself where there are no process groups.
func killGroup(cmd *exec.Cmd) {}
