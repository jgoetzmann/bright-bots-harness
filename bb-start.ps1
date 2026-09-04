# One-command start for local mode: the bb container + its host watchdog, configured by
# bb-config.json (docs/delivery/DELIVERY-2-HANDOFF.md section 10; platform section 6.1).
#   .\bb-start.ps1            start (image must already be built)
#   .\bb-start.ps1 -Build     rebuild the image first - REQUIRED after any change to local\entrypoint.sh
#                             or local\Dockerfile (editing harness\ needs only a restart; local\README.md)
# Edit settings with:  python bb-configure.py explain | show | set key=value
# Starting is a HARD restart (docker rm -f bb). Run .\bb-stop.ps1 first for a clean unit boundary.
param([switch]$Build)
$ErrorActionPreference = "Stop"
$root    = $PSScriptRoot
$local   = Join-Path $root "local"
$work    = Join-Path $root "bb-work"
$envFile = Join-Path $root ".env"
$cfgFile = Join-Path $root "bb-config.json"
$wd      = Join-Path $local "watchdog-bb.ps1"   # found by this FULL path, never a wildcard (A44)

foreach ($p in @($local, (Join-Path $root "harness"), (Join-Path $root "tests\test_invariants.py"), (Join-Path $root ".harness\config.json"), $wd)) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}
if (-not (Test-Path $envFile)) { throw "missing $envFile (copy .env.example and fill it in)" }
if (-not (Test-Path (Join-Path $root ".harness\PIN"))) { throw "missing .harness\PIN - the gate refuses to start without it (B142); it is written by a reviewed PR" }
if (-not (Test-Path $work)) { New-Item -ItemType Directory -Path $work | Out-Null; Write-Host "created $work (gitignored; bind-mounted at /work)" }
# A stale STOP from an interrupted shutdown would make the loop come up and exit immediately.
if (Test-Path (Join-Path $work "STOP")) { Remove-Item (Join-Path $work "STOP"); Write-Host "removed stale STOP file" }

# Settings (bb-config.json; defaults are the handoff values).
$cfg = @{ container = @{}; run = @{}; watchdog = @{}; watcher = @{} }
if (Test-Path $cfgFile) {
    $json = Get-Content $cfgFile -Raw | ConvertFrom-Json
    foreach ($sec in @("container", "run", "watchdog", "watcher")) {
        if ($json.PSObject.Properties[$sec]) {
            foreach ($prop in $json.$sec.PSObject.Properties) { $cfg[$sec][$prop.Name] = $prop.Value }
        }
    }
}
function Cfg($sec, $name, $default) { if ($cfg[$sec].ContainsKey($name) -and $null -ne $cfg[$sec][$name]) { return $cfg[$sec][$name] } else { return $default } }

$null = docker info 2>$null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running - start it and retry" }

$runArgs = @{
    Harness         = ($root -replace '\\','/')
    Work            = ($work -replace '\\','/')
    EnvFile         = ($envFile -replace '\\','/')
    Cpus            = [double](Cfg "container" "cpus" 4)
    MemoryGB        = [double](Cfg "container" "memory_gb" 8)
    PidsLimit       = [int](Cfg "container" "pids_limit" 512)
    CpuShares       = [int](Cfg "container" "cpu_shares" 256)
    LoopSeconds     = [int](Cfg "run" "loop_seconds" 300)
}
if ($Build) { $runArgs.Build = $true }
& (Join-Path $local "run.ps1") @runArgs
if ($LASTEXITCODE -ne 0) { throw "local\run.ps1 failed" }

# The rebuild-vs-restart trap (handoff section 10.2): print the image's build time on EVERY start,
# so a stale gate is visible instead of silent.
$created = docker image inspect --format "{{.Created}}" bb-harness:latest 2>$null
Write-Host ""
Write-Host "image bb-harness:latest built: $created"
Write-Host "  edited local\entrypoint.sh or local\Dockerfile since then? then this container runs the OLD gate: .\bb-start.ps1 -Build"

# Watchdog: always restarted so config changes take effect. Only THIS harness's watchdog is
# touched, matched by the full path of local\watchdog-bb.ps1 - rk's watchdog stays untouched (A44).
$existing = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -and
    $_.CommandLine.IndexOf($wd, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $_.CommandLine -notlike "*-Once*" }
foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "stopped previous bb watchdog (pid $($p.ProcessId))" }
$wdArgs = "-NoExit -ExecutionPolicy Bypass -File `"$wd`"" +
    " -Work `"$($work -replace '\\','/')`" -EnvFile `"$($envFile -replace '\\','/')`" -Container bb" +
    " -PollSeconds $([int](Cfg 'watchdog' 'poll_seconds' 10))" +
    " -HeartbeatStaleSeconds $([int](Cfg 'watchdog' 'heartbeat_stale_seconds' 180))" +
    " -MinFreeGB $([double](Cfg 'watchdog' 'min_free_gb' 5))" +
    " -PushMinutes $([int](Cfg 'watchdog' 'push_minutes' 10))" +
    " -CpuHigh $([int](Cfg 'watchdog' 'cpu_pause_high_percent' 50))" +
    " -CpuLow $([int](Cfg 'watchdog' 'cpu_pause_low_percent' 30))" +
    " -CpuSustainSeconds $([int](Cfg 'watchdog' 'cpu_pause_sustain_seconds' 30))"
if (-not [bool](Cfg "watchdog" "battery_guard" $true)) { $wdArgs += " -NoBatteryGuard" }
Start-Process powershell -ArgumentList $wdArgs -WindowStyle Minimized
Write-Host "watchdog started (minimized window; battery guard $(if ([bool](Cfg 'watchdog' 'battery_guard' $true)) { 'on' } else { 'off' }); it is the only thing that pushes)"
Write-Host ""
Write-Host "Running.  Live view: .\bb-watcher.ps1     log: docker logs -f bb     gate: docker logs bb | Select-String gate"
Write-Host "Stop:     .\bb-stop.ps1 (graceful)  or  .\bb-stop.ps1 -Force      Settings: python bb-configure.py show"
