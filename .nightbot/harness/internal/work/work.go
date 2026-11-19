// Package work is the model job: it holds one model login and a read-only GitHub token, writes
// nothing to GitHub, and leaves a result file and a git bundle for deliver to check and push.
//
// A build: the builder works on the item's branch, the repository's checks run (the builder
// fixes red ones), then a fresh session of the same model reviews the change adversarially; its
// findings are fixed and it reviews again, until it approves or the rounds run out.
package work

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"nightbot/internal/config"
	"nightbot/internal/gitx"
	"nightbot/internal/job"
	"nightbot/internal/model"
	"nightbot/internal/prompts"
)

// CILogs reads a failed CI run's logs; the model job's read-only token is enough.
type CILogs interface {
	FailedJobs(runID int64) ([]*JobRef, error)
	JobLog(jobID int64) (string, error)
}

// JobRef is a failed CI job.
type JobRef struct {
	ID   int64
	Name string
}

// Worker does one plan's model work.
type Worker struct {
	Cfg  *config.Config
	Plan *job.Plan
	Out  string
	Repo *gitx.Repo
	// Auth is the checkout with the read-only token, for the fetches made before any model runs;
	// after that the harness's git carries no token a model's hook or filter could catch.
	Auth *gitx.Repo
	// BaseRemote holds the base branch and BranchRemote the bot's branches: both "origin", or
	// in fork mode the upstream repository and "fork".
	BaseRemote   string
	BranchRemote string
	Runner       *model.Runner
	CI           CILogs
	Deadline     time.Time
	Log          io.Writer
	Now          func() time.Time
	// RunCheck runs one check command; a seam for the tests.
	RunCheck func(ctx context.Context, command, dir string, env []string) (string, error)

	result    *job.Result
	system    string
	templates prompts.Templates
}

// minCallMinutes is the least time left for a model call to be worth starting.
const minCallMinutes = 15

// Do runs the plan and writes result.json (and change.bundle when there is a change) into Out.
func (w *Worker) Do() *job.Result {
	if w.Now == nil {
		w.Now = time.Now
	}
	if w.Log == nil {
		w.Log = io.Discard
	}
	if w.BaseRemote == "" {
		w.BaseRemote = "origin"
	}
	if w.BranchRemote == "" {
		w.BranchRemote = "origin"
	}
	w.result = &job.Result{Action: w.Plan.Action, Branch: w.Plan.Branch}
	defer func() {
		if p := recover(); p != nil {
			w.result.Outcome, w.result.Error = job.Failed, fmt.Sprint(p)
		}
		w.redact()
		if err := job.Write(filepath.Join(w.Out, "result.json"), w.result); err != nil {
			fmt.Fprintln(w.Log, "result.json:", err)
		}
	}()
	templates, err := prompts.Load(w.Cfg)
	if err != nil {
		panic(err)
	}
	w.templates = templates
	w.system = w.render("system", nil)
	w.Repo.MustGit("config", "user.name", w.Cfg.BotLogin)
	w.Repo.MustGit("config", "user.email", noreply(w.Cfg.BotLogin))
	switch w.Plan.Action {
	case "build":
		w.build()
	case "revise":
		w.revise()
	case "review":
		w.audit()
	case "ask":
		w.ask()
	case "triage":
		w.triage()
	case "plan":
		w.plan()
	default:
		panic("unknown action " + w.Plan.Action)
	}
	return w.result
}

func noreply(login string) string {
	login = strings.TrimSuffix(login, "[bot]")
	return login + "@users.noreply.github.com"
}

// auth is the checkout for a fetch, which needs the token in a private repository.
func (w *Worker) auth() *gitx.Repo {
	if w.Auth != nil {
		return w.Auth
	}
	return w.Repo
}

// render fills a template from memory with the common variables and extra.
func (w *Worker) render(name string, extra map[string]string) string {
	text, err := w.templates.Render(w.Cfg, name, w.vars(extra))
	if err != nil {
		panic(err)
	}
	return text
}

// vars are the template variables every prompt has, plus extra.
func (w *Worker) vars(extra map[string]string) map[string]string {
	p := w.Plan
	var checks []string
	for _, c := range w.Cfg.Checks {
		checks = append(checks, fmt.Sprintf("- %s: `%s`", c.Name, c.Run))
	}
	if len(checks) == 0 {
		checks = append(checks, "- (none configured: run what the repository's README says)")
	}
	v := map[string]string{
		"number": fmt.Sprint(p.Number), "title": prompts.Fence("title", p.Title), "thread": p.Thread, "branch": p.Branch,
		"base": p.Base, "difficulty": p.Difficulty, "model": p.Model, "provider": p.Provider,
		"checks_list": strings.Join(checks, "\n"), "kind": map[bool]string{true: "pull request", false: "issue"}[p.IsPR],
		"forbidden_paths": strings.Join(w.Cfg.ForbiddenPaths, ", "),
		"plan":            "",
	}
	if p.PlanText != "" && p.Action != "plan" {
		v["plan"] = "## The approved plan\n\nA person approved this plan before the build started. Follow it; where the code " +
			"proves a step of it wrong, do what it was for and say why in your report.\n\n" +
			prompts.Fence("the approved plan", prompts.Truncate(p.PlanText, 30000))
	}
	for k, val := range extra {
		v[k] = val
	}
	return v
}

// call runs one model call and books its time; it reports false when the run must stop.
func (w *Worker) call(kind string, write bool, prompt string) (*model.Outcome, bool) {
	left := w.Deadline.Sub(w.Now())
	if left < minCallMinutes*time.Minute {
		w.note(fmt.Sprintf("Stopped before a %s call: %.0f minutes of the job were left.", kind, left.Minutes()))
		return nil, false
	}
	timeout := time.Duration(w.Cfg.CallTimeoutMinutes) * time.Minute
	if left-5*time.Minute < timeout {
		timeout = left - 5*time.Minute
	}
	head := w.Repo.MustGit("rev-parse", "HEAD")
	fmt.Fprintf(w.Log, "nightbot: %s call on %s (%s), up to %s\n", kind, w.Plan.Model, w.Plan.Provider, timeout)
	o := w.Runner.Run(model.Call{Kind: kind, Write: write, Model: w.Plan.Model, Effort: w.Plan.Effort,
		Prompt: prompt, System: w.system, Cwd: w.Repo.Dir, Turns: w.Cfg.MaxTurns[kind], Timeout: timeout})
	w.result.Minutes += o.Duration.Minutes()
	w.result.Calls = append(w.result.Calls, job.Call{Kind: kind, Model: w.Plan.Model,
		Minutes: round(o.Duration.Minutes()), ExitCode: o.ExitCode, Error: o.Err})
	w.backOnBranch(head)
	if !write {
		w.discard(head)
	}
	if o.RateLimited {
		w.result.RateLimited, w.result.ResetAt = true, o.ResetAt
		return o, false
	}
	if o.Err != "" {
		fmt.Fprintf(w.Log, "nightbot: the %s call failed: %s\n%s\n", kind, o.Err, prompts.Tail(o.Output, 4000))
	}
	return o, true
}

func round(x float64) float64 { return float64(int(x*10+0.5)) / 10 }

// backOnBranch puts the checkout back on the plan's branch if the model left it.
func (w *Worker) backOnBranch(head string) {
	current, _ := w.Repo.Git("symbolic-ref", "--quiet", "--short", "HEAD")
	if current != w.Plan.Branch {
		fmt.Fprintf(w.Log, "nightbot: the model left the branch (on %q); back to %s\n", current, w.Plan.Branch)
		w.Repo.MustGit("checkout", "-q", "-B", w.Plan.Branch)
	}
}

// discard throws away whatever a read-only call changed or committed.
func (w *Worker) discard(head string) {
	w.Repo.MustGit("reset", "-q", "--hard", head)
	w.Repo.MustGit("clean", "-q", "-fd")
}

// commit folds everything since head (the model's own commits too) into one commit, after
// putting back any forbidden path. It reports whether there was anything to commit, and panics
// rather than commit a forbidden path it could not put back.
func (w *Worker) commit(head, message string) bool {
	w.Repo.MustGit("reset", "-q", "--soft", head)
	dirty, err := w.Repo.Dirty()
	if err != nil {
		panic(err)
	}
	for _, path := range dirty {
		if !config.MatchAny(w.Cfg.ForbiddenPaths, path) {
			continue
		}
		if _, err := w.Repo.Git("cat-file", "-e", head+":"+path); err == nil {
			w.Repo.MustGit("checkout", head, "--", gitx.Literal(path))
		} else {
			w.Repo.MustGit("rm", "-q", "-r", "-f", "--cached", "--ignore-unmatch", "--", gitx.Literal(path))
			if err := os.RemoveAll(filepath.Join(w.Repo.Dir, path)); err != nil {
				panic(fmt.Sprintf("could not remove the forbidden path %q: %v", path, err))
			}
		}
		w.result.Reverted = appendNew(w.result.Reverted, path)
	}
	w.Repo.MustGit("add", "-A")
	staged, err := w.Repo.Staged(head)
	if err != nil {
		panic(err)
	}
	for _, path := range staged {
		if config.MatchAny(w.Cfg.ForbiddenPaths, path) {
			panic(fmt.Sprintf("could not put back the forbidden path %q", path))
		}
	}
	if len(staged) == 0 {
		return false
	}
	w.Repo.MustGit("commit", "-q", "--no-verify", "-m", message)
	return true
}

func appendNew(list []string, s string) []string {
	for _, x := range list {
		if x == s {
			return list
		}
	}
	return append(list, s)
}

func (w *Worker) note(text string) {
	if w.result.Report != "" {
		w.result.Report += "\n\n"
	}
	w.result.Report += "_" + text + "_"
}

// checkout puts the item's branch at HEAD and reports the base the change is measured from.
func (w *Worker) checkout(existing bool) string {
	p := w.Plan
	base := w.fetchBase()
	if existing {
		w.auth().MustGit("fetch", "-q", w.BranchRemote, "+refs/heads/"+p.Branch+":refs/remotes/"+w.BranchRemote+"/"+p.Branch)
		w.Repo.MustGit("checkout", "-q", "-B", p.Branch, w.BranchRemote+"/"+p.Branch)
		sha := w.Repo.MustGit("merge-base", base, "HEAD")
		w.result.Base = sha
		return sha
	}
	w.Repo.MustGit("checkout", "-q", "-B", p.Branch, base)
	sha := w.Repo.MustGit("rev-parse", base)
	w.result.Base = sha
	return sha
}

// fetchBase fetches the base branch and returns its remote ref.
func (w *Worker) fetchBase() string {
	w.auth().MustGit("fetch", "-q", w.BaseRemote, w.Plan.Base)
	return w.BaseRemote + "/" + w.Plan.Base
}

// fetchPR fetches a pull request's head (also a fork's) and returns its ref, holding it to the
// head the plan saw.
func (w *Worker) fetchPR() string {
	p := w.Plan
	ref := fmt.Sprintf("refs/remotes/origin/pr-%d", p.Number)
	w.auth().MustGit("fetch", "-q", "origin", p.Base, fmt.Sprintf("+refs/pull/%d/head:%s", p.Number, ref))
	if head := w.Repo.MustGit("rev-parse", ref); p.Head != "" && head != p.Head {
		panic(fmt.Sprintf("#%d moved from %.12s to %.12s since it was planned; it will be looked at again", p.Number, p.Head, head))
	}
	return ref
}

func (w *Worker) build() {
	base := w.checkout(w.Plan.Continue)
	w.baseline(base)
	carry := ""
	if w.Plan.Continue {
		carry = "The branch already holds earlier work on this item (an earlier run was cut off). Read `git log " +
			base[:12] + "..HEAD` and carry it on rather than starting over."
	}
	prompt := w.render("build", map[string]string{"continue": carry})
	head := w.Repo.MustGit("rev-parse", "HEAD")
	o, ok := w.call("build", true, prompt)
	if o != nil {
		w.result.Report = o.Answer
	}
	w.commit(head, fmt.Sprintf("%s (#%d)", w.Plan.Title, w.Plan.Number))
	if !w.hasChange(base) {
		switch {
		case w.result.RateLimited:
			w.finish(base, job.RateLimited)
		case o == nil:
			w.finish(base, job.Failed)
			w.result.Error = "the job's time ran out before the build started"
		case o.Err != "":
			w.finish(base, job.Failed)
			w.result.Error = o.Err
		default:
			w.finish(base, job.NoChange)
		}
		return
	}
	if ok {
		ok = w.checksAndFix(base)
	}
	if ok {
		w.reviewLoop(base)
	}
	w.finish(base, "")
}

func (w *Worker) revise() {
	base := w.checkout(true)
	if w.Plan.MergeBase {
		merged, err := w.mergeBase()
		if err != nil {
			if !w.result.RateLimited {
				w.result.Error = err.Error()
				w.finish(base, job.Failed)
				return
			}
			w.finish(base, "")
			return
		}
		base = merged
		w.result.Base = merged
	}
	w.baseline(base)
	request := w.Plan.Request
	if logs := w.ciLogs(); logs != "" {
		request += "\n\n## The failing CI jobs (the end of each log)\n\n" + logs
	}
	prompt := w.render("fix", map[string]string{
		"request": prompts.Fence("requests", request), "checks": "(not run yet for this revision)",
		"findings": "(none: this revision answers the request above)"})
	head := w.Repo.MustGit("rev-parse", "HEAD")
	o, ok := w.call("fix", true, prompt)
	if o != nil {
		w.result.Report = o.Answer
	}
	w.commit(head, fmt.Sprintf("Revise #%d", w.Plan.Number))
	if ok {
		ok = w.checksAndFix(base)
	}
	if ok {
		w.reviewLoop(base)
	}
	w.finish(base, "")
}

// mergeBase merges the newest base branch into the item's branch as a merge commit of its own,
// so a change the merge queue or a conflict sent back is fixed on top of what it will merge into.
// A conflict goes to the model to resolve; a forbidden path always takes the base's side, since
// the branch never changes one. It returns the new base the change is measured from.
func (w *Worker) mergeBase() (string, error) {
	p := w.Plan
	onto := w.BaseRemote + "/" + p.Base
	message := fmt.Sprintf("Merge %s into %s", p.Base, p.Branch)
	if _, err := w.Repo.Git("merge", "--no-ff", "--no-edit", "-m", message, onto); err != nil {
		conflicted, _ := w.Repo.Unmerged()
		if len(conflicted) == 0 {
			w.Repo.Git("merge", "--abort")
			return "", fmt.Errorf("merging %s failed: %v", onto, err)
		}
		prompt := w.render("fix", map[string]string{
			"request": fmt.Sprintf("Merging the newest `%s` into this branch conflicts in: %s.\n\nResolve every conflict, "+
				"keeping what both sides meant, and remove every conflict marker. Do not commit, abort or restart "+
				"the merge: the harness completes it.", p.Base, "`"+strings.Join(conflicted, "`, `")+"`"),
			"checks": "(not run yet: the merge comes first)", "findings": "(none: only the merge)"})
		o, ok := w.call("fix", true, prompt)
		if !ok || o.Err != "" {
			w.Repo.Git("merge", "--abort")
			if o != nil && o.Err != "" {
				return "", fmt.Errorf("resolving the merge with %s failed: %s", p.Base, o.Err)
			}
			return "", fmt.Errorf("the merge with %s was not resolved", p.Base)
		}
		for _, path := range conflicted {
			data, _ := os.ReadFile(filepath.Join(w.Repo.Dir, path))
			if bytes.HasPrefix(data, []byte("<<<<<<< ")) || bytes.Contains(data, []byte("\n<<<<<<< ")) ||
				bytes.Contains(data, []byte("\n>>>>>>> ")) {
				w.Repo.Git("merge", "--abort")
				return "", fmt.Errorf("the merge with %s left conflict markers in %s", p.Base, path)
			}
		}
		w.Repo.MustGit("add", "-A")
		staged, err := w.Repo.Staged(onto)
		if err != nil {
			panic(err)
		}
		for _, path := range staged {
			if !config.MatchAny(w.Cfg.ForbiddenPaths, path) {
				continue
			}
			if _, err := w.Repo.Git("cat-file", "-e", onto+":"+path); err == nil {
				w.Repo.MustGit("checkout", onto, "--", gitx.Literal(path))
			} else {
				w.Repo.MustGit("rm", "-q", "-r", "-f", "--ignore-unmatch", "--", gitx.Literal(path))
			}
			w.result.Reverted = appendNew(w.result.Reverted, path)
		}
		if _, err := w.Repo.Git("rev-parse", "-q", "--verify", "MERGE_HEAD"); err == nil {
			w.Repo.MustGit("commit", "-q", "--no-verify", "--no-edit")
		} else if parents := strings.Fields(w.Repo.MustGit("rev-list", "--parents", "-n", "1", "HEAD")); len(parents) != 3 {
			return "", fmt.Errorf("the merge with %s was abandoned during its resolution", p.Base)
		}
	}
	w.note(fmt.Sprintf("Merged the newest %s into the branch first.", p.Base))
	return w.Repo.MustGit("merge-base", onto, "HEAD"), nil
}

// audit reviews a pull request as it stands; it changes nothing. An untrusted one (another
// repository's, or a stranger's) is never checked out: the model reads its diff from the trusted
// base, and none of its code, checks, CLI settings or prompts run.
func (w *Worker) audit() {
	p := w.Plan
	ref := w.fetchPR()
	head := ref
	if p.Untrusted {
		w.Repo.MustGit("checkout", "-q", "-B", p.Branch, "origin/"+p.Base)
	} else {
		w.Repo.MustGit("checkout", "-q", "-B", p.Branch, ref)
		head = "HEAD"
	}
	base := w.Repo.MustGit("merge-base", "origin/"+p.Base, ref)
	w.result.Base = base
	if p.Untrusted {
		w.result.ChecksGreen = true
		w.note("The checks did not run: this pull request comes from outside the trust list, so none of its code runs here.")
	} else {
		w.result.Checks = w.runChecks()
		w.result.ChecksGreen = w.green(w.result.Checks)
	}
	v, ok := w.review(base, head, 1, 1, nil)
	if !ok {
		w.result.Outcome = job.RateLimited
		if !w.result.RateLimited {
			w.result.Outcome, w.result.Error = job.Failed, "the job's time ran out before the review"
		}
		return
	}
	w.result.Verdict = v
	w.result.Rounds = 1
	w.result.Approved = v.Approves() && w.result.ChecksGreen
	w.result.Outcome = job.Reviewed
	w.result.Head = w.Repo.MustGit("rev-parse", ref)
}

func (w *Worker) ask() {
	p := w.Plan
	change := ""
	if p.IsPR {
		ref := w.fetchPR()
		if p.Untrusted {
			w.Repo.MustGit("checkout", "-q", "-B", p.Branch, "origin/"+p.Base)
			base := w.Repo.MustGit("merge-base", "origin/"+p.Base, ref)
			diff, _ := w.Repo.Git("diff", base, ref)
			change = "## The pull request's change\n\nIt is not checked out (it comes from outside the trust list); this is its diff.\n\n" +
				prompts.Fence("diff", prompts.Truncate(diff, w.Cfg.MaxDiffChars))
		} else {
			w.Repo.MustGit("checkout", "-q", "-B", p.Branch, ref)
		}
	} else {
		w.Repo.MustGit("checkout", "-q", "-B", p.Branch, w.fetchBase())
	}
	prompt := w.render("ask", map[string]string{"change": change,
		"question": prompts.Fence("question by @"+p.Asker, p.Request)})
	o, ok := w.call("ask", false, prompt)
	switch {
	case !ok && w.result.RateLimited:
		w.result.Outcome = job.RateLimited
	case o == nil || o.Err != "" || o.Answer == "":
		w.result.Outcome = job.Failed
		if o != nil {
			w.result.Error = o.Err
		}
	default:
		w.result.Outcome, w.result.Answer = job.Answered, o.Answer
	}
}

func (w *Worker) triage() {
	w.Repo.MustGit("checkout", "-q", "-B", w.Plan.Branch, w.fetchBase())
	prompt := w.render("triage", nil)
	o, ok := w.call("triage", false, prompt)
	t := &job.Triage{}
	switch {
	case !ok && w.result.RateLimited:
		w.result.Outcome = job.RateLimited
	case o == nil || !job.LastJSON(o.Answer, t) || (t.Difficulty != "medium" && t.Difficulty != "hard"):
		// An unreadable rating rates nothing: the item builds as medium, as unrated items do.
		w.result.Outcome, w.result.Triage = job.Triaged, &job.Triage{Difficulty: "medium",
			Summary: "The triage gave no readable rating, so this builds as medium."}
	default:
		w.result.Outcome, w.result.Triage = job.Triaged, t
	}
}

// plan reads the code and writes a plan for a person to approve; it changes nothing.
func (w *Worker) plan() {
	p := w.Plan
	base := w.fetchBase()
	w.Repo.MustGit("checkout", "-q", "-B", p.Branch, base)
	w.result.Base = w.Repo.MustGit("rev-parse", base)
	previous, feedback := "(none: this is the first plan)", "(none)"
	if p.PlanText != "" {
		previous = prompts.Fence("the plan so far", prompts.Truncate(p.PlanText, 30000))
	}
	if strings.TrimSpace(p.Request) != "" {
		feedback = prompts.Fence("what to change", p.Request)
	}
	prompt := w.render("plan", map[string]string{"previous": previous, "feedback": feedback,
		"base_sha": w.result.Base})
	o, ok := w.call("plan", false, prompt)
	switch {
	case !ok && w.result.RateLimited:
		w.result.Outcome = job.RateLimited
	case o == nil || o.Err != "" || strings.TrimSpace(o.Answer) == "":
		w.result.Outcome = job.Failed
		w.result.Error = "the plan call gave no plan"
		if o != nil && o.Err != "" {
			w.result.Error = o.Err
		}
	default:
		w.result.Outcome, w.result.Plan = job.Planned, strings.TrimSpace(o.Answer)
	}
}

func (w *Worker) hasChange(base string) bool {
	out, _ := w.Repo.Git("rev-list", "--count", base+"..HEAD")
	return out != "" && out != "0"
}

// checksAndFix runs the checks and has the builder fix red ones, up to fix_passes times. It
// reports false when the run must stop.
func (w *Worker) checksAndFix(base string) bool {
	w.result.Checks = w.runChecks()
	w.result.ChecksGreen = w.green(w.result.Checks)
	for pass := 1; !w.result.ChecksGreen && pass <= w.Cfg.FixPasses; pass++ {
		prompt := w.render("fix", map[string]string{
			"request": "Make the failing checks below pass without weakening them. A check marked as red on the base " +
				"too fails without this change: leave it alone.",
			"checks":   prompts.Fence("checks", w.checksText(w.result.Checks)),
			"findings": "(none: only the checks)"})
		head := w.Repo.MustGit("rev-parse", "HEAD")
		o, ok := w.call("fix", true, prompt)
		w.commit(head, fmt.Sprintf("Fix the checks (#%d)", w.Plan.Number))
		if o != nil && o.Answer != "" {
			w.result.Report += "\n\n**Check fix " + fmt.Sprint(pass) + ":** " + o.Answer
		}
		if !ok {
			return false
		}
		w.result.Checks = w.runChecks()
		w.result.ChecksGreen = w.green(w.result.Checks)
	}
	return true
}

// baseline runs the checks on the base, with baseline_checks, and records the ones red there:
// they fail without the change, so they never block it.
func (w *Worker) baseline(base string) {
	if !w.Cfg.BaselineChecks || len(w.Cfg.Checks) == 0 {
		return
	}
	head := w.Repo.MustGit("rev-parse", "HEAD")
	w.Repo.MustGit("checkout", "-q", "--detach", base)
	for _, c := range w.runChecks() {
		if !c.Passed {
			w.result.BaselineRed = append(w.result.BaselineRed, c.Name)
		}
	}
	w.Repo.MustGit("checkout", "-q", "-B", w.Plan.Branch, head)
	fmt.Fprintf(w.Log, "nightbot: red on the base: %v\n", w.result.BaselineRed)
}

// green says whether the checks pass, a check red on the base aside.
func (w *Worker) green(checks []job.CheckResult) bool {
	for _, c := range checks {
		if !c.Passed && !w.result.RedOnBase(c.Name) {
			return false
		}
	}
	return true
}

func (w *Worker) checksText(checks []job.CheckResult) string { return checksText(checks, w.result) }

// reviewLoop reviews the change in a fresh session and fixes what it finds until it approves
// or the rounds run out.
func (w *Worker) reviewLoop(base string) {
	rounds := w.Plan.ReviewRounds
	var previous []job.Finding
	for round := 1; round <= rounds; round++ {
		v, ok := w.review(base, "HEAD", round, rounds, previous)
		if !ok {
			return
		}
		w.result.Verdict, w.result.Rounds = v, round
		if v.Approves() && w.result.ChecksGreen {
			w.result.Approved = true
			return
		}
		if round == rounds {
			return
		}
		findings := v.Blocking()
		if v.Approves() { // approved, but the checks are red
			findings = append(findings, job.Finding{Severity: "blocking", Problem: "The repository's checks fail."})
		}
		previous = findings
		prompt := w.render("fix", map[string]string{
			"request":  "Address every blocking finding of the review below.",
			"findings": prompts.Fence("review", findingsText(findings)),
			"checks":   prompts.Fence("checks", w.checksText(w.result.Checks))})
		head := w.Repo.MustGit("rev-parse", "HEAD")
		o, ok := w.call("fix", true, prompt)
		w.commit(head, fmt.Sprintf("Address review round %d (#%d)", round, w.Plan.Number))
		if o != nil && o.Answer != "" {
			w.result.Report += fmt.Sprintf("\n\n**After review %d:** %s", round, o.Answer)
		}
		if !ok || !w.checksAndFix(base) {
			return
		}
	}
}

// review is one adversarial review of base..head in a fresh, read-only session.
func (w *Worker) review(base, head string, round, rounds int, previous []job.Finding) (*job.Verdict, bool) {
	stat, _ := w.Repo.Git("diff", "--stat", base, head)
	diff, _ := w.Repo.Git("diff", base, head)
	earlier := "(none: this is the first review)"
	if len(previous) > 0 {
		earlier = prompts.Fence("earlier review", findingsText(previous))
	}
	where := fmt.Sprintf("The working directory is the branch `%s` with the change committed; it starts from `%s`.",
		w.Plan.Branch, base)
	if w.Plan.Untrusted {
		where = fmt.Sprintf("The change is **not** checked out: it comes from outside this repository or from someone "+
			"off the trust list, so none of its code runs here. The working directory is the base it starts from (`%s`); "+
			"read the change from the diff below, and the code around it from the working directory.", base)
	}
	prompt := w.render("review", map[string]string{
		"round": fmt.Sprint(round), "rounds": fmt.Sprint(rounds), "base_sha": base, "where": where,
		"report":   prompts.Fence("the builder's report", w.result.Report),
		"checks":   prompts.Fence("checks", w.checksText(w.result.Checks)),
		"previous": earlier,
		"diff":     prompts.Fence("the change", "```diff\n"+stat+"\n\n"+prompts.Truncate(diff, w.Cfg.MaxDiffChars)+"\n```")})
	o, ok := w.call("review", false, prompt)
	if !ok {
		return nil, false
	}
	return job.ParseVerdict(o.Answer), true
}

func (w *Worker) finish(base, outcome string) {
	r := w.result
	r.Head = w.Repo.MustGit("rev-parse", "HEAD")
	changed, _ := w.Repo.Changed(base, "HEAD")
	r.Changed = changed
	switch {
	case outcome != "":
		r.Outcome = outcome
	case r.RateLimited:
		r.Outcome = job.RateLimited
	case r.Approved:
		r.Outcome = job.Approved
	default:
		r.Outcome = job.Unapproved
	}
	if w.hasChange(base) && outcome != job.Failed {
		bundle := filepath.Join(w.Out, "change.bundle")
		if _, err := w.Repo.Git("bundle", "create", bundle, "refs/heads/"+w.Plan.Branch, "^"+base); err != nil {
			r.Outcome, r.Error = job.Failed, err.Error()
			return
		}
		r.Bundle = "change.bundle"
	}
}

// runChecks runs the repository's checks, each with its own timeout, in a clean environment.
func (w *Worker) runChecks() []job.CheckResult {
	run := w.RunCheck
	if run == nil {
		run = runCheck
	}
	env := cleanEnv(os.Environ())
	var results []job.CheckResult
	for _, check := range w.Cfg.Checks {
		ctx, cancel := context.WithTimeout(context.Background(), time.Duration(check.TimeoutMinutes)*time.Minute)
		start := w.Now()
		out, err := run(ctx, check.Run, w.Repo.Dir, env)
		cancel()
		r := job.CheckResult{Name: check.Name, Passed: err == nil, Minutes: round(w.Now().Sub(start).Minutes())}
		if err != nil {
			r.Output = prompts.Tail(strings.TrimSpace(out)+"\n"+err.Error(), 6000)
		}
		fmt.Fprintf(w.Log, "nightbot: check %s: %v\n", check.Name, map[bool]string{true: "passed", false: "failed"}[r.Passed])
		results = append(results, r)
	}
	return results
}

var sensitive = regexp.MustCompile(`(?i)(TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|CREDENTIAL|^ACTIONS_|^NIGHTBOT_)`)

func cleanEnv(env []string) []string {
	var out []string
	for _, kv := range env {
		if name, _, _ := strings.Cut(kv, "="); !sensitive.MatchString(name) {
			out = append(out, kv)
		}
	}
	return out
}

func runCheck(ctx context.Context, command, dir string, env []string) (string, error) {
	cmd := exec.CommandContext(ctx, "bash", "-c", command)
	cmd.Dir = dir
	cmd.Env = env
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	cmd.WaitDelay = 10 * time.Second
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		err = fmt.Errorf("timed out")
	}
	return out.String(), err
}

func checksText(checks []job.CheckResult, r *job.Result) string {
	if len(checks) == 0 {
		return "No checks are configured."
	}
	var b strings.Builder
	for _, c := range checks {
		state := "passed"
		if !c.Passed {
			state = "FAILED"
			if r.RedOnBase(c.Name) {
				state = "FAILED, and red on the base too (not this change's)"
			}
		}
		fmt.Fprintf(&b, "### %s: %s\n", c.Name, state)
		if c.Output != "" {
			fmt.Fprintf(&b, "```\n%s\n```\n", c.Output)
		}
	}
	return b.String()
}

func findingsText(findings []job.Finding) string {
	var b strings.Builder
	for i, f := range findings {
		where := f.File
		if f.Line > 0 {
			where += fmt.Sprintf(":%d", f.Line)
		}
		fmt.Fprintf(&b, "%d. [%s] %s %s", i+1, f.Severity, where, f.Problem)
		if f.Fix != "" {
			fmt.Fprintf(&b, " Fix: %s", f.Fix)
		}
		b.WriteString("\n")
	}
	return b.String()
}

func (w *Worker) ciLogs() string {
	if w.Plan.CIRun == 0 || w.CI == nil {
		return ""
	}
	jobs, err := w.CI.FailedJobs(w.Plan.CIRun)
	if err != nil {
		return "(the CI logs could not be read: " + err.Error() + ")"
	}
	var b strings.Builder
	for i, j := range jobs {
		if i == 3 {
			fmt.Fprintf(&b, "(%d more failed jobs)\n", len(jobs)-3)
			break
		}
		log, err := w.CI.JobLog(j.ID)
		if err != nil {
			log = "(unreadable: " + err.Error() + ")"
		}
		fmt.Fprintf(&b, "### %s\n```\n%s\n```\n", j.Name, prompts.Tail(log, 6000))
	}
	return b.String()
}

// redact keeps the model's login out of everything result.json records.
func (w *Worker) redact() {
	secret := w.Runner.Secret
	if len(secret) < 8 {
		return
	}
	scrub := func(s string) string { return strings.ReplaceAll(s, secret, "[redacted]") }
	r := w.result
	r.Report, r.Answer, r.Error, r.Plan = scrub(r.Report), scrub(r.Answer), scrub(r.Error), scrub(r.Plan)
	if r.Triage != nil {
		r.Triage.Summary = scrub(r.Triage.Summary)
	}
	for i := range r.Changed {
		r.Changed[i] = scrub(r.Changed[i])
	}
	for i := range r.Reverted {
		r.Reverted[i] = scrub(r.Reverted[i])
	}
	for i := range r.Checks {
		r.Checks[i].Output = scrub(r.Checks[i].Output)
	}
	for i := range r.Calls {
		r.Calls[i].Error = scrub(r.Calls[i].Error)
	}
	if r.Verdict != nil {
		r.Verdict.Summary = scrub(r.Verdict.Summary)
		for i := range r.Verdict.Findings {
			r.Verdict.Findings[i].Problem = scrub(r.Verdict.Findings[i].Problem)
			r.Verdict.Findings[i].Fix = scrub(r.Verdict.Findings[i].Fix)
			r.Verdict.Findings[i].File = scrub(r.Verdict.Findings[i].File)
		}
	}
}
