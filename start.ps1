$ErrorActionPreference = "Stop"

$ProjectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$PreferredPythonPath = "C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe"
$DashboardPath = Join-Path $ProjectPath "dashboard.py"
$RequirementsPath = Join-Path $ProjectPath "requirements.txt"
$EnvPath = Join-Path $ProjectPath ".env"
$EnvExamplePath = Join-Path $ProjectPath ".env.example"
$MySqlStartPath = "C:\xampp\mysql_start.bat"
$DashboardUrl = "http://127.0.0.1:5000"
$DashboardStdoutLog = Join-Path $ProjectPath "dashboard.log"
$DashboardStderrLog = Join-Path $ProjectPath "dashboard-error.log"
$StartupLog = Join-Path $ProjectPath "startup.log"
$DashboardSource = Get-Content -Path $DashboardPath -Raw -ErrorAction Stop
if ($DashboardSource -notmatch '(?m)^APP_VERSION\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']') {
    throw "Cannot read APP_VERSION from $DashboardPath"
}
$ExpectedDashboardVersion = $Matches[1]

function Write-StartupLog([string]$Message) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Add-Content -Path $StartupLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

function Show-DashboardErrorTail {
    if (Test-Path $DashboardStderrLog) {
        $tail = Get-Content $DashboardStderrLog -Tail 20 -ErrorAction SilentlyContinue
        if ($tail) {
            Write-Host ""
            Write-Host "Last dashboard errors:" -ForegroundColor Yellow
            $tail | ForEach-Object { Write-Host $_ }
        }
    }
}

function Fail-Startup([string]$Code, [string]$Message, [switch]$ShowDashboardLog) {
    $full = "[$Code] $Message"
    Write-StartupLog $full
    Write-Host ""
    Write-Host $full -ForegroundColor Red
    if ($ShowDashboardLog) { Show-DashboardErrorTail }
    Write-Host "Startup details were saved to: $StartupLog" -ForegroundColor DarkGray
    exit 1
}

function Test-LocalPort([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Stop-JobFinderDashboardProcesses {
    param([int]$WaitSeconds = 15)

    $normalizedProject = [IO.Path]::GetFullPath($ProjectPath).TrimEnd('\').ToLowerInvariant()
    $deadline = (Get-Date).AddSeconds($WaitSeconds)

    while ((Get-Date) -lt $deadline) {
        $matching = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.CommandLine -and
            ($_.Name -match '^python(w)?\.exe$') -and
            $_.CommandLine.ToLowerInvariant().Contains('dashboard.py') -and
            $_.CommandLine.ToLowerInvariant().Contains($normalizedProject)
        })

        foreach ($proc in $matching) {
            try {
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
                Write-StartupLog "Stopped Job Finder dashboard PID $($proc.ProcessId)"
            } catch {
                Start-Sleep -Milliseconds 200
                if (Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue) {
                    Write-StartupLog "Could not stop Job Finder dashboard PID $($proc.ProcessId): $($_.Exception.Message)"
                }
            }
        }

        $listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $listener) { return $true }

        $ownerPid = $listener.OwningProcess
        $owner = $null
        if ($ownerPid) {
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
        }

        if ($owner -and $owner.CommandLine -and $owner.CommandLine -notmatch 'dashboard\.py') {
            return $false
        }

        if ($ownerPid -and -not $owner) {
            # During a process handoff Windows can briefly report a listener PID that no longer exists.
            # Try taskkill as a harmless best-effort cleanup, then re-check instead of failing immediately.
            & taskkill.exe /PID $ownerPid /F 2>$null | Out-Null
        }

        Start-Sleep -Milliseconds 400
    }

    return (-not (Test-LocalPort 5000))
}

Write-StartupLog "Starting Job Finder v$ExpectedDashboardVersion"

if (Test-Path $PreferredPythonPath) {
    $PythonPath = $PreferredPythonPath
} else {
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        Fail-Startup "E5001" "Python was not found. Install Python 3.12+ and run Job Finder again."
    }
    $PythonPath = $PythonCommand.Source
}
Write-StartupLog "Python: $PythonPath"

foreach ($RequiredPath in @($DashboardPath, $RequirementsPath, $EnvExamplePath)) {
    if (-not (Test-Path $RequiredPath)) {
        Fail-Startup "E5002" "Required file was not found: $RequiredPath"
    }
}

Set-Location $ProjectPath

if (-not (Test-Path $EnvPath)) {
    try {
        $SearxSecret = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
        $FlaskSecret = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
        @"
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=
DB_NAME=job_finder
SEARXNG_SECRET=$SearxSecret
FLASK_SECRET_KEY=$FlaskSecret
"@ | Set-Content -Path $EnvPath -Encoding UTF8
        Write-Host "Created a private local .env file."
    } catch {
        Fail-Startup "E5003" "Could not create the local .env file: $($_.Exception.Message)"
    }
}

& $PythonPath -c "import flask, mysql.connector, dotenv, requests, bs4, pypdf, docx" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing missing Python dependencies..."
    & $PythonPath -m pip install -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        Fail-Startup "E5004" "Required Python packages could not be installed."
    }
    & $PythonPath -c "import flask, mysql.connector, dotenv, requests, bs4, pypdf, docx" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail-Startup "E5016" "Python dependencies are installed but could not be imported."
    }
}

if (-not (Test-LocalPort 3306)) {
    if (-not (Test-Path $MySqlStartPath)) {
        Fail-Startup "E5005" "MySQL is not running and $MySqlStartPath was not found."
    }

    Write-Host "Starting MySQL..."
    try {
        Start-Process -FilePath $MySqlStartPath -WindowStyle Hidden
    } catch {
        Fail-Startup "E5006" "MySQL could not be launched: $($_.Exception.Message)"
    }

    for ($Attempt = 0; $Attempt -lt 30 -and -not (Test-LocalPort 3306); $Attempt++) {
        Start-Sleep -Seconds 1
    }

    if (-not (Test-LocalPort 3306)) {
        Fail-Startup "E5007" "MySQL did not become ready on port 3306 within 30 seconds."
    }
}

$DashboardWasRunning = Test-LocalPort 5000
$RestartDashboard = $false

if ($DashboardWasRunning) {
    try {
        $VersionResponse = Invoke-RestMethod -Uri "$DashboardUrl/app-version" -TimeoutSec 2 -ErrorAction Stop
        if ($VersionResponse.version -ne $ExpectedDashboardVersion) {
            Write-Host "Dashboard version is stale. Restarting it automatically..."
            $RestartDashboard = $true
        } else {
            Write-Host "Dashboard is already running and current."
        }
    } catch {
        $Listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        $ProcessInfo = $null
        if ($Listener -and $Listener.OwningProcess) {
            $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)" -ErrorAction SilentlyContinue
        }

        if (-not $Listener) {
            $DashboardWasRunning = $false
        } elseif ($ProcessInfo -and $ProcessInfo.CommandLine -match 'dashboard\.py') {
            Write-Host "Dashboard is running older/unresponsive code. Restarting it automatically..."
            $RestartDashboard = $true
        } elseif (-not $ProcessInfo) {
            # Unresolved listeners are often a transient PID handoff from an older Job Finder build.
            Write-Host "Port 5000 listener is changing. Cleaning up stale Job Finder processes..."
            if (Stop-JobFinderDashboardProcesses -WaitSeconds 15) {
                $DashboardWasRunning = $false
            } else {
                $Listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
                $ProcessInfo = if ($Listener -and $Listener.OwningProcess) { Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)" -ErrorAction SilentlyContinue } else { $null }
                $ProcessDescription = if ($ProcessInfo) {
                    "PID $($Listener.OwningProcess): $($ProcessInfo.Name) $($ProcessInfo.CommandLine)"
                } elseif ($Listener) {
                    "PID $($Listener.OwningProcess) remained unresolved after cleanup"
                } else {
                    "unknown listener"
                }
                Fail-Startup "E5008" "Port 5000 is still being used after Job Finder cleanup ($ProcessDescription)."
            }
        } else {
            $ProcessDescription = "PID $($Listener.OwningProcess): $($ProcessInfo.Name) $($ProcessInfo.CommandLine)"
            Fail-Startup "E5008" "Port 5000 is already being used by another application ($ProcessDescription)."
        }
    }
}

if ($RestartDashboard) {
    if (-not (Stop-JobFinderDashboardProcesses -WaitSeconds 15)) {
        $StuckListener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        $StuckPid = if ($StuckListener) { $StuckListener.OwningProcess } else { 'unknown' }
        $StuckProcess = if ($StuckListener -and $StuckListener.OwningProcess) { Get-CimInstance Win32_Process -Filter "ProcessId=$($StuckListener.OwningProcess)" -ErrorAction SilentlyContinue } else { $null }
        $detail = if ($StuckProcess) { "$($StuckProcess.Name) $($StuckProcess.CommandLine)" } else { 'unresolved' }
        Fail-Startup "E5010" "Job Finder could not release port 5000. Last listener PID: $StuckPid ($detail)."
    }
}

if (-not (Test-LocalPort 5000)) {
    Write-Host "Starting dashboard..."
    Remove-Item $DashboardStdoutLog, $DashboardStderrLog -ErrorAction SilentlyContinue

    try {
        $DashboardProcess = Start-Process `
            -FilePath $PythonPath `
            -ArgumentList $DashboardPath `
            -WorkingDirectory $ProjectPath `
            -WindowStyle Hidden `
            -RedirectStandardOutput $DashboardStdoutLog `
            -RedirectStandardError $DashboardStderrLog `
            -PassThru
        Write-StartupLog "Dashboard process started with PID $($DashboardProcess.Id)"
    } catch {
        Fail-Startup "E5011" "Python could not launch dashboard.py: $($_.Exception.Message)" -ShowDashboardLog
    }

    for ($Attempt = 0; $Attempt -lt 30 -and -not (Test-LocalPort 5000); $Attempt++) {
        Start-Sleep -Seconds 1
        try { $DashboardProcess.Refresh() } catch {}
        if ($DashboardProcess.HasExited) {
            Fail-Startup "E5012" "dashboard.py exited before opening port 5000. Exit code: $($DashboardProcess.ExitCode)." -ShowDashboardLog
        }
    }

    if (-not (Test-LocalPort 5000)) {
        Fail-Startup "E5013" "Dashboard did not open port 5000 within 30 seconds." -ShowDashboardLog
    }

    $HttpReady = $false
    $LastHttpError = $null
    for ($Attempt = 0; $Attempt -lt 20 -and -not $HttpReady; $Attempt++) {
        try {
            $VersionResponse = Invoke-RestMethod -Uri "$DashboardUrl/app-version" -TimeoutSec 2 -ErrorAction Stop
            if ($VersionResponse.version -eq $ExpectedDashboardVersion) {
                $HttpReady = $true
            } else {
                $LastHttpError = "Expected v$ExpectedDashboardVersion but server reported v$($VersionResponse.version)."
            }
        } catch {
            $LastHttpError = $_.Exception.Message
        }
        if (-not $HttpReady) { Start-Sleep -Milliseconds 500 }
    }
    if (-not $HttpReady) {
        Fail-Startup "E5014" "Dashboard opened port 5000 but /app-version never became ready. $LastHttpError" -ShowDashboardLog
    }
}

try {
    Start-Process $DashboardUrl -ErrorAction Stop
} catch {
    Fail-Startup "E5015" "Dashboard is running, but Windows could not open the browser automatically: $($_.Exception.Message)"
}

Write-StartupLog "Dashboard ready at $DashboardUrl"
Write-Host "Dashboard ready. Start Job Finder from the browser."
