$ErrorActionPreference = "Stop"

$ProjectPath = Split-Path -Parent $MyInvocation.MyCommand.Path

$PythonPath = "C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe"

$JobFinderPath = Join-Path $ProjectPath "job_finder.py"
$DashboardPath = Join-Path $ProjectPath "dashboard.py"


# =========================================================
# CHECK PYTHON
# =========================================================

if (-not (Test-Path $PythonPath)) {
    Write-Host ""
    Write-Host "ERROR: Python was not found."
    Write-Host $PythonPath
    Write-Host ""

    exit 1
}


# =========================================================
# CHECK PROJECT FILES
# =========================================================

if (-not (Test-Path $JobFinderPath)) {
    Write-Host ""
    Write-Host "ERROR: job_finder.py was not found."
    Write-Host $JobFinderPath
    Write-Host ""

    exit 1
}


if (-not (Test-Path $DashboardPath)) {
    Write-Host ""
    Write-Host "ERROR: dashboard.py was not found."
    Write-Host $DashboardPath
    Write-Host ""

    exit 1
}


# =========================================================
# MOVE TO PROJECT FOLDER
# =========================================================

Set-Location $ProjectPath


# =========================================================
# CHECK DASHBOARD PORT
# =========================================================

$DashboardPort = 5000

$DashboardAlreadyRunning = Get-NetTCPConnection `
    -LocalPort $DashboardPort `
    -State Listen `
    -ErrorAction SilentlyContinue


if ($DashboardAlreadyRunning) {
    Write-Host ""
    Write-Host "Dashboard is already running on port $DashboardPort."
    Write-Host "Not starting another copy."
}

else {
    Write-Host ""
    Write-Host "Starting dashboard..."

    try {
        Start-Process `
            powershell `
            -WorkingDirectory $ProjectPath `
            -ArgumentList `
            "-NoExit",
            "-Command",
            "& `"$PythonPath`" `"$DashboardPath`""
    }

    catch {
        Write-Host ""
        Write-Host "ERROR: Dashboard could not be started."
        Write-Host $_.Exception.Message
        Write-Host ""

        exit 1
    }
}


# =========================================================
# START JOB FINDER
# =========================================================

Write-Host ""
Write-Host "Starting Job Finder..."
Write-Host ""

try {
    & $PythonPath $JobFinderPath

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "ERROR: Job Finder exited with code $LASTEXITCODE."
        Write-Host ""

        exit $LASTEXITCODE
    }
}

catch {
    Write-Host ""
    Write-Host "ERROR: Job Finder stopped unexpectedly."
    Write-Host $_.Exception.Message
    Write-Host ""

    exit 1
}