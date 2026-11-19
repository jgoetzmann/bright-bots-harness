package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// templateRoot is the template directory this harness ships in (template/.nightbot/harness).
func templateRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs("../../../..")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, Dir, "config.json")); err != nil {
		t.Skip("not inside the template: ", err)
	}
	return root
}

func TestLoad_TheTemplateIsValid(t *testing.T) {
	cfg, err := Load(templateRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	if err := cfg.CheckWorkflows(); err != nil {
		t.Fatal(err)
	}
	// An install may rename and replace the providers; every one must still be complete.
	if len(cfg.Providers.Providers) == 0 {
		t.Fatal("no providers")
	}
	for id, p := range cfg.Providers.Providers {
		if p.Strongest() == "" || (p.CLI == "claude" && p.Login == "secret" && p.Secret == "") {
			t.Fatalf("%s: %+v", id, p)
		}
	}
	if cfg.CLIs["claude"].System != "file" || cfg.CLIs["gemini"].System != "prepend" {
		t.Fatalf("system delivery: claude %q gemini %q", cfg.CLIs["claude"].System, cfg.CLIs["gemini"].System)
	}
}

func TestValidate_ReportsEveryProblem(t *testing.T) {
	cfg := &Config{BotLogin: "", Timezone: "Mars/Olympus", ForbiddenPaths: []string{".github/"},
		Providers: &Providers{Providers: map[string]*Provider{
			"a": {Enabled: true, CLI: "nope", Login: "secret", Secret: "lower case"},
			"b": {Enabled: true, CLI: "claude", Models: []Seat{{Model: "x", Tier: "huge"}}, Login: "machine", RunsOn: "ubuntu-latest"},
			"c": {Enabled: true, CLI: "claude", Models: []Seat{{Model: "x", Tier: "weak"}}, RunsOn: "box", Schedule: Schedule{Mode: "window", Start: "9", End: "17:00"}},
			"d": {Enabled: true, CLI: "claude", Models: []Seat{{Model: "x", Tier: "weak"}}, RunsOn: "box"},
		}}}
	cfg.applyDefaults()
	err := cfg.Validate()
	if err == nil {
		t.Fatal("no error")
	}
	for _, want := range []string{"bot_login", "timezone", "forbidden_paths must cover .nightbot", `providers.a.cli "nope"`,
		"providers.a.secret", "providers.a.models", "tier (weak, medium or strong)", `login "machine" lives on a machine`,
		"providers.c.schedule", `also c's`} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("missing %q in:\n%v", want, err)
		}
	}
}

func TestMatchAny(t *testing.T) {
	cases := []struct {
		pattern, path string
		want          bool
	}{
		{".github/", ".github/workflows/ci.yml", true},
		{".github/", "src/.github/x", false},
		{"**/go.mod", "go.mod", true},
		{"**/go.mod", "a/b/go.mod", true},
		{"**/go.mod", "a/b/go.mod.bak", false},
		{"*.md", "README.md", true},
		{"*.md", "docs/README.md", false},
		{"**/migrations/**", "db/migrations/001.sql", true},
		{".mcp.json", ".mcp.json", true},
		{"src/?.go", "src/a.go", true},
	}
	for _, c := range cases {
		if got := MatchAny([]string{c.pattern}, c.path); got != c.want {
			t.Errorf("MatchAny(%q, %q) = %v", c.pattern, c.path, got)
		}
	}
}

func TestInWindow_AcrossMidnight(t *testing.T) {
	cfg := &Config{Location: time.UTC}
	night := Schedule{Mode: "window", Start: "22:00", End: "07:00"}
	at := func(h, m int) time.Time { return time.Date(2026, 1, 1, h, m, 0, 0, time.UTC) }
	for _, c := range []struct {
		t    time.Time
		want bool
	}{{at(21, 59), false}, {at(22, 0), true}, {at(3, 0), true}, {at(6, 59), true}, {at(7, 0), false}, {at(12, 0), false}} {
		if got := cfg.InWindow(night, c.t); got != c.want {
			t.Errorf("%s: %v", c.t.Format("15:04"), got)
		}
	}
	if !cfg.InWindow(Schedule{Mode: "always"}, at(12, 0)) {
		t.Error("always")
	}
}

func TestSeatFor_TakesTheWeakestThatFits(t *testing.T) {
	p := &Provider{Models: []Seat{{Model: "opus", Tier: "strong"}, {Model: "sonnet", Tier: "medium"}, {Model: "haiku", Tier: "weak"}}}
	for tier, want := range map[string]string{"weak": "haiku", "medium": "sonnet", "strong": "opus"} {
		if got := p.SeatFor(tier); got == nil || got.Model != want {
			t.Errorf("SeatFor(%s) = %+v", tier, got)
		}
	}
	medium := &Provider{Models: []Seat{{Model: "m", Tier: "medium"}}}
	if medium.SeatFor("strong") != nil {
		t.Error("a medium provider took a strong item")
	}
	if got := medium.SeatFor("weak"); got == nil || got.Model != "m" {
		t.Error("a medium model should answer queries when no weak one is there")
	}
}

func TestCIRunsInMergeQueue(t *testing.T) {
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, ".github", "workflows"), 0o755)
	write := func(text string) {
		os.WriteFile(filepath.Join(root, ".github", "workflows", "ci.yml"), []byte(text), 0o644)
	}
	cfg := &Config{Root: root, CIWorkflow: "CI"}
	write("name: CI\non:\n  pull_request:\n")
	if cfg.CIRunsInMergeQueue() {
		t.Error("a CI without merge_group")
	}
	write("name: \"CI\"\non:\n  pull_request:\n  merge_group:\n")
	if !cfg.CIRunsInMergeQueue() {
		t.Error("a CI with merge_group")
	}
	cfg.CIWorkflow = "Build"
	if cfg.CIRunsInMergeQueue() {
		t.Error("no workflow is named Build")
	}
}
