// Package prompts renders the templates in .nightbot/prompts/ and fences text that came from
// GitHub, so the model reads it as data rather than as instructions.
package prompts

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"text/template"

	"nightbot/internal/config"
)

// Templates are the prompt templates, read once from the trusted checkout before any model
// runs, so nothing a model writes to .nightbot/prompts/ can change a later call's prompt.
type Templates map[string]string

// Load reads every template in .nightbot/prompts/.
func Load(cfg *config.Config) (Templates, error) {
	t := Templates{}
	for _, name := range config.PromptNames {
		text, err := os.ReadFile(filepath.Join(cfg.Root, config.Dir, "prompts", name+".md"))
		if err != nil {
			return nil, err
		}
		t[name] = string(text)
	}
	return t, nil
}

// Render fills the template name (system, build, fix, review, ask, triage) with vars. A
// template reads a variable as {{.name}}; one it names but vars lacks is empty.
func (t Templates) Render(cfg *config.Config, name string, vars map[string]string) (string, error) {
	text, ok := t[name]
	if !ok {
		return "", fmt.Errorf("no prompt template %q", name)
	}
	tmpl, err := template.New(name).Option("missingkey=zero").Parse(text)
	if err != nil {
		return "", fmt.Errorf("prompts/%s.md: %w", name, err)
	}
	data := map[string]string{"repo": cfg.Repo, "default_branch": cfg.DefaultBranch,
		"bot_login": cfg.BotLogin, "command_prefix": cfg.CommandPrefix}
	if cfg.Forked() { // the model works on the product, not on the repository that queues it
		data["repo"], data["default_branch"] = cfg.Upstream.Repo, cfg.Upstream.DefaultBranch
	}
	for k, v := range vars {
		data[k] = v
	}
	var out bytes.Buffer
	if err := tmpl.Execute(&out, data); err != nil {
		return "", fmt.Errorf("prompts/%s.md: %w", name, err)
	}
	return out.String(), nil
}

const (
	fenceOpen  = "<untrusted-data"
	fenceClose = "</untrusted-data>"
)

// fenceTag is anything a model might read as the fence's own tag: any case, any spacing.
var fenceTag = regexp.MustCompile(`(?i)<\s*(/?)\s*untrusted\s*-\s*data`)

// Fence wraps text from outside (issue titles and bodies, comments, CI logs, a diff, another
// model's report) as data. Anything inside that looks like the fence's tag is defused, so the
// text cannot close the fence early and speak as the harness.
func Fence(source, text string) string {
	text = fenceTag.ReplaceAllString(text, "<$1untrusted\u200b-data")
	source = fenceTag.ReplaceAllString(strings.ReplaceAll(source, `"`, "'"), "<$1untrusted\u200b-data")
	return fmt.Sprintf("%s source=%q>\n%s\n%s", fenceOpen, source, strings.TrimRight(text, "\n"), fenceClose)
}

// Truncate keeps the first n bytes of s (on a rune boundary), saying how much it cut.
func Truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	cut := n
	for cut > 0 && (s[cut]&0xC0) == 0x80 {
		cut--
	}
	return s[:cut] + fmt.Sprintf("\n\n[… %d more bytes cut]", len(s)-cut)
}

// Tail keeps the last n bytes of s, saying how much it cut.
func Tail(s string, n int) string {
	if len(s) <= n {
		return s
	}
	start := len(s) - n
	for start < len(s) && (s[start]&0xC0) == 0x80 {
		start++
	}
	return fmt.Sprintf("[… %d earlier bytes cut]\n", start) + s[start:]
}
