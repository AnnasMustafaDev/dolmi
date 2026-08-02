# Removes Dolmi: shortcuts, the uninstall registry entry, and the install folder.
# Your saved transcripts and inbox in Documents\Dolmi are left untouched.
$ErrorActionPreference = "SilentlyContinue"
$InstallDir = Join-Path $env:LOCALAPPDATA "Dolmi"

# Stop Dolmi if it is running from the install folder (venv files would be locked otherwise)
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*$InstallDir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    Remove-Item (Join-Path $dir "Dolmi.lnk") -Force
}
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Dolmi" -Recurse -Force

# Remove the install folder (schedule a delayed delete since venv files may still be in use)
if (Test-Path $InstallDir) {
    Start-Process cmd -ArgumentList '/c', ('timeout /t 2 >nul & rmdir /s /q "{0}"' -f $InstallDir) -WindowStyle Hidden
}
Write-Host "Dolmi has been uninstalled. Your saved transcripts in Documents\Dolmi were kept." -ForegroundColor Green
