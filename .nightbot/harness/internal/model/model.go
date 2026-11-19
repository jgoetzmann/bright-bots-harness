// Package model runs one call of a model CLI, as its config.CLI entry describes, and reads back
// its answer, how long it took and whether its subscription refused for usage limits.
package model

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"nightbot/internal/config"
)

// Call is one model call.
type Call struct {
	Kind    string // build, fix, review, ask or triage: its max_turns
	Write   bool   // may edit files (WriteArgs) or not (ReadArgs)
	Model   string
	Effort  string
	Prompt  string
	System  string
	Cwd     string
	Turns   int
	Timeout time.Duration
}

// Outcome is what a call came back with.
type Outcome struct {
	Answer      string        `json:"answer"`
	Output      string        `json:"output"` // the tail of stdout and stderr, for the record
	Duration    time.Duration `json:"duration"`
	ExitCode    int           `json:"exit_code"`
	TimedOut    bool          `json:"timed_out"`
	RateLimited bool          `json:"rate_limited"`
	ResetAt     time.Time     `json:"reset_at,omitzero"`
	Err         string        `json:"error,omitempty"`
}

// Failed says whether the call produced nothing usable.
func (o *Outcome) Failed() bool { return o.Err != "" || o.RateLimited || o.TimedOut }

// Exec runs argv; a seam for the tests.
type Exec func(ctx context.Context, argv, env []string, stdin, cwd string) (stdout, stderr string, code int, err error)

// Runner runs calls of one CLI with one login.
type Runner struct {
	Name     string
	CLI      config.CLI
	Secret   string   // the secret login's value, or "" on a machine login
	Env      []string // the environment to start from, os.Environ() by default
	TempDir  string
	Location *time.Location
	Exec     Exec
	Now      func() time.Time
}

// RateLimitWords mark a refusal for usage limits in a CLI's output, across the CLIs.
var RateLimitWords = regexp.MustCompile(`(?i)(usage limit|rate limit|too many requests|limit reached|` +
	`hit your (?:\w+ )?limit|quota exceeded|exhausted your (?:\w+ )?quota|resource_exhausted|` +
	`quota reached|quota exhausted|rate_limit_error|overloaded_error|insufficient_quota)`)

// sensitive are environment variables the model never sees: the runner's tokens and every other
// secret-looking name. The one login it needs is added back by name.
var sensitive = regexp.MustCompile(`(?i)(TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|CREDENTIAL|^ACTIONS_|^NIGHTBOT_)`)

// Run makes the call.
func (r *Runner) Run(call Call) *Outcome {
	now := r.Now
	if now == nil {
		now = time.Now
	}
	out := &Outcome{}
	dir, err := os.MkdirTemp(r.TempDir, "call-")
	if err != nil {
		out.Err = err.Error()
		return out
	}
	defer os.RemoveAll(dir)
	files := map[string]string{
		"prompt_file": filepath.Join(dir, "prompt.md"),
		"system_file": filepath.Join(dir, "system.md"),
		"answer_file": filepath.Join(dir, "answer.md"),
	}
	prompt := call.Prompt
	if r.CLI.System != "file" && call.System != "" {
		prompt = call.System + "\n\n---\n\n" + prompt
	}
	if err := os.WriteFile(files["prompt_file"], []byte(prompt), 0o600); err != nil {
		out.Err = err.Error()
		return out
	}
	if err := os.WriteFile(files["system_file"], []byte(call.System), 0o600); err != nil {
		out.Err = err.Error()
		return out
	}
	vars := map[string]string{"model": call.Model, "effort": call.Effort, "max_turns": strconv.Itoa(call.Turns),
		"cwd": call.Cwd, "prompt": prompt}
	for k, v := range files {
		vars[k] = v
	}
	argv := r.Argv(call, vars)
	env, err := r.environment(vars)
	if err != nil {
		out.Err = err.Error()
		return out
	}
	stdin := ""
	if r.CLI.Prompt == "stdin" {
		stdin = prompt
	}
	ctx, cancel := context.WithTimeout(context.Background(), call.Timeout)
	defer cancel()
	run := r.Exec
	if run == nil {
		run = execute
	}
	start := now()
	stdout, stderr, code, err := run(ctx, argv, env, stdin, call.Cwd)
	out.Duration = now().Sub(start)
	out.ExitCode = code
	out.Output = tail(stdout+"\n"+stderr, 20000)
	if ctx.Err() == context.DeadlineExceeded {
		out.TimedOut = true
		out.Err = fmt.Sprintf("the call ran past its %s", call.Timeout)
	} else if err != nil && code == 0 {
		out.Err = err.Error() // it never started
	}
	answer, isError := r.answer(stdout, files["answer_file"])
	out.Answer = strings.TrimSpace(answer)
	refused := code != 0 || isError || out.Answer == ""
	if refused && r.rateLimited(stdout+"\n"+stderr+"\n"+answer) {
		out.RateLimited = true
		out.ResetAt = ResetTime(stdout+"\n"+stderr+"\n"+answer, now(), r.Location)
	}
	if out.Err == "" && !out.RateLimited && (code != 0 || isError) {
		out.Err = fmt.Sprintf("%s exited %d: %s", r.Name, code, lastLine(stderr, stdout))
	}
	return out
}

// Argv is the command line of a call.
func (r *Runner) Argv(call Call, vars map[string]string) []string {
	var argv []string
	add := func(list []string) {
		for _, a := range list {
			argv = append(argv, expand(a, vars))
		}
	}
	add(r.CLI.Argv)
	if call.Write {
		add(r.CLI.WriteArgs)
	} else {
		add(r.CLI.ReadArgs)
	}
	if call.Effort != "" {
		add(r.CLI.EffortArgs)
	}
	add(r.CLI.Tail)
	return argv
}

func expand(s string, vars map[string]string) string {
	for k, v := range vars {
		s = strings.ReplaceAll(s, "{"+k+"}", v)
	}
	return s
}

func (r *Runner) environment(vars map[string]string) ([]string, error) {
	base := r.Env
	if base == nil {
		base = os.Environ()
	}
	var env []string
	home := ""
	for _, kv := range base {
		name, value, _ := strings.Cut(kv, "=")
		if name == "HOME" {
			home = value
		}
		if !sensitive.MatchString(name) {
			env = append(env, kv)
		}
	}
	for k, v := range r.CLI.Env {
		env = append(env, k+"="+expand(v, vars))
	}
	if r.Secret == "" {
		return env, nil
	}
	if r.CLI.SecretEnv != "" {
		env = append(env, r.CLI.SecretEnv+"="+r.Secret)
	}
	if r.CLI.SecretFile != "" {
		path := r.CLI.SecretFile
		if strings.HasPrefix(path, "~/") {
			if home == "" {
				home, _ = os.UserHomeDir()
			}
			path = filepath.Join(home, path[2:])
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			return nil, err
		}
		if err := os.WriteFile(path, []byte(r.Secret), 0o600); err != nil {
			return nil, err
		}
	}
	return env, nil
}

// answer reads the model's final message, and whether the CLI marked the call as failed.
func (r *Runner) answer(stdout, answerFile string) (string, bool) {
	switch {
	case r.CLI.Answer == "file":
		data, err := os.ReadFile(answerFile)
		if err != nil {
			return "", false
		}
		return string(data), false
	case strings.HasPrefix(r.CLI.Answer, "json:"):
		field := strings.TrimPrefix(r.CLI.Answer, "json:")
		object := lastJSONObject(stdout)
		if object == nil {
			return "", false
		}
		text, _ := object[field].(string)
		isError, _ := object["is_error"].(bool)
		return text, isError
	}
	return stdout, false
}

// lastJSONObject is the JSON object stdout ends with: the whole of it, or its last line that is
// one (a CLI that streams one event a line ends with its result).
func lastJSONObject(stdout string) map[string]any {
	var object map[string]any
	if json.Unmarshal([]byte(strings.TrimSpace(stdout)), &object) == nil {
		return object
	}
	lines := strings.Split(stdout, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if strings.HasPrefix(line, "{") && json.Unmarshal([]byte(line), &object) == nil {
			return object
		}
	}
	return nil
}

func (r *Runner) rateLimited(text string) bool {
	if RateLimitWords.MatchString(text) {
		return true
	}
	for _, pattern := range r.CLI.RateLimit {
		if re, err := regexp.Compile(pattern); err == nil && re.MatchString(text) {
			return true
		}
	}
	return false
}

var (
	epochRE   = regexp.MustCompile(`\|(\d{10})\b`)
	isoRE     = regexp.MustCompile(`(?i)resets?\s+(?:at\s+|on\s+)?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))`)
	inRE      = regexp.MustCompile(`(?i)(?:try again|retry|resets?)\s+in\s+(\d+)\s*(s|sec|second|m|min|minute|h|hr|hour)s?\b`)
	clockRE   = regexp.MustCompile(`(?i)resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b(?:\s*\(([A-Za-z_]+/[A-Za-z_]+)\))?`)
	errNoTime = errors.New("no reset time")
)

// maxPark is the longest a refusal may park a provider: a weekly limit, and a day to spare.
const maxPark = 8 * 24 * time.Hour

// ResetTime is when a refusal says the limit resets, or the zero time when it does not say (or
// says something past maxPark, which is no reset time a subscription has).
func ResetTime(text string, now time.Time, loc *time.Location) time.Time {
	t := resetTime(text, now, loc)
	if t.After(now.Add(maxPark)) || t.Before(now) {
		return time.Time{}
	}
	return t
}

func resetTime(text string, now time.Time, loc *time.Location) time.Time {
	if m := isoRE.FindStringSubmatch(text); m != nil {
		if t, err := time.Parse(time.RFC3339, m[1]); err == nil {
			return t
		}
		if t, err := time.Parse("2006-01-02T15:04Z07:00", m[1]); err == nil {
			return t
		}
	}
	if m := epochRE.FindStringSubmatch(text); m != nil {
		if s, err := strconv.ParseInt(m[1], 10, 64); err == nil {
			if t := time.Unix(s, 0).UTC(); t.After(now) && t.Before(now.Add(8*24*time.Hour)) {
				return t
			}
		}
	}
	if m := inRE.FindStringSubmatch(text); m != nil {
		n, _ := strconv.Atoi(m[1])
		unit := time.Minute
		switch strings.ToLower(m[2])[0] {
		case 's':
			unit = time.Second
		case 'h':
			unit = time.Hour
		}
		return now.Add(time.Duration(n) * unit)
	}
	if t, err := clockReset(text, now, loc); err == nil {
		return t
	}
	return time.Time{}
}

func clockReset(text string, now time.Time, loc *time.Location) (time.Time, error) {
	m := clockRE.FindStringSubmatch(text)
	if m == nil {
		return time.Time{}, errNoTime
	}
	if m[4] != "" {
		if zone, err := time.LoadLocation(m[4]); err == nil {
			loc = zone
		}
	}
	if loc == nil {
		loc = time.UTC
	}
	hour, _ := strconv.Atoi(m[1])
	minute, _ := strconv.Atoi(m[2])
	if hour < 1 || hour > 12 || minute > 59 {
		return time.Time{}, errNoTime
	}
	hour %= 12
	if strings.EqualFold(m[3], "pm") {
		hour += 12
	}
	local := now.In(loc)
	t := time.Date(local.Year(), local.Month(), local.Day(), hour, minute, 0, 0, loc)
	if !t.After(now) {
		t = t.AddDate(0, 0, 1)
	}
	return t, nil
}

func tail(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) <= n {
		return s
	}
	return "…" + s[len(s)-n:]
}

func lastLine(texts ...string) string {
	for _, text := range texts {
		lines := strings.Split(strings.TrimSpace(text), "\n")
		if line := strings.TrimSpace(lines[len(lines)-1]); line != "" {
			if len(line) > 300 {
				line = line[:300] + "…"
			}
			return line
		}
	}
	return "no output"
}

func execute(ctx context.Context, argv, env []string, stdin, cwd string) (string, string, int, error) {
	if len(argv) == 0 {
		return "", "", 0, errors.New("empty command")
	}
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Env = env
	cmd.Dir = cwd
	cmd.Stdin = strings.NewReader(stdin)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	killGroup(cmd)
	cmd.WaitDelay = 30 * time.Second
	err := cmd.Run()
	code := 0
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		code = exitErr.ExitCode()
		if code == 0 {
			code = -1
		}
		err = nil
	}
	return stdout.String(), stderr.String(), code, err
}
