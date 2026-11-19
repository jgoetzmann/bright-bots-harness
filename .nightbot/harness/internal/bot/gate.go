package bot

// The plan gate: an item is planned before it is built. The plan goes up as a draft pull
// request adding plans/<number>-<slug>.md, an approver approves it with a comment or a review,
// and only then is the item queued to build, against the plan as approved (gate 1).

import (
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/job"
	"nightbot/internal/prompts"
	"nightbot/internal/state"
)

// planDocLimit keeps a plan inside what a pull request body holds.
const planDocLimit = 60000

// planPath is where an item's plan is written in this repository.
func (b *Bot) planPath(number int, title string) string {
	slug := strings.TrimPrefix(b.BranchFor(number, title), b.Cfg.BranchPrefix)
	return b.Cfg.PlansDir + "/" + slug + ".md"
}

// intake queues an issue to be planned, and in fork mode links the upstream issue it names (in
// text or in its body) and says so there once. how is the intake label (requested or
// auto-opened). It returns the reply and whether a run may need starting.
func (b *Bot) intake(issue *github.Issue, how, text string) (string, bool) {
	upstream := 0
	if b.Cfg.Forked() {
		upstream = b.upstreamIssueIn(text)
		if upstream == 0 {
			upstream = b.upstreamIssueIn(issue.Body)
		}
	}
	st, _, err := b.Store.Load()
	if err != nil {
		return "I could not read my state: " + err.Error(), false
	}
	if mem := st.Peek(issue.Number); mem != nil && mem.UpstreamIssue != 0 && upstream == 0 {
		upstream = mem.UpstreamIssue
	}
	if other := st.LinkedTo(upstream); upstream != 0 && other != 0 && other != issue.Number {
		if theirs, err := b.GH.GetIssue(other); err == nil && theirs.State == "open" {
			return fmt.Sprintf("%s#%d is already #%d here; I left this one alone.", b.Cfg.Upstream.Repo, upstream, other), false
		}
	}
	first := upstream != 0 && st.LinkedTo(upstream) != issue.Number
	if _, err := b.Store.Update(fmt.Sprintf("intake #%d", issue.Number), func(s *state.State) error {
		mem := s.Item(issue.Number)
		if mem.Intake == "" {
			mem.Intake = how
		}
		if upstream != 0 {
			mem.UpstreamIssue = upstream
			s.Link(upstream, issue.Number)
		}
		mem.SetStage(state.StagePlanning, b.now())
		mem.Waiting = "a plan run"
		return nil
	}); err != nil {
		return "I could not queue it: " + err.Error(), false
	}
	labels := []string{b.Cfg.Label("plan")}
	if !issue.HasLabel(config.Requested) && !issue.HasLabel(config.AutoOpened) {
		labels = append(labels, how)
	}
	if err := b.GH.AddLabels(issue.Number, labels...); err != nil {
		return "I could not queue it: " + err.Error(), false
	}
	if first {
		b.tellUpstream(upstream, issue.Number, how)
	}
	reply := fmt.Sprintf("Queued: I write a plan for %s to approve, and build nothing before that.", b.Cfg.ApproverList())
	if upstream != 0 {
		reply += fmt.Sprintf(" It answers %s.", b.upstreamRef(upstream))
	} else if b.Cfg.Forked() {
		reply += fmt.Sprintf(" It names no %s issue, so the plan answers this issue's text.", b.Cfg.Upstream.Repo)
	}
	return reply, true
}

// queuePlan is the `plan` command, and `build` on an item with no approved plan.
func (b *Bot) queuePlan(number int, isPR bool, text string) (string, bool) {
	if isPR {
		return "`plan` works on issues.", false
	}
	issue, err := b.GH.GetIssue(number)
	if err != nil {
		return "I could not read this issue: " + err.Error(), false
	}
	if issue.HasLabel("human") {
		return "This carries `human`, which keeps me off it; take the label off first.", false
	}
	if st, _, err := b.Store.Load(); err == nil {
		if mem := st.Peek(number); mem != nil && mem.ApprovedPlan != "" {
			return fmt.Sprintf("Its plan is approved already (by %s); `%s build` builds it, `%s revise <change>` changes the work.",
				mem.ApprovedBy, b.Cfg.CommandPrefix, b.Cfg.CommandPrefix), false
		}
	}
	return b.intake(issue, config.Requested, text)
}

// planItem is the item a plan command names: the issue itself, or the issue a plan's pull
// request is for.
func (b *Bot) planItem(st *state.State, number int) (int, *state.Item) {
	if mem := st.Peek(number); mem != nil && mem.Issue != 0 {
		if parent := st.Peek(mem.Issue); parent != nil && parent.PlanPR == number {
			return mem.Issue, parent
		}
	}
	return number, st.Peek(number)
}

// deliverPlan puts a plan up for approval: the file on the plan's branch here, and its draft
// pull request, opened the first time and rewritten after.
func (b *Bot) deliverPlan(p *job.Plan, r *job.Result) error {
	switch r.Outcome {
	case job.RateLimited:
		return nil // the item stays queued to be planned
	case job.Planned:
	default:
		b.fail(p.Number, "the plan failed: "+firstNonEmpty(r.Error, "it gave no plan")+b.runLink(p))
		return nil
	}
	st, _, err := b.Store.Load()
	if err != nil {
		return err
	}
	issue := p.Number
	branch, path := b.planBranch(issue), b.planPath(issue, p.Title)
	if mem := st.Peek(issue); mem != nil && mem.PlanPath != "" {
		path = mem.PlanPath
	}
	exists, err := b.GH.BranchExists(branch)
	if err != nil {
		return err
	}
	if !exists {
		sha, err := b.GH.BranchSHA(b.Cfg.DefaultBranch)
		if err != nil {
			return err
		}
		if err := b.GH.CreateBranch(branch, sha); err != nil {
			return fmt.Errorf("the plan's branch: %w", err)
		}
	}
	_, sha, err := b.GH.GetFile(branch, path)
	if err != nil && !errors.Is(err, github.ErrNotFound) {
		return err
	}
	doc := b.planDocument(p, r.Plan)
	if err := b.GH.PutFile(branch, path, []byte(doc), sha, fmt.Sprintf("Plan for #%d", issue)); err != nil {
		return fmt.Errorf("the plan's file: %w", err)
	}
	title := fmt.Sprintf("Plan: %s (#%d)", p.Title, issue)
	body := b.planBody(issue, doc)
	pr, err := b.GH.FindPR(branch)
	if err != nil {
		return err
	}
	rewrite := pr != nil
	if pr == nil {
		if pr, err = b.GH.CreatePR(branch, b.Cfg.DefaultBranch, title, body, true); err != nil {
			return err
		}
	} else if err := b.GH.UpdatePR(pr.Number, title, body); err != nil {
		return err
	}
	b.GH.AddLabels(pr.Number, b.Cfg.Label("plan-pr"))
	b.GH.RemoveLabel(issue, b.Cfg.Label("plan"))
	b.GH.AddLabels(issue, b.Cfg.Label("awaiting-approval"))
	if _, err := b.Store.Update(fmt.Sprintf("plan of #%d", issue), func(s *state.State) error {
		mem := s.Item(issue)
		mem.PlanPR, mem.PlanBranch, mem.PlanPath, mem.Plan = pr.Number, branch, path, doc
		mem.ApprovedPlan, mem.ApprovedBy, mem.Notes = "", "", nil
		mem.LastProvider = p.Provider
		mem.SetStage(state.StageApproval, b.now())
		mem.Waiting = "approval by " + b.Cfg.ApproverList()
		s.Item(pr.Number).Issue = issue
		return nil
	}); err != nil {
		return err
	}
	how := fmt.Sprintf("%s: comment `%s approve` on it (or approve it in a review) to start the build, "+
		"`%s revise <what to change>` to have it rewritten, or close it to turn the work down.",
		b.Cfg.ApproverList(), b.Cfg.CommandPrefix, b.Cfg.CommandPrefix)
	if rewrite {
		b.say(pr.Number, "I rewrote the plan as asked."+b.runLink(p))
		b.say(issue, fmt.Sprintf("The plan in #%d is rewritten and waits for approval again. %s", pr.Number, how))
	} else {
		b.say(issue, fmt.Sprintf("The plan is up for approval in #%d. %s%s", pr.Number, how, b.runLink(p)))
	}
	return nil
}

// planDocument is the plan's file: where it comes from, then the model's plan.
func (b *Bot) planDocument(p *job.Plan, plan string) string {
	var head strings.Builder
	fmt.Fprintf(&head, "# Plan: %s\n\n", p.Title)
	fmt.Fprintf(&head, "- For: %s\n", b.issueURL(p.Number))
	if p.UpstreamIssue != 0 {
		fmt.Fprintf(&head, "- Answers: %s\n", b.upstreamIssueURL(p.UpstreamIssue))
	}
	fmt.Fprintf(&head, "- Written by %s on %s, against `%s`\n\n", p.Model, p.Provider, p.Base)
	return prompts.Truncate(head.String()+strings.TrimSpace(plan)+"\n", planDocLimit-2000)
}

func (b *Bot) planBody(issue int, doc string) string {
	return fmt.Sprintf("%s\n%s\n\n---\n\n<sub>The plan for #%d. Nothing is built until %s approves it: comment `%s approve`, "+
		"or approve this pull request in a review. Edit the file here before approving if you like: the build follows "+
		"the plan as it stands when it is approved. `%s revise <what to change>` has it rewritten; closing this pull "+
		"request turns the work down.</sub>", Marker, doc, issue, b.Cfg.ApproverList(), b.Cfg.CommandPrefix, b.Cfg.CommandPrefix)
}

// approve is gate 1: the plan as it stands on its pull request becomes the plan the build
// follows, the pull request merges, and the item is queued to build.
func (b *Bot) approve(number int, author string) (string, bool) {
	if !b.Cfg.PlanGate {
		return "There is no plan gate here: `" + b.Cfg.CommandPrefix + " build` builds.", false
	}
	if !b.Cfg.Approver(author) {
		return fmt.Sprintf("Only %s may approve a plan.", b.Cfg.ApproverList()), false
	}
	st, _, err := b.Store.Load()
	if err != nil {
		return "I could not read my state: " + err.Error(), false
	}
	issue, mem := b.planItem(st, number)
	if mem == nil || mem.PlanPR == 0 {
		return "There is no plan to approve here yet; `" + b.Cfg.CommandPrefix + " plan` writes one.", false
	}
	if mem.ApprovedPlan != "" {
		return fmt.Sprintf("Its plan is approved already, by %s.", mem.ApprovedBy), false
	}
	if mem.Stage == state.StagePlanning {
		return "The plan is being rewritten; approve it once the new one is up.", false
	}
	pr, err := b.GH.GetPR(mem.PlanPR)
	if err != nil {
		return "I could not read the plan's pull request: " + err.Error(), false
	}
	if pr.State != "open" {
		return fmt.Sprintf("The plan's pull request #%d is closed; `%s plan` writes a new one.", pr.Number, b.Cfg.CommandPrefix), false
	}
	text, _, err := b.GH.GetFile(pr.Head.SHA, mem.PlanPath)
	if err != nil {
		return "I could not read the plan at the pull request's head: " + err.Error(), false
	}
	if _, err := b.Store.Update(fmt.Sprintf("approve #%d", issue), func(s *state.State) error {
		m := s.Item(issue)
		m.ApprovedPlan, m.ApprovedBy, m.ApprovedAt = string(text), "@"+author, b.now()
		m.Failures = 0
		m.SetStage(state.StageApproved, b.now())
		m.Waiting = "a build run"
		return nil
	}); err != nil {
		return "I could not record the approval: " + err.Error(), false
	}
	b.GH.RemoveLabel(issue, b.Cfg.Label("awaiting-approval"))
	b.GH.AddLabels(issue, b.Cfg.Label("build"))
	merged := ""
	if err := b.GH.MarkReady(pr); err != nil {
		b.logf("ready #%d: %v", pr.Number, err)
	}
	err = b.GH.MergePR(pr.Number, b.Cfg.MergeMethod, pr.Head.SHA)
	if err != nil {
		b.Sleep(3 * time.Second) // GitHub may not have taken the draft's change to ready yet
		err = b.GH.MergePR(pr.Number, b.Cfg.MergeMethod, pr.Head.SHA)
	}
	if err != nil {
		b.GH.ClosePR(pr.Number)
		merged = fmt.Sprintf("\n\nI could not merge the plan's pull request (%v), so I closed it: the approved plan stays on "+
			"`%s`, and the build follows it all the same.", err, mem.PlanBranch)
	}
	reply := fmt.Sprintf("Approved by @%s (gate 1), as the plan stands at %.12s. %s%s", author, pr.Head.SHA, b.position(issue), merged)
	if number != issue {
		b.say(issue, fmt.Sprintf("@%s approved the plan in #%d; queued to build. %s", author, pr.Number, b.position(issue)))
	}
	return reply, true
}

// reject turns an item's plan down: the plan's pull request closes and the item is a person's.
func (b *Bot) reject(number int, author, why string) (string, bool) {
	if !b.Cfg.Approver(author) {
		return fmt.Sprintf("Only %s may turn a plan down.", b.Cfg.ApproverList()), false
	}
	st, _, err := b.Store.Load()
	if err != nil {
		return "I could not read my state: " + err.Error(), false
	}
	issue, mem := b.planItem(st, number)
	if mem == nil || mem.PlanPR == 0 {
		return "There is no plan here to turn down.", false
	}
	if mem.ApprovedPlan != "" {
		return "Its plan is approved already; `" + b.Cfg.CommandPrefix + " stop` stops the work.", false
	}
	b.turnDown(issue, mem.PlanPR, "@"+author, why)
	if number == issue {
		return "", false // turnDown said it here
	}
	return fmt.Sprintf("Turned down; #%d is a person's now.", issue), false
}

// turnDown ends an item whose plan was turned down, by a command or by closing its pull request.
func (b *Bot) turnDown(issue, planPR int, by, why string) {
	for _, name := range []string{"plan", "awaiting-approval", "build"} {
		b.GH.RemoveLabel(issue, b.Cfg.Label(name))
	}
	b.GH.AddLabels(issue, "human")
	b.Store.Update(fmt.Sprintf("turned down #%d", issue), func(s *state.State) error {
		mem := s.Item(issue)
		mem.SetStage(state.StageClosed, b.now())
		mem.Waiting = ""
		return nil
	})
	if pr, err := b.GH.GetPR(planPR); err == nil && pr.State == "open" {
		b.GH.ClosePR(planPR)
	}
	if why = strings.TrimSpace(why); why != "" {
		why = ": " + why
	}
	b.say(issue, fmt.Sprintf("%s turned the plan in #%d down%s. I keep off this now (`human`); take `human` off and "+
		"comment `%s plan` to plan it again.", by, planPR, why, b.Cfg.CommandPrefix))
}

// revisePlan queues a rewrite of a plan that waits for approval. handled is false when the item
// has no plan waiting, so the caller treats `revise` as it would without the gate.
func (b *Bot) revisePlan(number int, author, text string) (reply string, start, handled bool) {
	st, _, err := b.Store.Load()
	if err != nil {
		return "I could not read my state: " + err.Error(), false, true
	}
	issue, mem := b.planItem(st, number)
	if mem == nil || mem.PlanPR == 0 || mem.ApprovedPlan != "" {
		return "", false, false
	}
	if strings.TrimSpace(text) == "" {
		return "What should the plan change? `" + b.Cfg.CommandPrefix + " revise <what to change>`", false, true
	}
	if _, err := b.Store.Update(fmt.Sprintf("rewrite the plan of #%d", issue), func(s *state.State) error {
		m := s.Item(issue)
		m.Notes = append(m.Notes, "@"+author+" asked: "+text)
		m.SetStage(state.StagePlanning, b.now())
		m.Waiting = "a plan run"
		return nil
	}); err != nil {
		return "I could not queue it: " + err.Error(), false, true
	}
	b.GH.RemoveLabel(issue, b.Cfg.Label("awaiting-approval"))
	b.GH.AddLabels(issue, b.Cfg.Label("plan"))
	return "Queued a rewrite of the plan.", true, true
}

// planSweep turns down a plan whose pull request a person closed while no event reached the bot.
func (b *Bot) planSweep(st *state.State) {
	for _, n := range itemNumbers(st) {
		mem := st.Peek(n)
		if mem.PlanPR == 0 || mem.ApprovedPlan != "" || mem.Stage != state.StageApproval {
			continue
		}
		if pr, err := b.GH.GetPR(mem.PlanPR); err == nil && pr.State == "closed" && !pr.Merged {
			b.turnDown(n, mem.PlanPR, "A person", "they closed the plan's pull request")
		}
	}
}

// fillPlan reads the plan as it stands on its pull request, which a person may have edited,
// for a rewrite to start from.
func (b *Bot) fillPlan(p *job.Plan) {
	st, _, err := b.Store.Load()
	if err != nil {
		return
	}
	mem := st.Peek(p.Number)
	if mem == nil || mem.PlanBranch == "" || mem.PlanPath == "" {
		return
	}
	if text, _, err := b.GH.GetFile(mem.PlanBranch, mem.PlanPath); err == nil {
		p.PlanText = string(text)
	}
}

func (b *Bot) issueURL(number int) string {
	return fmt.Sprintf("%s/%s/issues/%d", serverURL(), b.Cfg.Repo, number)
}

func (b *Bot) upstreamIssueURL(number int) string {
	return fmt.Sprintf("%s/%s/issues/%d", serverURL(), b.Cfg.Upstream.Repo, number)
}

func (b *Bot) upstreamRef(number int) string {
	return fmt.Sprintf("[%s#%d](%s)", b.Cfg.Upstream.Repo, number, b.upstreamIssueURL(number))
}

// upstreamIssueIn finds the first upstream issue a text names: its link, or OWNER/NAME#N.
func (b *Bot) upstreamIssueIn(text string) int {
	if !b.Cfg.Forked() {
		return 0
	}
	repo := regexp.QuoteMeta(b.Cfg.Upstream.Repo)
	re := regexp.MustCompile(`(?i)(?:https?://github\.com/` + repo + `/issues/|(?:^|[^\w/.-])` + repo + `#)(\d+)\b`)
	if m := re.FindStringSubmatch(text); m != nil {
		var n int
		fmt.Sscan(m[1], &n)
		return n
	}
	return 0
}
