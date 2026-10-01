param(
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [int]$CurrentPid = 0
)

$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$PythonDir = Split-Path $ProjectDir -Parent
$BackupRoot = Join-Path $PythonDir "job-finder-dashboard-backups"
$TempRoot = Join-Path $env:TEMP ("job-finder-update-" + [guid]::NewGuid().ToString("N"))
$UpdateLog = Join-Path $ProjectDir "update.log"
# Logs the running dashboard or a search keeps appending to. They are backed up
# but not hash-checked, or every backup would fail as "changed during backup".
$VolatileFiles = @("update.log", "dashboard.log", "dashboard-error.log", "job_finder.log", "startup.log") |
    ForEach-Object { Join-Path $ProjectDir $_ }
$backup = $null
$backupVerified = $false
$installationStarted = $false
$backupFiles = @()
$backupDirectories = @()

function Assert-BackupComplete {
    if (-not $backup -or -not (Test-Path -LiteralPath $backup -PathType Container)) {
        throw "The backup directory is missing."
    }
    $backupItems = @(Get-ChildItem -LiteralPath $backup -Recurse -Force)
    if (@($backupItems | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
        throw "Backup contains a reparse point; recovery must be reviewed manually."
    }
    foreach ($relative in $backupDirectories) {
        if (-not (Test-Path -LiteralPath (Join-Path $backup $relative) -PathType Container)) {
            throw "Backup directory is missing: $relative"
        }
    }
    foreach ($entry in $backupFiles) {
        $savedHash = (Get-FileHash -LiteralPath (Join-Path $backup $entry.Path) -Algorithm SHA256).Hash
        if ($savedHash -ne $entry.Hash) { throw "Backup verification failed: $($entry.Path)" }
    }
}

function Write-UpdateLog([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -Path $UpdateLog -Value $line -Encoding UTF8
}

function Export-DatabaseBackup([string]$Name) {
    # Saves the job_finder database next to the file backup so a bad update can be undone completely.
    # It never stops the update: if the export cannot run, that is logged and the update continues.
    $sqlPath = Join-Path $BackupRoot ($Name + " - database.sql")
    $config = Join-Path $env:TEMP ("job-finder-db-" + [guid]::NewGuid().ToString("N") + ".cnf")
    try {
        $db = @{ DB_HOST = "127.0.0.1"; DB_PORT = "3306"; DB_USER = "root"; DB_PASSWORD = ""; DB_NAME = "job_finder" }
        $envFile = Join-Path $ProjectDir ".env"
        if (Test-Path -LiteralPath $envFile) {
            foreach ($line in Get-Content -LiteralPath $envFile) {
                if ($line -match '^\s*(DB_[A-Z_]+)\s*=\s*(.*)$') { $db[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
            }
        }
        $dump = @("C:\xampp\mysql\bin\mysqldump.exe", "C:\xampp\mysql\bin\mariadb-dump.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $dump) {
            $found = Get-Command mysqldump.exe, mariadb-dump.exe -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { $dump = $found.Source }
        }
        if (-not $dump) { Write-UpdateLog "Database backup skipped: mysqldump was not found."; return }
        # The password goes in a temporary options file, not on the command line where other programs could read it.
        $escaped = $db.DB_PASSWORD.Replace('\', '\\').Replace('"', '\"')
        Set-Content -LiteralPath $config -Value ("[client]`nhost=" + $db.DB_HOST + "`nport=" + $db.DB_PORT + "`nuser=" + $db.DB_USER + "`npassword=`"" + $escaped + "`"") -Encoding ASCII
        & $dump "--defaults-extra-file=$config" --single-transaction --routines "--result-file=$sqlPath" $db.DB_NAME 2>$null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $sqlPath) -or (Get-Item -LiteralPath $sqlPath).Length -lt 200) {
            Remove-Item -LiteralPath $sqlPath -Force -ErrorAction SilentlyContinue
            Write-UpdateLog "Database backup failed (is MySQL running?); continuing with the file backup only."
            return
        }
        Write-UpdateLog "Database backup saved: $sqlPath"
        # Keep the ten newest database backups.
        Get-ChildItem -LiteralPath $BackupRoot -Filter "* - database.sql" -File | Sort-Object LastWriteTime -Descending |
            Select-Object -Skip 10 | Remove-Item -Force -ErrorAction SilentlyContinue
    } catch {
        Write-UpdateLog ("Database backup failed: " + $_.Exception.Message + "; continuing with the file backup only.")
    } finally {
        Remove-Item -LiteralPath $config -Force -ErrorAction SilentlyContinue
    }
}

function Restore-Backup {
    if ($backupVerified -and $installationStarted) {
        # Recheck the saved bytes before removing any partially installed files.
        Assert-BackupComplete
        Write-UpdateLog "Restoring backup after failed update."

        # Remove partially installed files first so rollback cannot leave a mixed-version project.
        Get-ChildItem $ProjectDir -Force | Where-Object { $_.Name -ne "update.log" } | ForEach-Object {
            Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }

        Get-ChildItem $backup -Force | Where-Object { $_.Name -ne "update.log" } | ForEach-Object {
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
    $originalItems = @(Get-ChildItem -LiteralPath $ProjectDir -Recurse -Force)
    if (@($originalItems | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
        throw "Project contains a reparse point; create a reviewed backup before updating."
    }
    $backupDirectories = @($originalItems | Where-Object { $_.PSIsContainer } | ForEach-Object {
        $_.FullName.Substring($ProjectDir.Length + 1)
    })
    # Log files are actively appended; update.log is also never deleted by rollback.
    $backupFiles = @($originalItems | Where-Object { -not $_.PSIsContainer -and $_.FullName -notin $VolatileFiles } | ForEach-Object {
        [PSCustomObject]@{
            Path = $_.FullName.Substring($ProjectDir.Length + 1)
            Hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    })
    Get-ChildItem $ProjectDir -Force | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $backup $_.Name) -Recurse -Force
    }
    Assert-BackupComplete
    foreach ($entry in $backupFiles) {
        if ((Get-FileHash -LiteralPath (Join-Path $ProjectDir $entry.Path) -Algorithm SHA256).Hash -ne $entry.Hash) {
            throw "Project changed during backup: $($entry.Path)"
        }
    }
    $currentPaths = @(Get-ChildItem -LiteralPath $ProjectDir -Recurse -Force | ForEach-Object { $_.FullName } | Where-Object { $_ -notin $VolatileFiles })
    $originalPaths = @($originalItems | ForEach-Object { $_.FullName } | Where-Object { $_ -notin $VolatileFiles })
    if (Compare-Object $originalPaths $currentPaths) { throw "Project contents changed during backup." }
    $backupVerified = $true
    Write-UpdateLog "Backup verified at $backup"
    Export-DatabaseBackup (Split-Path $backup -Leaf)

    $preserve = @(".env", "settings.json", "blocked_domains.txt", "blocked_companies.txt", "blocked_country_domains.txt", "block_metadata.json", "dashboard.log", "dashboard-error.log", "job_finder.log", "search_skips.jsonl", "search_debug.json", "watched_employers.json", "discovered_employers.json", "board_health.json", "update.log")

    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $TempRoot -Force
    $dashboardFile = Get-ChildItem $TempRoot -Recurse -Filter "dashboard.py" -File | Select-Object -First 1
    if (-not $dashboardFile) { throw "dashboard.py was not found inside the update ZIP." }
    $sourceRoot = $dashboardFile.Directory.FullName

    $installationStarted = $true
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
