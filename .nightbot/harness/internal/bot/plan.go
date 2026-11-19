package bot

import (
	"errors"
	"fmt"
	"sort"
	"strings"

	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/job"
	"nightbot/internal/prompts"
	"nightbot/internal/sched"
	"nightbot/internal/state"
)

// PlanOptions narrow what the planner may pick.
type PlanOptions struct {
	Item  int    // one issue or pull request (0: any)
	Mode  string // one action (auto: any)
	Force bool   // outside the providers' hours
}

// threadLimit is how much of an item's text goes to the model, and upstreamThreadLimit how much
// of an upstream issue's, which the state keeps a copy of.
const (
	threadLimit         = 40000
	upstreamThreadLimit = 20000
)

// Plan picks the next item and provider, claims them and returns the plan, or nil and why not.
func (b *Bot) Plan(opts PlanOptions) (*job.Plan, string, error) {
	if b.HaltFile() {
		return nil, "halted: .nightbot/HALT is committed", nil
	}
	var plan *job.Plan
	var why string
	_, err := b.Store.Update("plan", func(st *state.State) error {
		plan, why = nil, ""
		st.Secrets = sortedKeys(b.Secrets)
		st.Prune(b.now())
		if st.Halted {
			why = "halted by " + st.HaltedBy
			return nil
		}
		snap, err := b.snapshotOf(st, opts.Force)
		if err != nil {
			return err
		}
		as := sched.Next(b.Cfg, st, snap.Queue, snap.Avail, opts.Item, opts.Mode)
		if as == nil {
			why = "nothing a free provider can take"
			for _, a := range snap.Assignments {
				if (opts.Item == 0 || a.Item.Number == opts.Item) && a.Waits != "" {
					why = fmt.Sprintf("#%d (%s) waits: %s", a.Item.Number, a.Item.Action, a.Waits)
					break
				}
			}
			if len(snap.Queue) == 0 {
				why = "the queue is empty"
			}
			return nil
		}
		plan = b.planFor(st, as)
		if st.Running == nil {
			st.Running = map[string]*state.Claim{}
		}
		st.Running[as.Item.Key()] = &state.Claim{Number: plan.Number, Action: plan.Action, Provider: plan.Provider,
			Model: plan.Model, RunID: b.RunID, Started: b.now(), QueryID: plan.QueryID}
		return nil
	})
	if err != nil || plan == nil {
		return nil, why, err
	}
	if err := b.fill(plan); err != nil {
		b.release(state.ClaimKey(plan.Number, plan.QueryID), plan.Number, plan.RunID)
		var refused *skip
		if errors.As(err, &refused) {
			// Not an outage: the item cannot be worked, so it leaves the queue with a word why.
			b.GH.RemoveLabel(plan.Number, refused.label)
			b.say(plan.Number, refused.why+".")
			return nil, refused.why, nil
		}
		return nil, "", err
	}
	if plan.Action != sched.Ask {
		if err := b.GH.AddLabels(plan.Number, b.Cfg.Label("building")); err != nil {
			b.logf("label #%d: %v", plan.Number, err)
		}
	}
	return plan, "", nil
}

func sortedKeys(m map[string]bool) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// planFor is the plan's skeleton from the assignment and the state; fill reads GitHub for the rest.
func (b *Bot) planFor(st *state.State, as *sched.Assignment) *job.Plan {
	it, prov, seat := as.Item, as.Provider, as.Seat
	p := &job.Plan{Action: it.Action, Number: it.Number, Title: it.Title, IsPR: it.IsPR,
		Difficulty: it.Difficulty, Rated: it.Rated, Provider: prov.ID, CLI: prov.CLI, Model: seat.Model,
		Effort: seat.Effort, Tier: seat.Tier, RunsOn: prov.RunsOn, Login: prov.Login, Base: b.Cfg.DefaultBranch,
		ReviewRounds: b.Cfg.ReviewRounds[it.Difficulty], RunID: b.RunID, RunURL: b.RunURL, Created: b.now()}
	if prov.Login == "secret" {
		p.Secret = prov.Secret
	}
	if p.ReviewRounds == 0 {
		p.ReviewRounds = b.Cfg.ReviewRounds["medium"]
	}
	if u := b.Cfg.Upstream; b.Cfg.Forked() {
		p.Upstream, p.Fork, p.Base = u.Repo, u.Fork, u.DefaultBranch
	}
	if mem := st.Peek(it.Number); mem != nil {
		p.Issue = mem.Issue
		p.Branch = mem.Branch
		p.UpstreamIssue = mem.UpstreamIssue
		switch it.Action {
		case sched.Revise:
			p.Request = strings.Join(mem.Notes, "\n\n")
			p.CIRun = mem.CIRun
			p.MergeBase = mem.MergeMain
			p.CIRepo, p.CIJobs = mem.CIRepo, mem.CIJobs
			p.PlanText = mem.ApprovedPlan
			if b.Cfg.Forked() {
				p.PR = mem.UpstreamPR
				if p.PR == 0 {
					p.PR = mem.ForkPR
				}
			}
		case sched.Plan:
			p.Request = strings.Join(mem.Notes, "\n\n")
			p.PlanText = mem.Plan
		case sched.Build:
			p.PlanText = mem.ApprovedPlan
		}
		if it.Action == sched.Build || it.Action == sched.Revise {
			p.UpstreamThread = mem.UpstreamThread
		}
		if parent := st.Peek(mem.Issue); mem.Issue != 0 && parent != nil && parent.ApprovedPlan != "" && p.PlanText == "" {
			p.PlanText = parent.ApprovedPlan // a pull request's revision or audit, in one repository
		}
	}
	switch it.Action {
	case sched.Ask:
		p.QueryID, p.CommentID, p.Asker, p.Request = it.Query.ID, it.Query.CommentID, it.Query.Author, it.Query.Text
		p.Branch = fmt.Sprintf("%sask-%s", b.Cfg.BranchPrefix, it.Query.ID)
	case sched.Triage:
		p.Branch = fmt.Sprintf("%striage-%d", b.Cfg.BranchPrefix, it.Number)
	case sched.Plan:
		p.Branch = b.planBranch(it.Number) // here, for the plan's pull request; the work job pushes nothing
	}
	return p
}

// planBranch is the branch of an item's plan in this repository.
func (b *Bot) planBranch(number int) string {
	return fmt.Sprintf("%splan-%d", b.Cfg.BranchPrefix, number)
}

// fill reads the item's text, and its branch for builds, from GitHub.
func (b *Bot) fill(p *job.Plan) error {
	issue, err := b.GH.GetIssue(p.Number)
	if err != nil {
		return err
	}
	p.Title = issue.Title
	thread, err := b.thread(issue)
	if err != nil {
		return err
	}
	if p.IsPR && p.Issue != 0 {
		if parent, err := b.GH.GetIssue(p.Issue); err == nil {
			parentThread, err := b.thread(parent)
			if err == nil {
				thread = parentThread + "\n\n" + thread
			}
		}
	}
	if p.UpstreamIssue != 0 && b.Upstream != nil && (p.Action == sched.Plan || p.Action == sched.Ask) {
		upstream, err := b.upstreamThread(p.UpstreamIssue)
		if err != nil {
			return err
		}
		p.UpstreamThread = prompts.Truncate(upstream, upstreamThreadLimit)
	}
	if p.UpstreamThread != "" {
		thread += "\n\n" + p.UpstreamThread
	}
	p.Thread = prompts.Truncate(thread, threadLimit)
	switch p.Action {
	case sched.Build:
		if p.Branch == "" {
			p.Branch = b.BranchFor(p.Number, p.Title)
		}
		exists, err := b.workGH().BranchExists(p.Branch)
		if err != nil {
			return err
		}
		p.Continue = exists
	case sched.Revise:
		if b.Cfg.Forked() {
			return b.fillForkRevision(p)
		}
		pr, err := b.GH.GetPR(p.Number)
		if err != nil {
			return err
		}
		if why := b.notOurs(pr); why != "" {
			return &skip{fmt.Sprintf("#%d cannot be revised: %s", p.Number, why), b.Cfg.Label("revise")}
		}
		p.Branch, p.PR, p.Head = pr.Head.Ref, pr.Number, pr.Head.SHA
	case sched.Review:
		pr, err := b.GH.GetPR(p.Number)
		if err != nil {
			return err
		}
		p.PR, p.Head, p.Untrusted = pr.Number, pr.Head.SHA, !b.trustedPR(pr)
		p.Branch = fmt.Sprintf("%sreview-%d", b.Cfg.BranchPrefix, pr.Number) // local only, never pushed
	case sched.Plan:
		b.fillPlan(p)
	case sched.Ask:
		if p.IsPR && b.Cfg.Forked() {
			p.IsPR = false // the question is about the product's code, not this pull request's
		}
		if p.IsPR {
			pr, err := b.GH.GetPR(p.Number)
			if err != nil {
				return err
			}
			p.Head, p.Untrusted = pr.Head.SHA, !b.trustedPR(pr)
		}
	}
	return nil
}

// notOurs says why the bot may not push to a pull request's branch, or "" when it may: only the
// bot's own pull requests, from a branch of its own in this repository.
func (b *Bot) notOurs(pr *github.PR) string {
	switch ref := pr.Head.Ref; {
	case !b.isBot(pr.User.Login):
		return "it is not the bot's own pull request"
	case !strings.EqualFold(pr.HeadRepo(), b.Cfg.Repo):
		return "its branch is in another repository"
	case !strings.HasPrefix(ref, b.Cfg.BranchPrefix) || ref == b.Cfg.DefaultBranch || ref == b.Cfg.StateBranch:
		return fmt.Sprintf("its branch %q is not one of the bot's (%s…)", ref, b.Cfg.BranchPrefix)
	}
	return ""
}

// trustedPR says whether a pull request's code may run in the model job: the bot's own, or a
// trusted person's branch in this repository. Any other is reviewed from its diff alone.
func (b *Bot) trustedPR(pr *github.PR) bool {
	if !strings.EqualFold(pr.HeadRepo(), b.Cfg.Repo) {
		return false
	}
	return b.isBot(pr.User.Login) || b.Trusted(pr.User.Login, pr.AuthorAssociation)
}

// skip is an item the planner claimed but must not work, and the queue label that put it there.
type skip struct{ why, label string }

func (s *skip) Error() string { return s.why }

// thread is an issue's text and its trusted people's comments, each fenced as data.
func (b *Bot) thread(issue *github.Issue) (string, error) {
	kind := "Issue"
	if issue.IsPR() {
		kind = "Pull request"
	}
	// The title and the text are the author's, and an author off the trust list can change them
	// after a trusted person queued the item, so a stranger's text is left out: the person who
	// queues it says in a comment of their own what to build.
	author := issue.User.Login
	body := issue.Body
	if !b.isBot(author) && !b.Trusted(author, issue.AuthorAssociation) {
		body = fmt.Sprintf("(Left out: @%s, who wrote this %s, is not on the trust list. The trusted people's comments below say what to do.)",
			author, strings.ToLower(kind))
	}
	var parts []string
	parts = append(parts, fmt.Sprintf("%s #%d", kind, issue.Number),
		prompts.Fence(fmt.Sprintf("the title of %s #%d", strings.ToLower(kind), issue.Number), issue.Title),
		prompts.Fence(fmt.Sprintf("%s #%d by @%s", strings.ToLower(kind), issue.Number, author), body))
	comments, err := b.GH.ListComments(issue.Number)
	if err != nil {
		return "", err
	}
	for _, c := range comments {
		if b.isBot(c.User.Login) || strings.Contains(c.Body, Marker) || !b.Trusted(c.User.Login, c.AuthorAssociation) {
			continue
		}
		parts = append(parts, prompts.Fence(fmt.Sprintf("comment by @%s on %s", c.User.Login,
			c.CreatedAt.Format("2006-01-02")), c.Body))
	}
	return strings.Join(parts, "\n\n"), nil
}

// Outputs are the plan's fields the workflow's later jobs read, which the model job cannot touch.
func Outputs(cfg *config.Config, p *job.Plan) map[string]string {
	if p == nil {
		return map[string]string{"action": "none"}
	}
	cli := cfg.CLIs[p.CLI]
	return map[string]string{"action": p.Action, "number": fmt.Sprint(p.Number), "provider": p.Provider,
		"runs_on": p.RunsOn, "secret": p.Secret, "cli": p.CLI,
		"hosted": fmt.Sprint(config.GitHubHosted(p.RunsOn)), "install": cli.Install,
		"product": p.Upstream, "fork": p.Fork}
}
