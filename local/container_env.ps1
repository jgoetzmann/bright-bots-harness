# The credential filter (docs/delivery/DELIVERY-2-HANDOFF.md section 10.5; platform P4/P5).
# Hand-written; never machine-generated. Builds the env file the container receives: everything
# in .env EXCEPT every line starting with HARNESS_GITHUB_TOKEN= (and any comment naming that
# key, so a grep of the result is clean). That token is what makes work public, so it stays on
# the host: the container commits, local/watchdog-bb.ps1 pushes. CLAUDE_CODE_OAUTH_TOKEN passes
# through - the loop needs it, and the filter is selective, not blanket (review R5.8).
# Prints "dropped N line(s)" (A46: non-zero in every startup log, because .env.example ships the
# key) and writes the path of the filtered file to the pipeline (Select-Object -Last 1 to read it).
#   .\local\container_env.ps1 [-EnvFile <path>] [-OutFile <path>]
param(
    [string]$EnvFile = (Join-Path (Split-Path $PSScriptRoot -Parent) ".env"),
    [string]$OutFile = (Join-Path $env:TEMP "bb-container.env")
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $EnvFile)) { Write-Error "env file not found: $EnvFile (copy .env.example and fill it in)"; exit 2 }
$kept = @()
$dropped = 0
foreach ($line in Get-Content $EnvFile) {
    if ($line -match 'HARNESS_GITHUB_TOKEN') { $dropped += 1; continue }
    $kept += $line
}
Set-Content -Path $OutFile -Value $kept -Encoding ascii
# Self-check: the filtered file must not carry the key in any form. Fail closed rather than start.
if (Select-String -Path $OutFile -Pattern 'HARNESS_GITHUB_TOKEN' -Quiet) {
    Remove-Item $OutFile -ErrorAction SilentlyContinue
    Write-Error "filter self-check failed: HARNESS_GITHUB_TOKEN survived in $OutFile"; exit 3
}
Write-Host "container env: dropped $dropped line(s) (HARNESS_GITHUB_TOKEN), kept $($kept.Count) -> $OutFile"
Write-Output $OutFile
