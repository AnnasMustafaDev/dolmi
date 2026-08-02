# Dolmi installer. Run via "Install Dolmi.bat" (double-click). Per-user, no admin rights needed.
# Uses only signed programs (python.org Python, winget), so Windows Smart App Control allows it.
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Dolmi"),
    [switch]$NoShortcuts,   # for testing
    [switch]$NoLaunch
)
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Get-Content (Join-Path $src "VERSION") -ErrorAction SilentlyContinue) -as [string]
if (-not $version) { $version = "1.0" }

function Step($n, $text) { Write-Host "`n[$n/5] $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host "`nInstallation failed: $text" -ForegroundColor Red
    Write-Host "Send a screenshot of this window to the person who gave you Dolmi."
    exit 1
}

function Find-Python {
    # A real Python 3.10-3.12 (the Microsoft Store "python" alias prints nothing useful)
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    foreach ($p in $candidates) {
        if (-not (Test-Path $p)) { continue }
        $v = & $p -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -in @("3.10", "3.11", "3.12")) { return $p }
    }
    return $null
}

Write-Host "Dolmi $version - live German to English meeting subtitles" -ForegroundColor White
Write-Host "Installing to $InstallDir"

Step 1 "Checking Python..."
$python = Find-Python
if (-not $python) {
    Write-Host "Python 3.12 not found - installing it (a few minutes)..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "Python is missing and winget is not available. Install Python 3.12 from https://www.python.org/downloads/ and run this installer again."
    }
    winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements
    $python = Find-Python
    if (-not $python) { Fail "Python could not be installed automatically. Install Python 3.12 from https://www.python.org/downloads/ and run this installer again." }
}
Write-Host "Using $python"

Step 2 "Copying Dolmi..."
New-Item -ItemType Directory -Force $InstallDir | Out-Null
$keep = @("vocabulary.txt", "glossary.txt")   # don't overwrite the user's edits on update
Get-ChildItem (Join-Path $src "app") | ForEach-Object {
    $dest = Join-Path $InstallDir $_.Name
    if ($keep -contains $_.Name -and (Test-Path $dest)) { return }
    Copy-Item $_.FullName $dest -Recurse -Force
}
Copy-Item (Join-Path $src "uninstall.ps1") $InstallDir -Force

Step 3 "Installing components (first time: 5-15 minutes, please wait)..."
$venvPy = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $python -m venv (Join-Path $InstallDir "venv")
    if ($LASTEXITCODE -ne 0) { Fail "could not create the Python environment." }
}
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install --progress-bar on -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "downloading components failed. On a company network a proxy may block pip." }

Step 4 "Creating shortcuts..."
$pyw = Join-Path $InstallDir "venv\Scripts\pythonw.exe"
$appPy = Join-Path $InstallDir "app.py"
$icon = Join-Path $InstallDir "assets\dolmi-2.ico"
if (-not $NoShortcuts) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallDir "create_shortcuts.ps1")
    # Listed in Settings > Apps, so it can be uninstalled like any other app
    $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Dolmi"
    New-Item -Path $key -Force | Out-Null
    $uninstall = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
    $props = @{ DisplayName = "Dolmi"; DisplayVersion = $version; Publisher = "Dolmi";
                DisplayIcon = $icon; InstallLocation = $InstallDir; UninstallString = $uninstall;
                NoModify = 1; NoRepair = 1 }
    foreach ($k in $props.Keys) { Set-ItemProperty -Path $key -Name $k -Value $props[$k] }
}

Step 5 "Done!"
Write-Host "Dolmi is installed. Open it from the Desktop or Start Menu." -ForegroundColor Green
Write-Host "The first start downloads the speech models (about 0.5-1 GB) - give it a minute."
if (-not $NoLaunch) { Start-Process $pyw -ArgumentList "`"$appPy`"" -WorkingDirectory $InstallDir }
