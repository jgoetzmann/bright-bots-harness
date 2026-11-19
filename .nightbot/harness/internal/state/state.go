// Package state is the bot's memory: one JSON file on its own branch, written compare-and-set,
// so handlers that overlap never lose each other's changes.
package state

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"time"

	"nightbot/internal/github"
)

// File is the state file's path on the state branch.
const File = "state.json"

// State is everything the bot remembers between runs.
type State struct {
	Halted    bool                    `json:"halted,omitempty"`
	HaltedBy  string                  `json:"halted_by,omitempty"`
	Suspended map[string]string       `json:"suspended,omitempty"` // provider → reason
	Parked    map[string]time.Time    `json:"parked,omitempty"`    // provider → until
	Running   map[string]*Claim       `json:"running,omitempty"`   // item number → its run
	Usage     map[string][]UsageEntry `json:"usage,omitempty"`     // provider → model minutes
	Items     map[string]*Item        `json:"items,omitempty"`     // issue or PR number → memory
	Queries   []*Query                `json:"queries,omitempty"`
	History   []*Entry                `json:"history,omitempty"`
	Handled   []int64                 `json:"handled,omitempty"` // comment and review ids answered
	Secrets   []string                `json:"secrets,omitempty"` // which provider secrets were set, last seen
	// Linked is fork mode's upstream issues and the item here that took each one up, kept after
	// the item ends, so an issue is taken up by itself only once.
	Linked map[string]int `json:"linked,omitempty"`
	// Shipped is the bot's pull requests a person merged upstream, for the status issue.
	Shipped []*Shipped `json:"shipped,omitempty"`
	// Sandbox is what the last sync of the fork's sandbox branch did.
	Sandbox string `json:"sandbox,omitempty"`
}

// Shipped is one change merged upstream.
type Shipped struct {
	At     time.Time `json:"at"`
	Issue  int       `json:"issue"`
	PR     int       `json:"pr"`
	Title  string    `json:"title"`
	Builds int       `json:"builds"`
}

// Claim is an item a run holds.
type Claim struct {
	Number   int       `json:"number"`
	Action   string    `json:"action"`
	Provider string    `json:"provider"`
	Model    string    `json:"model"`
	RunID    int64     `json:"run_id,omitempty"`
	Started  time.Time `json:"started"`
	QueryID  string    `json:"query_id,omitempty"`
}

// UsageEntry is the model minutes one run spent.
type UsageEntry struct {
	At      time.Time `json:"at"`
	Minutes float64   `json:"minutes"`
}

// Item is what the bot remembers of one issue or pull request.
type Item struct {
	Failures     int      `json:"failures,omitempty"`
	PR           int      `json:"pr,omitempty"`    // an issue's pull request
	Issue        int      `json:"issue,omitempty"` // a pull request's issue
	Branch       string   `json:"branch,omitempty"`
	Difficulty   string   `json:"difficulty,omitempty"`
	Triaged      bool     `json:"triaged,omitempty"`
	Notes        []string `json:"notes,omitempty"`      // what the next revision must address
	MergeMain    bool     `json:"merge_main,omitempty"` // the next revision merges the default branch in first
	CIRun        int64    `json:"ci_run,omitempty"`
	CIFixes      int      `json:"ci_fixes,omitempty"`
	Audits       int      `json:"audits,omitempty"`
	LastProvider string   `json:"last_provider,omitempty"`
	Builders     []string `json:"builders,omitempty"`

	// The plan gate: the plan's pull request here, and the plan a person approved.
	PlanPR       int       `json:"plan_pr,omitempty"`
	PlanBranch   string    `json:"plan_branch,omitempty"`
	PlanPath     string    `json:"plan_path,omitempty"`
	Plan         string    `json:"plan,omitempty"`
	ApprovedPlan string    `json:"approved_plan,omitempty"`
	ApprovedBy   string    `json:"approved_by,omitempty"`
	ApprovedAt   time.Time `json:"approved_at,omitzero"`

	// Fork mode: where the item came from, and its pull requests on the fork and upstream.
	Intake        string    `json:"intake,omitempty"`
	UpstreamIssue int       `json:"upstream_issue,omitempty"`
	ForkPR        int       `json:"fork_pr,omitempty"`
	ForkMerged    bool      `json:"fork_merged,omitempty"`
	ForkSkipped   string    `json:"fork_skipped,omitempty"` // why the sandbox merge was passed over
	ApprovedHead  string    `json:"approved_head,omitempty"`
	UpstreamPR    int       `json:"upstream_pr,omitempty"`
	CIHead        string    `json:"ci_head,omitempty"` // the head whose red CI was queued for a fix
	CIRepo        string    `json:"ci_repo,omitempty"` // fork or upstream
	CIJobs        []int64   `json:"ci_jobs,omitempty"` // the failed checks' job ids, for their logs
	Ready         bool      `json:"ready,omitempty"`   // the upstream pull request asked for review
	Stage         string    `json:"stage,omitempty"`
	Waiting       string    `json:"waiting,omitempty"`  // what it waits on, as the sweep last saw it
	Since         time.Time `json:"since,omitzero"`     // when it entered its stage
	HeadSeen      time.Time `json:"head_seen,omitzero"` // when the sweep first saw the head it waits on
	HeadSeenSHA   string    `json:"head_seen_sha,omitempty"`
}

// Stages an item passes through in fork mode and with the plan gate, for the status issue.
const (
	StagePlanning = "planning"
	StageApproval = "awaiting approval"
	StageApproved = "approved"
	StageBuilding = "building"
	StageForkCI   = "fork CI"
	StageHeld     = "held in the fork"
	StageUpstream = "upstream CI"
	StageInReview = "in review"
	StageRevising = "revising"
	StageHuman    = "with a person"
	StageMerged   = "merged"
	StageClosed   = "closed"
)

// SetStage moves an item to a stage, stamping when it got there.
func (i *Item) SetStage(stage string, now time.Time) {
	if i.Stage != stage {
		i.Stage, i.Since = stage, now
	}
}

// Query is a question someone asked the bot, waiting for a weak-tier answer.
type Query struct {
	ID        string    `json:"id"`
	Number    int       `json:"number"`
	IsPR      bool      `json:"is_pr,omitempty"`
	CommentID int64     `json:"comment_id"`
	Author    string    `json:"author"`
	Text      string    `json:"text"`
	Created   time.Time `json:"created"`
}

// Entry is one finished run.
type Entry struct {
	At       time.Time `json:"at"`
	Number   int       `json:"number"`
	Action   string    `json:"action"`
	Provider string    `json:"provider"`
	Model    string    `json:"model"`
	Outcome  string    `json:"outcome"`
	Minutes  float64   `json:"minutes"`
	RunURL   string    `json:"run_url,omitempty"`
	PR       int       `json:"pr,omitempty"`
}

// Limits on what the file keeps.
const (
	keepHistory = 100
	keepHandled = 3000
	keepShipped = 300
	keepUsage   = 7 * 24 * time.Hour
)

// Key is an item number as a map key.
func Key(number int) string { return strconv.Itoa(number) }

// Item is the memory of an item, made when missing.
func (s *State) Item(number int) *Item {
	if s.Items == nil {
		s.Items = map[string]*Item{}
	}
	item := s.Items[Key(number)]
	if item == nil {
		item = &Item{}
		s.Items[Key(number)] = item
	}
	return item
}

// Peek is the memory of an item, or nil, without making one.
func (s *State) Peek(number int) *Item {
	if s.Items == nil {
		return nil
	}
	return s.Items[Key(number)]
}

// WasHandled says whether a comment or review was answered already.
func (s *State) WasHandled(id int64) bool {
	for _, h := range s.Handled {
		if h == id {
			return true
		}
	}
	return false
}

// MarkHandled records a comment or review as answered.
func (s *State) MarkHandled(id int64) {
	if !s.WasHandled(id) {
		s.Handled = append(s.Handled, id)
	}
	if len(s.Handled) > keepHandled {
		s.Handled = s.Handled[len(s.Handled)-keepHandled:]
	}
}

// Record adds a finished run to the history and its minutes to the provider's usage.
func (s *State) Record(e *Entry) {
	s.History = append(s.History, e)
	if len(s.History) > keepHistory {
		s.History = s.History[len(s.History)-keepHistory:]
	}
	if e.Minutes > 0 && e.Provider != "" {
		if s.Usage == nil {
			s.Usage = map[string][]UsageEntry{}
		}
		s.Usage[e.Provider] = append(s.Usage[e.Provider], UsageEntry{At: e.At, Minutes: e.Minutes})
	}
}

// MinutesSince is a provider's model minutes since a time, counting runs still going as spent
// from their start until now.
func (s *State) MinutesSince(provider string, since, now time.Time) float64 {
	total := 0.0
	for _, u := range s.Usage[provider] {
		if !u.At.Before(since) {
			total += u.Minutes
		}
	}
	for _, claim := range s.Running {
		if claim.Provider == provider && claim.Started.After(since) {
			total += now.Sub(claim.Started).Minutes()
		}
	}
	return total
}

// Prune drops what is too old to matter: usage beyond seven days and parks that ended.
func (s *State) Prune(now time.Time) {
	for provider, entries := range s.Usage {
		kept := entries[:0]
		for _, u := range entries {
			if now.Sub(u.At) < keepUsage {
				kept = append(kept, u)
			}
		}
		if len(kept) == 0 {
			delete(s.Usage, provider)
		} else {
			s.Usage[provider] = kept
		}
	}
	for provider, until := range s.Parked {
		if !until.After(now) {
			delete(s.Parked, provider)
		}
	}
}

// Ship records a change merged upstream.
func (s *State) Ship(e *Shipped) {
	s.Shipped = append(s.Shipped, e)
	if len(s.Shipped) > keepShipped {
		s.Shipped = s.Shipped[len(s.Shipped)-keepShipped:]
	}
}

// Link records the item that took up an upstream issue.
func (s *State) Link(upstreamIssue, number int) {
	if s.Linked == nil {
		s.Linked = map[string]int{}
	}
	s.Linked[Key(upstreamIssue)] = number
}

// LinkedTo is the item that took up an upstream issue, or 0.
func (s *State) LinkedTo(upstreamIssue int) int { return s.Linked[Key(upstreamIssue)] }

// Running lists the claims in item order.
func (s *State) Claims() []*Claim {
	keys := make([]string, 0, len(s.Running))
	for k := range s.Running {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		a, _ := strconv.Atoi(keys[i])
		b, _ := strconv.Atoi(keys[j])
		return a < b
	})
	claims := make([]*Claim, 0, len(keys))
	for _, k := range keys {
		claims = append(claims, s.Running[k])
	}
	return claims
}

// ClaimKey is the Running key of a claim: the item, or the query's id for a question, so a
// question on an item never blocks the item's build.
func ClaimKey(number int, queryID string) string {
	if queryID != "" {
		return "q:" + queryID
	}
	return Key(number)
}

// Store reads and writes the state file through GitHub.
type Store struct {
	GH     github.Client
	Branch string
}

// Load reads the state; a missing file is an empty state.
func (st *Store) Load() (*State, string, error) {
	data, sha, err := st.GH.GetFile(st.Branch, File)
	if errors.Is(err, github.ErrNotFound) {
		return &State{}, "", nil
	}
	if err != nil {
		return nil, "", err
	}
	s := &State{}
	if err := json.Unmarshal(data, s); err != nil {
		return nil, "", fmt.Errorf("state: %s on %s: %w", File, st.Branch, err)
	}
	return s, sha, nil
}

// Update reads the state, applies change and writes it back, again from a fresh read whenever
// another writer got there first. change may run more than once, so it must only change s.
func (st *Store) Update(message string, change func(s *State) error) (*State, error) {
	for attempt := 0; attempt < 8; attempt++ {
		s, sha, err := st.Load()
		if err != nil {
			return nil, err
		}
		before, err := json.Marshal(s)
		if err != nil {
			return nil, err
		}
		if err := change(s); err != nil {
			return nil, err
		}
		after, err := json.Marshal(s)
		if err != nil {
			return nil, err
		}
		if bytes.Equal(before, after) && sha != "" {
			return s, nil // nothing changed: no commit on the state branch
		}
		data, err := json.MarshalIndent(s, "", "  ")
		if err != nil {
			return nil, err
		}
		err = st.GH.PutFile(st.Branch, File, append(data, '\n'), sha, message)
		if err == nil {
			return s, nil
		}
		if !errors.Is(err, github.ErrConflict) {
			return nil, err
		}
		time.Sleep(time.Duration(attempt*attempt) * 200 * time.Millisecond)
	}
	return nil, errors.New("state: gave up after 8 conflicting writes")
}
