# Builds dist\Dolmi-Setup-<version>.zip: unzip anywhere, double-click "Install Dolmi.bat".
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$version = "1.1"
$stage = Join-Path $root "dist\Dolmi-Setup"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $stage "app\assets") | Out-Null

$appFiles = "app.py", "overlay.py", "theme.py", "models.py", "assistant.py", "inbox.py", "stealth.py", "widgets.py", "live_subs.py", "requirements.txt",
            "vocabulary.example.txt", "glossary.example.txt", ".env.example", "README.md", "SPECS.md", "create_shortcuts.ps1"
foreach ($f in $appFiles) { Copy-Item (Join-Path $root $f) (Join-Path $stage "app") }
Copy-Item (Join-Path $root "assets\dolmi-2.ico"), (Join-Path $root "assets\dolmi.png") (Join-Path $stage "app\assets")
Copy-Item (Join-Path $root "assets\fonts") (Join-Path $stage "app\assets") -Recurse
# strip caches so no stale bytecode ships
Get-ChildItem (Join-Path $stage "app") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
foreach ($f in "install.ps1", "uninstall.ps1", "Install Dolmi.bat") {
    Copy-Item (Join-Path $root "installer\$f") $stage
}
Set-Content (Join-Path $stage "VERSION") $version -Encoding ascii

$zip = Join-Path $root "dist\Dolmi-Setup-$version.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $stage -DestinationPath $zip
Write-Output "Built $zip ($([math]::Round((Get-Item $zip).Length / 1KB)) KB)"
