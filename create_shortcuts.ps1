# Creates "Dolmi" shortcuts on the Desktop and in the Start Menu (run once, or via start.bat).
# Dolmi runs on the signed Python from python.org, so Smart App Control lets it start.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "")
)
foreach ($dir in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "Dolmi.lnk"))
    $lnk.TargetPath = Join-Path $here "venv\Scripts\pythonw.exe"
    $lnk.Arguments = '"' + (Join-Path $here "app.py") + '"'
    $lnk.WorkingDirectory = $here
    $lnk.IconLocation = (Join-Path $here "assets\dolmi-2.ico") + ",0"
    $lnk.Description = "Dolmi - live German to English meeting subtitles"
    $lnk.Save()
    Write-Output "Shortcut: $($lnk.FullName)"
}
