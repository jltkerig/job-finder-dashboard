$ErrorActionPreference = 'Stop'
$updater = Join-Path (Split-Path $PSScriptRoot -Parent) 'update.ps1'
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($updater, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }

# Keep isolated fixtures for inspection. Never execute against the working project.
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('job-finder-updater-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$wrapper = @'
param([string]$Project, [string]$Scenario, [string]$Audit)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility
Import-Module Microsoft.PowerShell.Management
$script:copyCalls = 0
function Copy-Item {
    param($Path, $Destination, [switch]$Recurse, [switch]$Force)
    $script:copyCalls++
    if ($Scenario -eq 'copy-failure' -and $script:copyCalls -eq 2) { throw 'Injected backup copy failure' }
    if ($Scenario -in @('corrupt-recovery', 'valid-recovery') -and $Path -like '*job-finder-update-*') {
        throw 'Injected installation failure'
    }
    Microsoft.PowerShell.Management\Copy-Item -LiteralPath $Path -Destination $Destination -Recurse:$Recurse -Force:$Force
}
function Get-FileHash {
    param($LiteralPath, $Algorithm)
    $result = Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm
    if ($LiteralPath -like '*job-finder-backups*' -and
        ($Scenario -eq 'hash-mismatch' -or ($Scenario -eq 'corrupt-recovery' -and $installationStarted))) {
        $result.Hash = 'INJECTED-MISMATCH'
    }
    $result
}
function Expand-Archive {
    param($LiteralPath, $DestinationPath, [switch]$Force)
    if ($Scenario -eq 'invalid-zip') { throw 'Injected invalid ZIP' }
    Set-Content -LiteralPath (Join-Path $DestinationPath 'dashboard.py') -Value 'APP_VERSION = "9.9.9"'
}
function Remove-Item {
    param($Path, [switch]$Recurse, [switch]$Force, $ErrorAction)
    # Record the real updater's intended deletions without performing them.
    Add-Content -LiteralPath $Audit -Value $Path
}
function Start-Process { throw 'Unexpected process launch during failure test' }
function Stop-Process { throw 'Unexpected process stop during failure test' }
& (Join-Path $Project 'update.ps1') -ZipPath (Join-Path $Project 'fixture.zip')
exit $LASTEXITCODE
'@
$wrapperPath = Join-Path $testRoot 'run-fixture.ps1'
Set-Content -LiteralPath $wrapperPath -Value $wrapper

foreach ($scenario in @('copy-failure', 'hash-mismatch', 'invalid-zip', 'corrupt-recovery', 'valid-recovery')) {
    $scenarioRoot = Join-Path $testRoot $scenario
    $project = Join-Path $scenarioRoot 'job-finder'
    New-Item -ItemType Directory -Path $project -Force | Out-Null
    Copy-Item -LiteralPath $updater -Destination (Join-Path $project 'update.ps1')
    Set-Content -LiteralPath (Join-Path $project 'dashboard.py') -Value 'APP_VERSION = "1.1.67"'
    Set-Content -LiteralPath (Join-Path $project 'saved-data.txt') -Value 'Disposable synthetic data'
    Set-Content -LiteralPath (Join-Path $project 'fixture.zip') -Value 'Archive extraction is mocked'
    $original = @(Get-ChildItem -LiteralPath $project -File | Get-FileHash -Algorithm SHA256)
    $audit = Join-Path $scenarioRoot 'deletion-attempts.txt'
    $ErrorActionPreference = 'Continue'
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wrapperPath -Project $project -Scenario $scenario -Audit $audit 2>&1
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -eq 0) { throw "$scenario should fail safely" }
    foreach ($entry in $original) {
        if ((Get-FileHash -LiteralPath $entry.Path -Algorithm SHA256).Hash -ne $entry.Hash) {
            throw "$scenario changed an original fixture file"
        }
    }
    $deletions = if (Test-Path -LiteralPath $audit) { @(Get-Content -LiteralPath $audit) } else { @() }
    $projectDeletions = @($deletions | Where-Object { $_.StartsWith($project + '\') })
    if ($scenario -eq 'valid-recovery') {
        if ($projectDeletions.Count -ne $original.Count) { throw 'Verified recovery did not enter rollback' }
    } elseif ($projectDeletions.Count) {
        throw "$scenario attempted to delete project files"
    }
    $log = Get-Content -LiteralPath (Join-Path $project 'update.log') -Raw
    $expected = switch ($scenario) {
        'copy-failure' { 'Injected backup copy failure' }
        'hash-mismatch' { 'Backup verification failed' }
        'invalid-zip' { 'Injected invalid ZIP' }
        'corrupt-recovery' { 'Injected installation failure' }
        'valid-recovery' { 'Restoring backup after failed update' }
    }
    if (-not $log.Contains($expected)) { throw "$scenario did not reach expected failure: $output" }
    if ($scenario -eq 'corrupt-recovery' -and ($output | Out-String) -notmatch 'Backup verification failed') {
        throw 'Corrupted recovery was not rejected by hash verification'
    }
    Write-Output "PASS: $scenario"
}
Write-Output "Fixtures retained at: $testRoot"
