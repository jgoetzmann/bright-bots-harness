# Stop local mode (docs/delivery/DELIVERY-2-HANDOFF.md section 10; platform section 6.2).
#   .\bb-stop.ps1          graceful: drop the STOP killfile, wait for the unit boundary, stop the watchdog
#   .\bb-stop.ps1 -Force   docker stop now (at most the unit in flight is lost; state replays on restart)
# Either way, whatever the container committed but the watchdog had not pushed yet is pushed last.
# Neither path fights --restart on-failure:5: a graceful stop exits 0, and docker never re-applies
# the policy after an explicit docker stop.
param([switch]$Force, [int]$WaitMinutes = 10)
$ErrorActionPreference = "Continue"
$root    = $PSScriptRoot
$work    = Join-Path $root "bb-work"
$envFile = Join-Path $root ".env"
$wd      = Join-Path $root "local\watchdog-bb.ps1"   # found by this FULL path, never a wildcard (A44)

$running = (docker inspect -f "{{.State.Status}}" bb 2>$null)
if ($running -eq "paused") { docker unpause bb | Out-Null; $running = "running" }
if ($running -eq "running") {
    if ($Force) {
        docker stop bb | Out-Null
        Write-Host "container stopped (forced)"
    } else {
        if (-not (Test-Path $work)) { New-Item -ItemType Directory -Path $work | Out-Null }
        Set-Content -Path (Join-Path $work "STOP") -Value "stop" -Encoding ascii
        Write-Host "STOP written; waiting for the loop to reach its unit boundary (up to $WaitMinutes min)..."
        $deadline = (Get-Date).AddMinutes($WaitMinutes)
        while ((Get-Date) -lt $deadline -and (docker inspect -f "{{.State.Status}}" bb 2>$null) -eq "running") { Start-Sleep 10 }
        if ((docker inspect -f "{{.State.Status}}" bb 2>$null) -eq "running") {
            Write-Host "still running after $WaitMinutes min; forcing"; docker stop bb | Out-Null
        }
        Remove-Item (Join-Path $work "STOP") -ErrorAction SilentlyContinue
        Write-Host "container stopped at a unit boundary (exit $(docker inspect -f '{{.State.ExitCode}}' bb 2>$null)); STOP cleaned up"
    }
} else {
    Write-Host "container is not running ($running)"
    Remove-Item (Join-Path $work "STOP") -ErrorAction SilentlyContinue
}

# Only THIS harness's watchdog, matched by the full path of local\watchdog-bb.ps1 (A44).
$procs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -and
    $_.CommandLine.IndexOf($wd, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $_.CommandLine -notlike "*-Once*" }
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "watchdog stopped (pid $($p.ProcessId))" }

# Push anything the container committed but the watchdog had not pushed yet. The watchdog is the
# only publisher, so this is one -Once pass of it: push first, one check, exit.
if (Test-Path $wd) {
    & $wd -Work ($work -replace '\\','/') -EnvFile ($envFile -replace '\\','/') -Container bb -Once
}
