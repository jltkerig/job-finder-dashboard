param(
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [int]$CurrentPid = 0
)

$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$PythonDir = Split-Path $ProjectDir -Parent
$BackupRoot = Join-Path $PythonDir "job-finder-backups"
$TempRoot = Join-Path $env:TEMP ("job-finder-update-" + [guid]::NewGuid().ToString("N"))
$UpdateLog = Join-Path $ProjectDir "update.log"
$backup = $null

function Write-UpdateLog([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -Path $UpdateLog -Value $line -Encoding UTF8
}

function Restore-Backup {
    if ($backup -and (Test-Path $backup)) {
        Write-UpdateLog "Restoring backup after failed update."

        # Remove partially installed files first so rollback cannot leave a mixed-version project.
        Get-ChildItem $ProjectDir -Force | Where-Object { $_.Name -ne "update.log" } | ForEach-Object {
            Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }

        Get-ChildItem $backup -Force | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $ProjectDir $_.Name) -Recurse -Force
        }
    }
}

try {
    Write-UpdateLog "Update started from $ZipPath"
    if (-not (Test-Path $ZipPath -PathType Leaf)) { throw "Update ZIP was not found: $ZipPath" }

    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $installedDashboard = Get-Content (Join-Path $ProjectDir "dashboard.py") -Raw
    $installedVersion = if ($installedDashboard -match 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') { $Matches[1] } else { "unknown" }
    $stamp = Get-Date -Format "yyyy-MM-dd 'at' HH-mm-ss"
    $backupName = "Job Finder v$installedVersion - $stamp"
    $backup = Join-Path $BackupRoot $backupName
    $copyNumber = 2
    while (Test-Path $backup) {
        $backup = Join-Path $BackupRoot "$backupName ($copyNumber)"
        $copyNumber++
    }
    New-Item -ItemType Directory -Path $backup -ErrorAction Stop | Out-Null
    Get-ChildItem $ProjectDir -Force | Where-Object { $_.Name -ne "__pycache__" } | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $backup $_.Name) -Recurse -Force
    }
    Write-UpdateLog "Backup created at $backup"

    $preserve = @(".env", "settings.json", "blocked_domains.txt", "blocked_companies.txt", "blocked_country_domains.txt", "block_metadata.json", "dashboard.log", "dashboard-error.log", "job_finder.log", "update.log")

    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $TempRoot -Force
    $dashboardFile = Get-ChildItem $TempRoot -Recurse -Filter "dashboard.py" -File | Select-Object -First 1
    if (-not $dashboardFile) { throw "dashboard.py was not found inside the update ZIP." }
    $sourceRoot = $dashboardFile.Directory.FullName

    Get-ChildItem $sourceRoot -Force | ForEach-Object {
        if ($_.Name -notin $preserve -and $_.Name -ne "__pycache__") {
            $destination = Join-Path $ProjectDir $_.Name
            if ($_.PSIsContainer -and (Test-Path $destination)) {
                Remove-Item $destination -Recurse -Force
            }
            Copy-Item $_.FullName $destination -Recurse -Force
        }
    }

    # Remove files retired from newer releases so old installs do not accumulate stale copies.
    foreach ($obsolete in @("readme.md", "test_database.py", "searxng\settings.json", ".update-in-progress")) {
        Remove-Item (Join-Path $ProjectDir $obsolete) -Recurse -Force -ErrorAction SilentlyContinue
    }

    foreach ($required in @("dashboard.py", "job_finder.py", "start.ps1", "templates", "static")) {
        if (-not (Test-Path (Join-Path $ProjectDir $required))) { throw "Required updated item is missing: $required" }
    }

    $dashboardText = Get-Content (Join-Path $ProjectDir "dashboard.py") -Raw
    if ($dashboardText -notmatch 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
        throw "Could not verify the installed dashboard version."
    }
    Write-UpdateLog "New files copied and verified as version $($Matches[1])."

    Remove-Item $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    if ($CurrentPid -gt 0) {
        $CurrentProcess = Get-Process -Id $CurrentPid -ErrorAction SilentlyContinue
        if ($CurrentProcess) {
            Write-UpdateLog "Stopping old dashboard process $CurrentPid"
            try {
                Stop-Process -Id $CurrentPid -Force -ErrorAction Stop
            } catch {
                Start-Sleep -Milliseconds 250
                $StillExists = Get-Process -Id $CurrentPid -ErrorAction SilentlyContinue
                if ($StillExists) { throw }
                Write-UpdateLog "Dashboard PID $CurrentPid disappeared during shutdown; continuing."
            }
        } else {
            Write-UpdateLog "Dashboard PID $CurrentPid was already gone; continuing."
        }
    }

    Start-Sleep -Milliseconds 750
    Write-UpdateLog "Starting updated Job Finder."
    Start-Process powershell.exe -ArgumentList '-ExecutionPolicy','Bypass','-File',(Join-Path $ProjectDir 'start.ps1') -WorkingDirectory $ProjectDir
}
catch {
    Write-UpdateLog ("UPDATE FAILED: " + $_.Exception.Message)
    Restore-Backup
    Remove-Item $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
