// Package sched decides what the bot does next: the queue of work, which providers can start
// work now, and which provider and model take which item. It reads no GitHub and no clock of its
// own: everything comes in as arguments, so the planner, the sweep and the status issue all
// agree on the same answer.
package sched

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/state"
)

// Actions, in the order a provider's model would cost the least.
const (
	Ask    = "ask"    // answer a question (a weak query)
	Triage = "triage" // rate an unrated item before it is built (a weak query)
	Build  = "build"  // build an issue into a pull request
	Revise = "revise" // change the bot's pull request: review findings, red CI, a person's ask
	Review = "review" // review a pull request (an audit, by another provider when one is free)
	Plan   = "plan"   // write a plan a person approves before anything is built (the plan gate)
)

// IsQuery says whether an action only reads: no change, no checks, the weakest seat.
func IsQuery(action string) bool { return action == Ask || action == Triage }

// Item is one piece of queued work.
type Item struct {
	Number     int
	Title      string
	IsPR       bool
	Action     string
	Difficulty string // medium or hard; an unrated item builds as medium
	Rated      bool
	Priority   int // 0 high, 1 normal, 2 low
	Created    time.Time
	Query      *state.Query
	Avoid      string // a provider to pass over when another can take it (a review's builder)
}

// Key is the item's claim key in the state.
func (it *Item) Key() string {
	if it.Query != nil {
		return state.ClaimKey(it.Number, it.Query.ID)
	}
	return state.ClaimKey(it.Number, "")
}

// Tier is the weakest tier that may take the item.
func (it *Item) Tier() string {
	if IsQuery(it.Action) {
		return "weak"
	}
	if tier, ok := config.Difficulties[it.Difficulty]; ok {
		return tier
	}
	return "medium"
}

// Queue is the work waiting, in the order it is taken: questions and triage first (cheap, and
// a person may be waiting), then by priority label, finishing started work (revisions, then
// reviews) before new builds, oldest first.
func Queue(cfg *config.Config, st *state.State, issues []*github.Issue) []*Item {
	var queue []*Item
	seen := map[int]bool{}
	for _, issue := range issues {
		if seen[issue.Number] || issue.State != "open" || issue.HasLabel("human") {
			continue
		}
		seen[issue.Number] = true
		if _, running := st.Running[state.Key(issue.Number)]; running {
			continue
		}
		mem := st.Peek(issue.Number)
		item := &Item{Number: issue.Number, Title: issue.Title, IsPR: issue.IsPR(), Created: issue.CreatedAt,
			Priority: priority(issue)}
		item.Difficulty, item.Rated = difficulty(issue, mem)
		if !item.Rated && mem != nil && mem.Issue != 0 {
			if parent := st.Peek(mem.Issue); parent != nil && parent.Difficulty != "" {
				item.Difficulty, item.Rated = parent.Difficulty, true
			}
		}
		switch {
		case cfg.Forked() && !issue.IsPR() && issue.HasLabel(cfg.Label("revise")) && mem != nil && mem.Branch != "":
			item.Action = Revise // in fork mode the issue here carries the work on the fork's branch
		case cfg.PlanGate && !issue.IsPR() && issue.HasLabel(cfg.Label("plan")):
			item.Action = Plan
		case !issue.IsPR() && issue.HasLabel(cfg.Label("build")):
			if mem != nil && (mem.PR != 0 || mem.ForkPR != 0 || mem.UpstreamPR != 0) {
				continue // its pull request carries the work on
			}
			if cfg.PlanGate && (mem == nil || mem.ApprovedPlan == "") {
				continue // nothing is built before a person approves its plan
			}
			item.Action = Build
			if !item.Rated && cfg.Triage && (mem == nil || !mem.Triaged) {
				item.Action = Triage
			}
		case issue.IsPR() && issue.HasLabel(cfg.Label("revise")) && issue.HasLabel(cfg.Label("pr")) &&
			strings.EqualFold(issue.User.Login, cfg.BotLogin):
			item.Action = Revise
		case issue.IsPR() && issue.HasLabel(cfg.Label("review")) && !cfg.Forked():
			item.Action = Review
			if mem != nil {
				item.Avoid = mem.LastProvider
			}
		default:
			continue
		}
		queue = append(queue, item)
	}
	for _, q := range st.Queries {
		if _, running := st.Running[state.ClaimKey(q.Number, q.ID)]; running {
			continue
		}
		queue = append(queue, &Item{Number: q.Number, IsPR: q.IsPR, Title: "question from @" + q.Author, Action: Ask,
			Difficulty: "medium", Rated: true, Priority: 0, Created: q.Created, Query: q})
	}
	sort.SliceStable(queue, func(i, j int) bool {
		a, b := queue[i], queue[j]
		if ra, rb := queryRank(a.Action), queryRank(b.Action); ra != rb {
			return ra < rb
		}
		if a.Priority != b.Priority {
			return a.Priority < b.Priority
		}
		if ka, kb := kindRank(a.Action), kindRank(b.Action); ka != kb {
			return ka < kb
		}
		if !a.Created.Equal(b.Created) {
			return a.Created.Before(b.Created)
		}
		return a.Number < b.Number
	})
	return queue
}

func queryRank(action string) int {
	switch action {
	case Ask:
		return 0
	case Triage:
		return 1
	}
	return 2
}

func kindRank(action string) int {
	switch action {
	case Revise:
		return 0
	case Plan:
		return 1 // a person waits on a plan
	case Review:
		return 2
	}
	return 3
}

func priority(issue *github.Issue) int {
	switch {
	case issue.HasLabel("priority:high"):
		return 0
	case issue.HasLabel("priority:low"):
		return 2
	}
	return 1
}

func difficulty(issue *github.Issue, mem *state.Item) (string, bool) {
	switch {
	case issue.HasLabel("difficulty:hard"):
		return "hard", true
	case issue.HasLabel("difficulty:medium"):
		return "medium", true
	case mem != nil && mem.Difficulty != "":
		return mem.Difficulty, true
	}
	return "medium", false
}

// Availability is whether a provider can start work now, and if not, why.
type Availability struct {
	Provider *config.Provider
	Free     int    // lanes it may start now
	Busy     int    // lanes its runs hold
	Reason   string // why it starts nothing, when Free is 0
	FiveHour float64
	SevenDay float64
}

// Available is every provider's availability at now. force ignores the schedules, as an
// operator's `build --force` does.
func Available(cfg *config.Config, st *state.State, secrets map[string]bool, now time.Time, force bool) []*Availability {
	busy := map[string]int{}
	for _, claim := range st.Running {
		busy[claim.Provider]++
	}
	var out []*Availability
	for _, prov := range cfg.Providers.Ordered() {
		a := &Availability{Provider: prov, Busy: busy[prov.ID],
			FiveHour: st.MinutesSince(prov.ID, now.Add(-5*time.Hour), now),
			SevenDay: st.MinutesSince(prov.ID, now.Add(-7*24*time.Hour), now)}
		switch until, parked := st.Parked[prov.ID]; {
		case !prov.Enabled:
			a.Reason = "disabled"
		case st.Suspended[prov.ID] != "":
			a.Reason = "suspended: " + st.Suspended[prov.ID]
		case parked && until.After(now):
			a.Reason = "usage limit: parked until " + until.In(cfg.Location).Format("Jan 2 15:04 MST")
		case prov.Login == "secret" && !secrets[prov.Secret]:
			a.Reason = "its secret " + prov.Secret + " is not set"
		case !force && !cfg.InWindow(prov.Schedule, now):
			a.Reason = fmt.Sprintf("outside its hours (%s–%s)", prov.Schedule.Start, prov.Schedule.End)
		case prov.Limits.FiveHourMinutes > 0 && a.FiveHour >= float64(prov.Limits.FiveHourMinutes):
			a.Reason = fmt.Sprintf("5-hour cap: %.0f of %d minutes", a.FiveHour, prov.Limits.FiveHourMinutes)
		case prov.Limits.SevenDayMinutes > 0 && a.SevenDay >= float64(prov.Limits.SevenDayMinutes):
			a.Reason = fmt.Sprintf("7-day cap: %.0f of %d minutes", a.SevenDay, prov.Limits.SevenDayMinutes)
		case a.Busy >= prov.Lanes:
			a.Reason = fmt.Sprintf("all %d lanes busy", prov.Lanes)
		default:
			a.Free = prov.Lanes - a.Busy
		}
		out = append(out, a)
	}
	return out
}

// Assignment is an item given to a provider and one of its models.
type Assignment struct {
	Item     *Item
	Provider *config.Provider
	Seat     *config.Seat
	Waits    string // why the item is not assigned, when Provider is nil
}

// Assign hands out the queue greedily, as the planner would run after run: each item to the
// provider whose weakest fitting model is the weakest (cheap models for cheap work, strong ones
// kept for hard items), ties broken by priority order, while lanes and max_parallel allow.
func Assign(cfg *config.Config, st *state.State, queue []*Item, avail []*Availability) []*Assignment {
	free := map[string]int{}
	for _, a := range avail {
		free[a.Provider.ID] = a.Free
	}
	room := cfg.Providers.MaxParallel - len(st.Running)
	var out []*Assignment
	for _, item := range queue {
		as := &Assignment{Item: item}
		out = append(out, as)
		if room <= 0 {
			as.Waits = fmt.Sprintf("max_parallel (%d runs at once) reached", cfg.Providers.MaxParallel)
			continue
		}
		var best *Availability
		var bestSeat *config.Seat
		better := func(a *Availability, seat *config.Seat) bool {
			if best == nil {
				return true
			}
			avoidA, avoidB := a.Provider.ID == item.Avoid, best.Provider.ID == item.Avoid
			if avoidA != avoidB {
				return !avoidA
			}
			return config.TierRank(seat.Tier) < config.TierRank(bestSeat.Tier)
		}
		for _, a := range avail { // already in priority order, so the first of equals wins
			seat := a.Provider.SeatFor(item.Tier())
			if seat == nil || free[a.Provider.ID] <= 0 || !a.Provider.Takes(item.Action) {
				continue
			}
			if better(a, seat) {
				best, bestSeat = a, seat
			}
		}
		if best == nil {
			as.Waits = waits(item, avail, free)
			continue
		}
		as.Provider, as.Seat = best.Provider, bestSeat
		free[best.Provider.ID]--
		room--
	}
	return out
}

// waits says what the first provider that could take the item is waiting on.
func waits(item *Item, avail []*Availability, free map[string]int) string {
	var reasons []string
	for _, a := range avail {
		if a.Provider.SeatFor(item.Tier()) == nil || !a.Provider.Takes(item.Action) {
			continue
		}
		reason := a.Reason
		if reason == "" && free[a.Provider.ID] <= 0 {
			reason = "lanes taken by work ahead in the queue"
		}
		reasons = append(reasons, a.Provider.ID+": "+reason)
	}
	if len(reasons) == 0 {
		return "no provider has a " + item.Tier() + " model"
	}
	return strings.Join(reasons, "; ")
}

// Next is the first item a provider can take now, or nil. item limits it to one number (0: any),
// and mode to one action ("" or "auto": any).
func Next(cfg *config.Config, st *state.State, queue []*Item, avail []*Availability, item int, mode string) *Assignment {
	var filtered []*Item
	for _, it := range queue {
		if (item == 0 || it.Number == item) && (mode == "" || mode == "auto" || it.Action == mode) {
			filtered = append(filtered, it)
		}
	}
	for _, as := range Assign(cfg, st, filtered, avail) {
		if as.Provider != nil {
			return as
		}
	}
	return nil
}

// Spare is how many more runs could start now: a lane free, room under max_parallel, and an item
// for it. The sweep dispatches that many.
func Spare(cfg *config.Config, st *state.State, queue []*Item, avail []*Availability) int {
	n := 0
	for _, as := range Assign(cfg, st, queue, avail) {
		if as.Provider != nil {
			n++
		}
	}
	return n
}
