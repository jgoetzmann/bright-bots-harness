package sched

import (
	"strconv"
	"strings"
	"testing"
	"time"

	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/state"
)

var now = time.Date(2026, 3, 1, 12, 0, 0, 0, time.UTC)

func testConfig() *config.Config {
	cfg := &config.Config{BotLogin: "bot", Triage: true, Location: time.UTC, Providers: &config.Providers{
		MaxParallel: 3,
		Priority:    []string{"big", "small"},
		Providers: map[string]*config.Provider{
			"big": {Enabled: true, CLI: "claude", Lanes: 2, Login: "secret", Secret: "BIG", Models: []config.Seat{
				{Model: "opus", Tier: "strong"}, {Model: "sonnet", Tier: "medium"}}},
			"small": {Enabled: true, CLI: "gemini", Lanes: 1, Login: "secret", Secret: "SMALL", Models: []config.Seat{
				{Model: "flash", Tier: "weak"}}},
		}}}
	for id, p := range cfg.Providers.Providers {
		p.ID = id
		p.Schedule.Mode = "always"
		p.RunsOn = "ubuntu-latest"
	}
	cfg.LabelPrefix = "bot:"
	cfg.ReviewRounds = map[string]int{"medium": 3, "hard": 6}
	return cfg
}

func issue(n int, labels ...string) *github.Issue {
	i := &github.Issue{Number: n, Title: "issue", State: "open", CreatedAt: now.Add(time.Duration(n) * time.Minute)}
	for _, l := range labels {
		i.Labels = append(i.Labels, github.Label{Name: l})
	}
	return i
}

func pr(n int, labels ...string) *github.Issue {
	i := issue(n, labels...)
	i.PullRequest = &struct {
		URL string `json:"url"`
	}{URL: "x"}
	return i
}

func botPR(n int, labels ...string) *github.Issue {
	i := pr(n, labels...)
	i.User.Login = "bot"
	return i
}

var secrets = map[string]bool{"BIG": true, "SMALL": true}

func TestQueue_Order(t *testing.T) {
	cfg := testConfig()
	st := &state.State{Queries: []*state.Query{{ID: "9", Number: 1, Author: "a", Text: "why?", Created: now}}}
	st.Item(4).Difficulty = "medium"
	st.Item(5).Difficulty = "medium"
	issues := []*github.Issue{
		issue(5, "bot:build"),
		issue(4, "bot:build", "priority:high"),
		issue(3, "bot:build", "human"),
		issue(2, "bot:build"), // unrated: triage first
		botPR(6, "bot:revise", "bot:pr"),
		pr(8, "bot:revise", "bot:pr"), // the label alone does not make a pull request the bot's
		pr(7, "bot:review"),
	}
	var got []string
	for _, it := range Queue(cfg, st, issues) {
		got = append(got, it.Action+"#"+strconv.Itoa(it.Number))
	}
	want := "ask#1 triage#2 build#4 revise#6 review#7 build#5"
	if strings.Join(got, " ") != want {
		t.Fatalf("got  %s\nwant %s", strings.Join(got, " "), want)
	}
}

func TestQueue_SkipsClaimedItemsAndIssuesWithAPullRequest(t *testing.T) {
	cfg := testConfig()
	st := &state.State{Running: map[string]*state.Claim{"1": {Number: 1}}}
	st.Item(2).PR = 9
	if q := Queue(cfg, st, []*github.Issue{issue(1, "bot:build"), issue(2, "bot:build")}); len(q) != 0 {
		t.Fatalf("queued %d", len(q))
	}
}

func TestAssign_WeakQueriesMediumBuildsStrongHard(t *testing.T) {
	cfg := testConfig()
	st := &state.State{}
	st.Item(2).Difficulty = "hard"
	queue := Queue(cfg, st, []*github.Issue{issue(2, "bot:build"), issue(3, "bot:build", "difficulty:medium")})
	queue = append([]*Item{{Number: 1, Action: Ask, Query: &state.Query{ID: "q"}}}, queue...)
	got := Assign(cfg, st, queue, Available(cfg, st, secrets, now, false))
	if got[0].Provider.ID != "small" || got[0].Seat.Model != "flash" {
		t.Errorf("the question went to %s/%s, not the weak model", got[0].Provider.ID, got[0].Seat.Model)
	}
	if got[1].Seat.Model != "opus" {
		t.Errorf("the hard build got %s", got[1].Seat.Model)
	}
	if got[2].Seat.Model != "sonnet" {
		t.Errorf("the medium build got %s, not the cheaper medium model", got[2].Seat.Model)
	}
}

func TestAssign_RespectsLanesAndMaxParallel(t *testing.T) {
	cfg := testConfig()
	cfg.Providers.MaxParallel = 2
	st := &state.State{}
	queue := Queue(cfg, st, []*github.Issue{issue(1, "bot:build", "difficulty:medium"),
		issue(2, "bot:build", "difficulty:medium"), issue(3, "bot:build", "difficulty:medium")})
	got := Assign(cfg, st, queue, Available(cfg, st, secrets, now, false))
	if got[0].Provider == nil || got[1].Provider == nil || got[2].Provider != nil {
		t.Fatalf("assignments: %+v %+v %+v", got[0], got[1], got[2])
	}
	if !strings.Contains(got[2].Waits, "max_parallel") {
		t.Errorf("waits: %s", got[2].Waits)
	}
}

func TestAssign_ReviewAvoidsTheBuilderWhenItCan(t *testing.T) {
	cfg := testConfig()
	cfg.Providers.Providers["other"] = &config.Provider{ID: "other", Enabled: true, Lanes: 1, Login: "machine",
		RunsOn: "box", Schedule: config.Schedule{Mode: "always"}, Models: []config.Seat{{Model: "o", Tier: "strong"}}}
	st := &state.State{}
	st.Item(5).LastProvider = "big"
	st.Item(5).Difficulty = "medium"
	queue := Queue(cfg, st, []*github.Issue{pr(5, "bot:review")})
	got := Assign(cfg, st, queue, Available(cfg, st, secrets, now, false))
	if got[0].Provider == nil || got[0].Provider.ID != "other" {
		t.Fatalf("the audit went to %+v", got[0].Provider)
	}
}

func TestAvailable_Reasons(t *testing.T) {
	cfg := testConfig()
	cfg.Providers.Providers["big"].Limits.FiveHourMinutes = 60
	cfg.Providers.Providers["small"].Schedule = config.Schedule{Mode: "window", Start: "22:00", End: "06:00"}
	st := &state.State{Usage: map[string][]state.UsageEntry{"big": {{At: now.Add(-time.Hour), Minutes: 61}}}}
	reasons := map[string]string{}
	for _, a := range Available(cfg, st, secrets, now, false) {
		reasons[a.Provider.ID] = a.Reason
	}
	if !strings.Contains(reasons["big"], "5-hour cap") || !strings.Contains(reasons["small"], "outside its hours") {
		t.Fatalf("%v", reasons)
	}
	for _, a := range Available(cfg, st, secrets, now, true) {
		if a.Provider.ID == "small" && a.Free != 1 {
			t.Errorf("force should ignore the hours: %+v", a)
		}
	}
	st.Parked = map[string]time.Time{"small": now.Add(time.Hour)}
	st.Suspended = map[string]string{"big": "testing"}
	for _, a := range Available(cfg, st, map[string]bool{}, now, true) {
		if a.Free != 0 {
			t.Errorf("%s should be held: %+v", a.Provider.ID, a)
		}
	}
}

func TestAssign_AProviderTakesOnlyItsActions(t *testing.T) {
	cfg := &config.Config{Providers: &config.Providers{MaxParallel: 4, Providers: map[string]*config.Provider{
		"day":   {ID: "day", Enabled: true, Lanes: 1, Actions: []string{"plan", "ask"}, Models: []config.Seat{{Model: "o", Tier: "strong"}}},
		"night": {ID: "night", Enabled: true, Lanes: 1, Models: []config.Seat{{Model: "o", Tier: "strong"}}},
	}}}
	avail := []*Availability{{Provider: cfg.Providers.Providers["day"], Free: 1}, {Provider: cfg.Providers.Providers["night"], Free: 0, Reason: "outside its hours"}}
	queue := []*Item{{Number: 1, Action: Build, Difficulty: "medium"}, {Number: 2, Action: Plan, Difficulty: "medium"}}
	got := Assign(cfg, &state.State{}, queue, avail)
	if got[0].Provider != nil || !strings.Contains(got[0].Waits, "night: outside its hours") || strings.Contains(got[0].Waits, "day") {
		t.Fatalf("the build went to %v (%s)", got[0].Provider, got[0].Waits)
	}
	if got[1].Provider == nil || got[1].Provider.ID != "day" {
		t.Fatalf("the plan should go to the day provider: %+v", got[1])
	}
}
