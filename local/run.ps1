# The docker run contract (platform section 5.3) for the bb container - DELIVERY-2-HANDOFF.md
# section 10.1. Hand-written; never machine-generated. Called by bb-start.ps1 with the settings
# from bb-config.json; the defaults below are the handoff's values.
#   -v <repo>:/harness:ro     the package (P1) - never COPYed into the image
#   -v <bb-work>:/work        the durable record, run state, HEARTBEAT, STOP, and the loop's .env
#   -v bb-data:/data          named volume for harness.db (SQLite locking on a bind mount is unreliable)
#   --env-file <filtered>     process environment through the one credential filter (P4)
#   --network bb-net          plain bridge (section 10.6); no host ports
#   --restart on-failure:5    a kill self-heals; a clean exit or docker stop stays down (P6)
# Running this is a HARD restart (docker rm -f bb). Use bb-stop.ps1 first for a clean unit boundary.
param(
    [string]$Harness = (Split-Path $PSScriptRoot -Parent),
    [string]$Work = (Join-Path (Split-Path $PSScriptRoot -Parent) "bb-work"),
    [string]$EnvFile = (Join-Path (Split-Path $PSScriptRoot -Parent) ".env"),
    [double]$Cpus = 4,
    [double]$MemoryGB = 8,
    [int]$PidsLimit = 512,
    [int]$CpuShares = 256,
    [int]$LoopSeconds = 300,
    [int]$MaxItemsPerUnit = 1,
    [switch]$Build
)
$ErrorActionPreference = "Stop"
$Harness = ($Harness -replace '\\', '/').TrimEnd('/')
$Work = ($Work -replace '\\', '/').TrimEnd('/')
$EnvFile = $EnvFile -replace '\\', '/'

foreach ($p in @($Harness, $Work, "$Harness/harness", "$Harness/tests/test_invariants.py", "$Harness/.harness/config.json")) {
    if (-not (Test-Path $p)) { Write-Error "missing: $p"; exit 2 }
}
if (-not (Test-Path $EnvFile)) { Write-Error "missing env file: $EnvFile (copy .env.example and fill it in)"; exit 2 }
if (-not (Test-Path "$Harness/.harness/PIN")) {
    Write-Error "no pin at $Harness/.harness/PIN - the gate would refuse to start (B142). It is written by a reviewed PR: python -m harness.verify_pin --write"
    exit 2
}

if ($Build) {
    docker build -t bb-harness:latest -f "$PSScriptRoot/Dockerfile" $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
$created = docker image inspect --format "{{.Created}}" bb-harness:latest 2>$null
if (-not $created) { Write-Error "image bb-harness:latest not found; run: .\bb-start.ps1 -Build"; exit 2 }
Write-Host "image bb-harness:latest built $created  (entrypoint.sh + Dockerfile are frozen at that time; harness/ is mounted live)"

$nets = docker network ls --format "{{.Name}}"
if ($nets -notcontains "bb-net") { docker network create --driver bridge bb-net | Out-Null; Write-Host "created bb-net (plain bridge, handoff section 10.6)" }
$vols = docker volume ls --format "{{.Name}}"
if ($vols -notcontains "bb-data") { docker volume create bb-data | Out-Null; Write-Host "created named volume bb-data (mounted at /data; holds harness.db)" }

$existing = docker ps -a --format "{{.Names}}"
if ($existing -contains "bb") { docker rm -f bb | Out-Null; Write-Host "removed previous bb container (hard restart)" }

# P4: the ONE credential filter. Its output feeds BOTH files below; the raw .env is never read again.
$Filtered = (& (Join-Path $PSScriptRoot "container_env.ps1") -EnvFile $EnvFile | Select-Object -Last 1)
if (-not $Filtered -or -not (Test-Path $Filtered)) { Write-Error "container_env.ps1 produced no filtered file"; exit 1 }

# The loop reads its configuration from /work/.env (python -m harness local-loop --config /work/.env),
# so repo_root inside the container is /work: state/, proposals/ and runs/ land on the writable mount
# and never on the read-only package. Container paths and the local-mode shape are forced here;
# every other key is the host's, minus the filtered credential.
$overrides = New-Object System.Collections.Specialized.OrderedDictionary
$overrides["DB_PATH"] = "/data/harness.db"
$overrides["RUNS_DIR"] = "/work/runs"
$overrides["PACKAGES_DIR"] = "/work/packages"
$overrides["HALT_FILE"] = "/work/HALT"
$overrides["TRUST_FILE"] = "/harness/.harness/trust.txt"
$overrides["PERMISSION_TIER"] = "0"          # no GitHub credential inside, so the token door stays shut (I-11)
$overrides["STORE_BACKEND"] = "sqlite"       # local mode = the SQLite store on /data (handoff section 2)
$overrides["MAX_CONCURRENT_ITEMS"] = "1"     # B123: concurrency above 1 is Actions mode only
$overrides["MAX_CONCURRENT_CLONES"] = "1"    # B123
$seen = @{}
$loopEnv = @(
    "# Written by local/run.ps1 on every start from the filtered host .env (local/container_env.ps1).",
    "# Container paths and the local-mode shape are forced below. Edit the host .env, not this file."
)
foreach ($line in Get-Content $Filtered) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
        $k = $Matches[1]
        if ($overrides.Contains($k)) { $loopEnv += "$k=$($overrides[$k])"; $seen[$k] = $true; continue }
    }
    $loopEnv += $line
}
foreach ($k in $overrides.Keys) { if (-not $seen.ContainsKey($k)) { $loopEnv += "$k=$($overrides[$k])" } }
Set-Content -Path "$Work/.env" -Value $loopEnv -Encoding ascii

# .harness/config.json overrides .env for the P12 knobs and is read relative to repo_root, which is
# /work inside the container: mirror it there on every start so both modes read the same knobs.
if (-not (Test-Path "$Work/.harness")) { New-Item -ItemType Directory -Path "$Work/.harness" | Out-Null }
Copy-Item "$Harness/.harness/config.json" "$Work/.harness/config.json" -Force

# The process environment carries ONLY the model credential (taken from the filter's output, never
# from the raw .env) plus the BB_* settings below. `docker exec bb env` must show no variable
# naming GITHUB at all (A46, review R5.7): the harness's own GITHUB_API_CEILING_PER_HOUR lives in
# /work/.env, which load_config reads as a file, not from the environment.
$ProcessEnv = Join-Path $env:TEMP "bb-process.env"
$procLines = @("# process environment for the bb container; built by local/run.ps1 from the filtered env")
$haveClaude = $false
foreach ($line in Get-Content $Filtered) {
    if ($line -match '^\s*CLAUDE_CODE_OAUTH_TOKEN\s*=\s*(.*)$') {
        $v = $Matches[1].Trim().Trim('"').Trim("'")
        if ($v) { $procLines += "CLAUDE_CODE_OAUTH_TOKEN=$v"; $haveClaude = $true }
    }
}
Set-Content -Path $ProcessEnv -Value $procLines -Encoding ascii
if (-not $haveClaude) { Write-Host "warning: CLAUDE_CODE_OAUTH_TOKEN is empty in $EnvFile; the loop cannot call the model (BACKEND=fake still runs)" }

$mounts = @(
    "-v", "${Harness}:/harness:ro",
    "-v", "${Work}:/work",
    "-v", "bb-data:/data"
)
$envFlags = @(
    "-e", "BB_WORK_DIR=/work",
    "-e", "BB_LOOP_SECONDS=$LoopSeconds",
    "-e", "BB_MAX_ITEMS_PER_UNIT=$MaxItemsPerUnit",
    "-e", "DB_PATH=/data/harness.db"
)
$mem = "{0}g" -f $MemoryGB
Write-Host "resources: cpus=$Cpus memory=$mem pids-limit=$PidsLimit cpu-shares=$CpuShares (no tmpfs, no host ports)"
Write-Host "settings: loop_seconds=$LoopSeconds max_items_per_unit=$MaxItemsPerUnit store=sqlite tier=0 db=/data/harness.db"
Write-Host "mounts: ${Harness}:/harness:ro  ${Work}:/work  bb-data:/data"

# on-failure: a wrongful kill (nonzero exit) self-heals; a graceful STOP exit (0) or an explicit
# `docker stop` from the watchdog stays down.
$dockerArgs = @("run", "-d", "--name", "bb", "--restart", "on-failure:5",
    "--cpus=$Cpus", "--memory=$mem", "--pids-limit=$PidsLimit", "--cpu-shares=$CpuShares") +
    $mounts + @("--env-file", $ProcessEnv) + $envFlags + @("--network", "bb-net", "bb-harness:latest")
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "bb started."
