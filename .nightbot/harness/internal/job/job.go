// Package job is what passes between a run's three jobs: the plan the planner writes (plan.json)
// and the result the model job leaves beside its git bundle (result.json), which deliver reads.
package job

import (
	"encoding/json"
	"os"
	"regexp"
	"strings"
	"time"
)

// Plan is one run's work, written by the plan job, which holds the GitHub token and runs no
// model. The model job only reads it; deliver trusts the plan job's outputs over anything the
// model job wrote.
type Plan struct {
	Action     string `json:"action"`
	Number     int    `json:"number"`
	Title      string `json:"title"`
	IsPR       bool   `json:"is_pr"`
	Issue      int    `json:"issue,omitempty"` // a pull request's issue
	Difficulty string `json:"difficulty"`
	Rated      bool   `json:"rated"`
	Provider   string `json:"provider"`
	CLI        string `json:"cli"`
	Model      string `json:"model"`
	Effort     string `json:"effort,omitempty"`
	Tier       string `json:"tier"`
	RunsOn     string `json:"runs_on"`
	Login      string `json:"login"`
	Secret     string `json:"secret,omitempty"`
	Base       string `json:"base"`
	Branch     string `json:"branch"`
	Continue   bool   `json:"continue"` // the branch exists: carry its work on
	// MergeBase merges the newest base branch into the pull request first: the merge queue sent
	// it back, or it conflicts with the base.
	MergeBase bool `json:"merge_base,omitempty"`
	// Untrusted is a pull request from another repository or a person off the trust list: it is
	// reviewed (or asked about) from its diff alone, never checked out, built or run.
	Untrusted    bool   `json:"untrusted,omitempty"`
	PR           int    `json:"pr,omitempty"`
	Head         string `json:"head,omitempty"`    // a pull request's head when planned: what an audit reviews
	Thread       string `json:"thread"`            // the item and its trusted comments
	Request      string `json:"request,omitempty"` // what a revision must change, or the question
	CIRun        int64  `json:"ci_run,omitempty"`
	QueryID      string `json:"query_id,omitempty"`
	CommentID    int64  `json:"comment_id,omitempty"`
	Asker        string `json:"asker,omitempty"`
	ReviewRounds int    `json:"review_rounds"`
	// PlanText is the approved plan a build follows, or the plan a re-plan rewrites.
	PlanText string `json:"plan_text,omitempty"`
	// Upstream and Fork are set in fork mode: the work job clones Upstream, with the fork as the
	// remote "fork" that holds the bot's branches.
	Upstream      string    `json:"upstream,omitempty"`
	Fork          string    `json:"fork,omitempty"`
	UpstreamIssue int       `json:"upstream_issue,omitempty"`
	CIRepo        string    `json:"ci_repo,omitempty"` // fork or upstream: where CIJobs ran
	CIJobs        []int64   `json:"ci_jobs,omitempty"`
	RunID         int64     `json:"run_id,omitempty"`
	RunURL        string    `json:"run_url,omitempty"`
	Created       time.Time `json:"created"`
}

// Result is what the model job did.
type Result struct {
	Action      string        `json:"action"`
	Outcome     string        `json:"outcome"`
	Approved    bool          `json:"approved"`
	Verdict     *Verdict      `json:"verdict,omitempty"`
	Rounds      int           `json:"rounds"`
	Checks      []CheckResult `json:"checks,omitempty"`
	ChecksGreen bool          `json:"checks_green"`
	Report      string        `json:"report,omitempty"`
	Answer      string        `json:"answer,omitempty"`
	Plan        string        `json:"plan,omitempty"`
	Triage      *Triage       `json:"triage,omitempty"`
	Minutes     float64       `json:"minutes"`
	RateLimited bool          `json:"rate_limited"`
	ResetAt     time.Time     `json:"reset_at,omitzero"`
	Error       string        `json:"error,omitempty"`
	Base        string        `json:"base,omitempty"`
	Head        string        `json:"head,omitempty"`
	Branch      string        `json:"branch,omitempty"`
	Bundle      string        `json:"bundle,omitempty"`
	Changed     []string      `json:"changed,omitempty"`
	Reverted    []string      `json:"reverted,omitempty"`
	Calls       []Call        `json:"calls,omitempty"`
}

// Outcomes of a run.
const (
	Approved    = "approved"     // a change its reviewer approved, checks green
	Unapproved  = "unapproved"   // a change the review rounds ran out on
	NoChange    = "no_change"    // the builder changed nothing (it may have asked something)
	RateLimited = "rate_limited" // the subscription refused; the work so far is kept
	Failed      = "failed"       // something broke
	Answered    = "answered"     // a question answered
	Triaged     = "triaged"      // an item rated
	Reviewed    = "reviewed"     // an audit's verdict
	Planned     = "planned"      // a plan written for a person to approve
)

// Call is one model call, for the record.
type Call struct {
	Kind     string  `json:"kind"`
	Model    string  `json:"model"`
	Minutes  float64 `json:"minutes"`
	ExitCode int     `json:"exit_code"`
	Error    string  `json:"error,omitempty"`
}

// CheckResult is one of the repository's checks after a change.
type CheckResult struct {
	Name    string  `json:"name"`
	Passed  bool    `json:"passed"`
	Output  string  `json:"output,omitempty"`
	Minutes float64 `json:"minutes"`
}

// Verdict is a reviewer's answer.
type Verdict struct {
	Verdict  string    `json:"verdict"`
	Summary  string    `json:"summary"`
	Findings []Finding `json:"findings"`
}

// Finding is one problem a reviewer found.
type Finding struct {
	Severity string `json:"severity"` // blocking or minor
	File     string `json:"file,omitempty"`
	Line     int    `json:"line,omitempty"`
	Problem  string `json:"problem"`
	Fix      string `json:"fix,omitempty"`
}

// Approves says whether the verdict approves: it says so, and names no blocking finding.
func (v *Verdict) Approves() bool {
	if v == nil || !strings.HasPrefix(strings.ToLower(v.Verdict), "approve") {
		return false
	}
	return len(v.Blocking()) == 0
}

// Blocking is the findings that must be fixed before the change ships.
func (v *Verdict) Blocking() []Finding {
	var out []Finding
	if v == nil {
		return out
	}
	for _, f := range v.Findings {
		if !strings.EqualFold(f.Severity, "minor") {
			out = append(out, f)
		}
	}
	return out
}

// Triage is a weak model's rating of an item.
type Triage struct {
	Difficulty string `json:"difficulty"`
	Summary    string `json:"summary"`
}

var jsonBlock = regexp.MustCompile("(?s)```(?:json)?\\s*\\n(\\{.*?\\})\\s*\\n```")

// LastJSON decodes the last JSON object an answer gives in a fenced block, or failing that the
// last line that is one, into out.
func LastJSON(answer string, out any) bool {
	blocks := jsonBlock.FindAllStringSubmatch(answer, -1)
	for i := len(blocks) - 1; i >= 0; i-- {
		if json.Unmarshal([]byte(blocks[i][1]), out) == nil {
			return true
		}
	}
	lines := strings.Split(answer, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if strings.HasPrefix(line, "{") && json.Unmarshal([]byte(line), out) == nil {
			return true
		}
	}
	return false
}

// ParseVerdict reads a review's verdict. A review that gives none is a blocking finding itself.
func ParseVerdict(answer string) *Verdict {
	v := &Verdict{}
	if !LastJSON(answer, v) || v.Verdict == "" {
		return &Verdict{Verdict: "request_changes", Summary: "The review gave no verdict.",
			Findings: []Finding{{Severity: "blocking", Problem: "The reviewer's answer had no JSON verdict block; review again."}}}
	}
	return v
}

// Write saves v as indented JSON.
func Write(path string, v any) error {
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}

// Read loads JSON from path into v.
func Read(path string, v any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, v)
}
