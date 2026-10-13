[CmdletBinding()]
param([string]$RepoPath = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$taskRepoRoot = (Resolve-Path -LiteralPath $RepoPath).Path
$taskLogDir = Join-Path $taskRepoRoot 'logs'
New-Item -ItemType Directory -Path $taskLogDir -Force | Out-Null
$taskLogFile = Join-Path $taskLogDir 'windows-sync.log'
try {
    if (-not (Test-Path -LiteralPath (Join-Path $taskRepoRoot '.git'))) { throw 'This directory is not a Git repository.' }
    $taskDirty = & git -C $taskRepoRoot status --porcelain
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect local Git status.' }
    if ($taskDirty) { throw 'Local changes found. Commit or save them before synchronizing.' }
    $taskBranch = & git -C $taskRepoRoot branch --show-current
    if ($LASTEXITCODE -ne 0 -or $taskBranch -ne 'main') { throw 'Automatic sync requires the main branch.' }
    $taskOutput = & git -C $taskRepoRoot pull --ff-only origin main 2>&1
    $taskExitCode = $LASTEXITCODE
    # Persist fixed status text only; raw Git output may contain private URLs.
    if ($taskExitCode -ne 0) { throw 'Git pull failed. Check connectivity, credentials or branch divergence.' }
    Add-Content -LiteralPath $taskLogFile -Value ("{0:o} Sync succeeded" -f (Get-Date))
    Write-Output 'AI daily archive synchronized.'
    exit 0
} catch {
    Add-Content -LiteralPath $taskLogFile -Value ("{0:o} Sync failed: {1}" -f (Get-Date), $_.Exception.Message)
    Write-Error $_.Exception.Message
    exit 1
}
