package github

import (
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

// Fake is an in-memory GitHub for the tests: enough behaviour for the harness's flows, and a
// record of every write.
type Fake struct {
	mu         sync.Mutex
	Files      map[string]fakeFile // "branch:path"
	Branches   map[string]bool
	Issues     map[int]*Issue
	Comments   map[int][]*Comment
	PRs        map[int]*PR
	Reviews    map[int][]*Review
	Runs       map[int64]*Run
	Jobs       map[int64][]*Job
	Logs       map[int64]string
	Labels     map[string]bool
	Required   []string
	MergeQueue bool
	Rulesets   map[string]map[string]any
	Login      string
	RepoName   string
	Reactions  map[int64][]string
	Dispatched []map[string]string
	AutoMerge  map[int]string
	Cancelled  []int64
	Repo       map[string]any
	Protected  map[string][]string
	nextID     int64
	nextNumber int
	// ConflictOnce makes the next PutFile lose its compare-and-set once.
	ConflictOnce bool

	// Fork mode and the plan gate.
	Info           RepoInfo
	CheckRuns      map[string][]*CheckRun // by commit
	ReviewComments map[int][]*ReviewComment
	Requested      map[int][]string
	BranchHeads    map[string]string
	Merged         []int
	Synced         []string
	// NoMerge makes MergePR and EnableAutoMerge refuse, as the upstream repository's client does.
	NoMerge bool
	// ChangedFiles is each pull request's changed paths, for PRFiles; ActionsOff turns Actions off.
	ChangedFiles map[int][]string
	ActionsOff   bool
	Pinned       []int
	// OnMerge and OnSync stand in for what GitHub does to the repository's git, in the tests.
	OnMerge func(pr *PR, method string) error
	OnSync  func(branch string) (string, error)
}

type fakeFile struct {
	content []byte
	sha     string
}

// NewFake is an empty repository whose numbers start at 1.
func NewFake() *Fake {
	return &Fake{Files: map[string]fakeFile{}, Branches: map[string]bool{"main": true},
		Issues: map[int]*Issue{}, Comments: map[int][]*Comment{}, PRs: map[int]*PR{},
		Reviews: map[int][]*Review{}, Runs: map[int64]*Run{}, Jobs: map[int64][]*Job{},
		Logs: map[int64]string{}, Labels: map[string]bool{}, Reactions: map[int64][]string{},
		AutoMerge: map[int]string{}, Repo: map[string]any{}, Protected: map[string][]string{},
		Rulesets: map[string]map[string]any{}, CheckRuns: map[string][]*CheckRun{},
		ReviewComments: map[int][]*ReviewComment{}, Requested: map[int][]string{}, BranchHeads: map[string]string{},
		Login: "nightbot", RepoName: "o/r", nextID: 1000, nextNumber: 1}
}

// AddIssue files an issue, or a pull request with pr set, and returns its number.
func (f *Fake) AddIssue(title, body, author string, labels ...string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.addIssue(title, body, author, labels)
}

func (f *Fake) addIssue(title, body, author string, labels []string) int {
	n := f.nextNumber
	f.nextNumber++
	issue := &Issue{Number: n, Title: title, Body: body, State: "open", User: User{Login: author},
		CreatedAt: time.Date(2026, 1, 1, 0, n, 0, 0, time.UTC), HTMLURL: fmt.Sprintf("https://github.com/%s/issues/%d", f.RepoName, n)}
	for _, l := range labels {
		issue.Labels = append(issue.Labels, Label{Name: l})
	}
	issue.UpdatedAt = issue.CreatedAt
	f.Issues[n] = issue
	return n
}

// AddComment adds a comment by author to an issue and returns its id.
func (f *Fake) AddComment(number int, author, association, body string) int64 {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.nextID++
	f.Comments[number] = append(f.Comments[number], &Comment{ID: f.nextID, Body: body,
		User: User{Login: author}, AuthorAssociation: association, CreatedAt: time.Now().UTC()})
	return f.nextID
}

// LabelsOf lists an issue's labels, sorted.
func (f *Fake) LabelsOf(number int) []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var names []string
	if issue := f.Issues[number]; issue != nil {
		for _, l := range issue.Labels {
			names = append(names, l.Name)
		}
	}
	sort.Strings(names)
	return names
}

// CommentsOn is the bodies of an issue's comments.
func (f *Fake) CommentsOn(number int) []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var bodies []string
	for _, c := range f.Comments[number] {
		bodies = append(bodies, c.Body)
	}
	return bodies
}

func (f *Fake) GetFile(branch, path string) ([]byte, string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	file, ok := f.Files[branch+":"+path]
	for name, sha := range f.BranchHeads { // a ref may be a commit: a branch's head
		if !ok && sha == branch {
			file, ok = f.Files[name+":"+path]
		}
	}
	if !ok {
		return nil, "", fmt.Errorf("%w: %s:%s", ErrNotFound, branch, path)
	}
	return append([]byte(nil), file.content...), file.sha, nil
}

func (f *Fake) PutFile(branch, path string, content []byte, sha, message string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.ConflictOnce {
		f.ConflictOnce = false
		f.nextID++
		old := f.Files[branch+":"+path]
		f.Files[branch+":"+path] = fakeFile{content: old.content, sha: fmt.Sprint("sha", f.nextID)}
		return ErrConflict
	}
	if f.Files[branch+":"+path].sha != sha {
		return ErrConflict
	}
	f.nextID++
	f.Files[branch+":"+path] = fakeFile{content: append([]byte(nil), content...), sha: fmt.Sprint("sha", f.nextID)}
	f.BranchHeads[branch] = fmt.Sprint("commit", f.nextID)
	return nil
}

// fresh is a copy of a pull request whose head, for a branch here the fake moved, is the
// branch's head now, as GitHub's would be.
func (f *Fake) fresh(pr *PR) *PR {
	copied := *pr
	if sha, ok := f.BranchHeads[pr.Head.Ref]; ok && strings.EqualFold(pr.HeadRepo(), f.RepoName) && pr.State == "open" {
		copied.Head.SHA = sha
	}
	return &copied
}

func (f *Fake) BranchExists(branch string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.Branches[branch], nil
}

func (f *Fake) CreateOrphanBranch(branch, path string, content []byte, message string) error {
	f.mu.Lock()
	f.Branches[branch] = true
	f.mu.Unlock()
	return f.PutFile(branch, path, content, "", message)
}

func (f *Fake) DeleteBranch(branch string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.Branches, branch)
	return nil
}

func (f *Fake) GetIssue(number int) (*Issue, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	issue, ok := f.Issues[number]
	if !ok {
		return nil, fmt.Errorf("%w: issue %d", ErrNotFound, number)
	}
	copied := *issue
	copied.Labels = append([]Label(nil), issue.Labels...)
	return &copied, nil
}

func (f *Fake) ListIssues(label, state string) ([]*Issue, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []*Issue
	numbers := make([]int, 0, len(f.Issues))
	for n := range f.Issues {
		numbers = append(numbers, n)
	}
	sort.Ints(numbers)
	for _, n := range numbers {
		issue := f.Issues[n]
		if (state == "all" || issue.State == state) && (label == "" || issue.HasLabel(label)) {
			copied := *issue
			copied.Labels = append([]Label(nil), issue.Labels...)
			out = append(out, &copied)
		}
	}
	return out, nil
}

func (f *Fake) CreateIssue(title, body string, labels []string) (*Issue, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := f.addIssue(title, body, f.Login, labels)
	return f.Issues[n], nil
}

func (f *Fake) UpdateIssueBody(number int, body string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Issues[number].Body = body
	return nil
}

func (f *Fake) ListComments(number int) ([]*Comment, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]*Comment(nil), f.Comments[number]...), nil
}

func (f *Fake) CreateComment(number int, body string) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.nextID++
	f.Comments[number] = append(f.Comments[number], &Comment{ID: f.nextID, Body: body,
		User: User{Login: f.Login, Type: "User"}, CreatedAt: time.Now().UTC()})
	return f.nextID, nil
}

func (f *Fake) UpdateComment(id int64, body string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, comments := range f.Comments {
		for _, c := range comments {
			if c.ID == id {
				c.Body = body
				return nil
			}
		}
	}
	return fmt.Errorf("%w: comment %d", ErrNotFound, id)
}

func (f *Fake) AddReaction(commentID int64, content string, review bool) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Reactions[commentID] = append(f.Reactions[commentID], content)
	return nil
}

func (f *Fake) AddLabels(number int, labels ...string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	issue, ok := f.Issues[number]
	if !ok {
		return fmt.Errorf("%w: issue %d", ErrNotFound, number)
	}
	for _, l := range labels {
		if !issue.HasLabel(l) {
			issue.Labels = append(issue.Labels, Label{Name: l})
		}
		if pr := f.PRs[number]; pr != nil {
			pr.Labels = issue.Labels
		}
	}
	return nil
}

func (f *Fake) RemoveLabel(number int, label string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	issue, ok := f.Issues[number]
	if !ok {
		return nil
	}
	kept := issue.Labels[:0]
	for _, l := range issue.Labels {
		if l.Name != label {
			kept = append(kept, l)
		}
	}
	issue.Labels = kept
	if pr := f.PRs[number]; pr != nil {
		pr.Labels = kept
	}
	return nil
}

func (f *Fake) EnsureLabel(name, color, description string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Labels[name] = true
	return nil
}

func (f *Fake) GetPR(number int) (*PR, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	pr, ok := f.PRs[number]
	if !ok {
		return nil, fmt.Errorf("%w: pull request %d", ErrNotFound, number)
	}
	return f.fresh(pr), nil
}

func (f *Fake) FindPR(head string) (*PR, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	repo := f.RepoName
	if owner, branch, ok := strings.Cut(head, ":"); ok {
		repo, head = f.forkName(owner), branch
	}
	for _, pr := range f.PRs {
		if pr.Head.Ref == head && pr.State == "open" && strings.EqualFold(pr.HeadRepo(), repo) {
			return f.fresh(pr), nil
		}
	}
	return nil, nil
}

func (f *Fake) CreatePR(head, base, title, body string, draft bool) (*PR, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := f.addIssue(title, body, f.Login, nil)
	f.Issues[n].PullRequest = &struct {
		URL string `json:"url"`
	}{URL: fmt.Sprint("pulls/", n)}
	pr := &PR{Number: n, NodeID: fmt.Sprint("PR_", n), Title: title, Body: body, State: "open",
		Draft: draft, User: User{Login: f.Login}, HTMLURL: fmt.Sprintf("https://github.com/%s/pull/%d", f.RepoName, n)}
	repo := f.RepoName
	if owner, branch, ok := strings.Cut(head, ":"); ok {
		repo, head = f.forkName(owner), branch
	}
	pr.Head.Ref = head
	pr.Head.Repo = &struct {
		FullName string `json:"full_name"`
	}{FullName: repo}
	f.Issues[n].HTMLURL = pr.HTMLURL
	pr.Base.Ref = base
	f.PRs[n] = pr
	return f.fresh(pr), nil
}

func (f *Fake) UpdatePR(number int, title, body string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if pr := f.PRs[number]; pr != nil {
		pr.Title, pr.Body = title, body
	}
	return nil
}

func (f *Fake) ListReviews(number int) ([]*Review, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]*Review(nil), f.Reviews[number]...), nil
}

func (f *Fake) CreateReview(number int, body string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Reviews[number] = append(f.Reviews[number], &Review{Body: body, State: "COMMENTED", User: User{Login: f.Login}})
	return nil
}

func (f *Fake) MarkReady(pr *PR) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if p := f.PRs[pr.Number]; p != nil {
		p.Draft = false
	}
	return nil
}

func (f *Fake) EnableAutoMerge(pr *PR, method, headSHA string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.NoMerge {
		return ErrMergeForbidden
	}
	if p := f.PRs[pr.Number]; p != nil && p.Head.SHA != "" && headSHA != p.Head.SHA {
		return fmt.Errorf("head is %s, not the expected %s", p.Head.SHA, headSHA)
	}
	f.AutoMerge[pr.Number] = method + "@" + headSHA
	return nil
}

func (f *Fake) DisableAutoMerge(pr *PR) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.AutoMerge, pr.Number)
	if p := f.PRs[pr.Number]; p != nil {
		p.Draft = true
	}
	return nil
}

// SetHead records a pull request's head, as GitHub would after a push.
func (f *Fake) SetHead(number int, sha string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if p := f.PRs[number]; p != nil {
		p.Head.SHA = sha
	}
}

func (f *Fake) Protection(branch string) (*Protection, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return &Protection{Checks: append([]string(nil), f.Required...), MergeQueue: f.MergeQueue}, nil
}

func (f *Fake) PutRuleset(name string, ruleset map[string]any) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Rulesets[name] = ruleset
	for _, rule := range ruleset["rules"].([]map[string]any) {
		switch rule["type"] {
		case "merge_queue":
			f.MergeQueue = true
		case "required_status_checks":
			f.Required = nil
			for _, c := range rule["parameters"].(map[string]any)["required_status_checks"].([]map[string]string) {
				f.Required = append(f.Required, c["context"])
			}
		}
	}
	return nil
}

func (f *Fake) DispatchWorkflow(file, ref string, inputs map[string]string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	record := map[string]string{"file": file, "ref": ref}
	for k, v := range inputs {
		record[k] = v
	}
	f.Dispatched = append(f.Dispatched, record)
	return nil
}

func (f *Fake) ListRuns(file, status string) ([]*Run, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []*Run
	for _, run := range f.Runs {
		if run.Status == status {
			out = append(out, run)
		}
	}
	return out, nil
}

func (f *Fake) GetRun(id int64) (*Run, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	run, ok := f.Runs[id]
	if !ok {
		return nil, fmt.Errorf("%w: run %d", ErrNotFound, id)
	}
	return run, nil
}

func (f *Fake) CancelRun(id int64) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Cancelled = append(f.Cancelled, id)
	return nil
}

func (f *Fake) FailedJobs(runID int64) ([]*Job, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.Jobs[runID], nil
}

func (f *Fake) JobLog(jobID int64) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.Logs[jobID], nil
}

func (f *Fake) Whoami() (string, error) { return f.Login, nil }

func (f *Fake) UpdateRepo(settings map[string]any) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	for k, v := range settings {
		f.Repo[k] = v
	}
	return nil
}

func (f *Fake) ProtectBranch(branch string, checks []string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Protected[branch] = checks
	f.Required = checks
	return nil
}

// HasComment says whether any comment on the issue contains text.
func (f *Fake) HasComment(number int, text string) bool {
	for _, body := range f.CommentsOn(number) {
		if strings.Contains(body, text) {
			return true
		}
	}
	return false
}

// forkName is the repository a fork's owner holds, named as this one is (the tests' convention).
func (f *Fake) forkName(owner string) string {
	_, name, _ := strings.Cut(f.RepoName, "/")
	return owner + "/" + name
}

func (f *Fake) Repository() (*RepoInfo, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	info := f.Info
	if info.FullName == "" {
		info.FullName = f.RepoName
	}
	return &info, nil
}

func (f *Fake) ListAssigned(login string) ([]*Issue, error) {
	all, _ := f.ListIssues("", "open")
	var out []*Issue
	for _, issue := range all {
		for _, a := range issue.Assignees {
			if strings.EqualFold(a.Login, login) {
				out = append(out, issue)
				break
			}
		}
	}
	return out, nil
}

func (f *Fake) CloseIssue(number int, reason string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if issue := f.Issues[number]; issue != nil {
		issue.State = "closed"
	}
	return nil
}

func (f *Fake) ClosePR(number int) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if pr := f.PRs[number]; pr != nil {
		pr.State = "closed"
		f.Issues[number].State = "closed"
	}
	return nil
}

func (f *Fake) BranchSHA(branch string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if sha, ok := f.BranchHeads[branch]; ok {
		return sha, nil
	}
	if f.Branches[branch] {
		return "sha-of-" + branch, nil
	}
	return "", fmt.Errorf("%w: branch %s", ErrNotFound, branch)
}

func (f *Fake) CreateBranch(branch, sha string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Branches[branch] = true
	f.BranchHeads[branch] = sha
	return nil
}

func (f *Fake) ResetBranch(branch, sha string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.BranchHeads[branch] = sha
	return nil
}

func (f *Fake) SyncFork(branch string) (string, error) {
	f.mu.Lock()
	f.Synced = append(f.Synced, branch)
	sync := f.OnSync
	f.mu.Unlock()
	if sync != nil {
		return sync(branch)
	}
	return "none", nil
}

func (f *Fake) Checks(sha string) ([]*CheckRun, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []*CheckRun
	for _, c := range f.CheckRuns[sha] {
		copied := *c
		out = append(out, &copied)
	}
	return out, nil
}

func (f *Fake) MergePR(number int, method, sha string) error {
	f.mu.Lock()
	if f.NoMerge {
		f.mu.Unlock()
		return ErrMergeForbidden
	}
	pr := f.PRs[number]
	if pr == nil || pr.State != "open" {
		f.mu.Unlock()
		return fmt.Errorf("%w: open pull request %d", ErrNotFound, number)
	}
	current := f.fresh(pr)
	if sha != "" && current.Head.SHA != "" && current.Head.SHA != sha {
		f.mu.Unlock()
		return fmt.Errorf("head is %s, not %s", current.Head.SHA, sha)
	}
	merge := f.OnMerge
	copied := *current
	f.mu.Unlock()
	if merge != nil {
		if err := merge(&copied, method); err != nil {
			return err
		}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	pr.State, pr.Merged = "closed", true
	f.Issues[number].State = "closed"
	f.Merged = append(f.Merged, number)
	return nil
}

func (f *Fake) ListReviewComments(number int) ([]*ReviewComment, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]*ReviewComment(nil), f.ReviewComments[number]...), nil
}

func (f *Fake) RequestReviewers(number int, logins []string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Requested[number] = append(f.Requested[number], logins...)
	return nil
}

// AddPRComment adds a comment by author to a pull request, as AddComment does to an issue.
func (f *Fake) AddPRComment(number int, author, body string) int64 {
	return f.AddComment(number, author, "CONTRIBUTOR", body)
}

func (f *Fake) ListOpenPRs() ([]*PR, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var numbers []int
	for n, pr := range f.PRs {
		if pr.State == "open" {
			numbers = append(numbers, n)
		}
	}
	sort.Ints(numbers)
	var out []*PR
	for _, n := range numbers {
		out = append(out, f.fresh(f.PRs[n]))
	}
	return out, nil
}

func (f *Fake) MarkDraft(pr *PR) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if p := f.PRs[pr.Number]; p != nil {
		p.Draft = true
	}
	return nil
}

// PRFiles is the paths set in ChangedFiles, or for a pull request from a branch the fake wrote
// files to through its API, those files.
func (f *Fake) PRFiles(number int) ([]string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if files, ok := f.ChangedFiles[number]; ok {
		return append([]string(nil), files...), nil
	}
	var out []string
	if pr := f.PRs[number]; pr != nil {
		for key := range f.Files {
			if branch, path, _ := strings.Cut(key, ":"); branch == pr.Head.Ref {
				out = append(out, path)
			}
		}
	}
	sort.Strings(out)
	return out, nil
}

func (f *Fake) ActionsEnabled() (bool, error) { return !f.ActionsOff, nil }

func (f *Fake) EnableActions() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.ActionsOff = false
	return nil
}

func (f *Fake) PinIssue(issue *Issue) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Pinned = append(f.Pinned, issue.Number)
	return nil
}
