package model

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"nightbot/internal/config"
)

var now = time.Date(2026, 3, 1, 12, 0, 0, 0, time.UTC)

func runner(cli config.CLI, exec Exec) *Runner {
	return &Runner{Name: "x", CLI: cli, Secret: "s3cret-value-123", Env: []string{"HOME=" + os.TempDir(), "PATH=/bin",
		"GITHUB_TOKEN=ghs_x", "NIGHTBOT_GITHUB_TOKEN=ghp_y", "ACTIONS_RUNTIME_TOKEN=z", "OTHER_API_KEY=k", "LANG=C"},
		Exec: exec, Now: func() time.Time { return now }, Location: time.UTC}
}

func TestRun_ClaudeJSONAnswerAndCleanEnvironment(t *testing.T) {
	cli := config.BuiltinCLIs()["claude"]
	cli.System = "file"
	var gotArgv, gotEnv []string
	var gotStdin string
	r := runner(cli, func(ctx context.Context, argv, env []string, stdin, cwd string) (string, string, int, error) {
		gotArgv, gotEnv, gotStdin = argv, env, stdin
		system, _ := os.ReadFile(argv[indexOf(argv, "--append-system-prompt-file")+1])
		if string(system) != "be good" {
			t.Errorf("system file: %q", system)
		}
		return `{"type":"result","result":"done it","is_error":false}`, "", 0, nil
	})
	o := r.Run(Call{Kind: "build", Write: true, Model: "opus", Effort: "high", Prompt: "do it", System: "be good", Cwd: "/w", Turns: 7, Timeout: time.Minute})
	if o.Answer != "done it" || o.Err != "" || o.RateLimited {
		t.Fatalf("%+v", o)
	}
	joined := strings.Join(gotArgv, " ")
	for _, want := range []string{"--model opus", "--max-turns 7", "--effort high", "--permission-mode bypassPermissions"} {
		if !strings.Contains(joined, want) {
			t.Errorf("argv lacks %q: %s", want, joined)
		}
	}
	if gotStdin != "do it" {
		t.Errorf("stdin %q", gotStdin)
	}
	env := strings.Join(gotEnv, "\n")
	for _, leaked := range []string{"ghs_x", "ghp_y", "ACTIONS_RUNTIME_TOKEN", "OTHER_API_KEY"} {
		if strings.Contains(env, leaked) {
			t.Errorf("the model saw %s", leaked)
		}
	}
	if !strings.Contains(env, "CLAUDE_CODE_OAUTH_TOKEN=s3cret-value-123") || !strings.Contains(env, "LANG=C") {
		t.Errorf("env: %s", env)
	}
}

func indexOf(list []string, s string) int {
	for i, x := range list {
		if x == s {
			return i
		}
	}
	return -1
}

func TestRun_ReadOnlyCallsTakeReadArgs(t *testing.T) {
	cli := config.CLI{Argv: []string{"x"}, WriteArgs: []string{"--write"}, ReadArgs: []string{"--read"}, Tail: []string{"-"}, Prompt: "stdin", Answer: "stdout"}
	r := runner(cli, nil)
	if got := strings.Join(r.Argv(Call{}, map[string]string{}), " "); got != "x --read -" {
		t.Fatalf("%s", got)
	}
}

func TestRun_AnswerFileAndSecretFile(t *testing.T) {
	home := t.TempDir()
	cli := config.CLI{Argv: []string{"codex", "-o", "{answer_file}"}, Prompt: "stdin", Answer: "file", SecretFile: "~/.codex/auth.json"}
	r := runner(cli, func(ctx context.Context, argv, env []string, stdin, cwd string) (string, string, int, error) {
		os.WriteFile(argv[2], []byte("from the file\n"), 0o600)
		return "noise", "", 0, nil
	})
	r.Env = []string{"HOME=" + home}
	o := r.Run(Call{Timeout: time.Minute})
	if o.Answer != "from the file" {
		t.Fatalf("%+v", o)
	}
	data, err := os.ReadFile(filepath.Join(home, ".codex", "auth.json"))
	if err != nil || string(data) != "s3cret-value-123" {
		t.Fatalf("secret file: %q %v", data, err)
	}
	if info, _ := os.Stat(filepath.Join(home, ".codex", "auth.json")); info.Mode().Perm() != 0o600 {
		t.Errorf("mode %v", info.Mode())
	}
}

func TestRun_RateLimitParksWithTheResetTime(t *testing.T) {
	cli := config.BuiltinCLIs()["claude"]
	r := runner(cli, func(ctx context.Context, argv, env []string, stdin, cwd string) (string, string, int, error) {
		return `{"type":"result","result":"Claude AI usage limit reached. Your limit resets at 2026-03-01T15:30:00Z","is_error":true}`, "", 1, nil
	})
	o := r.Run(Call{Timeout: time.Minute})
	if !o.RateLimited || !o.ResetAt.Equal(time.Date(2026, 3, 1, 15, 30, 0, 0, time.UTC)) {
		t.Fatalf("%+v", o)
	}
}

func TestRun_AnAnswerAboutRateLimitsIsNotARefusal(t *testing.T) {
	r := runner(config.CLI{Argv: []string{"x"}, Prompt: "stdin", Answer: "stdout"}, func(ctx context.Context, argv, env []string, stdin, cwd string) (string, string, int, error) {
		return "I added a rate limit to the API.", "", 0, nil
	})
	if o := r.Run(Call{Timeout: time.Minute}); o.RateLimited || o.Err != "" {
		t.Fatalf("%+v", o)
	}
}

func TestResetTime(t *testing.T) {
	cases := map[string]time.Time{
		"try again in 30 minutes":             now.Add(30 * time.Minute),
		"Rate limit. Retry in 2 hours":        now.Add(2 * time.Hour),
		"limit reached|1772380800":            time.Unix(1772380800, 0).UTC(),
		"resets 3pm (America/New_York)":       time.Date(2026, 3, 1, 20, 0, 0, 0, time.UTC),
		"usage limit reached, resets at 11am": time.Date(2026, 3, 2, 11, 0, 0, 0, time.UTC),
		"quota exceeded":                      {},
		"resets at 2031-01-01T00:00:00Z":      {},
		"try again in 9000 hours":             {},
	}
	for text, want := range cases {
		if got := ResetTime(text, now, time.UTC); !got.Equal(want) {
			t.Errorf("%q: %v, want %v", text, got, want)
		}
	}
}

func TestRun_TimeoutKillsTheCall(t *testing.T) {
	r := runner(config.CLI{Argv: []string{"sleep", "30"}, Prompt: "stdin", Answer: "stdout"}, nil)
	r.Now = time.Now
	start := time.Now()
	o := r.Run(Call{Timeout: 300 * time.Millisecond, Cwd: t.TempDir()})
	if !o.TimedOut || time.Since(start) > 10*time.Second {
		t.Fatalf("%+v after %s", o, time.Since(start))
	}
}
