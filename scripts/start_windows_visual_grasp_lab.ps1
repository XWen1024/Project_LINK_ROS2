param(
    [string]$PythonPath = "",
    [switch]$CheckOnly,
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $repoRoot "tools\windows_visual_grasp_lab\app.py"
$checkPath = Join-Path $repoRoot "tools\windows_visual_grasp_lab\check_dependencies.py"
$smokePath = Join-Path $repoRoot "tools\windows_visual_grasp_lab\smoke_test.py"

function Test-PythonRuntime {
    param([string]$Candidate)
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
        return $false
    }
    try {
        & $Candidate -c "import sys; raise SystemExit(0)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$visualTrackerPython = $null
if ($env:VISUAL_TRACKER_ROOT) {
    $candidate = Join-Path $env:VISUAL_TRACKER_ROOT "venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $visualTrackerPython = $candidate
    }
}
if (-not $visualTrackerPython) {
    $visualTrackerPython = Get-ChildItem -LiteralPath (Join-Path $HOME "Desktop") `
        -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*VisualTracker\venv\Scripts\python.exe" } |
        Select-Object -First 1 -ExpandProperty FullName
}
$repoPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if ($PythonPath) {
    if (-not (Test-PythonRuntime $PythonPath)) {
        throw "The selected Python runtime cannot start: $PythonPath"
    }
} else {
    if ($visualTrackerPython -and (Test-PythonRuntime $visualTrackerPython)) {
        $PythonPath = $visualTrackerPython
    } elseif (Test-PythonRuntime $repoPython) {
        $PythonPath = $repoPython
    } else {
        if ($visualTrackerPython) {
            Write-Warning "VisualTracker venv exists but cannot start; skipping: $visualTrackerPython"
        }
        $systemPython = (Get-Command python -ErrorAction Stop).Source
        if (-not (Test-PythonRuntime $systemPython)) {
            throw "No working Python runtime found. Install Python 3.12 or rebuild the repository .venv."
        }
        $PythonPath = $systemPython
    }
}

Write-Host "Python: $PythonPath"
Write-Host "App:    $appPath"

if ($visualTrackerPython) {
    $visualTrackerRoot = Split-Path -Parent (
        Split-Path -Parent (Split-Path -Parent $visualTrackerPython)
    )
    $lerobotSource = Join-Path $visualTrackerRoot "lerobot\src"
    if (Test-Path -LiteralPath $lerobotSource) {
        if ($env:PYTHONPATH) {
            $env:PYTHONPATH = "$lerobotSource;$env:PYTHONPATH"
        } else {
            $env:PYTHONPATH = $lerobotSource
        }
        Write-Host "LeRobot source: $lerobotSource"
    }
}

Push-Location $repoRoot
try {
    & $PythonPath ".\tools\windows_visual_grasp_lab\check_dependencies.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Install Windows upper-computer dependencies with:"
        Write-Host "  & '$PythonPath' -m pip install -r '$repoRoot\tools\windows_visual_grasp_lab\requirements.txt'"
        Write-Host "Keep using the locally validated VisualTracker LeRobot source; do not upgrade it onsite."
        exit $LASTEXITCODE
    }

    if ($CheckOnly) {
        exit 0
    }

    if ($SmokeTest) {
        & $PythonPath ".\tools\windows_visual_grasp_lab\smoke_test.py"
        exit $LASTEXITCODE
    }

    & $PythonPath ".\tools\windows_visual_grasp_lab\app.py"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
