# Removes Dolmi. Your transcripts are kept.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*$dir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
foreach ($d in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    Remove-Item (Join-Path $d "Dolmi.lnk") -ErrorAction SilentlyContinue
}
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Dolmi" -Recurse -ErrorAction SilentlyContinue
Get-ChildItem $dir | Where-Object { $_.Name -ne "transcripts" } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path (Join-Path $dir "transcripts")) {
    Write-Host "Dolmi was removed. Your transcripts were kept in $dir\transcripts"
} else {
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Dolmi was removed."
}
Start-Sleep -Seconds 4
