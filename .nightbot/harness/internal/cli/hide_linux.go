//go:build linux

package cli

import "syscall"

// hide makes this process non-dumpable: its /proc/<pid> files (environ, mem) then belong to
// root, so the model and the checks it starts, which run as the same user, cannot read the
// tokens out of the harness's environment or memory. It does not stop a model with sudo.
func hide() error {
	_, _, errno := syscall.RawSyscall(syscall.SYS_PRCTL, syscall.PR_SET_DUMPABLE, 0, 0)
	if errno != 0 {
		return errno
	}
	return nil
}
