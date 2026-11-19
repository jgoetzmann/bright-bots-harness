# shellcheck shell=bash
# Sourced by each cloud's on-machine.sh, ON YOUR COMPUTER (bash 3.2 or later, so a Mac's own bash
# will do). Two functions:
#
#   nightbot_pack SCRIPT
#       A gzipped tar on stdout: SCRIPT's directory as machine/<dir>/, beside .nightbot's
#       providers.json (PROVIDERS overrides it) and config.json, if present. It is the layout of
#       .nightbot/ itself, so a script finds ../../providers.json on the machine as it does here.
#       Markdown, Python and __pycache__ stay behind.
#
#   nightbot_remote FROM SCRIPT [ARG …]
#       One line of POSIX sh that runs SCRIPT as root on the machine: it unpacks the pack (FROM is
#       `stdin`, or the pack itself in base64) into a fresh /root/nightbot-run.*, runs SCRIPT there
#       with ARGs and stdin /dev/null, keeps its output in /var/log/nightbot/<script>.log, removes
#       the directory and ends with the line `nightbot-exit=<status>`. While it runs, the machine's
#       idle stop counts the machine busy.

nightbot_pack() {
  local script="$1" nightbot providers
  nightbot="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  providers="${PROVIDERS:-$nightbot/providers.json}"
  (
    set -e
    stage="$(mktemp -d)"
    trap 'rm -rf "$stage"' EXIT
    mkdir "$stage/machine"
    cp -R "$(cd "$(dirname "$script")" && pwd)" "$stage/machine/"
    if [ -f "$providers" ]; then cp "$providers" "$stage/providers.json"; fi
    if [ -f "$nightbot/config.json" ]; then cp "$nightbot/config.json" "$stage/config.json"; fi
    # A Mac's tar would pack its extended attributes, which GNU tar on the machine warns about.
    COPYFILE_DISABLE=1 tar czf - --exclude='*.md' --exclude='*.py' --exclude=__pycache__ \
      -C "$stage" .
  )
}

nightbot_quote() {  # $1 as one single-quoted word for any POSIX shell
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

nightbot_remote() {
  local from="$1" script="$2" dir name args="" arg unpack
  shift 2
  dir="$(nightbot_quote "$(basename "$(cd "$(dirname "$script")" && pwd)")")"
  name="$(nightbot_quote "$(basename "$script")")"
  for arg in "$@"; do args="$args $(nightbot_quote "$arg")"; done
  # $d and $s are the machine's, expanded there.
  # shellcheck disable=SC2016
  if [ "$from" = stdin ]; then
    unpack='tar xzf - -C "$d"'
  else
    unpack="echo $from | base64 -d | tar xzf - -C \"\$d\""
  fi
  # shellcheck disable=SC2016
  printf '%s\n' 'd=$(mktemp -d /root/nightbot-run.XXXXXX) && '"$unpack"' && mkdir -p -m 700 /var/log/nightbot && { cd / && bash "$d"/machine/'"$dir/$name$args"' </dev/null; echo $? >"$d/.status"; } 2>&1 | tee /var/log/nightbot/'"$name"'.log; s=$(cat "$d/.status" 2>/dev/null || echo 1); rm -rf "$d"; echo "nightbot-exit=$s"; exit "$s"'
}
