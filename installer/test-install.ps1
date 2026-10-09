# Automated installer test — the release checklist:
#   silent install, files + shortcut + Apps & features entry, the app really launches,
#   upgrade keeps ONE entry, uninstall removes {app} and keeps the user's data.
#
#   powershell -ExecutionPolicy Bypass -File installer\test-install.ps1 `
#     -Setup dist\Dolmi-Setup-2.0.0.exe -Version 2.0.0 `
#     [-UpgradeSetup dist\Dolmi-Setup-2.0.0.1.exe -UpgradeVersion 2.0.0.1]
#
# Runs in CI on windows-latest (no Smart App Control). On a PC with Smart App Control on, Windows
# blocks the unsigned setup.exe itself, so the script stops early and says so.
param(
  [Parameter(Mandatory)][string]$Setup,
  [Parameter(Mandatory)][string]$Version,
  [string]$UpgradeSetup,
  [string]$UpgradeVersion,
  [string]$Dir = (Join-Path $env:LOCALAPPDATA "Programs\Dolmi-InstallTest")
)
$ErrorActionPreference = "Stop"
$AppId = "{01C6145B-761B-4EB9-A11D-36BEE004B479}"            # must match dolmi.iss
$UninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\${AppId}_is1"
$StartLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Dolmi.lnk"
$DeskLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Dolmi.lnk"
$results = [System.Collections.Generic.List[object]]::new()

function Check($name, [bool]$ok, $detail = "") {
  $results.Add([pscustomobject]@{ Result = $(if ($ok) { "PASS" } else { "FAIL" }); Check = $name })
  Write-Host ("{0}  {1}{2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $name, $(if ($detail) { "  [$detail]" } else { "" }))
}
function Run-Setup($exe, $log) {
  $p = Start-Process $exe -PassThru -Wait -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CURRENTUSER",
    "/DIR=`"$Dir`"", "/MERGETASKS=`"!desktopicon`"", "/LOG=`"$log`"")
  return $p.ExitCode
}
function Uninstall-Entries {
  foreach ($root in "HKCU:", "HKLM:") {
    Get-ChildItem "$root\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue |
      ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DisplayName -like "Dolmi*" -and $_.PSChildName -like "*_is1" }
  }
}
function Lnk-Target($path) {
  if (-not (Test-Path $path)) { return "" }
  $l = (New-Object -ComObject WScript.Shell).CreateShortcut($path); return "$($l.TargetPath) $($l.Arguments)"
}
function Read-Shared($path) {   # the running app keeps its log open, so read with sharing
  if (-not (Test-Path $path)) { return "" }
  $fs = [IO.File]::Open($path, "Open", "Read", "ReadWrite")
  try { (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Dispose() }
}

# ---- guards: never touch a real install, and explain Smart App Control instead of failing obscurely
$sac = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" -Name VerifiedAndReputablePolicyState -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
if ($sac -eq 1) {
  Write-Host "Smart App Control is ON here, so Windows blocks the unsigned setup.exe before it starts." -ForegroundColor Yellow
  Write-Host "Run this test in CI (Actions -> 'Installer test' -> Run workflow) or on a PC with Smart App Control off." -ForegroundColor Yellow
  exit 2
}
if (Test-Path $UninstKey) {
  $loc = (Get-ItemProperty $UninstKey).InstallLocation
  if ($loc -and ($loc.TrimEnd('\') -ne $Dir.TrimEnd('\'))) {
    throw "Dolmi is already installed for real at '$loc'. This test would upgrade and uninstall it, so it refuses to run."
  }
}
$Setup = (Resolve-Path $Setup).Path
$logs = Join-Path ([IO.Path]::GetTempPath()) "dolmi-install-test"
New-Item -ItemType Directory -Force $logs | Out-Null

# keep any existing Dolmi shortcuts (e.g. a developer's) and put them back afterwards
$backups = @{}
foreach ($l in $StartLnk, $DeskLnk) { if (Test-Path $l) { $b = Join-Path $logs ([IO.Path]::GetFileName([IO.Path]::GetDirectoryName($l)) + ".lnk.bak"); Copy-Item $l $b -Force; $backups[$l] = $b } }

# user data that uninstall must NOT touch: markers (folders created here are removed afterwards)
$dataDirs = @((Join-Path $env:APPDATA "Dolmi"), (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Dolmi"))
$createdDirs = @($dataDirs | Where-Object { -not (Test-Path $_) })
$markers = foreach ($d in $dataDirs) { New-Item -ItemType Directory -Force $d | Out-Null; $m = Join-Path $d "install-test-marker.txt"; Set-Content $m "keep me"; $m }

try {
  # ---- 1. silent install
  $code = Run-Setup $Setup (Join-Path $logs "install.log")
  Check "silent install exits 0" ($code -eq 0) "exit $code"
  Check "runtime\pythonw.exe installed (launcher)" (Test-Path "$Dir\runtime\pythonw.exe")
  Check "app\src\main.py and the core installed" ((Test-Path "$Dir\app\src\main.py") -and (Test-Path "$Dir\app\live_subs.py"))
  Check "licence + notices installed" ((Test-Path "$Dir\LICENSE.txt") -and (Test-Path "$Dir\THIRD-PARTY-NOTICES.md"))
  Check "uninstaller present" (Test-Path "$Dir\unins000.exe")
  $sig = Get-AuthenticodeSignature "$Dir\runtime\pythonw.exe"
  Check "launcher is PSF-signed (Smart App Control trusts it)" ($sig.Status -eq "Valid" -and $sig.SignerCertificate.Subject -match "Python Software Foundation") $sig.SignerCertificate.Subject
  $t = Lnk-Target $StartLnk
  Check "Start menu shortcut -> pythonw.exe main.py (no console)" ($t -like "$Dir\runtime\pythonw.exe*" -and $t -match "main\.py") $t
  Check "no Desktop shortcut into the install when the task is unticked" (-not ((Lnk-Target $DeskLnk) -like "$Dir\*"))
  $entry = Get-ItemProperty $UninstKey -ErrorAction SilentlyContinue
  Check "Apps & features entry, version $Version" ($entry -and $entry.DisplayVersion -eq $Version) "$($entry.DisplayName) $($entry.DisplayVersion)"

  # ---- 2. the bundled runtime imports the whole app and reports the version Settings shows
  $out = & "$Dir\runtime\python.exe" -c "import sys; sys.path[:0] = [r'$Dir\app', r'$Dir\app\src']; import webview, faster_whisper, ctranslate2, onnxruntime, numpy, sentencepiece, anthropic, openai, live_subs; from ui import app; print(app.VERSION)" 2>&1
  Check "installed runtime imports the app" ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq $Version) "$out"

  # ---- 3. the app really starts (window process stays up, no traceback in its log)
  $logFile = Join-Path $env:LOCALAPPDATA "Dolmi\dolmi.log"
  $before = (Read-Shared $logFile).Length
  $proc = Start-Process "$Dir\runtime\pythonw.exe" -ArgumentList "`"$Dir\app\src\main.py`"" -WorkingDirectory "$Dir\app" -PassThru
  Start-Sleep -Seconds 15
  $alive = -not $proc.HasExited
  $all = Read-Shared $logFile
  $newLog = if ($all.Length -gt $before) { $all.Substring($before) } else { "" }
  Check "app launches and keeps running (15 s)" $alive $(if ($alive) { "pid $($proc.Id)" } else { "exited $($proc.ExitCode)" })
  Check "no traceback in dolmi.log" ($newLog -notmatch "Traceback") (($newLog -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join " | ")
  if ($alive) { Stop-Process -Id $proc.Id -Force; Start-Sleep -Seconds 2 }

  # ---- 4. upgrade: same AppId -> one entry, new version
  if ($UpgradeSetup) {
    $code = Run-Setup (Resolve-Path $UpgradeSetup).Path (Join-Path $logs "upgrade.log")
    $entries = @(Uninstall-Entries)
    Check "upgrade exits 0" ($code -eq 0) "exit $code"
    Check "after upgrade Apps & features shows ONE Dolmi" ($entries.Count -eq 1) "$($entries.Count) entries"
    Check "entry now shows $UpgradeVersion" ($entries.Count -eq 1 -and $entries[0].DisplayVersion -eq $UpgradeVersion) "$($entries.DisplayVersion -join ', ')"
  }

  # ---- 5. uninstall: {app} gone, entry + shortcut gone, user data kept
  Start-Process "$Dir\unins000.exe" -Wait -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
  for ($i = 0; $i -lt 60 -and (Test-Path $Dir); $i++) { Start-Sleep -Seconds 1 }   # it re-launches itself from %TEMP%
  Check "uninstall removes the install folder" (-not (Test-Path $Dir))
  Check "uninstall removes the Apps & features entry" (-not (Test-Path $UninstKey))
  Check "uninstall removes its Start menu shortcut" (-not ((Lnk-Target $StartLnk) -like "$Dir\*"))
  Check "user data is kept (%APPDATA%\Dolmi + Documents\Dolmi)" (@($markers | Where-Object { Test-Path $_ }).Count -eq $markers.Count)
}
finally {
  $markers | Remove-Item -ErrorAction SilentlyContinue
  $createdDirs | Where-Object { Test-Path $_ } | ForEach-Object { Remove-Item $_ -Recurse -Force -ErrorAction SilentlyContinue }
  foreach ($l in $backups.Keys) { Copy-Item $backups[$l] $l -Force }
}

$failed = @($results | Where-Object Result -eq "FAIL").Count
Write-Host ""
Write-Host ("{0}/{1} checks passed" -f ($results.Count - $failed), $results.Count) -ForegroundColor $(if ($failed) { "Red" } else { "Green" })
if ($failed) { Write-Host "Setup logs: $logs"; exit 1 }
