// Package bot is everything that holds the GitHub token and runs no model: the planner, the
// deliverer, the event and command handlers, the sweep and the status issue.
package bot

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/sched"
	"nightbot/internal/state"
)

// Marker opens every comment the bot writes, so it can find its own.
const Marker = "<!-- nightbot -->"

// Bot is the harness's GitHub side.
type Bot struct {
	Cfg *config.Config
	GH  github.Client // the bot's token: comments, labels, pushes, pull requests
	// Actions is the workflow run's own token, for starting, reading and cancelling runs.
	Actions github.Client
	// Fork and Upstream are the bot's token on fork mode's other repositories. Upstream's
	// client is made unable to merge: a person merges there.
	Fork     github.Client
	Upstream github.Client
	Store    *state.Store
	Secrets  map[string]bool // which provider secrets are set (names only)
	Now      func() time.Time
	Sleep    func(time.Duration)
	Log      io.Writer
	RunID    int64
	RunURL   string
	// Workspace is the checkout deliver pushes from.
	Workspace string
}

// New is a bot over gh, with the run's identity read from the environment.
func New(cfg *config.Config, gh, actions github.Client) *Bot {
	b := &Bot{Cfg: cfg, GH: gh, Actions: actions, Now: time.Now, Sleep: time.Sleep, Log: os.Stderr, Workspace: cfg.Root,
		Store: &state.Store{GH: gh, Branch: cfg.StateBranch}, Secrets: map[string]bool{}}
	if b.Actions == nil {
		b.Actions = gh
	}
	for _, name := range strings.Fields(os.Getenv("NIGHTBOT_SECRETS_SET")) {
		b.Secrets[name] = true
	}
	fmt.Sscan(os.Getenv("GITHUB_RUN_ID"), &b.RunID)
	if b.RunID != 0 {
		server := os.Getenv("GITHUB_SERVER_URL")
		if server == "" {
			server = "https://github.com"
		}
		b.RunURL = fmt.Sprintf("%s/%s/actions/runs/%d", server, cfg.Repo, b.RunID)
	}
	return b
}

func (b *Bot) now() time.Time { return b.Now().UTC() }

func (b *Bot) logf(format string, args ...any) { fmt.Fprintf(b.Log, "nightbot: "+format+"\n", args...) }

// HaltFile is the committed off switch: only someone who can push to the default branch lifts it.
func (b *Bot) HaltFile() bool {
	_, err := os.Stat(filepath.Join(b.Cfg.Root, config.Dir, "HALT"))
	return err == nil
}

// Trusted says whether a person may command the bot: on trust.txt, the operator, or, with
// trust_collaborators, anyone GitHub calls an owner, member or collaborator.
func (b *Bot) Trusted(login, association string) bool {
	if b.isBot(login) {
		return false
	}
	if b.Cfg.Trust[strings.ToLower(login)] {
		return true
	}
	if b.Cfg.TrustCollaborators {
		switch association {
		case "OWNER", "MEMBER", "COLLABORATOR":
			return true
		}
	}
	return false
}

func (b *Bot) isBot(login string) bool {
	return strings.EqualFold(login, b.Cfg.BotLogin) || strings.EqualFold(login, "github-actions[bot]")
}

// say comments on an item as the bot.
func (b *Bot) say(number int, text string) {
	if _, err := b.GH.CreateComment(number, Marker+"\n"+text); err != nil {
		b.logf("comment on #%d: %v", number, err)
	}
}

func (b *Bot) mention(login string) string {
	if login == "" {
		return ""
	}
	return "@" + login + " "
}

func (b *Bot) operator() string { return b.mention(b.Cfg.Operator) }

// queued lists the open issues and pull requests that carry a queue label.
func (b *Bot) queued() ([]*github.Issue, error) {
	var all []*github.Issue
	names := []string{"build", "revise", "review"}
	if b.Cfg.PlanGate {
		names = append(names, "plan")
	}
	for _, name := range names {
		issues, err := b.GH.ListIssues(b.Cfg.Label(name), "open")
		if err != nil {
			return nil, err
		}
		all = append(all, issues...)
	}
	return all, nil
}

// Snapshot is the state with the queue and the providers worked out from it, as of now.
type Snapshot struct {
	State       *state.State
	Queue       []*sched.Item
	Avail       []*sched.Availability
	Assignments []*sched.Assignment
}

// Snapshot reads the state and the queue and works out who would take what.
func (b *Bot) Snapshot(force bool) (*Snapshot, error) {
	st, _, err := b.Store.Load()
	if err != nil {
		return nil, err
	}
	return b.snapshotOf(st, force)
}

func (b *Bot) snapshotOf(st *state.State, force bool) (*Snapshot, error) {
	issues, err := b.queued()
	if err != nil {
		return nil, err
	}
	s := &Snapshot{State: st, Queue: sched.Queue(b.Cfg, st, issues),
		Avail: sched.Available(b.Cfg, st, b.secrets(st), b.now(), force)}
	s.Assignments = sched.Assign(b.Cfg, st, s.Queue, s.Avail)
	return s, nil
}

// secrets are the secrets this job was told are set, or, in a job that was told none (the
// status loop's), the ones the last plan job saw.
func (b *Bot) secrets(st *state.State) map[string]bool {
	if len(b.Secrets) > 0 {
		return b.Secrets
	}
	seen := map[string]bool{}
	for _, name := range st.Secrets {
		seen[name] = true
	}
	return seen
}

// Kick starts as many runs as there are lanes free with work for them, less the runs already
// waiting to start, unless the bot is halted.
func (b *Bot) Kick() {
	if b.HaltFile() {
		return
	}
	snap, err := b.Snapshot(false)
	if err != nil {
		b.logf("kick: %v", err)
		return
	}
	if snap.State.Halted {
		return
	}
	spare := 0
	for _, as := range snap.Assignments {
		if as.Provider != nil {
			spare++
		}
	}
	waiting := 0
	for _, status := range []string{"queued", "pending", "requested", "waiting"} {
		runs, err := b.Actions.ListRuns(b.Cfg.RunWorkflow, status)
		if err != nil {
			b.logf("kick: list runs: %v", err)
			return
		}
		waiting += len(runs)
	}
	for i := 0; i < spare-waiting && i < 4; i++ {
		if err := b.Actions.DispatchWorkflow(b.Cfg.RunWorkflow, b.Cfg.DefaultBranch, map[string]string{}); err != nil {
			b.logf("kick: dispatch: %v", err)
			return
		}
		b.logf("started a run of %s", b.Cfg.RunWorkflow)
	}
}

// Force starts one run for one item at once, outside the providers' hours.
func (b *Bot) Force(number int, mode string) error {
	return b.Actions.DispatchWorkflow(b.Cfg.RunWorkflow, b.Cfg.DefaultBranch, map[string]string{
		"item": fmt.Sprint(number), "mode": mode, "force": "true"})
}

var slugRE = regexp.MustCompile(`[^a-z0-9]+`)

// BranchFor is the branch an issue's work goes on.
func (b *Bot) BranchFor(number int, title string) string {
	slug := strings.Trim(slugRE.ReplaceAllString(strings.ToLower(title), "-"), "-")
	if len(slug) > 40 {
		slug = strings.TrimRight(slug[:40], "-")
	}
	if slug == "" {
		return fmt.Sprintf("%s%d", b.Cfg.BranchPrefix, number)
	}
	return fmt.Sprintf("%s%d-%s", b.Cfg.BranchPrefix, number, slug)
}

// release drops a run's claim on an item and its live label.
// Only the claim of the run given is released (runID 0 matches a claim made outside a run): a
// late release by an old run must not drop the claim a newer run holds.
func (b *Bot) release(key string, number int, runID int64) {
	released := false
	_, err := b.Store.Update(fmt.Sprintf("release #%d", number), func(s *state.State) error {
		released = false
		if claim := s.Running[key]; claim != nil && claim.RunID == runID {
			delete(s.Running, key)
			released = true
		}
		return nil
	})
	if err != nil {
		b.logf("release #%d: %v", number, err)
	}
	if released && !strings.HasPrefix(key, "q:") {
		if err := b.GH.RemoveLabel(number, b.Cfg.Label("building")); err != nil {
			b.logf("unlabel #%d: %v", number, err)
		}
	}
}

// fail counts a failure on an item; at max_failures it is handed to a person.
func (b *Bot) fail(number int, why string) {
	failures := 0
	_, err := b.Store.Update(fmt.Sprintf("failure on #%d", number), func(s *state.State) error {
		item := s.Item(number)
		item.Failures++
		failures = item.Failures
		return nil
	})
	if err != nil {
		b.logf("count a failure on #%d: %v", number, err)
	}
	if failures >= b.Cfg.MaxFailures {
		for _, name := range []string{"build", "revise", "review", "plan"} {
			b.GH.RemoveLabel(number, b.Cfg.Label(name))
		}
		b.GH.AddLabels(number, "human")
		b.say(number, fmt.Sprintf("%sThis failed %d times, so I have handed it to a person (`human`). The last failure: %s\n\n"+
			"Fix what it needs, take `human` off, and comment `%s forget` then `%s build` to give it back.",
			b.operator(), failures, why, b.Cfg.CommandPrefix, b.Cfg.CommandPrefix))
		return
	}
	b.say(number, fmt.Sprintf("A run failed (%d of %d before I hand this to a person): %s", failures, b.Cfg.MaxFailures, why))
}

// Labels the bot uses, with their colours and descriptions.
func (b *Bot) Labels() [][3]string {
	l := b.Cfg.Label
	return [][3]string{
		{l("build"), "0e8a16", "Queued for the night bot to build"},
		{l("building"), "fbca04", "A night bot run holds this"},
		{l("revise"), "1d76db", "The bot's pull request is queued for a revision"},
		{l("review"), "5319e7", "Queued for a night bot review run"},
		{l("pr"), "c5def5", "Opened by the night bot"},
		{l("status"), "ededed", "The night bot's status issue"},
		{"human", "b60205", "The night bot keeps off this"},
		{"ready for merge", "0e8a16", "Approved by the bot's review; a person merges it"},
		{"difficulty:medium", "bfdadc", "A medium or strong model may build and approve it"},
		{"difficulty:hard", "d93f0b", "Only a strong model may build and approve it"},
		{"priority:high", "b60205", "The night bot takes it first"},
		{"priority:low", "c2e0c6", "The night bot takes it last"},
		{l("plan"), "0e8a16", "Queued for the night bot to plan"},
		{l("awaiting-approval"), "fbca04", "The night bot's plan waits for an approver (gate 1)"},
		{l("plan-pr"), "c5def5", "A plan the night bot wrote"},
		{config.Requested, "1d76db", "Work a person asked the night bot for"},
		{config.AutoOpened, "bfd4f2", "Work the night bot took up from upstream by itself"},
	}
}
