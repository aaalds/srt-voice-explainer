[CmdletBinding()]
param(
    [string]$Root = (Get-Location).Path,
    [string]$Name,
    [string]$Srt = "transcription.srt",
    [switch]$Force,
    [switch]$SkipRuntimeCheck
)

$ErrorActionPreference = "Stop"
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$configPath = Join-Path $rootPath "video.config.json"
$candidates = [System.Collections.Generic.List[string]]::new()

function Add-PythonCandidate([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }
    $candidate = $Value
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        $candidate = Join-Path $rootPath $candidate
    }
    if (-not $candidates.Contains($candidate)) {
        $candidates.Add($candidate)
    }
}

Add-PythonCandidate $env:QWEN_TTS_PYTHON
if (Test-Path -LiteralPath $configPath) {
    try {
        $config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath |
            ConvertFrom-Json
        Add-PythonCandidate ([string]$config.python)
    }
    catch {
        throw "Cannot read $configPath as UTF-8 JSON: $($_.Exception.Message)"
    }
}

Add-PythonCandidate (Join-Path $rootPath ".qwen-tts-venv\Scripts\python.exe")
Add-PythonCandidate (Join-Path $rootPath ".venv\Scripts\python.exe")

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($null -ne $pythonCommand -and $pythonCommand.Source) {
    Add-PythonCandidate $pythonCommand.Source
}

$python = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        continue
    }
    try {
        $null = & $candidate -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $python = (Resolve-Path -LiteralPath $candidate).Path
            break
        }
    }
    catch {
        continue
    }
}

if ($null -eq $python) {
    throw @"
No usable Python interpreter was found.
Set QWEN_TTS_PYTHON to the Qwen3-TTS venv python.exe, then rerun this script.
"@
}

$arguments = @(
    (Join-Path $PSScriptRoot "init_project.py"),
    "--root", $rootPath,
    "--srt", $Srt
)
if ($Name) {
    $arguments += @("--name", $Name)
}
if ($Force) {
    $arguments += "--force"
}
if ($SkipRuntimeCheck) {
    $arguments += "--skip-runtime-check"
}

Write-Output "[bootstrap] python=$python"
& $python @arguments
exit $LASTEXITCODE
