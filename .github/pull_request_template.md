## What

<!-- One or two sentences. -->

## Why

<!-- The issue this serves. Link it. -->

## Kind

- [ ] A plan the bot wrote (`plans/<n>-<slug>.md`): approve it with `/nightbot approve`, not by merging
- [ ] The harness (`.nightbot/harness/`), its prompts or its configuration
- [ ] Workflows or repository settings (`.github/`)
- [ ] Documentation only

## Checklist

- [ ] `nightbot-selftest` is green (`gofmt`, `go vet`, `go test ./...`, `nightbot validate`)
- [ ] No secret, token or private data appears in the diff
- [ ] New `.nightbot/trust.txt` lines are people who may command the bot; approvers are also in `config.json` `approvers`
