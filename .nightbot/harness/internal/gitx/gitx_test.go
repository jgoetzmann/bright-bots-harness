package gitx

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestNamesAreGitsOwnBytes(t *testing.T) {
	dir := t.TempDir()
	r := &Repo{Dir: dir}
	r.MustGit("init", "-q", "-b", "main")
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("a"), 0o644)
	r.MustGit("add", "-A")
	r.MustGit("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "a")
	base := r.MustGit("rev-parse", "HEAD")
	os.MkdirAll(filepath.Join(dir, ".github", "workflows"), 0o755)
	weird := []string{".github/workflows/café.yml", `odd "name".txt`, "tab\there.txt"}
	for _, name := range weird {
		os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o644)
	}
	dirty, err := r.Dirty()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range weird {
		if !slices.Contains(dirty, name) {
			t.Errorf("Dirty lacks %q: %q", name, dirty)
		}
	}
	r.MustGit("add", "-A")
	r.MustGit("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "b")
	changed, err := r.Changed(base, "HEAD")
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range weird {
		if !slices.Contains(changed, name) {
			t.Errorf("Changed lacks %q: %q", name, changed)
		}
	}
}

func TestHooksDoNotRun(t *testing.T) {
	dir := t.TempDir()
	r := &Repo{Dir: dir}
	r.MustGit("init", "-q", "-b", "main")
	hook := filepath.Join(dir, ".git", "hooks", "post-commit")
	os.WriteFile(hook, []byte("#!/bin/sh\ntouch "+filepath.Join(dir, "ran")+"\n"), 0o755)
	os.WriteFile(filepath.Join(dir, "a"), []byte("a"), 0o644)
	r.MustGit("add", "-A")
	r.MustGit("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "a")
	if _, err := os.Stat(filepath.Join(dir, "ran")); err == nil {
		t.Fatal("the checkout's post-commit hook ran inside the harness's git")
	}
}
