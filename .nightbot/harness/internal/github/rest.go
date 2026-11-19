package github

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// REST is the Client over GitHub's REST API (and GraphQL for the two calls REST lacks).
type REST struct {
	Repo    string
	Token   string
	API     string // https://api.github.com, or GITHUB_API_URL on GitHub Enterprise Server
	GraphQL string
	HTTP    *http.Client
	// DryRun records every write on Log instead of sending it.
	DryRun bool
	Log    io.Writer
	// NoMerge makes MergePR refuse: set on the client for a repository a person merges into.
	NoMerge bool
}

// NewREST is a client for repo with token, reading GITHUB_API_URL, GITHUB_GRAPHQL_URL and
// NIGHTBOT_DRY_RUN from the environment.
func NewREST(repo, token string) *REST {
	api := strings.TrimRight(os.Getenv("GITHUB_API_URL"), "/")
	if api == "" {
		api = "https://api.github.com"
	}
	graphql := os.Getenv("GITHUB_GRAPHQL_URL")
	if graphql == "" {
		graphql = api + "/graphql"
	}
	return &REST{Repo: repo, Token: token, API: api, GraphQL: graphql,
		HTTP: &http.Client{Timeout: 60 * time.Second}, DryRun: os.Getenv("NIGHTBOT_DRY_RUN") != "",
		Log: os.Stderr}
}

// HTTPError is a response GitHub refused.
type HTTPError struct {
	Method, Path string
	Status       int
	Body         string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("github: %s %s: %d %s", e.Method, e.Path, e.Status, truncate(e.Body, 300))
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}

var nextLink = regexp.MustCompile(`<([^>]+)>;\s*rel="next"`)

// do sends one request and decodes the JSON answer into out (when out is not nil). A write in a
// dry run is logged and answered with nothing.
func (c *REST) do(method, path string, body, out any) (http.Header, error) {
	write := method != http.MethodGet
	if write && c.DryRun {
		data, _ := json.Marshal(body)
		fmt.Fprintf(c.Log, "dry run: %s %s %s\n", method, path, truncate(string(data), 500))
		return http.Header{}, nil
	}
	target := path
	if !strings.HasPrefix(path, "https://") && !strings.HasPrefix(path, "http://") {
		target = c.API + path
	}
	var payload []byte
	if body != nil {
		var err error
		if payload, err = json.Marshal(body); err != nil {
			return nil, err
		}
	}
	for attempt := 0; ; attempt++ {
		request, err := http.NewRequest(method, target, bytes.NewReader(payload))
		if err != nil {
			return nil, err
		}
		request.Header.Set("Accept", "application/vnd.github+json")
		request.Header.Set("X-GitHub-Api-Version", "2022-11-28")
		request.Header.Set("User-Agent", "nightbot-harness")
		if c.Token != "" {
			request.Header.Set("Authorization", "Bearer "+c.Token)
		}
		if body != nil {
			request.Header.Set("Content-Type", "application/json")
		}
		response, err := c.HTTP.Do(request)
		if err != nil {
			if attempt < 3 {
				time.Sleep(time.Duration(attempt+1) * 2 * time.Second)
				continue
			}
			return nil, err
		}
		data, _ := io.ReadAll(response.Body)
		response.Body.Close()
		status := response.StatusCode
		retryable := status >= 500 || status == 429 ||
			(status == 403 && (response.Header.Get("Retry-After") != "" || strings.Contains(string(data), "secondary rate limit")))
		if retryable && attempt < 3 {
			wait := time.Duration(attempt+1) * 5 * time.Second
			if s, err := strconv.Atoi(response.Header.Get("Retry-After")); err == nil && s < 120 {
				wait = time.Duration(s) * time.Second
			}
			time.Sleep(wait)
			continue
		}
		if status == http.StatusNotFound {
			return response.Header, fmt.Errorf("%w: %s %s", ErrNotFound, method, path)
		}
		if status >= 300 {
			return response.Header, &HTTPError{Method: method, Path: path, Status: status, Body: string(data)}
		}
		if out != nil && len(data) > 0 {
			if s, ok := out.(*string); ok {
				*s = string(data)
			} else if err := json.Unmarshal(data, out); err != nil {
				return response.Header, fmt.Errorf("github: %s %s: %w", method, path, err)
			}
		}
		return response.Header, nil
	}
}

// list follows the Link header's next pages, up to pages of them.
func list[T any](c *REST, path string, pages int) ([]T, error) {
	var all []T
	for page := 0; path != "" && page < pages; page++ {
		var batch []T
		header, err := c.do(http.MethodGet, path, nil, &batch)
		if err != nil {
			return nil, err
		}
		all = append(all, batch...)
		path = ""
		if m := nextLink.FindStringSubmatch(header.Get("Link")); m != nil {
			path = m[1]
		}
	}
	return all, nil
}

func (c *REST) repo(format string, args ...any) string {
	return "/repos/" + c.Repo + fmt.Sprintf(format, args...)
}

func (c *REST) GetFile(branch, path string) ([]byte, string, error) {
	var file struct {
		Content string `json:"content"`
		SHA     string `json:"sha"`
	}
	if _, err := c.do(http.MethodGet, c.repo("/contents/%s?ref=%s", path, url.QueryEscape(branch)), nil, &file); err != nil {
		return nil, "", err
	}
	content, err := base64.StdEncoding.DecodeString(strings.ReplaceAll(file.Content, "\n", ""))
	return content, file.SHA, err
}

func (c *REST) PutFile(branch, path string, content []byte, sha, message string) error {
	body := map[string]any{"message": message, "branch": branch,
		"content": base64.StdEncoding.EncodeToString(content)}
	if sha != "" {
		body["sha"] = sha
	}
	_, err := c.do(http.MethodPut, c.repo("/contents/%s", path), body, nil)
	var httpErr *HTTPError
	if errors.As(err, &httpErr) && (httpErr.Status == http.StatusConflict ||
		(httpErr.Status == http.StatusUnprocessableEntity && strings.Contains(httpErr.Body, "sha"))) {
		return ErrConflict
	}
	return err
}

func (c *REST) BranchExists(branch string) (bool, error) {
	_, err := c.do(http.MethodGet, c.repo("/branches/%s", url.PathEscape(branch)), nil, nil)
	if errors.Is(err, ErrNotFound) {
		return false, nil
	}
	return err == nil, err
}

func (c *REST) CreateOrphanBranch(branch, path string, content []byte, message string) error {
	var tree, commit struct {
		SHA string `json:"sha"`
	}
	if _, err := c.do(http.MethodPost, c.repo("/git/trees"), map[string]any{"tree": []map[string]string{
		{"path": path, "mode": "100644", "type": "blob", "content": string(content)}}}, &tree); err != nil {
		return err
	}
	if _, err := c.do(http.MethodPost, c.repo("/git/commits"), map[string]any{
		"message": message, "tree": tree.SHA, "parents": []string{}}, &commit); err != nil {
		return err
	}
	_, err := c.do(http.MethodPost, c.repo("/git/refs"), map[string]string{
		"ref": "refs/heads/" + branch, "sha": commit.SHA}, nil)
	return err
}

func (c *REST) DeleteBranch(branch string) error {
	_, err := c.do(http.MethodDelete, c.repo("/git/refs/heads/%s", branch), nil, nil)
	if errors.Is(err, ErrNotFound) {
		return nil
	}
	var httpErr *HTTPError
	if errors.As(err, &httpErr) && httpErr.Status == http.StatusUnprocessableEntity {
		return nil // already gone
	}
	return err
}

func (c *REST) GetIssue(number int) (*Issue, error) {
	issue := &Issue{}
	_, err := c.do(http.MethodGet, c.repo("/issues/%d", number), nil, issue)
	return issue, err
}

func (c *REST) ListIssues(label, state string) ([]*Issue, error) {
	path := c.repo("/issues?state=%s&per_page=100", state)
	if label != "" {
		path += "&labels=" + url.QueryEscape(label)
	}
	return list[*Issue](c, path, 10)
}

func (c *REST) CreateIssue(title, body string, labels []string) (*Issue, error) {
	issue := &Issue{}
	_, err := c.do(http.MethodPost, c.repo("/issues"), map[string]any{
		"title": title, "body": body, "labels": labels}, issue)
	return issue, err
}

func (c *REST) UpdateIssueBody(number int, body string) error {
	_, err := c.do(http.MethodPatch, c.repo("/issues/%d", number), map[string]string{"body": body}, nil)
	return err
}

func (c *REST) ListComments(number int) ([]*Comment, error) {
	return list[*Comment](c, c.repo("/issues/%d/comments?per_page=100", number), 10)
}

func (c *REST) CreateComment(number int, body string) (int64, error) {
	var comment Comment
	_, err := c.do(http.MethodPost, c.repo("/issues/%d/comments", number), map[string]string{"body": body}, &comment)
	return comment.ID, err
}

func (c *REST) UpdateComment(id int64, body string) error {
	_, err := c.do(http.MethodPatch, c.repo("/issues/comments/%d", id), map[string]string{"body": body}, nil)
	return err
}

func (c *REST) AddReaction(commentID int64, content string, review bool) error {
	kind := "issues"
	if review {
		kind = "pulls"
	}
	_, err := c.do(http.MethodPost, c.repo("/%s/comments/%d/reactions", kind, commentID),
		map[string]string{"content": content}, nil)
	return err
}

func (c *REST) AddLabels(number int, labels ...string) error {
	_, err := c.do(http.MethodPost, c.repo("/issues/%d/labels", number), map[string]any{"labels": labels}, nil)
	return err
}

func (c *REST) RemoveLabel(number int, label string) error {
	_, err := c.do(http.MethodDelete, c.repo("/issues/%d/labels/%s", number, url.PathEscape(label)), nil, nil)
	if errors.Is(err, ErrNotFound) {
		return nil
	}
	return err
}

func (c *REST) EnsureLabel(name, color, description string) error {
	body := map[string]string{"name": name, "color": color, "description": description}
	_, err := c.do(http.MethodPost, c.repo("/labels"), body, nil)
	var httpErr *HTTPError
	if errors.As(err, &httpErr) && httpErr.Status == http.StatusUnprocessableEntity {
		_, err = c.do(http.MethodPatch, c.repo("/labels/%s", url.PathEscape(name)), body, nil)
	}
	return err
}

func (c *REST) GetPR(number int) (*PR, error) {
	pr := &PR{}
	_, err := c.do(http.MethodGet, c.repo("/pulls/%d", number), nil, pr)
	return pr, err
}

func (c *REST) FindPR(head string) (*PR, error) {
	if !strings.Contains(head, ":") {
		head = strings.SplitN(c.Repo, "/", 2)[0] + ":" + head
	}
	var prs []*PR
	if _, err := c.do(http.MethodGet, c.repo("/pulls?state=open&head=%s", url.QueryEscape(head)), nil, &prs); err != nil {
		return nil, err
	}
	if len(prs) == 0 {
		return nil, nil
	}
	return prs[0], nil
}

func (c *REST) CreatePR(head, base, title, body string, draft bool) (*PR, error) {
	pr := &PR{}
	_, err := c.do(http.MethodPost, c.repo("/pulls"), map[string]any{
		"head": head, "base": base, "title": title, "body": body, "draft": draft}, pr)
	return pr, err
}

func (c *REST) UpdatePR(number int, title, body string) error {
	_, err := c.do(http.MethodPatch, c.repo("/pulls/%d", number), map[string]string{"title": title, "body": body}, nil)
	return err
}

func (c *REST) ListReviews(number int) ([]*Review, error) {
	return list[*Review](c, c.repo("/pulls/%d/reviews?per_page=100", number), 5)
}

func (c *REST) CreateReview(number int, body string) error {
	_, err := c.do(http.MethodPost, c.repo("/pulls/%d/reviews", number),
		map[string]string{"body": body, "event": "COMMENT"}, nil)
	return err
}

func (c *REST) graphql(query string, variables map[string]any) error {
	var out struct {
		Errors []struct {
			Message string `json:"message"`
		} `json:"errors"`
	}
	if _, err := c.do(http.MethodPost, c.GraphQL, map[string]any{"query": query, "variables": variables}, &out); err != nil {
		return err
	}
	if len(out.Errors) > 0 {
		return fmt.Errorf("github graphql: %s", out.Errors[0].Message)
	}
	return nil
}

func (c *REST) MarkReady(pr *PR) error {
	if !pr.Draft {
		return nil
	}
	return c.graphql(`mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { clientMutationId } }`,
		map[string]any{"id": pr.NodeID})
}

func (c *REST) EnableAutoMerge(pr *PR, method, headSHA string) error {
	if c.NoMerge {
		return ErrMergeForbidden
	}
	vars := map[string]any{"id": pr.NodeID, "head": headSHA}
	if method != "" {
		vars["method"] = strings.ToUpper(method)
	}
	return c.graphql(`mutation($id: ID!, $method: PullRequestMergeMethod, $head: GitObjectID) {
  enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: $method, expectedHeadOid: $head}) { clientMutationId } }`,
		vars)
}

// DisableAutoMerge turns auto-merge off (an error that it was not on is no error) and converts
// the pull request back to a draft.
func (c *REST) DisableAutoMerge(pr *PR) error {
	err := c.graphql(`mutation($id: ID!) { disablePullRequestAutoMerge(input: {pullRequestId: $id}) { clientMutationId } }`,
		map[string]any{"id": pr.NodeID})
	if err != nil && !strings.Contains(strings.ToLower(err.Error()), "not enabled") {
		return err
	}
	if pr.Draft {
		return nil
	}
	return c.graphql(`mutation($id: ID!) { convertPullRequestToDraft(input: {pullRequestId: $id}) { clientMutationId } }`,
		map[string]any{"id": pr.NodeID})
}

// Protection reads what a branch requires from classic branch protection and from rulesets,
// both readable with read access.
func (c *REST) Protection(branch string) (*Protection, error) {
	var b struct {
		Protection struct {
			RequiredStatusChecks struct {
				Contexts []string `json:"contexts"`
				Checks   []struct {
					Context string `json:"context"`
				} `json:"checks"`
			} `json:"required_status_checks"`
		} `json:"protection"`
	}
	if _, err := c.do(http.MethodGet, c.repo("/branches/%s", url.PathEscape(branch)), nil, &b); err != nil {
		return nil, err
	}
	p := &Protection{}
	seen := map[string]bool{}
	add := func(name string) {
		if name != "" && !seen[name] {
			seen[name] = true
			p.Checks = append(p.Checks, name)
		}
	}
	for _, name := range b.Protection.RequiredStatusChecks.Contexts {
		add(name)
	}
	for _, check := range b.Protection.RequiredStatusChecks.Checks {
		add(check.Context)
	}
	var rules []struct {
		Type       string `json:"type"`
		Parameters struct {
			RequiredStatusChecks []struct {
				Context string `json:"context"`
			} `json:"required_status_checks"`
		} `json:"parameters"`
	}
	if _, err := c.do(http.MethodGet, c.repo("/rules/branches/%s", url.PathEscape(branch)), nil, &rules); err == nil {
		for _, rule := range rules {
			switch rule.Type {
			case "required_status_checks":
				for _, check := range rule.Parameters.RequiredStatusChecks {
					add(check.Context)
				}
			case "merge_queue":
				p.MergeQueue = true
			}
		}
	}
	return p, nil
}

// PutRuleset creates the ruleset of that name, or updates it in place.
func (c *REST) PutRuleset(name string, ruleset map[string]any) error {
	var existing []struct {
		ID   int64  `json:"id"`
		Name string `json:"name"`
	}
	if _, err := c.do(http.MethodGet, c.repo("/rulesets?per_page=100"), nil, &existing); err != nil {
		return err
	}
	body := map[string]any{"name": name}
	for k, v := range ruleset {
		body[k] = v
	}
	for _, r := range existing {
		if r.Name == name {
			_, err := c.do(http.MethodPut, c.repo("/rulesets/%d", r.ID), body, nil)
			return err
		}
	}
	_, err := c.do(http.MethodPost, c.repo("/rulesets"), body, nil)
	return err
}

func (c *REST) DispatchWorkflow(file, ref string, inputs map[string]string) error {
	_, err := c.do(http.MethodPost, c.repo("/actions/workflows/%s/dispatches", file),
		map[string]any{"ref": ref, "inputs": inputs}, nil)
	return err
}

func (c *REST) ListRuns(file, status string) ([]*Run, error) {
	var out struct {
		Runs []*Run `json:"workflow_runs"`
	}
	_, err := c.do(http.MethodGet, c.repo("/actions/workflows/%s/runs?status=%s&per_page=50", file, status), nil, &out)
	return out.Runs, err
}

func (c *REST) GetRun(id int64) (*Run, error) {
	run := &Run{}
	_, err := c.do(http.MethodGet, c.repo("/actions/runs/%d", id), nil, run)
	return run, err
}

func (c *REST) CancelRun(id int64) error {
	_, err := c.do(http.MethodPost, c.repo("/actions/runs/%d/cancel", id), nil, nil)
	var httpErr *HTTPError
	if errors.As(err, &httpErr) && httpErr.Status == http.StatusConflict {
		return nil // it had already finished
	}
	return err
}

func (c *REST) FailedJobs(runID int64) ([]*Job, error) {
	var out struct {
		Jobs []*Job `json:"jobs"`
	}
	if _, err := c.do(http.MethodGet, c.repo("/actions/runs/%d/jobs?filter=latest&per_page=100", runID), nil, &out); err != nil {
		return nil, err
	}
	var failed []*Job
	for _, job := range out.Jobs {
		if job.Conclusion == "failure" || job.Conclusion == "timed_out" {
			failed = append(failed, job)
		}
	}
	return failed, nil
}

// JobLog is a job's log; GitHub answers with a redirect to storage, which net/http follows
// without the Authorization header.
func (c *REST) JobLog(jobID int64) (string, error) {
	var text string
	_, err := c.do(http.MethodGet, c.repo("/actions/jobs/%d/logs", jobID), nil, &text)
	return text, err
}

func (c *REST) Whoami() (string, error) {
	var user User
	_, err := c.do(http.MethodGet, "/user", nil, &user)
	return user.Login, err
}

func (c *REST) UpdateRepo(settings map[string]any) error {
	_, err := c.do(http.MethodPatch, "/repos/"+c.Repo, settings, nil)
	return err
}

func (c *REST) ProtectBranch(branch string, checks []string) error {
	_, err := c.do(http.MethodPut, c.repo("/branches/%s/protection", url.PathEscape(branch)), map[string]any{
		"required_status_checks":        map[string]any{"strict": false, "contexts": checks},
		"enforce_admins":                false,
		"required_pull_request_reviews": nil,
		"restrictions":                  nil,
	}, nil)
	return err
}

func (c *REST) Repository() (*RepoInfo, error) {
	info := &RepoInfo{}
	_, err := c.do(http.MethodGet, "/repos/"+c.Repo, nil, info)
	return info, err
}

func (c *REST) ListAssigned(login string) ([]*Issue, error) {
	return list[*Issue](c, c.repo("/issues?state=open&per_page=100&assignee=%s", url.QueryEscape(login)), 5)
}

func (c *REST) CloseIssue(number int, reason string) error {
	body := map[string]string{"state": "closed"}
	if reason != "" {
		body["state_reason"] = reason
	}
	_, err := c.do(http.MethodPatch, c.repo("/issues/%d", number), body, nil)
	return err
}

func (c *REST) ClosePR(number int) error {
	_, err := c.do(http.MethodPatch, c.repo("/pulls/%d", number), map[string]string{"state": "closed"}, nil)
	return err
}

func (c *REST) BranchSHA(branch string) (string, error) {
	var ref struct {
		Object struct {
			SHA string `json:"sha"`
		} `json:"object"`
	}
	_, err := c.do(http.MethodGet, c.repo("/git/ref/heads/%s", branch), nil, &ref)
	return ref.Object.SHA, err
}

func (c *REST) CreateBranch(branch, sha string) error {
	_, err := c.do(http.MethodPost, c.repo("/git/refs"), map[string]string{"ref": "refs/heads/" + branch, "sha": sha}, nil)
	return err
}

func (c *REST) ResetBranch(branch, sha string) error {
	_, err := c.do(http.MethodPatch, c.repo("/git/refs/heads/%s", branch), map[string]any{"sha": sha, "force": true}, nil)
	return err
}

func (c *REST) SyncFork(branch string) (string, error) {
	var out struct {
		MergeType string `json:"merge_type"`
	}
	_, err := c.do(http.MethodPost, c.repo("/merge-upstream"), map[string]string{"branch": branch}, &out)
	var httpErr *HTTPError
	if errors.As(err, &httpErr) && httpErr.Status == http.StatusConflict {
		return "", ErrConflict
	}
	return out.MergeType, err
}

// Checks reads a commit's check runs and its commit statuses (the older API some CI uses).
func (c *REST) Checks(sha string) ([]*CheckRun, error) {
	var out []*CheckRun
	for page := 1; page <= 5; page++ {
		var batch struct {
			Total int         `json:"total_count"`
			Runs  []*CheckRun `json:"check_runs"`
		}
		if _, err := c.do(http.MethodGet, c.repo("/commits/%s/check-runs?filter=latest&per_page=100&page=%d", sha, page), nil, &batch); err != nil {
			return nil, err
		}
		out = append(out, batch.Runs...)
		if len(out) >= batch.Total || len(batch.Runs) == 0 {
			break
		}
	}
	var combined struct {
		Statuses []struct {
			Context   string `json:"context"`
			State     string `json:"state"`
			TargetURL string `json:"target_url"`
		} `json:"statuses"`
	}
	if _, err := c.do(http.MethodGet, c.repo("/commits/%s/status?per_page=100", sha), nil, &combined); err != nil {
		return nil, err
	}
	for _, s := range combined.Statuses {
		run := &CheckRun{Name: s.Context, Status: "completed", Conclusion: s.State, HTMLURL: s.TargetURL}
		if s.State == "pending" {
			run.Status, run.Conclusion = "in_progress", ""
		}
		out = append(out, run)
	}
	return out, nil
}

func (c *REST) MergePR(number int, method, sha string) error {
	if c.NoMerge {
		return ErrMergeForbidden
	}
	_, err := c.do(http.MethodPut, c.repo("/pulls/%d/merge", number), map[string]string{"merge_method": method, "sha": sha}, nil)
	return err
}

func (c *REST) ListReviewComments(number int) ([]*ReviewComment, error) {
	return list[*ReviewComment](c, c.repo("/pulls/%d/comments?per_page=100", number), 5)
}

func (c *REST) RequestReviewers(number int, logins []string) error {
	_, err := c.do(http.MethodPost, c.repo("/pulls/%d/requested_reviewers", number), map[string]any{"reviewers": logins}, nil)
	return err
}

func (c *REST) ListOpenPRs() ([]*PR, error) {
	return list[*PR](c, c.repo("/pulls?state=open&per_page=100"), 10)
}

func (c *REST) MarkDraft(pr *PR) error {
	if pr.Draft {
		return nil
	}
	return c.graphql(`mutation($id: ID!) { convertPullRequestToDraft(input: {pullRequestId: $id}) { clientMutationId } }`,
		map[string]any{"id": pr.NodeID})
}

func (c *REST) PRFiles(number int) ([]string, error) {
	files, err := list[*struct {
		Filename string `json:"filename"`
	}](c, c.repo("/pulls/%d/files?per_page=100", number), 30)
	var names []string
	for _, f := range files {
		names = append(names, f.Filename)
	}
	return names, err
}

func (c *REST) ActionsEnabled() (bool, error) {
	var out struct {
		Enabled bool `json:"enabled"`
	}
	_, err := c.do(http.MethodGet, c.repo("/actions/permissions"), nil, &out)
	return out.Enabled, err
}

func (c *REST) EnableActions() error {
	if _, err := c.do(http.MethodPut, c.repo("/actions/permissions"), map[string]any{"enabled": true, "allowed_actions": "all"}, nil); err != nil {
		return err
	}
	var out struct {
		Workflows []struct {
			ID    int64  `json:"id"`
			State string `json:"state"`
		} `json:"workflows"`
	}
	if _, err := c.do(http.MethodGet, c.repo("/actions/workflows?per_page=100"), nil, &out); err != nil {
		return err
	}
	for _, w := range out.Workflows {
		if w.State == "disabled_fork" || w.State == "disabled_inactivity" {
			if _, err := c.do(http.MethodPut, c.repo("/actions/workflows/%d/enable", w.ID), nil, nil); err != nil {
				return err
			}
		}
	}
	return nil
}

func (c *REST) PinIssue(issue *Issue) error {
	return c.graphql(`mutation($id: ID!) { pinIssue(input: {issueId: $id}) { clientMutationId } }`,
		map[string]any{"id": issue.NodeID})
}
