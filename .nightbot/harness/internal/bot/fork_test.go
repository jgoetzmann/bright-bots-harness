package bot

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/gitx"
	"nightbot/internal/job"
	"nightbot/internal/model"
	"nightbot/internal/state"
	"nightbot/internal/work"
)

// forkEnv is fork mode: this repository (the queue), the bot's fork and the upstream
// repository, each an in-memory GitHub, with real git behind the fork and upstream.
type forkEnv struct {
	t        *testing.T
	queue    *github.Fake
	fork     *github.Fake
	upstream *github.Fake
	cfg      *config.Config
	bot      *Bot
	upGit    string // upstream's bare repository
	forkGit  string // the fork's
	product  string // the model job's clone
	helper   string // a clone the fakes use to merge, as GitHub would
	fakeDir  string
}

func newForkEnv(t *testing.T, edit func(cfg map[string]any)) *forkEnv {
	t.Helper()
	dir := t.TempDir()
	e := &forkEnv{t: t, queue: github.NewFake(), fork: github.NewFake(), upstream: github.NewFake(),
		upGit: filepath.Join(dir, "upstream.git"), forkGit: filepath.Join(dir, "fork.git"), fakeDir: filepath.Join(dir, "fake")}
	e.queue.RepoName, e.fork.RepoName, e.upstream.RepoName = "me/harness", "bot/product", "up/product"
	for _, f := range []*github.Fake{e.queue, e.fork, e.upstream} {
		f.Login = "nightbot"
	}
	e.upstream.NoMerge = true
	os.MkdirAll(e.fakeDir, 0o755)
	script := filepath.Join(dir, "fake-cli.sh")
	os.WriteFile(script, []byte(fakeCLI), 0o755)

	seed := filepath.Join(dir, "seed")
	git(t, dir, "init", "-q", "-b", "main", seed)
	os.WriteFile(filepath.Join(seed, "README.md"), []byte("# The product\n"), 0o644)
	git(t, seed, "add", "-A")
	git(t, seed, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "seed")
	git(t, dir, "init", "-q", "--bare", "-b", "main", e.upGit)
	git(t, seed, "push", "-q", e.upGit, "main")
	git(t, dir, "clone", "-q", "--bare", e.upGit, e.forkGit)

	clone := func(name string) string {
		path := filepath.Join(dir, name)
		git(t, dir, "clone", "-q", e.upGit, path)
		git(t, path, "remote", "add", "fork", e.forkGit)
		git(t, path, "config", "user.name", "t")
		git(t, path, "config", "user.email", "t@example.com")
		return path
	}
	e.product, e.helper = clone("product"), clone("helper")
	deliverer := clone("deliver")

	root := filepath.Join(dir, "harness")
	os.MkdirAll(filepath.Join(root, config.Dir), 0o755)
	template, _ := filepath.Abs("../../..")
	copyDir(t, filepath.Join(template, "prompts"), filepath.Join(root, config.Dir, "prompts"))
	cfg := map[string]any{
		"repo": "me/harness", "bot_login": "nightbot", "operator": "boss", "timezone": "UTC",
		"plan_gate": true, "approvers": []string{"boss", "nathan"},
		"upstream": map[string]any{"repo": "up/product", "fork": "bot/product", "max_open_prs": 2,
			"reviewers": []string{"nathan"}, "auto_open": map[string]any{"labels": []string{"harness-ok"}, "max_open": 2}},
		"checks":          []map[string]any{{"name": "hello", "run": "test -f hello.txt"}},
		"forbidden_paths": []string{".nightbot/", ".github/"},
		"clis":            map[string]any{"fake": map[string]any{"argv": []string{"bash", script}, "prompt": "stdin", "answer": "stdout", "secret_env": "FAKE_TOKEN"}},
	}
	if edit != nil {
		edit(cfg)
	}
	providers := map[string]any{"max_parallel": 2, "providers": map[string]any{
		"main": map[string]any{"enabled": true, "cli": "fake", "login": "secret", "secret": "FAKE_TOKEN", "lanes": 2,
			"models": []map[string]string{{"model": "opus", "effort": "xhigh", "tier": "strong"}}},
	}}
	for name, v := range map[string]any{"config.json": cfg, "providers.json": providers} {
		data, _ := json.MarshalIndent(v, "", "  ")
		os.WriteFile(filepath.Join(root, config.Dir, name), data, 0o644)
	}
	os.WriteFile(filepath.Join(root, config.Dir, "trust.txt"), []byte("nathan\ncarol\n"), 0o644)
	loaded, err := config.Load(root)
	if err != nil {
		t.Fatal(err)
	}
	e.cfg = loaded

	e.bot = New(loaded, e.queue, nil)
	e.bot.Fork, e.bot.Upstream = e.fork, e.upstream
	e.bot.Workspace = deliverer
	e.bot.Secrets = map[string]bool{"FAKE_TOKEN": true}
	e.bot.Log = testWriter{t}
	e.bot.Sleep = func(time.Duration) {}
	e.fork.OnSync = e.syncSandbox
	e.fork.OnMerge = e.mergeIntoSandbox
	t.Setenv("FAKE_DIR", e.fakeDir)
	t.Setenv("NIGHTBOT_GITHUB_TOKEN", "test-token")
	if err := e.bot.Setup(false); err != nil {
		t.Fatal(err)
	}
	e.mirror()
	return e
}

// syncSandbox merges upstream's main into the fork's, as GitHub's sync of a fork does.
func (e *forkEnv) syncSandbox(branch string) (string, error) {
	git(e.t, e.helper, "fetch", "-q", "origin", branch)
	git(e.t, e.helper, "fetch", "-q", "fork", branch)
	git(e.t, e.helper, "checkout", "-q", "-B", "sandbox", "fork/"+branch)
	if _, err := (&gitx.Repo{Dir: e.helper}).Git("merge-base", "--is-ancestor", "origin/"+branch, "HEAD"); err == nil {
		return "none", nil
	}
	if _, err := (&gitx.Repo{Dir: e.helper}).Git("merge", "-q", "--no-edit", "origin/"+branch); err != nil {
		(&gitx.Repo{Dir: e.helper}).Git("merge", "--abort")
		return "", github.ErrConflict
	}
	git(e.t, e.helper, "push", "-q", "fork", "HEAD:refs/heads/"+branch)
	e.mirror()
	return "merge", nil
}

// mergeIntoSandbox merges a fork pull request's branch into the fork's main.
func (e *forkEnv) mergeIntoSandbox(pr *github.PR, method string) error {
	git(e.t, e.helper, "fetch", "-q", "fork")
	git(e.t, e.helper, "checkout", "-q", "-B", "sandbox", "fork/"+pr.Base.Ref)
	git(e.t, e.helper, "merge", "-q", "--no-ff", "--no-edit", "fork/"+pr.Head.Ref)
	git(e.t, e.helper, "push", "-q", "fork", "HEAD:refs/heads/"+pr.Base.Ref)
	return nil
}

// mirror does what GitHub does after pushes: the fakes' branch heads and pull request heads.
func (e *forkEnv) mirror() {
	refs := git(e.t, e.forkGit, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
	for _, line := range gitx.Lines(refs) {
		name, sha, _ := strings.Cut(line, " ")
		e.fork.Branches[name] = true
		e.fork.BranchHeads[name] = sha
	}
	for n, pr := range e.upstream.PRs {
		if sha, ok := e.fork.BranchHeads[pr.Head.Ref]; ok {
			e.upstream.SetHead(n, sha)
		}
		if pr.MergeableState == "" && !pr.Draft {
			pr.MergeableState = "clean" // what GitHub works out once it is ready
		}
	}
	e.upstream.BranchHeads["main"] = git(e.t, e.upGit, "rev-parse", "main")
}

func (e *forkEnv) run(want string) (*job.Plan, *job.Result) {
	e.t.Helper()
	p, why, err := e.bot.Plan(PlanOptions{})
	if err != nil {
		e.t.Fatal(err)
	}
	if p == nil {
		e.t.Fatalf("nothing planned: %s", why)
	}
	if p.Action != want {
		e.t.Fatalf("planned %s #%d, want %s", p.Action, p.Number, want)
	}
	out := e.t.TempDir()
	w := &work.Worker{Cfg: e.cfg, Plan: p, Out: out, Repo: &gitx.Repo{Dir: e.product}, BaseRemote: "origin", BranchRemote: "fork",
		Log: testWriter{e.t}, Runner: &model.Runner{Name: p.CLI, CLI: e.cfg.CLIs[p.CLI], Secret: "fake-secret-123", TempDir: e.t.TempDir()},
		Deadline: time.Now().Add(time.Hour)}
	r := w.Do()
	if err := e.bot.Deliver(p, out, "success"); err != nil {
		e.t.Fatal(err)
	}
	e.mirror()
	return p, r
}

func (e *forkEnv) state() *state.State {
	st, _, err := e.bot.Store.Load()
	if err != nil {
		e.t.Fatal(err)
	}
	return st
}

func (e *forkEnv) say(number int, author, body string) {
	e.t.Helper()
	id := e.queue.AddComment(number, author, "NONE", body)
	issue, _ := e.queue.GetIssue(number)
	if err := e.bot.Handle("issue_comment", &Event{Action: "created", Issue: issue,
		Comment: &github.Comment{ID: id, Body: body, User: github.User{Login: author}, AuthorAssociation: "NONE"}}); err != nil {
		e.t.Fatal(err)
	}
}

func (e *forkEnv) sweep() {
	e.t.Helper()
	if err := e.bot.Sweep(); err != nil {
		e.t.Fatal(err)
	}
	e.mirror()
}

func (e *forkEnv) green(f *github.Fake, sha string) {
	f.CheckRuns[sha] = []*github.CheckRun{{ID: 1, Name: "CI", Status: "completed", Conclusion: "success"}}
}

// request opens an issue from the request template that names an upstream issue, as boss.
func (e *forkEnv) request() (issue, upstream int) {
	e.t.Helper()
	upstream = e.upstream.AddIssue("Keyboard focus is lost after the quiz", "Tab goes nowhere.", "teacher")
	issue = e.queue.AddIssue("Fix the quiz's keyboard focus", "Please fix https://github.com/up/product/issues/"+fmt.Sprint(upstream)+" .",
		"boss", config.Requested)
	opened, _ := e.queue.GetIssue(issue)
	if err := e.bot.Handle("issues", &Event{Action: "opened", Issue: opened, Sender: github.User{Login: "boss"}}); err != nil {
		e.t.Fatal(err)
	}
	return issue, upstream
}

// approved takes a request through its plan and nathan's approval.
func (e *forkEnv) approved() (issue, upstream int) {
	e.t.Helper()
	issue, upstream = e.request()
	e.run("plan")
	mem := e.state().Peek(issue)
	e.say(mem.PlanPR, "nathan", "/nightbot approve")
	return issue, upstream
}

// upstreamOpen takes a request all the way to a draft pull request upstream.
func (e *forkEnv) upstreamOpen() (issue, upstreamPR int) {
	e.t.Helper()
	issue, _ = e.approved()
	e.run("build")
	mem := e.state().Peek(issue)
	pr, _ := e.fork.GetPR(mem.ForkPR)
	e.green(e.fork, pr.Head.SHA)
	e.sweep()
	mem = e.state().Peek(issue)
	if mem.UpstreamPR == 0 {
		e.t.Fatalf("no upstream pull request: %+v", mem)
	}
	return issue, mem.UpstreamPR
}

func TestFork_ARequestGoesThroughBothGatesAndIsTrackedUntilAPersonMerges(t *testing.T) {
	e := newForkEnv(t, nil)
	issue, upstream := e.request()
	if labels := e.queue.LabelsOf(issue); !slices.Contains(labels, "bot:plan") || !slices.Contains(labels, config.Requested) {
		t.Fatalf("issue labels: %v", labels)
	}
	if !e.upstream.HasComment(upstream, "https://github.com/me/harness/issues/1") {
		t.Fatalf("the upstream issue should say where it is taken up: %q", e.upstream.CommentsOn(upstream))
	}
	if st := e.state(); st.LinkedTo(upstream) != issue || st.Peek(issue).Intake != config.Requested {
		t.Fatalf("state: %+v", st.Peek(issue))
	}

	// The plan: read-only, from the upstream issue, up for approval as a draft pull request here.
	p, r := e.run("plan")
	if r.Outcome != job.Planned || p.Model != "opus" || p.Effort != "xhigh" {
		t.Fatalf("plan %+v result %+v", p, r)
	}
	if prompt, _ := os.ReadFile(filepath.Join(e.fakeDir, "plan-prompt")); !strings.Contains(string(prompt), "Tab goes nowhere.") ||
		!strings.Contains(string(prompt), "up/product") {
		t.Fatalf("the plan should read the upstream issue:\n%s", prompt)
	}
	mem := e.state().Peek(issue)
	planPR, _ := e.queue.GetPR(mem.PlanPR)
	if planPR == nil || !planPR.Draft || !slices.Contains(e.queue.LabelsOf(planPR.Number), "bot:plan-pr") ||
		!strings.Contains(planPR.Body, "Add hello.txt.") {
		t.Fatalf("plan pull request: %+v", planPR)
	}
	if file, _, err := e.queue.GetFile(mem.PlanBranch, mem.PlanPath); err != nil || !strings.Contains(string(file), "1. Write hello.txt.") {
		t.Fatalf("plan file %s: %v %q", mem.PlanPath, err, file)
	}
	if labels := e.queue.LabelsOf(issue); !slices.Contains(labels, "bot:awaiting-approval") || slices.Contains(labels, "bot:plan") {
		t.Fatalf("issue labels: %v", labels)
	}
	if p, why, _ := e.bot.Plan(PlanOptions{}); p != nil {
		t.Fatalf("something was built before gate 1: %+v (%s)", p, why)
	}

	// Gate 1: carol is trusted but no approver; nathan approves.
	e.say(planPR.Number, "carol", "/nightbot approve")
	if e.state().Peek(issue).ApprovedPlan != "" || !e.queue.HasComment(planPR.Number, "Only @boss or @nathan may approve") {
		t.Fatalf("a non-approver approved: %q", e.queue.CommentsOn(planPR.Number))
	}
	e.say(planPR.Number, "nathan", "/nightbot approve")
	mem = e.state().Peek(issue)
	if mem.ApprovedBy != "@nathan" || !strings.Contains(mem.ApprovedPlan, "Write hello.txt") ||
		!slices.Contains(e.queue.Merged, planPR.Number) || !slices.Contains(e.queue.LabelsOf(issue), "bot:build") {
		t.Fatalf("approval: %+v, merged %v, labels %v", mem, e.queue.Merged, e.queue.LabelsOf(issue))
	}

	// The build: on upstream's main, pushed to the fork, a pull request into the sandbox.
	p, r = e.run("build")
	if r.Outcome != job.Approved || p.Base != "main" || p.Upstream != "up/product" {
		t.Fatalf("build %+v result %+v", p, r)
	}
	if prompt, _ := os.ReadFile(filepath.Join(e.fakeDir, "build-prompt")); !strings.Contains(string(prompt), "## The approved plan") {
		t.Fatal("the build prompt should carry the approved plan")
	}
	pushed := git(t, e.forkGit, "rev-parse", p.Branch)
	if files := git(t, e.forkGit, "ls-tree", "-r", "--name-only", p.Branch); !strings.Contains(files, "hello.txt") || strings.Contains(files, ".github") {
		t.Fatalf("pushed tree: %s", files)
	}
	if _, err := (&gitx.Repo{Dir: e.upGit}).Git("rev-parse", "--verify", p.Branch); err == nil {
		t.Fatal("the bot pushed to upstream")
	}
	mem = e.state().Peek(issue)
	forkPR, _ := e.fork.GetPR(mem.ForkPR)
	if forkPR == nil || forkPR.Base.Ref != "main" || forkPR.Draft || mem.ApprovedHead != pushed || mem.Stage != state.StageForkCI {
		t.Fatalf("fork pull request %+v, state %+v", forkPR, mem)
	}

	// The fork's CI: nothing reported yet, then green, and the sandbox merge, then upstream.
	e.sweep()
	if mem = e.state().Peek(issue); mem.ForkMerged || !strings.Contains(mem.Waiting, "the fork's CI to report") {
		t.Fatalf("it should wait for the fork's CI: %+v", mem)
	}
	e.green(e.fork, pushed)
	e.sweep()
	mem = e.state().Peek(issue)
	if !mem.ForkMerged || !slices.Contains(e.fork.Merged, forkPR.Number) || mem.UpstreamPR == 0 {
		t.Fatalf("after green CI on the fork: %+v", mem)
	}
	if git(t, e.forkGit, "merge-base", "--is-ancestor", pushed, "main") != "" {
		t.Fatal("the sandbox should hold the change")
	}
	up, _ := e.upstream.GetPR(mem.UpstreamPR)
	if !up.Draft || up.HeadRepo() != "bot/product" || up.Head.Ref != p.Branch || up.Base.Ref != "main" ||
		!strings.Contains(up.Body, fmt.Sprintf("Closes #%d.", upstream)) || !strings.Contains(up.Body, "approved by @nathan") {
		t.Fatalf("upstream pull request: %+v", up)
	}

	// Red CI upstream: a fix with the failing job's log, pushed to the same branch.
	e.upstream.CheckRuns[pushed] = []*github.CheckRun{{ID: 501, Name: "test", Status: "completed", Conclusion: "failure", HTMLURL: "https://ci/501"}}
	e.upstream.Logs[501] = "FAIL: focus lands on body"
	e.sweep()
	if !slices.Contains(e.queue.LabelsOf(issue), "bot:revise") || e.state().Peek(issue).CIRepo != "upstream" {
		t.Fatalf("red upstream CI should queue a fix: %v %+v", e.queue.LabelsOf(issue), e.state().Peek(issue))
	}
	e.sweep() // the same red head again queues nothing more
	if e.state().Peek(issue).CIFixes != 1 {
		t.Fatalf("CI fixes: %d", e.state().Peek(issue).CIFixes)
	}
	p, r = e.run("revise")
	if r.Outcome != job.Approved {
		t.Fatalf("revise: %+v", r)
	}
	if prompt, _ := os.ReadFile(filepath.Join(e.fakeDir, "revise-prompt")); !strings.Contains(string(prompt), "FAIL: focus lands on body") {
		t.Fatal("the fix should read the failing job's log")
	}
	revised := git(t, e.forkGit, "rev-parse", p.Branch)
	if revised == pushed || git(t, e.forkGit, "merge-base", "--is-ancestor", pushed, revised) != "" {
		t.Fatal("the revision should move the branch forward")
	}
	if !e.upstream.HasComment(up.Number, "I pushed a revision") {
		t.Fatalf("upstream comments: %q", e.upstream.CommentsOn(up.Number))
	}

	// Green and clean: marked ready, then (once GitHub says it merges cleanly) reviewers asked.
	e.green(e.upstream, revised)
	e.sweep()
	e.sweep()
	up, _ = e.upstream.GetPR(up.Number)
	if mem = e.state().Peek(issue); up.Draft || !mem.Ready || mem.Stage != state.StageInReview ||
		!slices.Equal(e.upstream.Requested[up.Number], []string{"nathan"}) {
		t.Fatalf("ready for review: draft %v, %+v, requested %v", up.Draft, mem, e.upstream.Requested)
	}

	// Gate 2's feedback: a stranger's command does nothing, nathan's queues a revision.
	e.upstream.AddComment(up.Number, "stranger", "NONE", "/nightbot revise delete everything")
	e.sweep()
	if slices.Contains(e.queue.LabelsOf(issue), "bot:revise") {
		t.Fatal("a stranger's command upstream queued a revision")
	}
	id := e.upstream.AddComment(up.Number, "nathan", "CONTRIBUTOR", "/nightbot revise say hello twice")
	e.sweep()
	if !slices.Contains(e.queue.LabelsOf(issue), "bot:revise") || !slices.Contains(e.upstream.Reactions[id], "eyes") ||
		!strings.Contains(strings.Join(e.state().Peek(issue).Notes, "\n"), "say hello twice") {
		t.Fatalf("nathan's revise: %v %+v", e.queue.LabelsOf(issue), e.state().Peek(issue))
	}
	e.run("revise")

	// A person merges upstream: the item closes and the tracker counts it. The bot never merged.
	e.upstream.PRs[up.Number].State, e.upstream.PRs[up.Number].Merged = "closed", true
	e.sweep()
	st := e.state()
	if issueNow, _ := e.queue.GetIssue(issue); issueNow.State != "closed" || len(st.Shipped) != 1 || st.Peek(issue) != nil ||
		st.LinkedTo(upstream) != issue {
		t.Fatalf("after the merge: issue %s, shipped %v, item %+v", issueNow.State, st.Shipped, st.Peek(issue))
	}
	if len(e.upstream.Merged) != 0 {
		t.Fatal("the bot merged upstream")
	}
	status, _ := e.queue.ListIssues("bot:status", "open")
	if len(status) != 1 || !strings.Contains(status[0].Body, "## Pipeline") || !strings.Contains(status[0].Body, "1 in 7 days") {
		t.Fatalf("status issue: %+v", status)
	}
}

func TestFork_TheUpstreamClientNeverMerges(t *testing.T) {
	e := newForkEnv(t, nil)
	pr, _ := e.upstream.CreatePR("bot:nightbot/1-x", "main", "x", "x", false)
	if err := e.bot.Upstream.MergePR(pr.Number, "squash", ""); err != github.ErrMergeForbidden {
		t.Fatalf("merge upstream: %v", err)
	}
	rest := github.NewREST("up/product", "token")
	rest.NoMerge = true
	if err := rest.MergePR(1, "squash", "abc"); err != github.ErrMergeForbidden {
		t.Fatalf("the REST client merged: %v", err)
	}
}

func TestFork_AutoOpenTakesUpTwoAtATime(t *testing.T) {
	e := newForkEnv(t, nil)
	var pool []int
	for i := 0; i < 3; i++ {
		pool = append(pool, e.upstream.AddIssue(fmt.Sprintf("Pool issue %d", i), "Fix it.", "teacher", "harness-ok"))
	}
	e.upstream.AddIssue("Not in the pool", "No.", "teacher")
	e.sweep()
	opened, _ := e.queue.ListIssues(config.AutoOpened, "open")
	if len(opened) != 2 || !e.upstream.HasComment(pool[0], "taking this up") || !e.upstream.HasComment(pool[1], "taking this up") ||
		e.upstream.HasComment(pool[2], "taking this up") {
		t.Fatalf("auto-opened %d; upstream comments %q %q %q", len(opened), e.upstream.CommentsOn(pool[0]),
			e.upstream.CommentsOn(pool[1]), e.upstream.CommentsOn(pool[2]))
	}
	for _, issue := range opened {
		if !issue.HasLabel("bot:plan") {
			t.Fatalf("an auto-opened issue should be queued to plan: %v", e.queue.LabelsOf(issue.Number))
		}
	}
	e.sweep()
	if again, _ := e.queue.ListIssues(config.AutoOpened, "open"); len(again) != 2 {
		t.Fatalf("a second sweep opened more: %d", len(again))
	}
	e.queue.CloseIssue(opened[0].Number, "completed")
	e.sweep()
	if again, _ := e.queue.ListIssues(config.AutoOpened, "open"); len(again) != 2 || !e.upstream.HasComment(pool[2], "taking this up") {
		t.Fatalf("a closed one should make room for the third: %d", len(again))
	}
	e.sweep()
	all, _ := e.queue.ListIssues(config.AutoOpened, "all")
	if len(all) != 3 {
		t.Fatalf("an upstream issue was taken up twice: %d", len(all))
	}
}

func TestFork_TheUpstreamCapHoldsReadyWorkInTheFork(t *testing.T) {
	e := newForkEnv(t, func(cfg map[string]any) {
		cfg["upstream"].(map[string]any)["max_open_prs"] = 1
	})
	e.upstream.CreatePR("bot:nightbot/9-older", "main", "An older one", "x", false) // the bot's, opened earlier
	issue, _ := e.approved()
	e.run("build")
	pr, _ := e.fork.GetPR(e.state().Peek(issue).ForkPR)
	e.green(e.fork, pr.Head.SHA)
	e.sweep()
	mem := e.state().Peek(issue)
	if mem.UpstreamPR != 0 || !mem.ForkMerged || mem.Stage != state.StageHeld || !strings.Contains(mem.Waiting, "1 of 1") {
		t.Fatalf("the cap should hold it in the fork: %+v", mem)
	}
	e.upstream.PRs[1].State = "closed"
	e.sweep()
	if mem = e.state().Peek(issue); mem.UpstreamPR == 0 {
		t.Fatalf("a free slot should send it upstream: %+v", mem)
	}
}

func TestFork_AConflictUpstreamMergesUpstreamMainIn(t *testing.T) {
	e := newForkEnv(t, nil)
	issue, upPR := e.upstreamOpen()
	// Upstream's main moves on with its own hello.txt.
	git(t, e.helper, "fetch", "-q", "origin")
	git(t, e.helper, "checkout", "-q", "-B", "theirs", "origin/main")
	os.WriteFile(filepath.Join(e.helper, "hello.txt"), []byte("hello from main\n"), 0o644)
	git(t, e.helper, "add", "-A")
	git(t, e.helper, "commit", "-q", "-m", "main's hello")
	git(t, e.helper, "push", "-q", "origin", "HEAD:main")
	mainHead := git(t, e.helper, "rev-parse", "HEAD")
	e.upstream.PRs[upPR].MergeableState = "dirty"
	e.sweep()
	if mem := e.state().Peek(issue); !mem.MergeMain || !slices.Contains(e.queue.LabelsOf(issue), "bot:revise") {
		t.Fatalf("a conflict should queue a merge of upstream main: %+v", mem)
	}
	p, r := e.run("revise")
	if !p.MergeBase || r.Outcome != job.Approved {
		t.Fatalf("plan %+v result %+v", p, r)
	}
	if base := git(t, e.forkGit, "merge-base", p.Branch, mainHead); base != mainHead {
		t.Fatalf("the branch should now hold upstream's newest main: %s", base)
	}
	if text := git(t, e.forkGit, "show", p.Branch+":hello.txt"); strings.Contains(text, "<<<<<<<") {
		t.Fatalf("conflict markers: %q", text)
	}
}

func TestFork_ClosingThePlanTurnsItDownAndReviseRewritesIt(t *testing.T) {
	e := newForkEnv(t, nil)
	issue, _ := e.request()
	e.run("plan")
	planPR := e.state().Peek(issue).PlanPR
	e.say(planPR, "boss", "/nightbot revise cover the teacher view too")
	if !slices.Contains(e.queue.LabelsOf(issue), "bot:plan") {
		t.Fatalf("revise should queue a rewrite: %v", e.queue.LabelsOf(issue))
	}
	e.run("plan")
	if prompt, _ := os.ReadFile(filepath.Join(e.fakeDir, "plan-prompt")); !strings.Contains(string(prompt), "cover the teacher view too") ||
		!strings.Contains(string(prompt), "The plan so far") {
		t.Fatal("the rewrite should start from the plan and the feedback")
	}
	if prs, _ := e.queue.ListOpenPRs(); len(prs) != 1 || e.state().Peek(issue).PlanPR != planPR {
		t.Fatalf("a rewrite updates the one plan pull request: %d open", len(prs))
	}
	pr, _ := e.queue.GetPR(planPR)
	e.queue.ClosePR(planPR)
	e.bot.Handle("pull_request_target", &Event{Action: "closed", Sender: github.User{Login: "nathan"}, PullRequest: &struct {
		Number int            `json:"number"`
		Title  string         `json:"title"`
		Merged bool           `json:"merged"`
		Labels []github.Label `json:"labels"`
		User   github.User    `json:"user"`
		Head   struct {
			Ref string `json:"ref"`
		} `json:"head"`
	}{Number: planPR, Labels: pr.Labels}})
	if labels := e.queue.LabelsOf(issue); !slices.Contains(labels, "human") || slices.Contains(labels, "bot:awaiting-approval") {
		t.Fatalf("a closed plan should turn the work down: %v", labels)
	}
}

func TestFork_AStrangersRequestWaitsForATrustedPerson(t *testing.T) {
	e := newForkEnv(t, nil)
	n := e.queue.AddIssue("Do a thing", "https://github.com/up/product/issues/1", "stranger", config.Requested)
	issue, _ := e.queue.GetIssue(n)
	e.bot.Handle("issues", &Event{Action: "opened", Issue: issue, Sender: github.User{Login: "stranger"}})
	if slices.Contains(e.queue.LabelsOf(n), "bot:plan") || !e.queue.HasComment(n, "Someone on the trust list") {
		t.Fatalf("labels %v comments %q", e.queue.LabelsOf(n), e.queue.CommentsOn(n))
	}
	e.say(n, "carol", "/nightbot plan")
	if !slices.Contains(e.queue.LabelsOf(n), "bot:plan") {
		t.Fatal("a trusted person's plan command should queue it")
	}
}

func TestPlanGate_InOneRepositoryBuildWaitsForAnApprovedPlan(t *testing.T) {
	e := newEnv(t, func(cfg map[string]any) { cfg["plan_gate"] = true })
	n := e.gh.AddIssue("Add hello", "Please add hello.txt.", "boss", "difficulty:medium")
	id := e.gh.AddComment(n, "boss", "OWNER", "/nightbot build")
	e.bot.Handle("issue_comment", &Event{Action: "created", Issue: &github.Issue{Number: n},
		Comment: &github.Comment{ID: id, Body: "/nightbot build", User: github.User{Login: "boss"}, AuthorAssociation: "OWNER"}})
	if labels := e.gh.LabelsOf(n); !slices.Contains(labels, "bot:plan") || slices.Contains(labels, "bot:build") {
		t.Fatalf("build before a plan should queue the plan: %v", labels)
	}
	p, _ := e.run(PlanOptions{})
	if p.Action != "plan" {
		t.Fatalf("planned %s", p.Action)
	}
	planPR := e.state().Peek(n).PlanPR
	id = e.gh.AddComment(planPR, "boss", "OWNER", "/nightbot approve")
	e.bot.Handle("issue_comment", &Event{Action: "created", Issue: &github.Issue{Number: planPR, PullRequest: &struct {
		URL string `json:"url"`
	}{}}, Comment: &github.Comment{ID: id, Body: "/nightbot approve", User: github.User{Login: "boss"}, AuthorAssociation: "OWNER"}})
	if !slices.Contains(e.gh.LabelsOf(n), "bot:build") || !slices.Contains(e.gh.Merged, planPR) {
		t.Fatalf("approval: labels %v merged %v comments %q", e.gh.LabelsOf(n), e.gh.Merged, e.gh.CommentsOn(planPR))
	}
	p, r := e.run(PlanOptions{})
	if p.Action != "build" || r.Outcome != job.Approved || !strings.Contains(p.PlanText, "Write hello.txt") {
		t.Fatalf("build %+v result %+v", p, r)
	}
	pr, _ := e.gh.FindPR(p.Branch)
	if pr == nil || e.gh.AutoMerge[pr.Number] == "" {
		t.Fatalf("the approved build should ship as usual: %+v", pr)
	}
}

func TestBaseline_ACheckRedOnTheBaseDoesNotBlock(t *testing.T) {
	e := newEnv(t, func(cfg map[string]any) {
		cfg["baseline_checks"] = true
		cfg["checks"] = []map[string]any{{"name": "hello", "run": "test -f hello.txt"}, {"name": "needs a database", "run": "false"}}
	})
	e.gh.AddIssue("Add hello", "Please add hello.txt.", "boss", "bot:build", "difficulty:medium")
	_, r := e.run(PlanOptions{})
	if r.Outcome != job.Approved || !slices.Equal(r.BaselineRed, []string{"hello", "needs a database"}) || !r.ChecksGreen {
		t.Fatalf("a check red on the base blocked the change: %+v", r)
	}
	if calls := e.calls(); slices.Contains(calls, "# Revise #1") {
		t.Fatalf("the builder was sent to fix a check that fails without its change: %q", calls)
	}
}

func TestFork_AStaleBuildLabelDoesNotStopARevision(t *testing.T) {
	e := newForkEnv(t, nil)
	issue, _ := e.upstreamOpen()
	e.queue.AddLabels(issue, "bot:build") // a person's `/nightbot build` on a built item, from before
	e.bot.queueForkRevision(issue, "@boss asked: say it twice", false)
	if p, why, _ := e.bot.Plan(PlanOptions{}); p == nil || p.Action != "revise" {
		t.Fatalf("the revision was not scheduled: %+v %s", p, why)
	}
	if reply, _ := e.bot.queueBuild(issue, false); !strings.Contains(reply, "built already") {
		t.Fatalf("build on a built item: %q", reply)
	}
}

func TestFork_ACIFixThatChangesNothingGoesToAPerson(t *testing.T) {
	e := newForkEnv(t, nil)
	issue, upPR := e.upstreamOpen()
	head := e.upstream.PRs[upPR].Head.SHA
	e.upstream.CheckRuns[head] = []*github.CheckRun{{ID: 7, Name: "test", Status: "completed", Conclusion: "failure"}}
	e.sweep()
	os.WriteFile(filepath.Join(e.fakeDir, "mode"), []byte("nochange"), 0o644)
	e.run("revise")
	if labels := e.queue.LabelsOf(issue); !slices.Contains(labels, "human") || slices.Contains(labels, "bot:revise") {
		t.Fatalf("a revision that changed nothing should go to a person: %v", labels)
	}
	if e.upstream.HasComment(upPR, "I pushed a revision") {
		t.Fatal("upstream was told of a revision that changed nothing")
	}
}

func TestFork_ARevisionDraftsTheUpstreamPullRequestAndAnUnapprovedOneSaysSo(t *testing.T) {
	e := newForkEnv(t, nil)
	issue, upPR := e.upstreamOpen()
	e.green(e.upstream, e.upstream.PRs[upPR].Head.SHA)
	e.sweep()
	e.sweep()
	if !e.state().Peek(issue).Ready {
		t.Fatalf("it should be ready for review: %+v", e.state().Peek(issue))
	}
	block := `{"verdict": "request_changes", "summary": "no", "findings": [{"severity": "blocking", "problem": "wrong"}]}`
	os.WriteFile(filepath.Join(e.fakeDir, "verdicts"), []byte(strings.Repeat(block+"\n", 6)), 0o644)
	e.bot.queueForkRevision(issue, "@boss asked: change it", false)
	e.run("revise")
	pr, _ := e.upstream.GetPR(upPR)
	if !pr.Draft || e.state().Peek(issue).Ready || !e.upstream.HasComment(upPR, "did not pass my own review") {
		t.Fatalf("an unreviewed head is up for review: draft %v, ready %v", pr.Draft, e.state().Peek(issue).Ready)
	}
	e.green(e.upstream, pr.Head.SHA)
	e.sweep()
	if pr, _ = e.upstream.GetPR(upPR); !pr.Draft {
		t.Fatal("a head the review did not approve was marked ready")
	}
}

func TestFork_UpstreamCommandsCountOnlyFromTheTrustList(t *testing.T) {
	e := newForkEnv(t, func(cfg map[string]any) { cfg["trust_collaborators"] = true })
	issue, upPR := e.upstreamOpen()
	e.upstream.AddComment(upPR, "upstream-member", "MEMBER", "/nightbot revise delete the tests")
	e.sweep()
	if slices.Contains(e.queue.LabelsOf(issue), "bot:revise") {
		t.Fatal("an upstream member off the trust list instructed the bot")
	}
}

func TestPlanGate_APlanPullRequestWithOtherFilesIsNotMerged(t *testing.T) {
	e := newForkEnv(t, nil)
	issue, _ := e.request()
	e.run("plan")
	planPR := e.state().Peek(issue).PlanPR
	e.queue.ChangedFiles = map[int][]string{planPR: {e.state().Peek(issue).PlanPath, ".nightbot/trust.txt"}}
	e.say(planPR, "nathan", "/nightbot approve")
	if slices.Contains(e.queue.Merged, planPR) || e.state().Peek(issue).ApprovedPlan == "" {
		t.Fatalf("merged %v; approved %q", e.queue.Merged, e.state().Peek(issue).ApprovedPlan)
	}
	if pr, _ := e.queue.GetPR(planPR); pr.State != "closed" || !e.queue.HasComment(planPR, "changes more than the plan") {
		t.Fatalf("the plan's pull request should be closed with why: %+v", pr)
	}
}

func TestFork_TheRequestTemplatesTwoEventsTakeItInOnce(t *testing.T) {
	e := newForkEnv(t, nil)
	issue, upstream := e.request()
	opened, _ := e.queue.GetIssue(issue)
	e.bot.Handle("issues", &Event{Action: "labeled", Issue: opened, Label: &github.Label{Name: config.Requested},
		Sender: github.User{Login: "boss"}})
	e.say(issue, "boss", "/nightbot plan")
	if n := len(e.upstream.CommentsOn(upstream)); n != 1 {
		t.Fatalf("the upstream issue got %d comments", n)
	}
	if n := strings.Count(strings.Join(e.queue.CommentsOn(issue), "\n"), "Queued:"); n != 1 {
		t.Fatalf("%d Queued replies: %q", n, e.queue.CommentsOn(issue))
	}
}

func TestFork_SkippedChecksAreNotGreenAndOthersPullRequestsDoNotUseTheCap(t *testing.T) {
	e := newForkEnv(t, func(cfg map[string]any) { cfg["upstream"].(map[string]any)["max_open_prs"] = 1 })
	release, _ := e.upstream.CreatePR("release-please", "main", "chore: release", "x", false)
	e.upstream.PRs[release.Number].User.Login = "github-actions[bot]"
	issue, _ := e.approved()
	e.run("build")
	pr, _ := e.fork.GetPR(e.state().Peek(issue).ForkPR)
	e.fork.CheckRuns[pr.Head.SHA] = []*github.CheckRun{{Name: "CI", Status: "completed", Conclusion: "skipped"}}
	e.sweep()
	if e.state().Peek(issue).ForkMerged {
		t.Fatal("checks that were all skipped merged it into the sandbox")
	}
	e.green(e.fork, pr.Head.SHA)
	e.sweep()
	if mem := e.state().Peek(issue); mem.UpstreamPR == 0 {
		t.Fatalf("another account's pull request used the cap: %+v", mem)
	}
}

func TestConfig_AnUpgradedInstallWithoutThePlanPromptLoads(t *testing.T) {
	e := newEnv(t, nil)
	os.Remove(filepath.Join(e.workspace, config.Dir, "prompts", "plan.md"))
	if _, err := config.Load(e.workspace); err != nil {
		t.Fatalf("an install from before the plan gate stopped loading: %v", err)
	}
}

func TestFork_SetupTurnsOnTheForksActionsAndTheStatusIssueIsPinned(t *testing.T) {
	e := newForkEnv(t, nil)
	e.fork.ActionsOff = true
	if err := e.bot.Setup(true); err != nil {
		t.Fatal(err)
	}
	if e.fork.ActionsOff || e.fork.Repo["delete_branch_on_merge"] != false {
		t.Fatalf("the fork's settings: actions off %v, repo %v", e.fork.ActionsOff, e.fork.Repo)
	}
	e.sweep()
	status, _ := e.queue.ListIssues("bot:status", "open")
	if len(status) != 1 || !slices.Contains(e.queue.Pinned, status[0].Number) {
		t.Fatalf("the status issue should be pinned: %v %v", status, e.queue.Pinned)
	}
}
