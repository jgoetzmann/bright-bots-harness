// Package cli is the nightbot command line: one subcommand per job of the workflows, plus the
// ones a person runs.
package cli

import (
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"nightbot/internal/bot"
	"nightbot/internal/config"
	"nightbot/internal/github"
	"nightbot/internal/gitx"
	"nightbot/internal/job"
	"nightbot/internal/model"
	"nightbot/internal/work"
)

const usage = `nightbot: a night bot harness for GitHub Actions

Run in the repository (or below it). Commands:

  validate                 check .nightbot/ and the workflows, offline
  setup [--repo-settings]  labels and the state branch (with --repo-settings: auto-merge, branch protection)
  doctor                   say what is still missing
  event                    answer the GitHub event in GITHUB_EVENT_PATH (nightbot-events.yml)
  sweep                    release lost claims, start runs for free lanes, redraw the status issue
  plan --out FILE [--item N] [--mode M] [--force]   claim the next item (the run's plan job)
  install --plan FILE      install the plan's CLI on a GitHub-hosted runner
  work --plan FILE --out DIR [--product DIR]        the model job: no GitHub writes
  deliver --plan FILE --out DIR [--work-status S] [--product DIR]   push, open pull requests, comment

In fork mode (config.json "upstream"), --product is the clone of the upstream repository the
model works in and deliver pushes from: origin the upstream repository, "fork" the bot's fork.
  status                   print the status
  providers [--offline]    print the providers and whether each can start work

Environment: NIGHTBOT_GITHUB_TOKEN (the bot's token), GITHUB_TOKEN (the run's), NIGHTBOT_SECRETS_SET
(names of the provider secrets that are set), NIGHTBOT_MODEL_SECRET (the model job's one login),
NIGHTBOT_DRY_RUN=1 (record GitHub writes instead of sending them).
`

// Main runs the command line and returns the exit code.
func Main(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 || args[0] == "-h" || args[0] == "--help" || args[0] == "help" {
		fmt.Fprint(stdout, usage)
		return 0
	}
	command, rest := args[0], args[1:]
	root, err := config.FindRoot(".")
	if err != nil {
		fmt.Fprintln(stderr, "nightbot:", err)
		return 2
	}
	cfg, err := config.Load(root)
	if err != nil {
		fmt.Fprintln(stderr, "nightbot:", err)
		return 2
	}
	if err := runCommand(cfg, command, rest, stdout, stderr); err != nil {
		fmt.Fprintln(stderr, "nightbot:", err)
		return 1
	}
	return 0
}

func runCommand(cfg *config.Config, command string, args []string, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(stderr)
	planFile := flags.String("plan", "", "the plan file")
	out := flags.String("out", "", "the output file or directory")
	item := flags.Int("item", 0, "one issue or pull request")
	mode := flags.String("mode", "auto", "one action: auto, plan, build, revise, review, ask, triage")
	product := flags.String("product", "", "fork mode: the clone of the upstream repository")
	force := flags.Bool("force", false, "outside the providers' hours")
	repoSettings := flags.Bool("repo-settings", false, "also change the repository's settings")
	workStatus := flags.String("work-status", "", "the model job's result")
	offline := flags.Bool("offline", false, "read nothing from GitHub")
	if err := flags.Parse(args); err != nil {
		return err
	}

	switch command {
	case "validate":
		if err := cfg.CheckWorkflows(); err != nil {
			return err
		}
		for field, value := range map[string]string{"repo": cfg.Repo, "bot_login": cfg.BotLogin, "operator": cfg.Operator} {
			if strings.Contains(value, "OWNER") || strings.Contains(value, "BOT_LOGIN") || strings.Contains(value, "OPERATOR_LOGIN") {
				fmt.Fprintf(stderr, "warning: config.json %s is still the placeholder %q (install.sh fills it in)\n", field, value)
			}
		}
		fmt.Fprintf(stdout, "ok: %d providers, %d checks, %d CLIs\n", len(cfg.Providers.Providers), len(cfg.Checks), len(cfg.CLIs))
		return nil
	case "install":
		p, err := readPlan(*planFile)
		if err != nil {
			return err
		}
		cli := cfg.CLIs[p.CLI]
		if !config.GitHubHosted(p.RunsOn) || cli.Install == "" {
			fmt.Fprintln(stdout, "nothing to install")
			return nil
		}
		cmd := exec.Command("bash", "-c", cli.Install)
		cmd.Stdout, cmd.Stderr = stdout, stderr
		return cmd.Run()
	case "work":
		return doWork(cfg, *planFile, *out, *product, stderr)
	case "providers":
		if *offline {
			for _, p := range cfg.Providers.Ordered() {
				fmt.Fprintf(stdout, "%-12s %-7s enabled=%-5v login=%-7s runs_on=%-16s lanes=%d strongest=%s\n",
					p.ID, p.CLI, p.Enabled, p.Login, p.RunsOn, p.Lanes, p.Strongest())
			}
			return nil
		}
	}

	b := newBot(cfg)
	switch command {
	case "setup":
		if err := b.Setup(*repoSettings); err != nil {
			return err
		}
		fmt.Fprintln(stdout, "labels and the state branch are in place")
		for _, line := range b.Doctor() {
			fmt.Fprintln(stdout, line)
		}
		return nil
	case "doctor":
		for _, line := range b.Doctor() {
			fmt.Fprintln(stdout, line)
		}
		return nil
	case "event":
		ev, err := bot.ReadEvent(os.Getenv("GITHUB_EVENT_PATH"))
		if err != nil {
			return err
		}
		return b.Handle(os.Getenv("GITHUB_EVENT_NAME"), ev)
	case "sweep":
		return b.Sweep()
	case "status":
		snap, err := b.Snapshot(false)
		if err != nil {
			return err
		}
		fmt.Fprint(stdout, b.StatusMarkdown(snap, true))
		return nil
	case "providers":
		snap, err := b.Snapshot(false)
		if err != nil {
			return err
		}
		for _, a := range snap.Avail {
			state := fmt.Sprintf("%d free", a.Free)
			if a.Free == 0 {
				state = a.Reason
			}
			fmt.Fprintf(stdout, "%-12s %-7s %-16s %s\n", a.Provider.ID, a.Provider.CLI, a.Provider.RunsOn, state)
		}
		return nil
	case "plan":
		if *out == "" {
			return fmt.Errorf("plan needs --out")
		}
		p, why, err := b.Plan(bot.PlanOptions{Item: *item, Mode: *mode, Force: *force})
		if err != nil {
			return err
		}
		if p == nil {
			fmt.Fprintln(stdout, "nothing to do:", why)
		} else {
			if err := os.MkdirAll(filepath.Dir(*out), 0o755); err != nil {
				return err
			}
			if err := job.Write(*out, p); err != nil {
				return err
			}
			fmt.Fprintf(stdout, "plan: %s #%d on %s (%s, %s tier), runs on %s\n", p.Action, p.Number, p.Provider, p.Model, p.Tier, p.RunsOn)
		}
		return writeOutputs(bot.Outputs(cfg, p))
	case "deliver":
		p, err := readPlan(*planFile)
		if err != nil {
			return err
		}
		if cfg.Forked() {
			if *product == "" {
				return fmt.Errorf("fork mode: deliver needs --product, the clone it pushes to the fork from")
			}
			b.Workspace = *product
		}
		return b.Deliver(p, *out, *workStatus)
	}
	return fmt.Errorf("unknown command %q (nightbot --help lists them)", command)
}

func readPlan(path string) (*job.Plan, error) {
	if path == "" {
		return nil, fmt.Errorf("--plan is required")
	}
	p := &job.Plan{}
	return p, job.Read(path, p)
}

func newBot(cfg *config.Config) *bot.Bot {
	gh := github.NewREST(cfg.Repo, bot.Token())
	actions := gh
	if t := os.Getenv("GITHUB_TOKEN"); t != "" {
		actions = github.NewREST(cfg.Repo, t)
	}
	b := bot.New(cfg, gh, actions)
	if cfg.Forked() {
		b.Fork = github.NewREST(cfg.Upstream.Fork, bot.Token())
		upstream := github.NewREST(cfg.Upstream.Repo, bot.Token())
		upstream.NoMerge = true // a person merges upstream (gate 2)
		b.Upstream = upstream
	}
	return b
}

// writeOutputs appends the job's outputs to GITHUB_OUTPUT, or prints them outside Actions.
func writeOutputs(outputs map[string]string) error {
	path := os.Getenv("GITHUB_OUTPUT")
	var lines []string
	for _, k := range []string{"action", "number", "provider", "runs_on", "secret", "cli", "hosted", "install", "product", "fork"} {
		if v, ok := outputs[k]; ok {
			lines = append(lines, k+"="+strings.ReplaceAll(v, "\n", " "))
		}
	}
	if path == "" {
		fmt.Println(strings.Join(lines, "\n"))
		return nil
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = fmt.Fprintln(f, strings.Join(lines, "\n"))
	return err
}

// doWork is the model job. It checks the plan against providers.json before it touches the
// secret, so a plan cannot send a login to a runner its provider does not name.
func doWork(cfg *config.Config, planFile, out, product string, log io.Writer) error {
	p, err := readPlan(planFile)
	if err != nil {
		return err
	}
	if out == "" {
		return fmt.Errorf("work needs --out")
	}
	prov, ok := cfg.Providers.Providers[p.Provider]
	if !ok || prov.RunsOn != p.RunsOn || prov.CLI != p.CLI || prov.Login != p.Login || (p.Login == "secret" && prov.Secret != p.Secret) {
		return fmt.Errorf("the plan's provider %q does not match providers.json; refusing to run", p.Provider)
	}
	seatOK := false
	for _, s := range prov.Models {
		seatOK = seatOK || (s.Model == p.Model && s.Tier == p.Tier)
	}
	if !seatOK {
		return fmt.Errorf("the plan's model %q is not one of %s's", p.Model, p.Provider)
	}
	if err := os.MkdirAll(out, 0o755); err != nil {
		return err
	}
	if err := hide(); err != nil {
		return fmt.Errorf("could not make the work job's process private: %w", err)
	}
	secret := ""
	if p.Login == "secret" {
		secret = os.Getenv("NIGHTBOT_MODEL_SECRET")
		if secret == "" {
			return fmt.Errorf("the plan logs in with the secret %s, but NIGHTBOT_MODEL_SECRET is empty", p.Secret)
		}
	}
	os.Unsetenv("NIGHTBOT_MODEL_SECRET")
	temp := os.Getenv("RUNNER_TEMP")
	if temp == "" {
		temp = os.TempDir()
	}
	// The harness's own fetches need the run's read-only token in a private repository (the
	// checkout keeps no credentials). They all happen before the first model call, through Auth;
	// every git command after that runs without it, so no hook or filter a model leaves in the
	// checkout can catch it. The model's environment never has it.
	auth := &gitx.Repo{Dir: cfg.Root, Env: gitx.AuthEnv(os.Getenv("GITHUB_SERVER_URL"), os.Getenv("GITHUB_TOKEN"))}
	w := &work.Worker{Cfg: cfg, Plan: p, Out: out, Repo: &gitx.Repo{Dir: cfg.Root}, Auth: auth, Log: log,
		Runner:   &model.Runner{Name: p.CLI, CLI: cfg.CLIs[p.CLI], Secret: secret, TempDir: temp, Location: cfg.Location},
		Deadline: time.Now().Add(time.Duration(cfg.JobBudgetMinutes) * time.Minute)}
	if cfg.Forked() {
		// The product is public: its fetches need no token, and the run's token is this
		// repository's alone. The plan job read any CI logs a fix needs, with the bot's token.
		if product == "" {
			return fmt.Errorf("fork mode: work needs --product, the clone of %s", cfg.Upstream.Repo)
		}
		w.Repo, w.Auth = &gitx.Repo{Dir: product}, &gitx.Repo{Dir: product}
		w.BaseRemote, w.BranchRemote = "origin", "fork"
	} else if token := os.Getenv("GITHUB_TOKEN"); token != "" {
		w.CI = ciLogs{github.NewREST(cfg.Repo, token)}
	}
	os.Unsetenv("GITHUB_TOKEN")
	r := w.Do()
	fmt.Fprintf(log, "nightbot: %s #%d: %s in %.0f model minutes\n", p.Action, p.Number, r.Outcome, r.Minutes)
	return nil
}

type ciLogs struct{ gh *github.REST }

func (c ciLogs) FailedJobs(runID int64) ([]*work.JobRef, error) {
	jobs, err := c.gh.FailedJobs(runID)
	var refs []*work.JobRef
	for _, j := range jobs {
		refs = append(refs, &work.JobRef{ID: j.ID, Name: j.Name})
	}
	return refs, err
}

func (c ciLogs) JobLog(jobID int64) (string, error) { return c.gh.JobLog(jobID) }
