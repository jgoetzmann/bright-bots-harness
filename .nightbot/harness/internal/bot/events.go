package bot

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"

	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/state"
)

// Event is the part of a GitHub webhook payload the handlers read.
type Event struct {
	Action      string          `json:"action"`
	Issue       *github.Issue   `json:"issue"`
	Comment     *github.Comment `json:"comment"`
	Review      *github.Review  `json:"review"`
	PullRequest *struct {
		Number int            `json:"number"`
		Title  string         `json:"title"`
		Merged bool           `json:"merged"`
		Labels []github.Label `json:"labels"`
		User   github.User    `json:"user"`
		Head   struct {
			Ref string `json:"ref"`
		} `json:"head"`
	} `json:"pull_request"`
	Label       *github.Label `json:"label"`
	Assignee    *github.User  `json:"assignee"`
	Sender      github.User   `json:"sender"`
	WorkflowRun *struct {
		ID         int64  `json:"id"`
		Name       string `json:"name"`
		Conclusion string `json:"conclusion"`
		HeadBranch string `json:"head_branch"`
		HeadSHA    string `json:"head_sha"`
		HTMLURL    string `json:"html_url"`
		HeadRepo   *struct {
			FullName string `json:"full_name"`
		} `json:"head_repository"`
		PullRequests []struct {
			Number int `json:"number"`
		} `json:"pull_requests"`
	} `json:"workflow_run"`
}

// ReadEvent reads the payload GitHub Actions left at GITHUB_EVENT_PATH.
func ReadEvent(path string) (*Event, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	ev := &Event{}
	return ev, json.Unmarshal(data, ev)
}

// Handle answers one event.
func (b *Bot) Handle(name string, ev *Event) error {
	switch name {
	case "issue_comment", "pull_request_review_comment":
		if ev.Comment == nil || (ev.Action != "created" && ev.Action != "edited") {
			return nil
		}
		number, isPR := 0, false
		if ev.Issue != nil {
			number, isPR = ev.Issue.Number, ev.Issue.IsPR()
		} else if ev.PullRequest != nil {
			number, isPR = ev.PullRequest.Number, true
		}
		return b.onComment(number, isPR, ev.Comment.ID, name == "pull_request_review_comment",
			ev.Comment.User.Login, ev.Comment.AuthorAssociation, ev.Comment.Body)
	case "pull_request_review":
		if ev.Review == nil || ev.PullRequest == nil {
			return nil
		}
		return b.onReview(ev)
	case "issues", "pull_request_target", "pull_request":
		return b.onIssueEvent(ev)
	case "workflow_run":
		return b.onCI(ev)
	}
	b.logf("%s: nothing to do", name)
	return nil
}

// Command is one instruction in a comment.
type Command struct {
	Verb  string
	Args  []string
	Text  string // the rest of the line (or of the comment, for a mention)
	Force bool
}

var fenceLine = regexp.MustCompile("^\\s*(```|~~~)")

// ParseCommands reads the commands in a comment: lines that start with the prefix
// (`/nightbot build --force`), or, with none, a mention of the bot, which is one "mention"
// command holding the comment's text. Quoted lines and code blocks are skipped.
func ParseCommands(body, prefix, botLogin string) []Command {
	var commands []Command
	var plain []string
	inFence := false
	for _, line := range strings.Split(body, "\n") {
		if fenceLine.MatchString(line) {
			inFence = !inFence
			continue
		}
		trimmed := strings.TrimSpace(line)
		if inFence || strings.HasPrefix(trimmed, ">") {
			continue
		}
		plain = append(plain, line)
		fields := strings.Fields(trimmed)
		if len(fields) == 0 || !strings.EqualFold(fields[0], prefix) {
			continue
		}
		c := Command{Verb: "help"}
		if len(fields) > 1 {
			c.Verb = strings.ToLower(fields[1])
			for _, f := range fields[2:] {
				if f == "--force" {
					c.Force = true
				} else {
					c.Args = append(c.Args, f)
				}
			}
			rest := strings.TrimSpace(trimmed[len(fields[0]):])
			c.Text = strings.TrimSpace(rest[len(fields[1]):])
		}
		commands = append(commands, c)
	}
	if len(commands) > 0 {
		return commands
	}
	login := strings.TrimSuffix(botLogin, "[bot]")
	mention := regexp.MustCompile(`(?i)(^|[^\w@/-])@` + regexp.QuoteMeta(login) + `\b`)
	text := strings.Join(plain, "\n")
	if login != "" && mention.MatchString(text) {
		rest := strings.TrimSpace(mention.ReplaceAllString(text, "$1"))
		return []Command{{Verb: "mention", Text: rest}}
	}
	return nil
}

// onComment answers the commands in a comment or a review's body.
func (b *Bot) onComment(number int, isPR bool, id int64, reviewComment bool, author, association, body string) error {
	if b.isBot(author) || strings.Contains(body, Marker) {
		return nil
	}
	commands := ParseCommands(body, b.Cfg.CommandPrefix, b.Cfg.BotLogin)
	if len(commands) == 0 {
		return nil
	}
	if !b.Trusted(author, association) {
		b.logf("ignored @%s's command on #%d: not on the trust list", author, number)
		return nil
	}
	handled := false
	if _, err := b.Store.Update(fmt.Sprintf("comment %d", id), func(s *state.State) error {
		handled = s.WasHandled(id)
		s.MarkHandled(id)
		return nil
	}); err != nil {
		return err
	}
	if handled {
		return nil
	}
	if id > 0 {
		b.GH.AddReaction(id, "eyes", reviewComment)
	}
	var replies []string
	kick := false
	for _, c := range commands {
		reply, start := b.run(c, number, isPR, id, author)
		if reply != "" {
			replies = append(replies, reply)
		}
		kick = kick || start
	}
	if len(replies) > 0 {
		b.say(number, b.mention(author)+strings.Join(replies, "\n\n"))
	}
	if kick {
		b.Kick()
	}
	return nil
}

// run carries out one command; it returns the reply and whether a run may need starting.
func (b *Bot) run(c Command, number int, isPR bool, commentID int64, author string) (string, bool) {
	p := b.Cfg.CommandPrefix
	switch c.Verb {
	case "help":
		return b.help(), false
	case "status":
		snap, err := b.Snapshot(false)
		if err != nil {
			return "I could not read my state: " + err.Error(), false
		}
		return b.StatusMarkdown(snap, false), false
	case "ask":
		return b.queueQuestion(number, isPR, commentID, author, c.Text)
	case "mention":
		if isPR && b.isBotPR(number) {
			return b.queueRevision(number, author, c.Text)
		}
		return b.queueQuestion(number, isPR, commentID, author, c.Text)
	case "approve":
		return b.approve(number, author)
	case "reject":
		return b.reject(number, author, c.Text)
	case "plan":
		if !b.Cfg.PlanGate {
			return "There is no plan gate here: `" + p + " build` builds.", false
		}
		return b.queuePlan(number, isPR, c.Text)
	case "revise":
		if b.Cfg.PlanGate {
			if reply, start, handled := b.revisePlan(number, author, c.Text); handled {
				return reply, start
			}
		}
		if b.Cfg.Forked() && !isPR {
			return b.reviseForked(number, author, c.Text)
		}
		if !isPR || !b.isBotPR(number) {
			return "`revise` works on my own pull requests; on an issue, use `" + p + " build`.", false
		}
		return b.queueRevision(number, author, c.Text)
	case "build":
		if b.Cfg.PlanGate && !isPR {
			if st, _, err := b.Store.Load(); err == nil {
				if mem := st.Peek(number); mem == nil || mem.ApprovedPlan == "" {
					return b.queuePlan(number, false, c.Text)
				}
			}
		}
		if isPR {
			if !b.isBotPR(number) {
				return "I only build issues and revise my own pull requests; `" + p + " review` reviews this one.", false
			}
			return b.queueRevision(number, author, firstNonEmpty(c.Text, "Build this again: see the thread."))
		}
		return b.queueBuild(number, c.Force)
	case "review":
		if !isPR {
			return "`review` works on pull requests.", false
		}
		if b.Cfg.Forked() {
			return "In fork mode every build and revision is reviewed already; `" + p + " ask` asks about the code.", false
		}
		if err := b.GH.AddLabels(number, b.Cfg.Label("review")); err != nil {
			return "I could not queue the review: " + err.Error(), false
		}
		if c.Force {
			b.Force(number, "review")
			return "Reviewing now.", false
		}
		return "Queued a review run.", true
	case "stop":
		return b.stop(number), false
	case "rebuild":
		return b.rebuild(number, isPR)
	case "forget":
		b.Store.Update(fmt.Sprintf("forget #%d", number), func(s *state.State) error {
			if mem := s.Peek(number); mem != nil {
				mem.Failures, mem.CIFixes, mem.Audits = 0, 0, 0
			}
			return nil
		})
		return "Forgot this item's failures.", false
	case "halt", "start":
		halt := c.Verb == "halt"
		b.Store.Update(c.Verb, func(s *state.State) error {
			s.Halted, s.HaltedBy = halt, ""
			if halt {
				s.HaltedBy = "@" + author
			}
			return nil
		})
		if halt {
			return "Halted: no new run starts until `" + p + " start`. Runs going now finish.", false
		}
		return "Started again.", true
	case "suspend", "resume":
		if len(c.Args) == 0 {
			return fmt.Sprintf("Which provider? `%s %s <provider>`", p, c.Verb), false
		}
		id := c.Args[0]
		if _, ok := b.Cfg.Providers.Providers[id]; !ok {
			return fmt.Sprintf("There is no provider %q; `%s status` lists them.", id, p), false
		}
		reason := firstNonEmpty(strings.TrimSpace(strings.TrimPrefix(c.Text, id)), "by @"+author)
		b.Store.Update(c.Verb+" "+id, func(s *state.State) error {
			if s.Suspended == nil {
				s.Suspended = map[string]string{}
			}
			if c.Verb == "suspend" {
				s.Suspended[id] = reason
			} else {
				delete(s.Suspended, id)
			}
			return nil
		})
		if c.Verb == "suspend" {
			return fmt.Sprintf("Suspended %s: it starts no new run until `%s resume %s`.", id, p, id), false
		}
		return fmt.Sprintf("Resumed %s.", id), true
	}
	return fmt.Sprintf("I do not know `%s`.\n\n%s", c.Verb, b.help()), false
}

func (b *Bot) help() string {
	p := b.Cfg.CommandPrefix
	var gate []string
	if b.Cfg.PlanGate {
		gate = []string{
			"| `" + p + " plan` | write a plan for this issue, for an approver to approve (also what `build` does before one is approved) |",
			"| `" + p + " approve` | approve the plan, on its pull request or the issue (approvers: " + b.Cfg.ApproverList() + ") |",
			"| `" + p + " reject [why]` | turn the plan down |",
			"| `" + p + " revise <change>` | rewrite a plan that waits for approval, or change the work once it is built |",
		}
	}
	rows := append([]string{"| Command | What it does |", "|---|---|"}, gate...)
	rows = append(rows,
		"| `"+p+" build [--force]` | queue this issue (`--force`: start now, outside the providers' hours) |",
		"| `"+p+" ask <question>`, or `@"+strings.TrimSuffix(b.Cfg.BotLogin, "[bot]")+" <question>` | a question, answered by the cheapest model free (a weak one first) |",
		"| `"+p+" revise <change>`, or a mention on my pull request | change my pull request |",
		"| `"+p+" review [--force]` | an adversarial review of a pull request |",
		"| `"+p+" stop` | stop work on this item |",
		"| `"+p+" rebuild` | start this item over from the default branch |",
		"| `"+p+" forget` | forget this item's failures |",
		"| `"+p+" status` | what runs, the providers and the queue |",
		"| `"+p+" halt` / `start` | stop or restart every new run |",
		"| `"+p+" suspend <provider> [why]` / `resume <provider>` | stop or restart one provider |",
	)
	return strings.Join(rows, "\n")
}

// reviseForked queues a revision of an item's work on the fork, asked for on its issue.
func (b *Bot) reviseForked(number int, author, text string) (string, bool) {
	st, _, err := b.Store.Load()
	if err != nil {
		return "I could not read my state: " + err.Error(), false
	}
	mem := st.Peek(number)
	if mem == nil || mem.Branch == "" {
		return "Nothing is built for this yet.", false
	}
	if strings.TrimSpace(text) == "" {
		return "What should change? `" + b.Cfg.CommandPrefix + " revise <what to change>`", false
	}
	b.queueForkRevision(number, "@"+author+" asked: "+text, false)
	return "Queued a revision.", true
}

// isBotPR says whether a pull request is the bot's own: its label, and a branch of its own here.
func (b *Bot) isBotPR(number int) bool {
	pr, err := b.GH.GetPR(number)
	return err == nil && hasLabel(pr.Labels, b.Cfg.Label("pr")) && b.notOurs(pr) == ""
}

func (b *Bot) queueQuestion(number int, isPR bool, commentID int64, author, text string) (string, bool) {
	if strings.TrimSpace(text) == "" {
		return "What is the question? `" + b.Cfg.CommandPrefix + " ask <question>`", false
	}
	_, err := b.Store.Update(fmt.Sprintf("question on #%d", number), func(s *state.State) error {
		for _, q := range s.Queries {
			if q.CommentID == commentID {
				return nil
			}
		}
		s.Queries = append(s.Queries, &state.Query{ID: fmt.Sprint(commentID), Number: number, IsPR: isPR,
			CommentID: commentID, Author: author, Text: text, Created: b.now()})
		return nil
	})
	if err != nil {
		return "I could not queue the question: " + err.Error(), false
	}
	return "", true // the 👀 says it is queued; the answer is the reply
}

func (b *Bot) queueRevision(number int, author, text string) (string, bool) {
	_, err := b.Store.Update(fmt.Sprintf("revision of #%d", number), func(s *state.State) error {
		mem := s.Item(number)
		mem.Notes = append(mem.Notes, "@"+author+" asked: "+text)
		return nil
	})
	if err == nil {
		err = b.GH.AddLabels(number, b.Cfg.Label("revise"))
	}
	if err != nil {
		return "I could not queue the revision: " + err.Error(), false
	}
	return "Queued a revision.", true
}

func (b *Bot) queueBuild(number int, force bool) (string, bool) {
	issue, err := b.GH.GetIssue(number)
	if err != nil {
		return "I could not read this issue: " + err.Error(), false
	}
	if issue.HasLabel("human") {
		return "This carries `human`, which keeps me off it; take the label off first.", false
	}
	if err := b.GH.AddLabels(number, b.Cfg.Label("build")); err != nil {
		return "I could not queue it: " + err.Error(), false
	}
	if force {
		if err := b.Force(number, "auto"); err != nil {
			return "Queued, but I could not start a run now: " + err.Error(), false
		}
		return "Starting a run for this now.", false
	}
	return "Queued. " + b.position(number), true
}

// position says where an item stands in the queue and who takes it.
func (b *Bot) position(number int) string {
	snap, err := b.Snapshot(false)
	if err != nil {
		return ""
	}
	for i, as := range snap.Assignments {
		if as.Item.Number == number && as.Item.Query == nil {
			if as.Provider != nil {
				return fmt.Sprintf("%s (%s) takes it at the next run.", as.Provider.ID, as.Seat.Model)
			}
			return fmt.Sprintf("It is number %d in the queue; it waits on: %s.", i+1, as.Waits)
		}
	}
	return ""
}

// stop takes an item out of the queue and cancels the run that holds it. In fork mode it also
// keeps the sweep off the item's pull requests.
func (b *Bot) stop(number int) string {
	for _, name := range []string{"build", "revise", "review", "building", "plan"} {
		b.GH.RemoveLabel(number, b.Cfg.Label(name))
	}
	if b.Cfg.Forked() {
		b.forkStop(number)
	}
	var cancel []int64
	b.Store.Update(fmt.Sprintf("stop #%d", number), func(s *state.State) error {
		cancel = nil
		for key, claim := range s.Running {
			if claim.Number == number {
				if claim.RunID != 0 {
					cancel = append(cancel, claim.RunID)
				}
				delete(s.Running, key)
			}
		}
		kept := s.Queries[:0]
		for _, q := range s.Queries {
			if q.Number != number {
				kept = append(kept, q)
			}
		}
		s.Queries = kept
		return nil
	})
	for _, id := range cancel {
		if err := b.Actions.CancelRun(id); err != nil {
			b.logf("cancel run %d: %v", id, err)
		}
	}
	again := ""
	if b.Cfg.Forked() {
		again = " It carries `human` now: take that off and comment `" + b.Cfg.CommandPrefix + " revise` or `build` to carry on."
	}
	if len(cancel) > 0 {
		return "Stopped, and cancelled the run that held it." + again
	}
	return "Stopped: it is out of the queue." + again
}

// rebuild starts an item over: its branch deleted (which closes its pull request), its memory
// cleared, the issue queued again.
func (b *Bot) rebuild(number int, isPR bool) (string, bool) {
	if b.Cfg.Forked() {
		return b.forkRebuild(number)
	}
	issue := number
	st, _, err := b.Store.Load()
	if err != nil {
		return "I could not read my state: " + err.Error(), false
	}
	if mem := st.Peek(number); isPR {
		if mem == nil || mem.Issue == 0 {
			return "I do not know which issue this pull request builds; use `rebuild` on the issue.", false
		}
		issue = mem.Issue
	}
	b.stop(issue)
	var branches []string
	for _, n := range []int{number, issue} {
		if mem := st.Peek(n); mem != nil && mem.Branch != "" {
			branches = append(branches, mem.Branch)
		}
		if mem := st.Peek(n); mem != nil && mem.PR != 0 {
			b.stop(mem.PR)
		}
	}
	for _, branch := range branches {
		if err := b.GH.DeleteBranch(branch); err != nil {
			b.logf("delete %s: %v", branch, err)
		}
	}
	b.Store.Update(fmt.Sprintf("rebuild #%d", issue), func(s *state.State) error {
		if mem := s.Peek(issue); mem != nil && mem.PR != 0 {
			delete(s.Items, state.Key(mem.PR))
		}
		delete(s.Items, state.Key(issue))
		return nil
	})
	if issue != number {
		b.say(number, fmt.Sprintf("Starting #%d over from %s; this pull request closes with its branch.", issue, b.Cfg.DefaultBranch))
	}
	b.GH.AddLabels(issue, b.Cfg.Label("build"))
	return fmt.Sprintf("Starting #%d over from %s.", issue, b.Cfg.DefaultBranch), true
}

// onReview queues a revision when a trusted person requests changes on the bot's pull request,
// and answers commands in a review's body.
func (b *Bot) onReview(ev *Event) error {
	r, pr := ev.Review, ev.PullRequest
	if ev.Action != "submitted" || b.isBot(r.User.Login) {
		return nil
	}
	if strings.EqualFold(r.State, "approved") && hasLabel(pr.Labels, b.Cfg.Label("plan-pr")) && b.Cfg.PlanGate {
		if !b.Trusted(r.User.Login, r.AuthorAssociation) {
			return nil
		}
		handled := false
		if _, err := b.Store.Update(fmt.Sprintf("review %d", r.ID), func(s *state.State) error {
			handled = s.WasHandled(-r.ID)
			s.MarkHandled(-r.ID)
			return nil
		}); err != nil || handled {
			return err
		}
		reply, start := b.approve(pr.Number, r.User.Login)
		b.say(pr.Number, b.mention(r.User.Login)+reply)
		if start {
			b.Kick()
		}
		return nil
	}
	if !strings.EqualFold(r.State, "changes_requested") || !hasLabel(pr.Labels, b.Cfg.Label("pr")) {
		return b.onComment(pr.Number, true, -r.ID, false, r.User.Login, r.AuthorAssociation, r.Body)
	}
	if !b.Trusted(r.User.Login, r.AuthorAssociation) {
		return nil
	}
	text := firstNonEmpty(r.Body, "(no summary)") + "\n\n(Read this review's inline comments on the pull request too.)"
	reply, start := b.queueRevision(pr.Number, r.User.Login, "changes requested in a review: "+text)
	b.say(pr.Number, reply)
	if start {
		b.Kick()
	}
	return nil
}

func (b *Bot) onIssueEvent(ev *Event) error {
	switch {
	case b.Cfg.PlanGate && ev.Issue != nil && !ev.Issue.IsPR() && !b.isBot(ev.Sender.Login) &&
		((ev.Action == "opened" && ev.Issue.HasLabel(config.Requested)) ||
			(ev.Action == "labeled" && ev.Label != nil && ev.Label.Name == config.Requested)):
		return b.onRequested(ev)
	case b.Cfg.PlanGate && ev.Action == "closed" && ev.PullRequest != nil && !ev.PullRequest.Merged &&
		hasLabel(ev.PullRequest.Labels, b.Cfg.Label("plan-pr")) && !b.isBot(ev.Sender.Login):
		st, _, err := b.Store.Load()
		if err != nil {
			return err
		}
		if issue, mem := b.planItem(st, ev.PullRequest.Number); mem != nil && mem.PlanPR == ev.PullRequest.Number &&
			mem.ApprovedPlan == "" && mem.Stage != state.StageClosed {
			b.turnDown(issue, mem.PlanPR, "@"+ev.Sender.Login, "they closed the plan's pull request")
		}
		return nil
	case ev.Action == "labeled" && ev.Label != nil && ev.Issue != nil && ev.Label.Name == b.Cfg.Label("build"):
		if b.isBot(ev.Sender.Login) || ev.Issue.IsPR() {
			return nil
		}
		if ev.Issue.HasLabel("human") {
			b.say(ev.Issue.Number, "This carries `human` too, which keeps me off it; take that off and I will build it.")
			return nil
		}
		b.say(ev.Issue.Number, "Queued. "+b.position(ev.Issue.Number))
		b.Kick()
	case ev.Action == "labeled" && ev.Label != nil && !b.isBot(ev.Sender.Login) &&
		(ev.Label.Name == b.Cfg.Label("revise") || ev.Label.Name == b.Cfg.Label("review")):
		b.Kick()
	case ev.Action == "assigned" && ev.Assignee != nil && b.isBot(ev.Assignee.Login) && ev.Issue != nil && !ev.Issue.IsPR():
		// The label's own event is the bot's, which it ignores, so the reply and the run start here.
		reply, start := b.queueBuild(ev.Issue.Number, false)
		b.say(ev.Issue.Number, reply)
		if start {
			b.Kick()
		}
	case ev.Action == "closed" && ev.PullRequest != nil && hasLabel(ev.PullRequest.Labels, b.Cfg.Label("pr")):
		number := ev.PullRequest.Number
		_, err := b.Store.Update(fmt.Sprintf("closed #%d", number), func(s *state.State) error {
			if mem := s.Peek(number); mem != nil && mem.Issue != 0 {
				if parent := s.Peek(mem.Issue); parent != nil {
					parent.PR = 0
					if ev.PullRequest.Merged {
						delete(s.Items, state.Key(mem.Issue))
					}
				}
			}
			delete(s.Items, state.Key(number))
			outcome := "closed"
			if ev.PullRequest.Merged {
				outcome = "merged"
			}
			s.Record(&state.Entry{At: b.now(), Number: number, Action: "merge", Outcome: outcome, PR: number})
			return nil
		})
		return err
	}
	return nil
}

// onRequested takes up an issue a person opened from the request template (it carries
// `requested`), or labelled so: a trusted person's is queued to be planned.
func (b *Bot) onRequested(ev *Event) error {
	issue, err := b.GH.GetIssue(ev.Issue.Number)
	if err != nil {
		return err
	}
	if issue.HasLabel(b.Cfg.Label("plan")) || issue.HasLabel(b.Cfg.Label("awaiting-approval")) || issue.HasLabel("human") {
		return nil
	}
	trusted := b.Trusted(issue.User.Login, issue.AuthorAssociation) // opened: the sender is the author
	if ev.Action == "labeled" {
		trusted = b.Trusted(ev.Sender.Login, "")
	}
	if !trusted {
		b.say(issue.Number, fmt.Sprintf("Thanks. Someone on the trust list queues this by commenting `%s plan` on it.", b.Cfg.CommandPrefix))
		return nil
	}
	reply, start := b.intake(issue, config.Requested, "")
	b.say(issue.Number, b.mention(ev.Sender.Login)+reply)
	if start {
		b.Kick()
	}
	return nil
}

// queueBranch is the temporary branch a merge queue tests a pull request on:
// gh-readonly-queue/<base>/pr-<number>-<head sha>.
var queueBranch = regexp.MustCompile(`^gh-readonly-queue/(.+)/pr-(\d+)-([0-9a-f]{7,40})$`)

// onCI queues a CI fix when a required workflow fails on the bot's pull request, or on its entry
// in the merge queue, which tests it on top of the newest default branch and the pull requests
// queued ahead of it: that fix merges the default branch in first.
func (b *Bot) onCI(ev *Event) error {
	run := ev.WorkflowRun
	if run == nil || run.Name != b.Cfg.CIWorkflow || run.HeadRepo == nil || !strings.EqualFold(run.HeadRepo.FullName, b.Cfg.Repo) {
		return nil // not CI of a branch here (a fork's branch of the same name is not)
	}
	switch run.Conclusion {
	case "failure", "timed_out":
	default:
		return nil
	}
	var pr *github.PR
	var err error
	queued := queueBranch.FindStringSubmatch(run.HeadBranch)
	switch {
	case queued != nil:
		number, _ := strconv.Atoi(queued[2])
		if pr, err = b.GH.GetPR(number); err != nil {
			return err
		}
		if !strings.HasPrefix(pr.Head.SHA, queued[3]) {
			return nil // the queue's run of an older head
		}
	case strings.HasPrefix(run.HeadBranch, b.Cfg.BranchPrefix):
		if pr, err = b.GH.FindPR(run.HeadBranch); err != nil || pr == nil {
			return err
		}
		if pr.Head.SHA != "" && run.HeadSHA != "" && pr.Head.SHA != run.HeadSHA {
			return nil // CI of an older head
		}
	default:
		return nil
	}
	if !hasLabel(pr.Labels, b.Cfg.Label("pr")) || hasLabel(pr.Labels, "human") || b.notOurs(pr) != "" {
		return nil
	}
	note := fmt.Sprintf("CI failed on %.12s: %s. Its failing jobs' logs follow.", run.HeadSHA, run.HTMLURL)
	if queued != nil {
		note = fmt.Sprintf("CI failed in the merge queue (%s): the change does not hold on top of the newest %s "+
			"and the pull requests queued ahead of it. The harness merged %s in first; fix what fails now. "+
			"Its failing jobs' logs follow.", run.HTMLURL, queued[1], queued[1])
	}
	fixes := 0
	_, err = b.Store.Update(fmt.Sprintf("CI failed on #%d", pr.Number), func(s *state.State) error {
		mem := s.Item(pr.Number)
		if mem.CIRun == run.ID {
			fixes = -1
			return nil
		}
		mem.CIFixes++
		fixes = mem.CIFixes
		if fixes <= b.Cfg.MaxCIFixes {
			mem.CIRun = run.ID
			mem.Notes = append(mem.Notes, note)
			mem.MergeMain = mem.MergeMain || queued != nil
		}
		return nil
	})
	if err != nil || fixes < 0 {
		return err
	}
	if fixes > b.Cfg.MaxCIFixes {
		b.GH.AddLabels(pr.Number, "human")
		b.say(pr.Number, fmt.Sprintf("%sCI failed again after %d fixes ([run](%s)), so this is yours now (`human`).",
			b.operator(), b.Cfg.MaxCIFixes, run.HTMLURL))
		return nil
	}
	b.GH.AddLabels(pr.Number, b.Cfg.Label("revise"))
	where := "CI failed"
	if queued != nil {
		where = "CI failed in the merge queue"
	}
	b.say(pr.Number, fmt.Sprintf("%s ([run](%s)); queued a fix (%d of %d).", where, run.HTMLURL, fixes, b.Cfg.MaxCIFixes))
	b.Kick()
	return nil
}
