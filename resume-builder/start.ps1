$ErrorActionPreference = "Stop"

$ProjectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path (Split-Path -Parent $ProjectPath) "find-python.ps1")  # Job Finder's folder, one level up
$PythonPath = Find-Python
$Url = "http://127.0.0.1:5001/"
$Port = 5001

if (-not $PythonPath) { throw "Python was not found. Install Python 3.12+ and try again." }

$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Write-Host "Starting Resume Builder..."
    Start-Process -FilePath $PythonPath -ArgumentList "app.py" -WorkingDirectory $ProjectPath -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $ProjectPath "resume-builder.log") `
        -RedirectStandardError (Join-Path $ProjectPath "resume-builder-error.log")
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
        if ((Get-Date) -gt $deadline) {
            Write-Host "Resume Builder did not start. Last errors:" -ForegroundColor Yellow
            Get-Content (Join-Path $ProjectPath "resume-builder-error.log") -Tail 20 -ErrorAction SilentlyContinue
            exit 1
        }
        Start-Sleep -Milliseconds 500
    }
}
Write-Host "Resume Builder ready at $Url"
Start-Process $Url
