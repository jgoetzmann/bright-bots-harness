# Live, read-only view of local mode (DELIVERY-2-HANDOFF.md section 10; platform section 6.3, P11).
# The view reads bb-work\, bb-config.json and docker inspect/logs; it never writes and never
# signals. Ctrl+C here stops only the view (the window then offers to restart it), never the run.
#   .\bb-watcher.ps1              open a dedicated window that refreshes every watcher.refresh_seconds
#   .\bb-watcher.ps1 -Here        run the view in this terminal instead
#   .\bb-watcher.ps1 -Once        print one snapshot and exit (use this in scripts and reviews: R9.6)
param([switch]$Here, [switch]$Once)
$ErrorActionPreference = "Continue"
$root    = $PSScriptRoot
$work    = Join-Path $root "bb-work"
$cfgFile = Join-Path $root "bb-config.json"
$wdPath  = Join-Path $root "local\watchdog-bb.ps1"   # counted by FULL path; this view's own probe filtered out

$cfg = $null
try { $cfg = Get-Content $cfgFile -Raw | ConvertFrom-Json } catch {}
function Cfg($sec, $name, $default) {
    try { $v = $cfg.$sec.$name; if ($null -ne $v) { return $v } } catch {}
    return $default
}
$refresh = [int](Cfg "watcher" "refresh_seconds" 5)
$tail = [int](Cfg "watcher" "events_tail" 25)

function Get-WatchdogCount {
    # Process-hunting caveat (platform section 6.3): this query's own command line would otherwise be
    # counted. Exclude this process, any -Once pass, and any watcher window.
    $procs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and
        $_.CommandLine.IndexOf($wdPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $_.CommandLine -notlike "*-Once*" -and $_.CommandLine -notlike "*bb-watcher*" }
    return @($procs).Count
}

function Age([string]$iso) {
    try { return [int]((Get-Date).ToUniversalTime() - [datetime]::Parse($iso).ToUniversalTime()).TotalSeconds } catch { return $null }
}

function Inspect([string]$fmt) {
    $v = docker inspect -f $fmt bb 2>$null
    if ($v) { return "$v".Trim() } else { return "" }
}

function Show-Snapshot {
    $nowUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") + "Z"
    $status = Inspect "{{.State.Status}}"
    if (-not $status) { $status = "absent" }
    $uptime = "-"
    if ($status -ne "absent") { $a = Age (Inspect "{{.State.StartedAt}}"); if ($null -ne $a) { $uptime = "${a}s" } }
    $exitCode = Inspect "{{.State.ExitCode}}"
    $restarts = Inspect "{{.RestartCount}}"
    $hbAge = "-"
    $hbFile = Join-Path $work "HEARTBEAT"
    if (Test-Path $hbFile) {
        $a = Age ((Get-Content $hbFile -Raw).Trim())
        if ($null -ne $a) { $hbAge = "${a}s" } else { $hbAge = "unreadable" }
    }
    $wdCount = Get-WatchdogCount
    $wdText = "ABSENT - nothing pushes; run .\bb-start.ps1"
    if ($wdCount -gt 0) { $wdText = "present ($wdCount)" }

    Write-Host "bb local mode  -  $nowUtc  (read-only view; Ctrl+C stops only this view)"
    Write-Host ("=" * 110)
    Write-Host ("container: {0,-9} uptime: {1,-8} exit: {2,-3} restarts: {3,-3} heartbeat age: {4,-10} watchdog: {5}" -f $status, $uptime, $exitCode, $restarts, $hbAge, $wdText)

    # Configured (bb-config.json) next to LIVE (the container's environment and HostConfig): the most
    # useful thing this view does is reveal a setting you changed and never restarted for.
    Write-Host ""
    Write-Host "settings                     configured     live"
    $liveEnv = @{}
    $envDump = docker inspect -f "{{range .Config.Env}}{{println .}}{{end}}" bb 2>$null
    foreach ($line in @($envDump)) { if ("$line" -match '^([A-Z_]+)=(.*)$') { $liveEnv[$Matches[1]] = $Matches[2] } }
    $nano = Inspect "{{.HostConfig.NanoCpus}}"
    $memB = Inspect "{{.HostConfig.Memory}}"
    $liveCpus = "-"
    if ($nano -match '^\d+$' -and [double]$nano -gt 0) { $liveCpus = [string]([double]$nano / 1e9) }
    $liveMem = "-"
    if ($memB -match '^\d+$' -and [double]$memB -gt 0) { $liveMem = [string]([math]::Round([double]$memB / 1GB, 2)) }
    $rows = @(
        @("container.cpus", (Cfg "container" "cpus" 4), $liveCpus),
        @("container.memory_gb", (Cfg "container" "memory_gb" 8), $liveMem),
        @("container.pids_limit", (Cfg "container" "pids_limit" 512), (Inspect "{{.HostConfig.PidsLimit}}")),
        @("container.cpu_shares", (Cfg "container" "cpu_shares" 256), (Inspect "{{.HostConfig.CpuShares}}")),
        @("run.loop_seconds", (Cfg "run" "loop_seconds" 300), $liveEnv["BB_LOOP_SECONDS"]),
        @("run.max_items_per_unit", (Cfg "run" "max_items_per_unit" 1), $liveEnv["BB_MAX_ITEMS_PER_UNIT"]),
        @("(db)", "/data/harness.db", $liveEnv["DB_PATH"])
    )
    foreach ($r in $rows) {
        $c = "$($r[1])"; $l = "$($r[2])"
        if (-not $l) { $l = "-" }
        $flag = ""
        if ($status -ne "absent" -and $l -ne "-" -and $c -ne $l) { $flag = "  <- differs: .\bb-stop.ps1 then .\bb-start.ps1 to apply" }
        Write-Host ("  {0,-26} {1,-14} {2}{3}" -f $r[0], $c, $l, $flag)
    }

    # Health: pin, killfiles, image age, disk, ledger.
    Write-Host ""
    Write-Host "health"
    $pinText = "MISSING - the gate refuses to start"
    $pin = Join-Path $root ".harness\PIN"
    if (Test-Path $pin) { try { $pinText = ((Get-Content $pin -Raw).Trim() -split '\s+')[0] + "  (.harness\PIN)" } catch { $pinText = "unreadable" } }
    Write-Host "  pin:          $pinText"
    $stopText = "absent"
    if (Test-Path (Join-Path $work "STOP")) { $stopText = "present - the loop exits at the next unit boundary" }
    Write-Host "  STOP:         $stopText"
    $halts = @()
    foreach ($h in @((Join-Path $work "HALT"), (Join-Path $work ".harness\HALT"), (Join-Path $root ".harness\HALT"))) { if (Test-Path $h) { $halts += $h } }
    $haltText = "absent"
    if ($halts.Count -gt 0) { $haltText = "PRESENT: " + ($halts -join ", ") }
    Write-Host "  HALT:         $haltText"
    $created = docker image inspect --format "{{.Created}}" bb-harness:latest 2>$null
    $imgText = "no image - .\bb-start.ps1 -Build"
    if ($created) { $imgText = "$created  (rebuild after editing local\entrypoint.sh or local\Dockerfile)" }
    Write-Host "  image built:  $imgText"
    $free = "-"
    try { $free = [string]([math]::Round([double](Get-Item $root).PSDrive.Free / 1GB, 1)) + " GB free" } catch {}
    Write-Host "  disk:         $free  (watchdog floor: $(Cfg 'watchdog' 'min_free_gb' 5) GB)"
    $spend = "no ledger yet (bb-work\state\ledger.json)"
    $ledger = Join-Path $work "state\ledger.json"
    if (Test-Path $ledger) {
        try {
            $L = Get-Content $ledger -Raw | ConvertFrom-Json
            $rl = "-"
            if ($L.window.rate_limited_until) { $rl = $L.window.rate_limited_until }
            $spend = "$($L.window.spent_usd) USD spent since $($L.window.period_start), $($L.window.calls) calls, rate-limited until: $rl"
        } catch { $spend = "ledger unreadable" }
    }
    Write-Host "  ledger:       $spend"

    # Work: runs\item-*, newest first, with what the host has pushed.
    Write-Host ""
    Write-Host "work (bb-work\runs)"
    $runs = Join-Path $work "runs"
    $items = @()
    if (Test-Path $runs) { $items = @(Get-ChildItem $runs -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 8) }
    if ($items.Count -eq 0) { Write-Host "  (no runs yet)" }
    $lastPush = $null
    foreach ($it in $items) {
        $marks = @()
        foreach ($f in @("DELIVER.json", "PUSHED", "DIAGNOSIS.md", "EVIDENCE.md")) { if (Test-Path (Join-Path $it.FullName $f)) { $marks += $f } }
        $pushed = Join-Path $it.FullName "PUSHED"
        if (Test-Path $pushed) { $t = (Get-Item $pushed).LastWriteTimeUtc; if ($null -eq $lastPush -or $t -gt $lastPush) { $lastPush = $t } }
        $state = "in progress"
        if ($marks -contains "PUSHED") { $state = "pushed by the host" }
        elseif ($marks -contains "DELIVER.json") { $state = "committed; awaiting the watchdog push" }
        Write-Host ("  {0,-22} last write {1:yyyy-MM-dd HH:mm}Z  {2,-40} {3}" -f $it.Name, $it.LastWriteTimeUtc, $state, ($marks -join " "))
    }
    $pushText = "none"
    if ($lastPush) { $pushText = "{0:yyyy-MM-dd HH:mm}Z" -f $lastPush }
    Write-Host "  last push:    $pushText"

    # The tail of the record: events.jsonl when the loop writes one, else the container log.
    Write-Host ""
    $events = Join-Path $work "events.jsonl"
    if (Test-Path $events) {
        Write-Host "events (bb-work\events.jsonl, last $tail)"
        foreach ($line in (Get-Content $events -Tail $tail)) { Write-Host "  $line" }
    } else {
        Write-Host "log (docker logs --tail $tail bb)"
        $log = @(docker logs --tail $tail bb 2>&1 | ForEach-Object { "$_" })
        if ($log.Count -eq 0) { Write-Host "  (nothing)" }
        foreach ($line in $log) { Write-Host "  $line" }
    }
}

if ($Once) { Show-Snapshot; exit 0 }
if ($Here) {
    while ($true) { Clear-Host; Show-Snapshot; Start-Sleep -Seconds $refresh }
}

# Dedicated window: loops the view so Ctrl+C stops only the view and offers to restart it.
$self = $PSCommandPath
$inner = @"
`$host.UI.RawUI.WindowTitle = 'bb watcher (read-only; Ctrl+C stops only this view)'
cmd /c 'mode con: cols=170 lines=60' | Out-Null
while (`$true) {
  & '$self' -Here
  Write-Host ''
  Write-Host 'watcher stopped (the run is unaffected). Press Enter to restart the view, or close this window.' -ForegroundColor Yellow
  `$null = Read-Host
}
"@
$tmp = Join-Path $env:TEMP "bb-watcher-inner.ps1"
Set-Content -Path $tmp -Value $inner -Encoding ascii
Start-Process powershell -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$tmp`""
Write-Host "watcher window opened"
