// Package gitx runs git in a checkout.
package gitx

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"os/exec"
	"strings"
)

// Repo is a checkout.
type Repo struct {
	Dir string
	// Env is added to git's environment (an auth header for a push, say).
	Env []string
}

// safe keeps a checkout's own configuration from running anything inside the harness's git: a
// model can write .git/hooks and .git/config, and its hooks or fsmonitor would otherwise run with
// the harness's environment.
var safe = []string{"-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false"}

// Git runs git with args and returns its trimmed stdout.
func (r *Repo) Git(args ...string) (string, error) {
	cmd := exec.Command("git", append(append([]string(nil), safe...), args...)...)
	cmd.Dir = r.Dir
	cmd.Env = append(cmd.Environ(), r.Env...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("git %s: %v: %s", strings.Join(redact(args), " "), err, strings.TrimSpace(stderr.String()))
	}
	return strings.TrimSpace(stdout.String()), nil
}

// MustGit is Git for calls that only fail when the checkout is broken.
func (r *Repo) MustGit(args ...string) string {
	out, err := r.Git(args...)
	if err != nil {
		panic(err)
	}
	return out
}

func redact(args []string) []string {
	out := make([]string, len(args))
	for i, a := range args {
		if strings.Contains(strings.ToLower(a), "authorization") || strings.Contains(a, "x-access-token") {
			a = "[redacted]"
		}
		out[i] = a
	}
	return out
}

// Lines splits git's output into its non-empty lines.
func Lines(out string) []string {
	var lines []string
	for _, line := range strings.Split(out, "\n") {
		if line = strings.TrimSpace(line); line != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

// Changed lists the paths that differ between base and head, renames as both their paths. The
// names are git's own bytes (-z), never quoted, so a name with an accent or a quote in it matches
// the patterns it should.
func (r *Repo) Changed(base, head string) ([]string, error) {
	out, err := r.raw("diff", "--name-only", "-z", "--no-renames", base, head, "--")
	if err != nil {
		return nil, err
	}
	return nulSplit(out), nil
}

// Staged lists the paths the index changed since rev.
func (r *Repo) Staged(rev string) ([]string, error) {
	out, err := r.raw("diff", "--cached", "--name-only", "-z", "--no-renames", rev, "--")
	if err != nil {
		return nil, err
	}
	return nulSplit(out), nil
}

// Dirty lists the paths the working tree and the index changed since HEAD, tracked or not
// (ignored files are left out).
func (r *Repo) Dirty() ([]string, error) {
	out, err := r.raw("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
	if err != nil {
		return nil, err
	}
	var paths []string
	for _, entry := range strings.Split(out, "\x00") {
		if len(entry) > 3 {
			paths = append(paths, entry[3:])
		}
	}
	return paths, nil
}

// Unmerged lists the paths a merge left in conflict.
func (r *Repo) Unmerged() ([]string, error) {
	out, err := r.raw("diff", "--name-only", "-z", "--diff-filter=U")
	if err != nil {
		return nil, err
	}
	return nulSplit(out), nil
}

// Literal is a pathspec that means exactly path, with no glob or magic in it.
func Literal(path string) string { return ":(literal)" + path }

func nulSplit(out string) []string {
	var paths []string
	for _, p := range strings.Split(out, "\x00") {
		if p != "" {
			paths = append(paths, p)
		}
	}
	return paths
}

// raw runs git and returns its stdout untrimmed (for -z output).
func (r *Repo) raw(args ...string) (string, error) {
	cmd := exec.Command("git", append(append([]string(nil), safe...), args...)...)
	cmd.Dir = r.Dir
	cmd.Env = append(cmd.Environ(), r.Env...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("git %s: %v: %s", strings.Join(redact(args), " "), err, strings.TrimSpace(stderr.String()))
	}
	return stdout.String(), nil
}

// Exists says whether a revision exists.
func (r *Repo) Exists(rev string) bool {
	_, err := r.Git("rev-parse", "--verify", "--quiet", rev+"^{commit}")
	return err == nil
}

// AuthEnv has git authenticate to server with token, through the same extra header
// actions/checkout uses, without writing the token to any file or command line. It is set on the
// harness's own git commands only; the model's environment never has it.
func AuthEnv(server, token string) []string {
	if token == "" {
		return nil
	}
	if server == "" {
		server = "https://github.com"
	}
	header := "AUTHORIZATION: basic " + base64.StdEncoding.EncodeToString([]byte("x-access-token:"+token))
	return []string{"GIT_CONFIG_COUNT=1", "GIT_CONFIG_KEY_0=http." + strings.TrimRight(server, "/") + "/.extraheader",
		"GIT_CONFIG_VALUE_0=" + header, "GIT_TERMINAL_PROMPT=0"}
}
