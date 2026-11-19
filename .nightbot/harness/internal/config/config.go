// Package config reads .nightbot/config.json, .nightbot/providers.json and .nightbot/trust.txt,
// fills in defaults and validates them. It is the only package that reads the environment's
// repository settings (GITHUB_REPOSITORY and friends).
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	_ "time/tzdata" // schedules work on a runner without a zone database
)

// Dir is the harness's directory at the repository root.
const Dir = ".nightbot"

// Config is .nightbot/config.json.
type Config struct {
	Repo               string         `json:"repo"`
	DefaultBranch      string         `json:"default_branch"`
	BotLogin           string         `json:"bot_login"`
	Operator           string         `json:"operator"`
	Timezone           string         `json:"timezone"`
	CommandPrefix      string         `json:"command_prefix"`
	LabelPrefix        string         `json:"label_prefix"`
	BranchPrefix       string         `json:"branch_prefix"`
	StateBranch        string         `json:"state_branch"`
	RunWorkflow        string         `json:"run_workflow"`
	CIWorkflow         string         `json:"ci_workflow"`
	RequiredChecks     []string       `json:"required_checks"`
	AutoMerge          bool           `json:"auto_merge"`
	MergeQueue         bool           `json:"merge_queue"`
	MergeMethod        string         `json:"merge_method"`
	Checks             []Check        `json:"checks"`
	FixPasses          int            `json:"fix_passes"`
	ReviewRounds       map[string]int `json:"review_rounds"`
	MaxAudits          int            `json:"max_audits"`
	MaxCIFixes         int            `json:"max_ci_fixes"`
	MaxFailures        int            `json:"max_failures"`
	ForbiddenPaths     []string       `json:"forbidden_paths"`
	ReviewPaths        []string       `json:"review_paths"`
	MaxTurns           map[string]int `json:"max_turns"`
	CallTimeoutMinutes int            `json:"call_timeout_minutes"`
	JobBudgetMinutes   int            `json:"job_budget_minutes"`
	ParkMinutes        int            `json:"park_minutes"`
	TrustCollaborators bool           `json:"trust_collaborators"`
	Triage             bool           `json:"triage"`
	MaxDiffChars       int            `json:"max_diff_chars"`
	StatusIssueTitle   string         `json:"status_issue_title"`
	CLIs               map[string]CLI `json:"clis"`
	// PlanGate has the bot write a plan for every item, as a draft pull request, and build
	// nothing until an approver approves it (gate 1).
	PlanGate  bool     `json:"plan_gate"`
	Approvers []string `json:"approvers"`
	PlansDir  string   `json:"plans_dir"`
	// Upstream, when set, is fork mode: this repository is the queue, the bot builds in its fork
	// of another repository and delivers there by pull request, which a person merges (gate 2).
	Upstream *Upstream `json:"upstream"`

	// Set by Load, not read from the file.
	Root      string          `json:"-"`
	Providers *Providers      `json:"-"`
	Trust     map[string]bool `json:"-"`
	Location  *time.Location  `json:"-"`
}

// Upstream is fork mode's other two repositories and its limits.
type Upstream struct {
	// Repo is the product, where delivery pull requests go and a person merges them.
	Repo string `json:"repo"`
	// Fork is the bot's own fork of it: its branches, and the sandbox, the fork's branch named
	// like DefaultBranch, which its approved, CI-green changes merge into.
	Fork          string `json:"fork"`
	DefaultBranch string `json:"default_branch"`
	// ForkMergeMethod merges a change into the sandbox: merge, squash or rebase.
	ForkMergeMethod string `json:"fork_merge_method"`
	// MaxOpenPRs is how many of the bot's pull requests may be open upstream at once; a change
	// that is ready waits in the fork for a slot.
	MaxOpenPRs int `json:"max_open_prs"`
	// Reviewers are asked to review an upstream pull request once its CI is green.
	Reviewers []string `json:"reviewers"`
	// AutoOpen is the upstream issues the bot takes up by itself.
	AutoOpen AutoOpen `json:"auto_open"`
	// NoCIMinutes is how long a head may go without any check reporting before the bot stops
	// waiting for CI on the fork, or marks a draft upstream pull request ready.
	NoCIMinutes int `json:"no_ci_minutes"`
}

// AutoOpen picks upstream issues for the queue: open ones carrying one of Labels, or assigned
// to the bot with Assigned, at most Max of them open here at once.
type AutoOpen struct {
	Labels   []string `json:"labels"`
	Assigned bool     `json:"assigned"`
	Max      int      `json:"max_open"`
}

// Check is one of the repository's own checks, run after every change the model makes.
type Check struct {
	Name           string `json:"name"`
	Run            string `json:"run"`
	TimeoutMinutes int    `json:"timeout_minutes"`
}

// CLI says how to run one model CLI. Every string may hold the placeholders {model}, {effort},
// {max_turns}, {cwd}, {prompt}, {prompt_file}, {system_file} and {answer_file}.
type CLI struct {
	// Install is a shell command that installs the CLI on a fresh GitHub-hosted runner.
	Install string `json:"install"`
	// Argv is the command; WriteArgs are added for a call that may edit files, ReadArgs for one
	// that may not, EffortArgs when the seat names an effort, and Tail last of all.
	Argv       []string `json:"argv"`
	WriteArgs  []string `json:"write_args"`
	ReadArgs   []string `json:"read_args"`
	EffortArgs []string `json:"effort_args"`
	Tail       []string `json:"tail"`
	// Prompt is how the prompt reaches the CLI: "stdin" (default), "arg" (the {prompt}
	// placeholder) or "file" ({prompt_file}).
	Prompt string `json:"prompt"`
	// System is how the system prompt reaches it: "file" when Argv names {system_file},
	// otherwise "prepend" (it opens the prompt).
	System string `json:"system"`
	// Answer is where the model's final message is: "stdout" (default), "json:<field>" (a field
	// of the JSON object stdout ends with) or "file" ({answer_file}).
	Answer string `json:"answer"`
	// SecretEnv is the environment variable a secret login is handed in; SecretFile a file
	// (~ is the home directory) it is written to instead, such as an auth.json.
	SecretEnv  string            `json:"secret_env"`
	SecretFile string            `json:"secret_file"`
	Env        map[string]string `json:"env"`
	// RateLimit are extra regular expressions that mark a refusal for usage limits.
	RateLimit []string `json:"rate_limit"`
}

// Providers is .nightbot/providers.json: the subscriptions the bot may spend.
type Providers struct {
	MaxParallel int                  `json:"max_parallel"`
	Priority    []string             `json:"priority"`
	Providers   map[string]*Provider `json:"providers"`
}

// Provider is one subscription: one login of one CLI, with the models it may run.
type Provider struct {
	ID       string   `json:"-"`
	Enabled  bool     `json:"enabled"`
	CLI      string   `json:"cli"`
	Models   []Seat   `json:"models"`
	Login    string   `json:"login"`
	Secret   string   `json:"secret"`
	RunsOn   string   `json:"runs_on"`
	Lanes    int      `json:"lanes"`
	Schedule Schedule `json:"schedule"`
	Limits   Limits   `json:"limits"`
	// Actions, when set, are the only actions it takes (plan, ask, build, …): a subscription
	// kept for daytime plans and questions, say, with builds left to another provider's hours.
	Actions []string `json:"actions"`
	Note    string   `json:"note"`
}

// Takes says whether the provider takes an action.
func (p *Provider) Takes(action string) bool {
	if len(p.Actions) == 0 {
		return true
	}
	for _, a := range p.Actions {
		if a == action {
			return true
		}
	}
	return false
}

// Seat is one model a provider runs, with its tier.
type Seat struct {
	Model  string `json:"model"`
	Effort string `json:"effort"`
	Tier   string `json:"tier"`
}

// Schedule is when a provider may start work: always, or a daily window in the config's zone.
type Schedule struct {
	Mode  string `json:"mode"`
	Start string `json:"start"`
	End   string `json:"end"`
}

// Limits caps a provider's model minutes, counted by the harness, over rolling windows.
type Limits struct {
	FiveHourMinutes int `json:"five_hour_minutes"`
	SevenDayMinutes int `json:"seven_day_minutes"`
}

// Tiers, weakest first. A weak seat answers queries (questions and triage); a medium one builds
// and reviews medium items; a strong one also builds and reviews hard items.
var Tiers = []string{"weak", "medium", "strong"}

// TierRank is a tier's place in Tiers, or -1.
func TierRank(tier string) int {
	for i, t := range Tiers {
		if t == tier {
			return i
		}
	}
	return -1
}

// Difficulties an item may carry, and the weakest tier that may build and approve each.
var Difficulties = map[string]string{"medium": "medium", "hard": "strong"}

// Load reads the configuration under root (the directory that holds .nightbot/).
func Load(root string) (*Config, error) {
	cfg := &Config{}
	if err := readJSON(filepath.Join(root, Dir, "config.json"), cfg); err != nil {
		return nil, err
	}
	providers := &Providers{}
	if err := readJSON(filepath.Join(root, Dir, "providers.json"), providers); err != nil {
		return nil, err
	}
	cfg.Root = root
	cfg.Providers = providers
	trust, err := readTrust(filepath.Join(root, Dir, "trust.txt"))
	if err != nil {
		return nil, err
	}
	cfg.Trust = trust
	cfg.applyDefaults()
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	return cfg, nil
}

// FindRoot walks up from dir to the first directory holding .nightbot/config.json.
func FindRoot(dir string) (string, error) {
	dir, err := filepath.Abs(dir)
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, Dir, "config.json")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("no .nightbot/config.json in this directory or any above it")
		}
		dir = parent
	}
}

func readJSON(path string, into any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(into); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return nil
}

func readTrust(path string) (map[string]bool, error) {
	trust := map[string]bool{}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return trust, nil
	}
	if err != nil {
		return nil, err
	}
	for _, line := range strings.Split(string(data), "\n") {
		if i := strings.Index(line, "#"); i >= 0 {
			line = line[:i]
		}
		login := strings.TrimPrefix(strings.TrimSpace(line), "@")
		if login != "" {
			trust[strings.ToLower(login)] = true
		}
	}
	return trust, nil
}

func (c *Config) applyDefaults() {
	if c.Repo == "" {
		c.Repo = os.Getenv("GITHUB_REPOSITORY")
	}
	def := func(s *string, v string) {
		if *s == "" {
			*s = v
		}
	}
	defInt := func(n *int, v int) {
		if *n == 0 {
			*n = v
		}
	}
	def(&c.DefaultBranch, "main")
	def(&c.Timezone, "UTC")
	def(&c.CommandPrefix, "/nightbot")
	def(&c.LabelPrefix, "bot:")
	def(&c.BranchPrefix, "nightbot/")
	def(&c.StateBranch, "_nightbot-state")
	def(&c.RunWorkflow, "nightbot-run.yml")
	def(&c.CIWorkflow, "CI")
	def(&c.MergeMethod, "squash")
	def(&c.StatusIssueTitle, "Night bot status")
	def(&c.PlansDir, "plans")
	if u := c.Upstream; u != nil {
		def(&u.DefaultBranch, "main")
		def(&u.ForkMergeMethod, "merge")
		defInt(&u.MaxOpenPRs, 2)
		defInt(&u.AutoOpen.Max, 2)
		defInt(&u.NoCIMinutes, 30)
	}
	defInt(&c.FixPasses, 2)
	defInt(&c.MaxAudits, 2)
	defInt(&c.MaxCIFixes, 3)
	defInt(&c.MaxFailures, 3)
	defInt(&c.CallTimeoutMinutes, 120)
	defInt(&c.JobBudgetMinutes, 300)
	defInt(&c.ParkMinutes, 60)
	defInt(&c.MaxDiffChars, 60000)
	if c.ReviewRounds == nil {
		c.ReviewRounds = map[string]int{}
	}
	for d, n := range map[string]int{"medium": 3, "hard": 6} {
		if c.ReviewRounds[d] == 0 {
			c.ReviewRounds[d] = n
		}
	}
	if c.MaxTurns == nil {
		c.MaxTurns = map[string]int{}
	}
	for kind, n := range map[string]int{"build": 300, "fix": 200, "review": 100, "ask": 40, "triage": 30, "plan": 150} {
		if c.MaxTurns[kind] == 0 {
			c.MaxTurns[kind] = n
		}
	}
	clis := BuiltinCLIs()
	for name, cli := range c.CLIs {
		clis[name] = cli
	}
	c.CLIs = clis
	for name, cli := range c.CLIs {
		def(&cli.Prompt, "stdin")
		def(&cli.Answer, "stdout")
		if cli.System == "" {
			cli.System = "prepend"
			for _, a := range cli.Argv {
				if strings.Contains(a, "{system_file}") {
					cli.System = "file"
				}
			}
		}
		c.CLIs[name] = cli
	}
	for i := range c.Checks {
		defInt(&c.Checks[i].TimeoutMinutes, 30)
	}
	if loc, err := time.LoadLocation(c.Timezone); err == nil {
		c.Location = loc
	}
	if c.Trust == nil {
		c.Trust = map[string]bool{}
	}
	if c.Operator != "" {
		c.Trust[strings.ToLower(c.Operator)] = true
	}

	if c.Providers == nil {
		c.Providers = &Providers{}
	}
	p := c.Providers
	defInt(&p.MaxParallel, 4)
	if p.Providers == nil {
		p.Providers = map[string]*Provider{}
	}
	for id, prov := range p.Providers {
		prov.ID = id
		defInt(&prov.Lanes, 1)
		def(&prov.Login, "secret")
		def(&prov.RunsOn, "ubuntu-latest")
		def(&prov.Schedule.Mode, "always")
		if prov.Secret == "" && prov.Login == "secret" {
			prov.Secret = c.CLIs[prov.CLI].SecretEnv
		}
	}
}

// Label is a bot label: "build" is "bot:build" with the default prefix.
func (c *Config) Label(name string) string { return c.LabelPrefix + name }

// Intake labels: an item a person asked for, and one the bot took up from upstream by itself.
const (
	Requested  = "requested"
	AutoOpened = "auto-opened"
)

// Forked says whether the bot runs in fork mode.
func (c *Config) Forked() bool { return c.Upstream != nil && c.Upstream.Repo != "" }

// Approver says whether a login may approve a plan: one of approvers, or with none listed, the
// operator. Approving also needs the person trusted, which the caller checks.
func (c *Config) Approver(login string) bool {
	if len(c.Approvers) == 0 {
		return strings.EqualFold(login, c.Operator)
	}
	for _, a := range c.Approvers {
		if strings.EqualFold(strings.TrimPrefix(a, "@"), login) {
			return true
		}
	}
	return false
}

// ApproverList is the approvers as @-mentions, for the bot's comments.
func (c *Config) ApproverList() string {
	logins := c.Approvers
	if len(logins) == 0 && c.Operator != "" {
		logins = []string{c.Operator}
	}
	var out []string
	for _, l := range logins {
		out = append(out, "@"+strings.TrimPrefix(l, "@"))
	}
	return strings.Join(out, " or ")
}

// Ordered lists the providers in priority order: those named in priority first, the rest by id.
func (p *Providers) Ordered() []*Provider {
	var out []*Provider
	seen := map[string]bool{}
	for _, id := range p.Priority {
		if prov, ok := p.Providers[id]; ok && !seen[id] {
			out = append(out, prov)
			seen[id] = true
		}
	}
	var rest []string
	for id := range p.Providers {
		if !seen[id] {
			rest = append(rest, id)
		}
	}
	sort.Strings(rest)
	for _, id := range rest {
		out = append(out, p.Providers[id])
	}
	return out
}

// GitHubHosted says whether a runs_on label is one of GitHub's own runners, where the model job
// installs its CLI each time, rather than a self-hosted machine.
func GitHubHosted(runsOn string) bool {
	for _, prefix := range []string{"ubuntu-", "windows-", "macos-"} {
		if strings.HasPrefix(runsOn, prefix) {
			return true
		}
	}
	return false
}

// SeatFor is the weakest seat of the provider at or above tier, or nil.
func (p *Provider) SeatFor(tier string) *Seat {
	var best *Seat
	for i := range p.Models {
		s := &p.Models[i]
		if TierRank(s.Tier) >= TierRank(tier) && (best == nil || TierRank(s.Tier) < TierRank(best.Tier)) {
			best = s
		}
	}
	return best
}

// Strongest is the provider's strongest tier, or "".
func (p *Provider) Strongest() string {
	best := ""
	for _, s := range p.Models {
		if TierRank(s.Tier) > TierRank(best) {
			best = s.Tier
		}
	}
	return best
}
