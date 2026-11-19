package bot

import (
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/sched"
	"nightbot/internal/state"
)

// Sweep is the schedule's tick: it releases claims whose run ended without delivering, starts
// runs for free lanes, and redraws the status issue.
func (b *Bot) Sweep() error {
	st, _, err := b.Store.Load()
	if err != nil {
		return err
	}
	// A claim whose run's status cannot be read is held at most this long; one whose run is
	// still going is held however long the run takes.
	budget := time.Duration(b.Cfg.JobBudgetMinutes+180) * time.Minute
	for key, claim := range st.Running {
		lost, count := "", true
		readable := false
		if claim.RunID != 0 {
			run, err := b.Actions.GetRun(claim.RunID)
			switch {
			case errors.Is(err, github.ErrNotFound):
				lost, readable = "its run is gone", true
			case err != nil:
				b.logf("sweep: run %d: %v", claim.RunID, err)
			case run.Status == "completed":
				readable = true
				lost = fmt.Sprintf("its run ended (%s) without delivering", run.Conclusion)
				count = run.Conclusion != "cancelled" // a person or /nightbot stop cancelled it
			default:
				readable = true // queued or going: it is not lost
			}
		}
		if !readable && b.now().Sub(claim.Started) > budget {
			lost = fmt.Sprintf("it held the item for over %s and its run cannot be read", budget)
		}
		if lost == "" {
			continue
		}
		b.logf("sweep: releasing #%d (%s on %s): %s", claim.Number, claim.Action, claim.Provider, lost)
		b.release(key, claim.Number, claim.RunID)
		if claim.QueryID == "" && count {
			b.fail(claim.Number, "a run was lost: "+lost)
		}
	}
	// An issue whose pull request closed while no event reached the bot would wait on it forever.
	for key, item := range st.Items {
		if item.PR == 0 {
			continue
		}
		pr, err := b.GH.GetPR(item.PR)
		if err != nil || pr.State != "closed" {
			continue
		}
		number, _ := strconv.Atoi(key)
		b.logf("sweep: #%d's pull request #%d is closed", number, pr.Number)
		b.Store.Update(fmt.Sprintf("#%d closed", pr.Number), func(s *state.State) error {
			if mem := s.Peek(number); mem != nil && mem.PR == pr.Number {
				mem.PR = 0
			}
			delete(s.Items, state.Key(pr.Number))
			return nil
		})
	}
	if b.Cfg.PlanGate {
		b.planSweep(st)
	}
	if b.Cfg.Forked() {
		b.forkSweep()
	} else {
		b.mergeConflicted(st)
	}
	if _, err := b.Store.Update("prune", func(s *state.State) error { s.Prune(b.now()); return nil }); err != nil {
		b.logf("sweep: prune: %v", err)
	}
	b.Kick()
	return b.UpdateStatus()
}

// mergeConflicted queues a merge of the default branch into each of the bot's pull requests
// that conflicts with it: such a pull request can never merge, nor join a merge queue.
func (b *Bot) mergeConflicted(st *state.State) {
	open, err := b.GH.ListIssues(b.Cfg.Label("pr"), "open")
	if err != nil {
		b.logf("sweep: list the bot's pull requests: %v", err)
		return
	}
	for _, issue := range open {
		if issue.HasLabel("human") || issue.HasLabel(b.Cfg.Label("revise")) || st.Running[state.Key(issue.Number)] != nil {
			continue
		}
		pr, err := b.GH.GetPR(issue.Number)
		if err != nil || pr.MergeableState != "dirty" || b.notOurs(pr) != "" {
			continue
		}
		b.Store.Update(fmt.Sprintf("#%d conflicts", pr.Number), func(s *state.State) error {
			mem := s.Item(pr.Number)
			if !mem.MergeMain {
				mem.MergeMain = true
				mem.Notes = append(mem.Notes, fmt.Sprintf("It conflicts with the newest %s: the harness merges %s in, and the conflicts are yours to resolve.",
					b.Cfg.DefaultBranch, b.Cfg.DefaultBranch))
			}
			return nil
		})
		b.GH.AddLabels(pr.Number, b.Cfg.Label("revise"))
		b.say(pr.Number, fmt.Sprintf("This conflicts with %s; queued a revision that merges it in.", b.Cfg.DefaultBranch))
	}
}

// UpdateStatus rewrites the status issue, opening it the first time.
func (b *Bot) UpdateStatus() error {
	snap, err := b.Snapshot(false)
	if err != nil {
		return err
	}
	body := Marker + "\n" + b.StatusMarkdown(snap, true)
	issues, err := b.GH.ListIssues(b.Cfg.Label("status"), "open")
	if err != nil {
		return err
	}
	if len(issues) == 0 {
		_, err := b.GH.CreateIssue(b.Cfg.StatusIssueTitle, body, []string{b.Cfg.Label("status")})
		return err
	}
	if strings.TrimSpace(issues[0].Body) == strings.TrimSpace(body) {
		return nil
	}
	return b.GH.UpdateIssueBody(issues[0].Number, body)
}

// StatusMarkdown is the bot's status: what runs, the providers, the queue and the last runs.
func (b *Bot) StatusMarkdown(snap *Snapshot, full bool) string {
	cfg, st, now := b.Cfg, snap.State, b.now()
	var out strings.Builder
	w := func(format string, args ...any) { fmt.Fprintf(&out, format, args...) }
	if full {
		w("# %s\n\n_Rewritten %s by the sweep. Comment `%s help` anywhere for the commands._\n\n",
			cfg.StatusIssueTitle, now.In(cfg.Location).Format("Mon Jan 2 15:04 MST"), cfg.CommandPrefix)
	}
	switch {
	case b.HaltFile():
		w("> [!WARNING]\n> **Halted** by the committed `.nightbot/HALT`. Delete it to start again.\n\n")
	case st.Halted:
		w("> [!WARNING]\n> **Halted** by %s. `%s start` starts it again.\n\n", st.HaltedBy, cfg.CommandPrefix)
	}

	if full && (cfg.PlanGate || cfg.Forked()) {
		w("%s", b.pipelineMarkdown(st, now))
	}

	w("## Running now\n\n")
	if claims := st.Claims(); len(claims) == 0 {
		w("Nothing.\n\n")
	} else {
		w("| Item | Doing | Provider | Model | For |\n|---|---|---|---|---|\n")
		for _, c := range claims {
			w("| #%d | %s | %s | %s | %s |\n", c.Number, c.Action, c.Provider, c.Model, since(now, c.Started))
		}
		w("\n")
	}

	w("## Providers\n\n| Provider | CLI | Models | Runs on | Lanes | Model minutes 5 h / 7 d | Now |\n|---|---|---|---|---|---|---|\n")
	for _, a := range snap.Avail {
		p := a.Provider
		var seats []string
		for _, s := range p.Models {
			seats = append(seats, fmt.Sprintf("%s (%s)", s.Model, s.Tier))
		}
		now := "✅ free"
		if a.Free == 0 {
			now = "⏸️ " + a.Reason
		}
		w("| %s | %s | %s | %s | %d of %d | %s / %s | %s |\n", p.ID, p.CLI, strings.Join(seats, ", "), p.RunsOn,
			a.Busy, p.Lanes, minutes(a.FiveHour, p.Limits.FiveHourMinutes), minutes(a.SevenDay, p.Limits.SevenDayMinutes), now)
	}
	w("\n")

	w("## Queue\n\n")
	if len(snap.Assignments) == 0 && cfg.PlanGate {
		w("Empty. Open an issue with the request template (or comment `%s plan` on one) to queue it.\n\n", cfg.CommandPrefix)
	} else if len(snap.Assignments) == 0 {
		w("Empty. Label an issue `%s` (or comment `%s build`) to queue it.\n\n", cfg.Label("build"), cfg.CommandPrefix)
	} else {
		w("| Item | Next | Difficulty | Taken by |\n|---|---|---|---|\n")
		for _, as := range snap.Assignments {
			it := as.Item
			difficulty := it.Difficulty
			if !it.Rated {
				difficulty += " (unrated)"
			}
			if sched.IsQuery(it.Action) {
				difficulty = "— (a weak query)"
			}
			taker := "waits: " + as.Waits
			if as.Provider != nil {
				taker = fmt.Sprintf("%s (%s, %s) at the next run", as.Provider.ID, as.Seat.Model, as.Seat.Tier)
			}
			w("| #%d %s | %s | %s | %s |\n", it.Number, escape(it.Title), it.Action, difficulty, taker)
		}
		w("\n")
	}

	if full {
		w("## Last runs\n\n")
		if len(st.History) == 0 {
			w("None yet.\n")
		} else {
			w("| When | Item | Did | By | Outcome | Minutes |\n|---|---|---|---|---|---|\n")
			history := append([]*state.Entry(nil), st.History...)
			sort.SliceStable(history, func(i, j int) bool { return history[i].At.After(history[j].At) })
			for i, e := range history {
				if i == 15 {
					break
				}
				by := "—"
				if e.Provider != "" {
					by = e.Provider + " / " + e.Model
				}
				outcome := e.Outcome
				if e.RunURL != "" {
					outcome = fmt.Sprintf("[%s](%s)", e.Outcome, e.RunURL)
				}
				w("| %s | #%d | %s | %s | %s | %.0f |\n", e.At.In(cfg.Location).Format("Jan 2 15:04"), e.Number,
					e.Action, by, outcome, e.Minutes)
			}
		}
	}
	return out.String()
}

func since(now, t time.Time) string {
	d := now.Sub(t).Round(time.Minute)
	if d < time.Hour {
		return fmt.Sprintf("%d min", int(d.Minutes()))
	}
	return fmt.Sprintf("%dh %02dm", int(d.Hours()), int(d.Minutes())%60)
}

func minutes(used float64, cap int) string {
	if cap == 0 {
		return fmt.Sprintf("%.0f", used)
	}
	return fmt.Sprintf("%.0f of %d", used, cap)
}

func escape(s string) string { return strings.ReplaceAll(s, "|", "\\|") }

// Setup creates the labels and the state branch, and with repoSettings allows auto-merge,
// deletes merged branches and protects the default branch with the required checks: with
// merge_queue, through a ruleset that also sends every merge through a merge queue.
func (b *Bot) Setup(repoSettings bool) error {
	for _, l := range b.Labels() {
		if err := b.GH.EnsureLabel(l[0], l[1], l[2]); err != nil {
			return fmt.Errorf("label %s: %w", l[0], err)
		}
	}
	exists, err := b.GH.BranchExists(b.Cfg.StateBranch)
	if err != nil {
		return err
	}
	if !exists {
		if err := b.GH.CreateOrphanBranch(b.Cfg.StateBranch, state.File, []byte("{}\n"), "The night bot's state"); err != nil {
			return fmt.Errorf("state branch: %w", err)
		}
	}
	if !repoSettings {
		return nil
	}
	if b.Cfg.Forked() {
		// Nothing merges here but plans, and upstream is a person's: only the fork has settings
		// to change. A merge into the sandbox must keep the branch upstream's pull request needs.
		if err := b.Fork.UpdateRepo(map[string]any{"delete_branch_on_merge": false}); err != nil {
			return fmt.Errorf("the fork's settings: %w", err)
		}
		return nil
	}
	if err := b.GH.UpdateRepo(map[string]any{"allow_auto_merge": true, "delete_branch_on_merge": true}); err != nil {
		return fmt.Errorf("repository settings: %w", err)
	}
	if b.Cfg.MergeQueue {
		if err := b.GH.PutRuleset(RulesetName, b.Ruleset()); err != nil {
			return fmt.Errorf("the ruleset with the merge queue (merge queues need a repository an organization owns): %w", err)
		}
		return nil
	}
	if len(b.Cfg.RequiredChecks) > 0 {
		if err := b.GH.ProtectBranch(b.Cfg.DefaultBranch, b.Cfg.RequiredChecks); err != nil {
			return fmt.Errorf("branch protection: %w", err)
		}
	}
	return nil
}

// RulesetName is the ruleset setup writes for the default branch.
const RulesetName = "nightbot: default branch"

// Ruleset is the default branch's ruleset with merge_queue: the required checks, and a merge
// queue that tests each pull request on top of the newest default branch and the ones ahead of it.
func (b *Bot) Ruleset() map[string]any {
	var checks []map[string]string
	for _, name := range b.Cfg.RequiredChecks {
		checks = append(checks, map[string]string{"context": name})
	}
	rules := []map[string]any{{"type": "merge_queue", "parameters": map[string]any{
		"merge_method":                      strings.ToUpper(b.Cfg.MergeMethod),
		"grouping_strategy":                 "ALLGREEN",
		"max_entries_to_build":              5,
		"min_entries_to_merge":              1,
		"max_entries_to_merge":              5,
		"min_entries_to_merge_wait_minutes": 1,
		"check_response_timeout_minutes":    60,
	}}}
	if len(checks) > 0 {
		rules = append(rules, map[string]any{"type": "required_status_checks", "parameters": map[string]any{
			"strict_required_status_checks_policy": false,
			"required_status_checks":               checks,
		}})
	}
	return map[string]any{"target": "branch", "enforcement": "active",
		"conditions": map[string]any{"ref_name": map[string]any{"include": []string{"~DEFAULT_BRANCH"}, "exclude": []string{}}},
		"rules":      rules}
}

// Doctor says what is still missing, one line a check.
func (b *Bot) Doctor() []string {
	var lines []string
	ok := func(good bool, text string) {
		mark := "✓"
		if !good {
			mark = "✗"
		}
		lines = append(lines, mark+" "+text)
	}
	if login, err := b.GH.Whoami(); err == nil {
		ok(strings.EqualFold(login, b.Cfg.BotLogin), fmt.Sprintf("the token is @%s's (bot_login is %s)", login, b.Cfg.BotLogin))
	} else {
		ok(true, "the token is an app's or the workflow's (no /user): pushes made with GITHUB_TOKEN start no CI")
	}
	exists, err := b.GH.BranchExists(b.Cfg.StateBranch)
	ok(err == nil && exists, "the state branch "+b.Cfg.StateBranch+" exists (nightbot setup makes it)")
	if b.Cfg.Forked() {
		b.forkDoctor(ok)
		b.providersDoctor(ok)
		return lines
	}
	protection, err := b.GH.Protection(b.Cfg.DefaultBranch)
	if err != nil {
		protection = &github.Protection{}
	}
	ok(err == nil && len(protection.Checks) > 0, fmt.Sprintf("%s requires checks before a merge: %v", b.Cfg.DefaultBranch, protection.Checks))
	if b.Cfg.AutoMerge {
		missing, _, err := b.unprotected()
		ok(err == nil && len(missing) == 0, fmt.Sprintf("auto-merge can turn on (required_checks %v all required)", b.Cfg.RequiredChecks))
	}
	if b.Cfg.MergeQueue || protection.MergeQueue {
		ok(protection.MergeQueue, b.Cfg.DefaultBranch+" merges through a merge queue (setup --repo-settings writes the ruleset)")
		ok(b.Cfg.CIRunsInMergeQueue(), fmt.Sprintf("the %q workflow runs on merge_group, or the queue waits for its checks for ever", b.Cfg.CIWorkflow))
	}
	b.providersDoctor(ok)
	return lines
}

// providersDoctor checks the providers, the workflows and the committed off switch.
func (b *Bot) providersDoctor(ok func(bool, string)) {
	any := false
	for _, p := range b.Cfg.Providers.Ordered() {
		if !p.Enabled {
			continue
		}
		any = true
		switch {
		case p.Login == "secret":
			ok(b.Secrets[p.Secret], fmt.Sprintf("%s: its secret %s is set", p.ID, p.Secret))
		case config.GitHubHosted(p.RunsOn):
			ok(false, p.ID+": a machine login needs a self-hosted runs_on")
		default:
			ok(true, fmt.Sprintf("%s: logs in on the machine behind runner label %s (.nightbot/machine/)", p.ID, p.RunsOn))
		}
	}
	ok(any, "at least one provider is enabled")
	if err := b.Cfg.CheckWorkflows(); err != nil {
		ok(false, err.Error())
	}
	ok(!b.HaltFile(), "no .nightbot/HALT")
}
