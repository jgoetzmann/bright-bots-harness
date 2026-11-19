package config

// BuiltinCLIs are the CLIs the harness knows without configuration. An entry of the same name
// under "clis" in config.json replaces one whole; a new name adds a CLI (see docs/providers.md).
func BuiltinCLIs() map[string]CLI {
	// Network tools are denied to the model: they would only slow a determined one down, but
	// they keep an honest mistake from reaching outside the runner.
	deny := "Bash(curl:*),Bash(wget:*),Bash(ssh:*),Bash(scp:*),Bash(gh:*),Bash(git push:*)"
	return map[string]CLI{
		// Claude Code. A secret login is the token `claude setup-token` prints (a Pro or Max
		// subscription), or set secret_env to ANTHROPIC_API_KEY for an API key.
		"claude": {
			Install: "npm install -g @anthropic-ai/claude-code",
			// --setting-sources user: the checkout's own .claude/ settings (hooks among them) never
			// load, since in fork mode they are another repository's to write.
			Argv: []string{"claude", "--print", "--output-format", "json", "--model", "{model}",
				"--max-turns", "{max_turns}", "--strict-mcp-config", "--setting-sources", "user",
				"--append-system-prompt-file", "{system_file}"},
			WriteArgs:  []string{"--permission-mode", "bypassPermissions", "--disallowedTools", deny},
			ReadArgs:   []string{"--permission-mode", "bypassPermissions", "--disallowedTools", "Edit,Write,NotebookEdit," + deny},
			EffortArgs: []string{"--effort", "{effort}"},
			Prompt:     "stdin",
			Answer:     "json:result",
			SecretEnv:  "CLAUDE_CODE_OAUTH_TOKEN",
			RateLimit:  []string{`(?i)claude ai usage limit reached`},
		},
		// OpenAI Codex. A ChatGPT login is ~/.codex/auth.json after `codex login`; it refreshes
		// itself, so it is best kept on a machine (login "machine"). An API key works as a
		// secret through secret_env OPENAI_API_KEY.
		"codex": {
			Install: "npm install -g @openai/codex",
			Argv: []string{"codex", "exec", "--skip-git-repo-check", "--cd", "{cwd}", "--model", "{model}",
				"--output-last-message", "{answer_file}"},
			WriteArgs:  []string{"--sandbox", "workspace-write"},
			ReadArgs:   []string{"--sandbox", "read-only"},
			EffortArgs: []string{"-c", `model_reasoning_effort="{effort}"`},
			Tail:       []string{"-"},
			Prompt:     "stdin",
			Answer:     "file",
			SecretFile: "~/.codex/auth.json",
		},
		// Google's Gemini CLI, with an API key from Google AI Studio or a machine login.
		"gemini": {
			Install:   "npm install -g @google/gemini-cli",
			Argv:      []string{"gemini", "--model", "{model}", "--output-format", "json"},
			WriteArgs: []string{"--yolo"},
			ReadArgs:  []string{"--yolo"},
			Prompt:    "stdin",
			Answer:    "json:response",
			SecretEnv: "GEMINI_API_KEY",
		},
	}
}
