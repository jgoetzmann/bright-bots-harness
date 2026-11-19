package job

import "testing"

func TestParseVerdict(t *testing.T) {
	approve := "I checked everything.\n\n```json\n{\"verdict\": \"approve\", \"summary\": \"fine\", \"findings\": [{\"severity\": \"minor\", \"problem\": \"a nit\"}]}\n```\n"
	if v := ParseVerdict(approve); !v.Approves() || v.Summary != "fine" {
		t.Fatalf("%+v", v)
	}
	blocked := "```json\n{\"verdict\": \"approve\", \"summary\": \"ok\", \"findings\": [{\"severity\": \"blocking\", \"problem\": \"broken\"}]}\n```"
	if v := ParseVerdict(blocked); v.Approves() || len(v.Blocking()) != 1 {
		t.Fatalf("an approval with a blocking finding approved: %+v", v)
	}
	// The last block wins: a model that quotes an example verdict before its own.
	twice := "Example:\n```json\n{\"verdict\": \"approve\", \"summary\": \"x\", \"findings\": []}\n```\nMine:\n```json\n{\"verdict\": \"request_changes\", \"summary\": \"no\", \"findings\": [{\"severity\": \"blocking\", \"problem\": \"p\"}]}\n```"
	if v := ParseVerdict(twice); v.Approves() {
		t.Fatal("took the first block")
	}
	if v := ParseVerdict("looks good to me!"); v.Approves() || len(v.Blocking()) != 1 {
		t.Fatalf("no verdict should block: %+v", v)
	}
	if v := ParseVerdict(`{"verdict": "approve", "summary": "one line", "findings": []}`); !v.Approves() {
		t.Fatal("a bare JSON line")
	}
}
