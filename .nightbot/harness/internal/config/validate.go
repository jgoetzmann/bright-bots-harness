package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

var (
	clockRE  = regexp.MustCompile(`^([01]\d|2[0-3]):[0-5]\d$`)
	secretRE = regexp.MustCompile(`^[A-Z_][A-Z0-9_]*$`)
	labelRE  = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)
)

// Validate reports every problem in the configuration at once.
func (c *Config) Validate() error {
	var problems []string
	add := func(format string, args ...any) { problems = append(problems, fmt.Sprintf(format, args...)) }

	if c.Repo != "" && strings.Count(c.Repo, "/") != 1 {
		add("repo %q: want OWNER/NAME", c.Repo)
	}
	if c.BotLogin == "" {
		add("bot_login: the GitHub login the bot comments and pushes as (an account, or <app>[bot])")
	}
	if c.Location == nil {
		add("timezone %q: not an IANA zone such as America/Chicago", c.Timezone)
	}
	if !strings.HasPrefix(c.CommandPrefix, "/") || strings.ContainsAny(c.CommandPrefix, " \t\n") {
		add("command_prefix %q: want one word starting with /", c.CommandPrefix)
	}
	switch c.MergeMethod {
	case "squash", "merge", "rebase":
	default:
		add("merge_method %q: squash, merge or rebase", c.MergeMethod)
	}
	if c.PlanGate || c.Forked() {
		for _, a := range c.Approvers {
			if !c.Trust[strings.ToLower(strings.TrimPrefix(a, "@"))] {
				add("approvers: %s is not on trust.txt (an approver must be trusted)", a)
			}
		}
		if len(c.Approvers) == 0 && c.Operator == "" {
			add("plan_gate: name the approvers, or an operator, who approve plans")
		}
		if !labelRE.MatchString(strings.ReplaceAll(c.PlansDir, "/", "_")) || strings.HasPrefix(c.PlansDir, ".") {
			add("plans_dir %q: a directory such as plans", c.PlansDir)
		}
	}
	if u := c.Upstream; u != nil {
		if strings.Count(u.Repo, "/") != 1 || strings.Count(u.Fork, "/") != 1 {
			add("upstream: repo and fork are OWNER/NAME")
		}
		if strings.EqualFold(u.Repo, u.Fork) || strings.EqualFold(u.Repo, c.Repo) || strings.EqualFold(u.Fork, c.Repo) {
			add("upstream: repo, fork and this repository (%s) must be three different repositories", c.Repo)
		}
		if !c.PlanGate {
			add("upstream: fork mode needs plan_gate: true (a person approves each plan)")
		}
		switch u.ForkMergeMethod {
		case "squash", "merge", "rebase":
		default:
			add("upstream.fork_merge_method %q: squash, merge or rebase", u.ForkMergeMethod)
		}
		if u.MaxOpenPRs < 1 || u.AutoOpen.Max < 0 || u.NoCIMinutes < 1 {
			add("upstream: max_open_prs and no_ci_minutes are at least 1, auto_open.max_open at least 0")
		}
	}
	for _, check := range c.Checks {
		if check.Name == "" || check.Run == "" {
			add("checks: every check needs a name and a run command")
		}
	}
	for _, d := range []string{"medium", "hard"} {
		if c.ReviewRounds[d] < 1 {
			add("review_rounds.%s: at least 1", d)
		}
	}
	for _, pattern := range append(append([]string{}, c.ForbiddenPaths...), c.ReviewPaths...) {
		if _, err := globRegexp(pattern); err != nil {
			add("path pattern %q: %v", pattern, err)
		}
	}
	for _, must := range []string{Dir + "/x", ".github/x", ".github/actions/nightbot/action.yml"} {
		if !MatchAny(c.ForbiddenPaths, must) {
			add("forbidden_paths must cover %s/ (the bot may not change its own rules)", filepath.Dir(must))
		}
	}
	for name, cli := range c.CLIs {
		if len(cli.Argv) == 0 {
			add("clis.%s: argv is empty", name)
		}
		switch cli.Prompt {
		case "stdin", "arg", "file":
		default:
			add("clis.%s.prompt %q: stdin, arg or file", name, cli.Prompt)
		}
		if cli.Prompt == "arg" && !mentions(cli, "{prompt}") {
			add("clis.%s: prompt \"arg\" needs {prompt} in its arguments", name)
		}
		if cli.Prompt == "file" && !mentions(cli, "{prompt_file}") {
			add("clis.%s: prompt \"file\" needs {prompt_file} in its arguments", name)
		}
		if cli.Answer == "file" && !mentions(cli, "{answer_file}") {
			add("clis.%s: answer \"file\" needs {answer_file} in its arguments", name)
		}
		if cli.Answer != "stdout" && cli.Answer != "file" && !strings.HasPrefix(cli.Answer, "json:") {
			add("clis.%s.answer %q: stdout, file or json:<field>", name, cli.Answer)
		}
		for _, pattern := range cli.RateLimit {
			if _, err := regexp.Compile(pattern); err != nil {
				add("clis.%s.rate_limit %q: %v", name, pattern, err)
			}
		}
	}

	p := c.Providers
	if p.MaxParallel < 1 {
		add("providers.json max_parallel: at least 1")
	}
	for _, id := range p.Priority {
		if _, ok := p.Providers[id]; !ok {
			add("providers.json priority names %q, which is not a provider", id)
		}
	}
	ids := make([]string, 0, len(p.Providers))
	for id := range p.Providers {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	runners := map[string]string{}
	for _, id := range ids {
		prov := p.Providers[id]
		where := "providers." + id
		if !labelRE.MatchString(id) {
			add("%s: an id is letters, digits, dot, dash and underscore", where)
		}
		cli, ok := c.CLIs[prov.CLI]
		if !ok {
			add("%s.cli %q: not a CLI in config.json clis or the built-in ones (claude, codex, gemini)", where, prov.CLI)
		}
		if len(prov.Models) == 0 {
			add("%s.models: at least one {model, tier}", where)
		}
		for _, seat := range prov.Models {
			if seat.Model == "" || TierRank(seat.Tier) < 0 {
				add("%s.models: each needs a model and a tier (weak, medium or strong)", where)
			}
		}
		switch prov.Login {
		case "secret":
			if !secretRE.MatchString(prov.Secret) {
				add("%s.secret %q: the name of a repository secret (A-Z, 0-9, _)", where, prov.Secret)
			}
			if ok && cli.SecretEnv == "" && cli.SecretFile == "" {
				add("%s: login \"secret\" but clis.%s has neither secret_env nor secret_file", where, prov.CLI)
			}
		case "machine":
			if GitHubHosted(prov.RunsOn) {
				add("%s: login \"machine\" lives on a machine of your own; runs_on %q is GitHub's", where, prov.RunsOn)
			}
		default:
			add("%s.login %q: secret or machine", where, prov.Login)
		}
		if !GitHubHosted(prov.RunsOn) {
			if other, taken := runners[prov.RunsOn]; taken {
				add("%s.runs_on %q: also %s's; on a machine each provider has its own runner label (its own Linux user)", where, prov.RunsOn, other)
			}
			runners[prov.RunsOn] = id
		}
		if prov.Lanes < 1 {
			add("%s.lanes: at least 1", where)
		}
		switch prov.Schedule.Mode {
		case "always":
		case "window":
			if !clockRE.MatchString(prov.Schedule.Start) || !clockRE.MatchString(prov.Schedule.End) {
				add("%s.schedule: a window needs start and end as HH:MM", where)
			}
		default:
			add("%s.schedule.mode %q: always or window", where, prov.Schedule.Mode)
		}
		for _, a := range prov.Actions {
			switch a {
			case "plan", "ask", "triage", "build", "revise", "review":
			default:
				add("%s.actions %q: plan, ask, triage, build, revise or review", where, a)
			}
		}
		if prov.Limits.FiveHourMinutes < 0 || prov.Limits.SevenDayMinutes < 0 {
			add("%s.limits: minutes cannot be negative", where)
		}
	}

	for _, name := range PromptNames {
		if _, err := os.Stat(filepath.Join(c.Root, Dir, "prompts", name+".md")); c.Root != "" && err != nil {
			add("prompts/%s.md: missing", name)
		}
	}
	if len(problems) > 0 {
		return errors.New("configuration:\n  - " + strings.Join(problems, "\n  - "))
	}
	return nil
}

// PromptNames are the templates under .nightbot/prompts/.
var PromptNames = []string{"system", "build", "fix", "review", "ask", "triage", "plan"}

func mentions(cli CLI, placeholder string) bool {
	for _, list := range [][]string{cli.Argv, cli.WriteArgs, cli.ReadArgs, cli.Tail} {
		for _, a := range list {
			if strings.Contains(a, placeholder) {
				return true
			}
		}
	}
	return false
}

// SecretNames are the repository secrets the enabled providers log in with.
func (c *Config) SecretNames() []string {
	seen := map[string]bool{}
	var names []string
	for _, prov := range c.Providers.Ordered() {
		if prov.Login == "secret" && prov.Secret != "" && !seen[prov.Secret] {
			seen[prov.Secret] = true
			names = append(names, prov.Secret)
		}
	}
	return names
}

// CheckWorkflows holds the workflows to the providers: every secret a provider logs in with must
// be listed in each NIGHTBOT_SECRETS_SET block, or the planner never sees it set.
func (c *Config) CheckWorkflows() error {
	var problems []string
	for _, name := range []string{"nightbot-events.yml", "nightbot-run.yml"} {
		data, err := os.ReadFile(filepath.Join(c.Root, ".github", "workflows", name))
		if err != nil {
			problems = append(problems, fmt.Sprintf("%s: %v", name, err))
			continue
		}
		for _, secret := range c.SecretNames() {
			if !strings.Contains(string(data), "secrets."+secret+" != ''") {
				problems = append(problems, fmt.Sprintf("%s: NIGHTBOT_SECRETS_SET does not list %s (add a line like the others)", name, secret))
			}
		}
	}
	if len(problems) > 0 {
		return errors.New("workflows:\n  - " + strings.Join(problems, "\n  - "))
	}
	return nil
}

// InWindow says whether t falls inside a provider's schedule.
func (c *Config) InWindow(s Schedule, t time.Time) bool {
	if s.Mode != "window" {
		return true
	}
	local := t.In(c.Location)
	minutes := local.Hour()*60 + local.Minute()
	start, end := clock(s.Start), clock(s.End)
	if start == end {
		return true
	}
	if start < end {
		return minutes >= start && minutes < end
	}
	return minutes >= start || minutes < end // a window across midnight
}

func clock(hhmm string) int {
	var h, m int
	fmt.Sscanf(hhmm, "%d:%d", &h, &m)
	return h*60 + m
}

// CIRunsInMergeQueue says whether the workflow named ci_workflow runs on merge_group events,
// which a merge queue needs: it waits for the required checks on its own temporary branch.
func (c *Config) CIRunsInMergeQueue() bool {
	files, _ := filepath.Glob(filepath.Join(c.Root, ".github", "workflows", "*.y*ml"))
	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			continue
		}
		text := string(data)
		named := false
		for _, line := range strings.Split(text, "\n") {
			if strings.HasPrefix(line, "name:") && strings.Trim(strings.TrimSpace(line[5:]), `"'`) == c.CIWorkflow {
				named = true
			}
		}
		if named {
			return strings.Contains(text, "merge_group")
		}
	}
	return false
}
