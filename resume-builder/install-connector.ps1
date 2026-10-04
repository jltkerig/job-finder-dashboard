$ErrorActionPreference = "Stop"

# Adds Resume Builder to Claude Desktop's connectors. The settings file is backed up first.
$ProjectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path (Split-Path -Parent $ProjectPath) "find-python.ps1")  # Job Finder's folder, one level up
$PythonPath = Find-Python
if (-not $PythonPath) { throw "Python was not found. Install Python 3.12+ and try again." }

& $PythonPath (Join-Path $ProjectPath "connector.py") install
