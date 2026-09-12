# Host-side watchdog for the bb container (docs/delivery/DELIVERY-2-HANDOFF.md section 10;
# platform section 6.5).
# Hand-written; never machine-generated. Polls every 10 s from OUTSIDE the container (P10):
#   soft limits pause  - on battery, or non-container host CPU high and sustained (docker pause)
#   one limit kills    - stale HEARTBEAT, after a startup grace period (docker kill; the restart
#                        policy heals it and the gate re-runs)
#   hard limits stop   - free disk under the floor, weekly spend over the cap (docker stop; operator)
#   every N minutes    - push the branches the container committed. This process is the ONLY
#                        publisher in local mode (P5: the container commits, the host pushes).
# Pause state is derived from docker inspect on every poll, never from this process's variables.
# Coexistence (A44): this file is named so that rk's wildcard process kill cannot match it, and
# everything that looks for this process matches the FULL path of this file, never a wildcard.
#   .\local\watchdog-bb.ps1            persistent (bb-start.ps1 launches it minimised)
#   .\local\watchdog-bb.ps1 -Once      one pass: push what is owed, check once, exit
param(
    [string]$Work = (Join-Path (Split-Path $PSScriptRoot -Parent) "bb-work"),
    [string]$Container = "bb",
    [string]$EnvFile = (Join-Path (Split-Path $PSScriptRoot -Parent) ".env"),
    [int]$PollSeconds = 10,
    [int]$HeartbeatStaleSeconds = 180,
    [double]$MinFreeGB = 5.0,
    [int]$PushMinutes = 10,
    [int]$CpuHigh = 50,             # pause when non-container host CPU stays above this ...
    [int]$CpuLow = 30,              # ... unpause when it stays below this ...
    [int]$CpuSustainSeconds = 30,   # ... for this long
    [switch]$NoBatteryGuard,
    [switch]$Once
)
$ErrorActionPreference = "Continue"
$env:GIT_TERMINAL_PROMPT = "0"   # never block an unattended push on a credential prompt
$Work = ($Work -replace '\\', '/').TrimEnd('/')

# One persistent instance per host, found by this script's FULL path (never a wildcard - A44).
if (-not $Once) {
    $self = $PSCommandPath
    $twins = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and
        $_.CommandLine.IndexOf($self, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $_.CommandLine -notlike "*-Once*" }
    if (@($twins).Count -gt 0) {
        Write-Host "another bb watchdog is already running (pid $(@($twins | ForEach-Object { $_.ProcessId }) -join ', ')); exiting"
        exit 0
    }
}

function Stamp { return (Get-Date -Format s) }

# Battery guard: never run on battery. On battery -> docker pause; back on AC -> docker unpause.
# Pause is atomic, so the run resumes exactly where it was. Outranks the CPU logic.
Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
function On-Battery {
    try { return ([System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus -eq "Offline") } catch { return $false }
}
$pausedBattery = $false

function Read-EnvValue([string]$key) {
    # last assignment wins, surrounding quotes stripped - the rules the harness applies to .env
    $val = ""
    if (-not (Test-Path $EnvFile)) { return $val }
    $pattern = "^\s*" + [regex]::Escape($key) + "\s*=\s*(.*)$"
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match $pattern) { $val = $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $val
}

function Get-Spend {
    # window.spent_usd from the ledger the loop keeps at /work/state/ledger.json; 0 when absent
    $p = Join-Path $Work "state/ledger.json"
    if (-not (Test-Path $p)) { return 0.0 }
    try { return [double]((Get-Content $p -Raw | ConvertFrom-Json).window.spent_usd) } catch { return 0.0 }
}

function Container-UptimeSeconds {
    try {
        $st = docker inspect -f "{{.State.StartedAt}}" $Container 2>$null
        if (-not $st) { return 0 }
        return [int]((Get-Date).ToUniversalTime() - [datetime]::Parse($st).ToUniversalTime()).TotalSeconds
    } catch { return 0 }
}

# D67 / B304: the walk gh.push_branch makes before every push, mirrored for this publisher.
# Granting the machine PAT the `workflow` scope took away GitHub's own refusal of a push that
# edits a workflow, and this process pushes with that PAT: a guard that lived only in Python would
# leave local mode the unguarded half. The same `git log` (clone.harness_walk_argv) and the same
# rule: walk from the tip, stop at the first commit a harness email did not author - upstream's
# commits, relayed by a rebase, keep their authors - and refuse if any commit above that touches
# .github/. Past the cap it refuses rather than pass the rest unchecked. B312: and it refuses a
# clone whose history is substituted - a refs/replace/ entry, a grafts file, a shallow history -
# because git then shows the walk commits the push does not send (clone.substituted_history).
# harness/clone.py holds the Python half of every value below; local/preflight.py and
# tests/test_local_mode.py hold the two together. Returns one line per reason to refuse; nothing
# means the push may go.
function Get-UnpublishablePaths([string]$Clone, [string]$Ref) {
    $HarnessEmails = @("harness@brightboost-harness", "harness@localhost")
    $ProtectedPrefix = "/.github/"
    $ScanCommits = 100
    $rs = [string][char]30
    $us = [string][char]31
    $replaced = @(& git -C $Clone for-each-ref '--format=%(refname)' "refs/replace/" 2>$null)
    if ($LASTEXITCODE -ne 0) { return @("git for-each-ref failed (exit $LASTEXITCODE), so no commit was checked") }
    if ($replaced.Count -gt 0) { return @("the clone's history is substituted by refs/replace/ ($($replaced[0])), so no commit was checked") }
    $probe = @(& git -C $Clone rev-parse "--is-shallow-repository" --git-path "info/grafts" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $probe.Count -lt 2) { return @("git rev-parse failed (exit $LASTEXITCODE), so no commit was checked") }
    if ("$($probe[0])".Trim() -ne "false") { return @("the clone's history is shallow, so no commit was checked") }
    $grafts = "$($probe[1])".Trim()
    if (-not [System.IO.Path]::IsPathRooted($grafts)) { $grafts = Join-Path $Clone $grafts }
    if (Test-Path -LiteralPath $grafts) { return @("the clone's history is substituted by a grafts file ($grafts), so no commit was checked") }
    $lines = @(& git -C $Clone --no-replace-objects -c core.commitGraph=false -c core.quotepath=off -c log.showRoot=true log "--format=%x1e%H%x1f%ae" --name-only --no-renames --first-parent "--diff-merges=first-parent" -n ($ScanCommits + 1) $Ref -- 2>$null)
    if ($LASTEXITCODE -ne 0) { return @("git log $Ref failed (exit $LASTEXITCODE), so no commit was checked") }
    $found = @()
    $walked = 0
    $sha = ""
    foreach ($raw in $lines) {
        $line = "$raw"
        # Ordinal, always. PowerShell 7 compares with ICU, which treats U+001E as an
        # ignorable character: under it "".StartsWith($rs) is TRUE, so the blank line git
        # prints between a commit's header and its paths entered this branch and threw on
        # Substring(1). Windows PowerShell 5.1 (NLS) said false, so it passed there and
        # failed on both CI legs.
        if ($line.Length -eq 0) { continue }
        if ($line.StartsWith($rs, [System.StringComparison]::Ordinal)) {
            $fields = $line.Substring(1).Split($us)
            $email = ""
            if ($fields.Count -gt 1) { $email = $fields[1].Trim().ToLowerInvariant() }
            if ($HarnessEmails -notcontains $email) { break }
            if ($walked -ge $ScanCommits) { $found += "the top $ScanCommits commits are all the harness's, so the ones below were never checked"; break }
            $walked++
            $sha = $fields[0].Trim()
            continue
        }
        $p = $line.Replace([string][char]92, "/").Trim().Trim([char]96).Trim().Trim('"')
        if (-not $p) { continue }
        while ($p.StartsWith("./", [System.StringComparison]::Ordinal)) { $p = $p.Substring(2) }
        $p = "/" + $p.TrimStart("/")
        if ($p.Contains($ProtectedPrefix)) { $found += "harness commit $($sha.Substring(0, [math]::Min(12, $sha.Length))) touches $($p.Substring(1))" }
    }
    return $found
}

# Host-side push (P5). The loop's deliver stage cannot publish - it holds no GitHub credential - so
# it leaves the branch in its clone and writes runs/<item>/DELIVER.json. This pushes such a branch
# to the fork with the host's HARNESS_GITHUB_TOKEN under the rules gh.push_branch follows: only
# branches under harness/, only when the tip author is the harness identity (B139), only when no
# commit the harness authored touches .github/ (D67, Get-UnpublishablePaths above), the token
# never in a URL, an argv or a log line (it travels as GIT_CONFIG_* environment), and NEVER a bare
# --force: a lease, so a commit a human pushed to the same fork branch between two passes is not
# silently discarded. The lease must carry an explicit expected sha here - `--force-with-lease`
# with no value reads a remote-TRACKING ref, and pushing to a URL has none, so the bare form is
# rejected with "stale info" every time. The expected sha is the one this watchdog LAST LEFT on
# that branch, recorded in the PUSHED marker next to the manifest (empty = we have never pushed
# it, so the branch must not exist yet). Reading the fork's current sha and leasing against that
# would be `--force` spelled longer: it agrees with whatever a human just pushed. The marker also
# short-circuits an unchanged tip, so a rewritten one (a revise cycle) is pushed again and an
# unchanged one is not. Opening the upstream PR from that branch stays a human act here.
function Push-Delivered {
    $runs = Join-Path $Work "runs"
    if (-not (Test-Path $runs)) { return }
    $token = Read-EnvValue "HARNESS_GITHUB_TOKEN"
    $fork = Read-EnvValue "FORK_REPO"
    foreach ($dir in Get-ChildItem -Path $runs -Directory -ErrorAction SilentlyContinue) {
        $manifest = Join-Path $dir.FullName "DELIVER.json"
        if (-not (Test-Path $manifest)) { continue }
        $d = $null
        try { $d = Get-Content $manifest -Raw | ConvertFrom-Json } catch { Write-Host "$(Stamp) $($dir.Name): DELIVER.json unreadable"; continue }
        # Only keys deliver._write_record actually writes. This once probed branch_name,
        # remote_repo, clone_path, clone and workdir first - five names the producer has never
        # written. Every one read $null and fell through to the value below it, so the chains
        # looked like tolerance for several manifest versions while exactly one was ever parsed,
        # and a reader tuning the producer could not tell which key the consumer wanted.
        $branch = [string]$d.branch
        if (-not $branch) { continue }
        if ($branch -notlike "harness/*") { Write-Host "$(Stamp) $($dir.Name): refusing to push '$branch' (not under harness/)"; continue }
        # fork_repo is "" when FORK_REPO is unset in the container's .env, so the host .env is the
        # one real fallback here.
        $remote = [string]$d.fork_repo
        if (-not $remote) { $remote = $fork }
        if (-not $remote) { Write-Host "$(Stamp) $($dir.Name): nowhere to push $branch (FORK_REPO empty)"; continue }
        # The record carries no path: the clone is always <run dir>/clone (clone.py, deliver.py
        # and revise.py all build it that way), and the run dir is the one holding this manifest.
        # A container path would be useless here anyway - /work/... is $Work on the host.
        $clone = Join-Path $dir.FullName "clone"
        if (-not (Test-Path (Join-Path $clone ".git"))) { Write-Host "$(Stamp) $($dir.Name): no clone at $clone for $branch"; continue }
        # The call operator, NOT `cmd /c "git -C `"$clone`" ..."`: inside an interpolated
        # "$( ... )" the escaped quotes are eaten by the outer string, git is handed " <path>"
        # with a leading space and exits 128 - so every item failed "cannot read HEAD" and this
        # publisher pushed nothing at all. Verified on this host; see local/preflight.py.
        $head = "$(& git -C $clone rev-parse HEAD 2>&1)".Trim()
        if ($head -notmatch '^[0-9a-f]{40}$') { Write-Host "$(Stamp) $($dir.Name): cannot read HEAD of ${clone}: $head"; continue }
        $marker = Join-Path $dir.FullName "PUSHED"
        $lease = ""
        if (Test-Path $marker) {
            $mk = (Get-Content $marker -Raw)
            if ($mk -match $head) { continue }
            $first = ("$mk".Trim() -split '\s+')[0]
            if ($first -match '^[0-9a-f]{40}$') { $lease = $first }
        }
        $author = "$(& git -C $clone log -1 --format=%ae 2>&1)".Trim()
        if ($author -ne "harness@brightboost-harness") {
            Write-Host "$(Stamp) $($dir.Name): refusing to push $branch - tip author is '$author', not the harness (B139)"; continue
        }
        # D67 / B304: nothing under .github/ leaves this host either - the push goes as HEAD.
        $held = @(Get-UnpublishablePaths $clone "HEAD")
        if ($held.Count -gt 0) {
            Write-Host "$(Stamp) $($dir.Name): refusing to push $branch - $($held -join '; ') (I-15, D67)"; continue
        }
        $url = "https://github.com/$remote.git"
        $basic = ""
        if ($token) {
            $basic = [Convert]::ToBase64String([System.Text.Encoding]::ASCII.GetBytes("x-access-token:$token"))
            $env:GIT_CONFIG_COUNT = "1"
            $env:GIT_CONFIG_KEY_0 = "http.extraheader"
            $env:GIT_CONFIG_VALUE_0 = "AUTHORIZATION: basic $basic"
        } else {
            Write-Host "$(Stamp) no HARNESS_GITHUB_TOKEN in $EnvFile; pushing $branch with the host's own git credentials"
        }
        $code = 1
        $out = @()
        $found = ""
        try {
            $out = @(& git -C $clone push "--force-with-lease=refs/heads/${branch}:$lease" $url "HEAD:refs/heads/$branch" 2>&1 | ForEach-Object { "$_" })
            $code = $LASTEXITCODE
            if ($code -ne 0) {
                # Only to name the sha in the message; the lease decision is already made.
                $ls = @(& git ls-remote $url "refs/heads/$branch" 2>&1 | ForEach-Object { "$_" })
                foreach ($row in $ls) { if ("$row" -match '^([0-9a-f]{40})') { $found = $Matches[1] } }
            }
        } finally {
            Remove-Item Env:GIT_CONFIG_COUNT, Env:GIT_CONFIG_KEY_0, Env:GIT_CONFIG_VALUE_0 -ErrorAction SilentlyContinue
        }
        $text = ($out -join " | ")
        if ($token) { $text = $text.Replace($token, "***").Replace($basic, "***") }
        if ($code -eq 0) {
            $when = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            Set-Content -Path $marker -Value "$head $branch $remote $when" -Encoding ascii
            Write-Host "$(Stamp) pushed $branch ($($head.Substring(0, 12))) to $remote from $($dir.Name)"
        } elseif ($text -match "stale info") {
            # The lease refused. Adopting the fork's tip is a decision a human makes, so say what
            # is there and what to write into the marker; never retry with a wider force.
            $now = if ($found) { $found.Substring(0, 12) } else { "(absent)" }
            $was = if ($lease) { "this watchdog left $($lease.Substring(0, 12)) there" }
                   else { "this watchdog has no PUSHED marker for it" }
            Write-Host "$(Stamp) push REFUSED for $branch on ${remote}: the lease failed - $was, the fork now has $now, so someone else pushed. NOT forcing over it."
            Write-Host "$(Stamp)   inspect $remote $branch; to accept its tip as the base and let the next pass overwrite it, put that sha first in $marker"
        } else {
            Write-Host "$(Stamp) push FAILED for $branch to ${remote}: $text"
        }
    }
}
$lastPush = Get-Date

$cap = 0.0
$capRaw = Read-EnvValue "WEEKLY_CAP_USD"
if ($capRaw -match '^[0-9]+(\.[0-9]+)?$') { $cap = [double]$capRaw }
$highSince = $null
$lowSince = $null
Write-Host "bb watchdog: container=$Container work=$Work poll=${PollSeconds}s heartbeat-stale=${HeartbeatStaleSeconds}s min-free=${MinFreeGB}GB push=${PushMinutes}min weekly-cap=$cap USD cpu-pause=${CpuHigh}/${CpuLow}% for ${CpuSustainSeconds}s battery-guard=$(-not $NoBatteryGuard)"

while ($true) {
    if (-not $Once) { Start-Sleep -Seconds $PollSeconds }

    # 0. Host-side push of delivered branches every $PushMinutes (and on every -Once pass).
    if ($Once -or ((Get-Date) - $lastPush).TotalMinutes -ge $PushMinutes) {
        Push-Delivered
        $lastPush = Get-Date
    }

    # Pause state comes from docker on every poll (P10): bb-start.ps1 restarts this process, and a
    # fresh instance must adopt a paused container instead of killing it or leaving it frozen.
    $status = (docker inspect -f "{{.State.Status}}" $Container 2>$null)
    if ($status -ne "running" -and $status -ne "paused") {
        Write-Host "$(Stamp) container not running ($status); watchdog idle"
        if ($Once) { break }
        continue
    }
    $isPaused = ($status -eq "paused")

    # 1. Battery: pause while unplugged, resume on AC. Outranks the CPU logic below.
    if (-not $NoBatteryGuard) {
        if (On-Battery) {
            if (-not $isPaused) { Write-Host "$(Stamp) on battery -> docker pause"; docker pause $Container | Out-Null }
            $pausedBattery = $true
            if ($Once) { break }
            continue
        } elseif ($pausedBattery) {
            Write-Host "$(Stamp) back on AC -> docker unpause"
            if ($isPaused) { docker unpause $Container | Out-Null; $isPaused = $false }
            $pausedBattery = $false
        }
    }

    # 2. Killfile: informational only - the loop polls STOP itself at the unit boundary (P8).
    if (Test-Path (Join-Path $Work "STOP")) { Write-Host "$(Stamp) STOP present; the loop exits at the next unit boundary" }

    # 3. Heartbeat stale -> docker kill. Startup grace: never kill a container younger than the
    #    threshold - the gate runs before the loop exists, and the file on disk predates the start.
    $hb = Join-Path $Work "HEARTBEAT"
    if (Test-Path $hb) {
        try {
            $ts = [datetime]::Parse((Get-Content $hb -Raw).Trim()).ToUniversalTime()
            $age = [int]((Get-Date).ToUniversalTime() - $ts).TotalSeconds
            if ($age -gt $HeartbeatStaleSeconds -and -not $isPaused -and (Container-UptimeSeconds) -gt $HeartbeatStaleSeconds) {
                Write-Host "$(Stamp) heartbeat stale ${age}s -> docker kill (the restart policy retries; the gate re-runs)"
                docker kill $Container | Out-Null
                if ($Once) { break }
                continue
            }
        } catch { Write-Host "$(Stamp) heartbeat unreadable" }
    }

    # 4. Weekly spend over the cap -> hard stop. The loop's governor enforces the same number from
    #    inside; this is the independent enforcement from outside (platform section 5.4).
    if ($cap -gt 0) {
        $spend = Get-Spend
        if ($spend -gt $cap) {
            Write-Host "$(Stamp) spend $spend USD > WEEKLY_CAP_USD $cap -> docker stop"
            docker stop $Container | Out-Null
            if ($Once) { break }
            continue
        }
    }

    # 5. Free disk on the work drive under the floor -> hard stop.
    $freeGB = [double]::MaxValue
    try { $freeGB = [double](Get-Item $Work).PSDrive.Free / 1GB } catch {}
    if ($freeGB -lt $MinFreeGB) {
        Write-Host "$(Stamp) disk free $([math]::Round($freeGB, 1)) GB < $MinFreeGB -> docker stop"
        docker stop $Container | Out-Null
        if ($Once) { break }
        continue
    }

    # 6. CPU pause: non-container host CPU above CpuHigh for CpuSustainSeconds -> pause; below CpuLow
    #    for as long -> unpause. Host load = total minus this container's share. Another harness's
    #    container reads as host load here (platform section 8); subtract it by hand if it bites.
    $total = 0.0
    try { $total = [double](Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples[0].CookedValue } catch {}
    $ctr = 0.0
    try {
        $stats = docker stats --no-stream --format "{{.CPUPerc}}" $Container 2>$null
        if ($stats) { $ctr = [double]("$stats".Trim().Trim('%')) / [Environment]::ProcessorCount }
    } catch {}
    $hostCpu = [math]::Max(0.0, $total - $ctr)
    $now = Get-Date
    if ($hostCpu -gt $CpuHigh) { if ($null -eq $highSince) { $highSince = $now }; $lowSince = $null }
    elseif ($hostCpu -lt $CpuLow) { if ($null -eq $lowSince) { $lowSince = $now }; $highSince = $null }
    else { $highSince = $null; $lowSince = $null }
    if (-not $isPaused -and $null -ne $highSince -and ($now - $highSince).TotalSeconds -ge $CpuSustainSeconds) {
        Write-Host "$(Stamp) host CPU $([math]::Round($hostCpu))% for ${CpuSustainSeconds}s -> docker pause"
        docker pause $Container | Out-Null; $highSince = $null
    } elseif ($isPaused -and -not $pausedBattery -and $null -ne $lowSince -and ($now - $lowSince).TotalSeconds -ge $CpuSustainSeconds) {
        Write-Host "$(Stamp) host CPU $([math]::Round($hostCpu))% for ${CpuSustainSeconds}s -> docker unpause"
        docker unpause $Container | Out-Null; $lowSince = $null
    }
    if ($Once) { break }
}
