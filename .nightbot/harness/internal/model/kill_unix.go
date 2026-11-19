//go:build unix

package model

import (
	"os/exec"
	"syscall"
)

// killGroup makes a timed-out call take its whole process group with it: model CLIs start
// language servers, test runners and shells that would otherwise outlive the call.
func killGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
}
