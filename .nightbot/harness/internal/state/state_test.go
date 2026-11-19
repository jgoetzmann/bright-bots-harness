package state

import (
	"testing"
	"time"

	"nightbot/internal/github"
)

func TestUpdate_RetriesALostCompareAndSet(t *testing.T) {
	gh := github.NewFake()
	store := &Store{GH: gh, Branch: "_state"}
	gh.CreateOrphanBranch("_state", File, []byte("{}\n"), "init")
	gh.ConflictOnce = true
	calls := 0
	s, err := store.Update("test", func(s *State) error {
		calls++
		s.Item(7).Failures++
		return nil
	})
	if err != nil || calls != 2 || s.Peek(7).Failures != 1 {
		t.Fatalf("calls %d, err %v, state %+v", calls, err, s)
	}
	again, _, _ := store.Load()
	if again.Peek(7).Failures != 1 {
		t.Fatal("the retry's write was lost")
	}
}

func TestUpdate_WritesNothingWhenNothingChanged(t *testing.T) {
	gh := github.NewFake()
	store := &Store{GH: gh, Branch: "_state"}
	gh.CreateOrphanBranch("_state", File, []byte("{}\n"), "init")
	_, sha, _ := store.Load()
	store.Update("noop", func(s *State) error { return nil })
	if _, after, _ := store.Load(); after != sha {
		t.Fatal("a no-op update wrote the file")
	}
}

func TestMinutesSince_CountsRunsInProgress(t *testing.T) {
	now := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	s := &State{Usage: map[string][]UsageEntry{"p": {{At: now.Add(-6 * time.Hour), Minutes: 50}, {At: now.Add(-time.Hour), Minutes: 10}}},
		Running: map[string]*Claim{"3": {Provider: "p", Started: now.Add(-30 * time.Minute)}}}
	if got := s.MinutesSince("p", now.Add(-5*time.Hour), now); got != 40 {
		t.Fatalf("5 h: %v", got)
	}
	s.Prune(now.Add(7 * 24 * time.Hour))
	if len(s.Usage["p"]) != 0 {
		t.Fatal("prune kept week-old usage")
	}
}
