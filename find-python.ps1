# Finds python.exe on any Windows computer. Job Finder's and Résumé Builder's scripts load this with ". find-python.ps1".
#   1. the newest Python 3 installed for this user (python.org installer's default folder)
#   2. the "py" launcher
#   3. python.exe on PATH, skipping the Microsoft Store stub that only opens the Store
# Returns $null when none is found.
function Find-Python {
    $installed = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python") -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Name -replace '\D', '') } -Descending |
        ForEach-Object { Join-Path $_.FullName "python.exe" } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($installed) { return $installed }

    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        try {
            $fromLauncher = (& py.exe -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($fromLauncher -and (Test-Path $fromLauncher)) { return $fromLauncher }
        } catch { }
    }

    $onPath = Get-Command python.exe -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike "*\WindowsApps\*" } | Select-Object -First 1
    if ($onPath) { return $onPath.Source }
    return $null
}
