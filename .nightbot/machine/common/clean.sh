#!/bin/bash
# Gives back the disk the night bot's jobs can do without. Runs ON THE MACHINE; setup.sh installs it
# as /usr/local/bin/nightbot-clean, and it runs:
#
#   - as root, every ten minutes (nightbot-clean.timer): itself as each agent-* user, then the
#     system's share (the journal over 200 MB, apt's package cache);
#   - as an agent user after every job, with --job, from the runners' job-completed hook
#     (nightbot-job-done.sh): that job's RUNNER_TEMP too;
#   - as an agent user by hand, or from a job that finds the disk short (no --job: a job calling it
#     keeps its own files).
#
# As a user it cleans only its own home and its own files in /tmp: package caches (npm, pnpm, yarn,
# pip) once none of its other jobs runs; in a runner with no job, what a job that died left in
# _work/_temp, and checkouts in _work untouched for WORK_DAYS (7); runner versions a self-update
# replaced; runner logs after 2 days; its /tmp leftovers after 6 hours; CLI session logs after 7
# days. All of it comes back by itself (from the network, or at the next job's checkout); logins and
# registrations stay. It never fails: every error is ignored.
#
#   sudo nightbot-clean                         # every agent user, then the system
#   sudo -u agent-codex -H nightbot-clean       # one user
set +e
if [ "$(id -u)" = 0 ]; then
  self="$(readlink -f "$0")"
  for home in /home/agent-*; do
    user="${home##*/}"
    id "$user" >/dev/null 2>&1 || continue
    runuser -u "$user" -- env -i HOME="$home" PATH=/usr/local/bin:/usr/bin:/bin "$self" \
      | logger -t nightbot-clean
  done
  journalctl --vacuum-size=200M >/dev/null 2>&1
  apt-get clean >/dev/null 2>&1
  exit 0
fi
home="${HOME:?}"
uid="$(id -u)"
old=60                    # minutes: an update, a session or a job still going writes fresher files
days="${WORK_DAYS:-7}"
used() { df --output=pcent / 2>/dev/null | tail -1 | tr -dc 0-9; }
before="$(used)"

# --job: the job that just ended kept its files in RUNNER_TEMP.
if [ "${1:-}" = --job ] && [ -n "${RUNNER_TEMP:-}" ] && [ -d "$RUNNER_TEMP" ]; then
  find "$RUNNER_TEMP" -mindepth 1 -delete 2>/dev/null
fi

# This user's jobs, one line each. When pgrep cannot tell (it exits 0 with some, 1 with none),
# nothing a job might be using is touched.
workers="$(pgrep -a -u "$uid" -f Runner.Worker)"
known=$?
[ "$known" -le 1 ] && known=1 || known=0
running="$(grep -c . <<<"$workers")"
mine=0                     # a job running this (the hook runs inside its job) is one of them
[ -n "${RUNNER_TEMP:-}" ] && mine=1
if [ "$known" = 1 ] && [ "$running" -le "$mine" ]; then
  rm -rf "$home/.npm/_cacache" "$home/.cache/pnpm" "$home/.local/share/pnpm/store" \
    "$home/.cache/yarn" "$home/.cache/pip" 2>/dev/null
fi

for runner in "$home"/actions-runner*/; do
  [ -d "$runner" ] || continue
  # Worktrees that lived in a job's RUNNER_TEMP are gone; forget them, or the next checkout cannot
  # take a branch one still claims.
  for repo in "$runner"_work/*/*/; do
    [ -d "$repo/.git" ] && git -C "$repo" worktree prune 2>/dev/null
  done
  if [ "$known" = 1 ] && ! grep -qF "$runner" <<<"$workers"; then
    # No job here: what a job that died before its hook left in _temp (the runner empties it only
    # when it next takes a job), and checkouts no job has touched for $days days.
    if [ -d "${runner}_work/_temp" ]; then
      find "${runner}_work/_temp" -mindepth 1 -maxdepth 1 -mmin +"$old" -exec rm -rf {} + 2>/dev/null
    fi
    for dir in "${runner}_work"/*/; do
      case "$(basename "$dir")" in _*) continue ;; esac  # the runner's own: _temp, _actions, _tool …
      [ -n "$(find "$dir" -maxdepth 3 -mtime -"$days" -print -quit 2>/dev/null)" ] || rm -rf "$dir"
    done
  fi
  # A runner that updated itself keeps the version it replaced (bin.<version>, externals.<version>,
  # hundreds of MB) beside the one bin and externals link to, and its download in _work/_update.
  for kind in bin externals; do
    [ -L "$runner$kind" ] || continue
    current="$(readlink -f "$runner$kind")"
    for version in "$runner$kind".*; do
      if [ ! -d "$version" ] || [ -L "$version" ]; then continue; fi
      [ "$(readlink -f "$version")" = "$current" ] && continue
      [ -n "$(find "$version" -maxdepth 0 -mmin +"$old")" ] && rm -rf "$version"
    done
  done
  if [ -d "${runner}_work/_update" ] \
     && [ -n "$(find "${runner}_work/_update" -maxdepth 0 -mmin +"$old")" ]; then
    rm -rf "${runner}_work/_update"
  fi
  find "${runner}_diag" -maxdepth 1 -type f -name '*.log' -mmin +2880 -delete 2>/dev/null
done

# This user's /tmp leftovers, once older than a job runs. (NIGHTBOT_TMP is for the tests.)
find "${NIGHTBOT_TMP:-/tmp}" -mindepth 1 -maxdepth 1 -user "$uid" -mmin +360 -exec rm -rf {} + 2>/dev/null
# The CLIs' own session logs; the logins beside them stay.
for sessions in "$home/.codex/sessions" "$home/.claude/projects" "$home/.gemini/tmp"; do
  [ -d "$sessions" ] || continue
  find "$sessions" -type f -mtime +7 -delete 2>/dev/null
  find "$sessions" -mindepth 1 -type d -empty -delete 2>/dev/null
done
echo "nightbot-clean: $(id -un): disk ${before:-?}% used before, $(used)% after"
exit 0
