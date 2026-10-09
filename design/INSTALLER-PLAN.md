# Dolmi — Windows installer plan

**Goal:** a proper `Dolmi-Setup-x.y.z.exe` with a wizard, licence page, shortcuts and uninstaller, built and tested automatically on every release. Based on *Shipping a Windows Installer for Open-Source Apps* (9 Oct 2026), adapted to Dolmi's Python stack.

**Status (9 Oct 2026):**

| Stage | State |
|---|---|
| 1. Launcher | done |
| 2. `setup.exe` | done — `Dolmi-Setup-2.0.0.exe`, 97 MB |
| 3. Automated releases | done, plus an install test that gates every release |
| 4. Signing | prerequisites ready |
| 5. winget | after the first published release |

## Decisions

| Question | Choice | Why |
|---|---|---|
| Launcher | Bundled **embeddable CPython 3.12.10**: `runtime\pythonw.exe "app\src\main.py"` | `pythonw.exe` is signed by the Python Software Foundation and opens no console. Smart App Control is on for this PC and blocks unsigned, unknown executables. A PyInstaller `.exe` would be a new unsigned binary on every release. No bat-to-exe or VBScript wrappers, which the guide warns against. |
| Packages | Pre-installed from `requirements-lock.txt` (exact versions) | Installs work offline, the same way every time. No pip runs at install time, except the optional GPU add-on. |
| Installer tool | Inno Setup 6.7 | Free, it's the guide's default, and it's preinstalled on GitHub's Windows runners. |
| Scope | Per user by default (`PrivilegesRequired=lowest`), "all users" offered in a dialog | No admin prompt. `/CURRENTUSER` and `/ALLUSERS` work for package managers. |
| AppId | `{01C6145B-761B-4EB9-A11D-36BEE004B479}` | **Never change it.** Upgrades and the single Apps & features entry depend on it. |
| GPU | Optional task: pip installs `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` (~1.3 GB) into `{app}\gpu` | Keeps `setup.exe` small. With these libraries, Auto picks Whisper large-v3-turbo on CUDA. |
| Models | Not bundled. The user picks and downloads them in *Settings → This PC & speech models*; if Start needs a missing model, Dolmi asks first (size shown) instead of downloading silently | A single speech model is 0.5–3 GB, and which one fits depends on the PC. Settings shows what the PC can run. |
| Web assets | Fonts and icons vendored into `src/ui/web/vendor` | Works offline, and nothing contacts a font server or CDN. |

## Where things live

| What | Where | On uninstall |
|---|---|---|
| Program (runtime, app, GPU add-on) | `{app}` = `%LOCALAPPDATA%\Programs\Dolmi`, or `Program Files\Dolmi` for all users | Removed |
| Settings, keys, vocabulary, glossary | `%APPDATA%\Dolmi` (copied from the old location on first run) | Kept |
| Transcripts, summaries, chats | `Documents\Dolmi` | Kept |
| Log | `%LOCALAPPDATA%\Dolmi\dolmi.log` | Kept |
| Models | `%USERPROFILE%\.cache\huggingface` | Kept |

Nothing is written into `{app}` at runtime, so an all-users install under Program Files never hits "Access denied".

## Build and release

- **Local build:** `powershell -ExecutionPolicy Bypass -File installer\build.ps1`. It downloads the checksum-pinned Python runtime, installs the locked packages, copies the app, precompiles `.pyc` files, smoke-tests imports, then runs ISCC and writes `SHA256SUMS.txt`.
- **Release:** bump `VERSION`, commit, then `git tag vX.Y.Z` and `git push origin vX.Y.Z`. GitHub Actions then runs the tests, builds, runs the **install test**, and creates a **draft** release with `setup.exe` and checksums. Review the draft and publish it.
- **On demand:** *Actions → Installer test → Run workflow*. It also runs on pushes that change the app or the installer, and keeps the built `setup.exe` as an artifact for 7 days.

## The install test (`installer\test-install.ps1`)

It runs the guide's release checklist on a clean runner: silent install, the PSF signature on the launcher, the Start-menu shortcut, no Desktop shortcut when that task is unticked, and the Apps & features entry and its version. Then it checks that the installed runtime imports the app and reports the right version, and that **the app launches and runs for 15 s with no traceback**. Finally it checks that an **upgrade leaves exactly one entry**, and that **uninstall removes `{app}`, the entry and the shortcut while keeping user data**.

Locally it refuses to run when Smart App Control is on, because Windows blocks unsigned installers there. It also refuses if Dolmi is really installed, and it restores any existing Dolmi shortcuts.

## Next stages

1. **Signing (SignPath Foundation, free).** Ready: an OSI licence (MIT), a public repo, CI builds, an uninstaller, the privacy policy (`PRIVACY.md`) and the code signing policy section (README). Still to do: turn on MFA for GitHub and SignPath, apply at https://signpath.org/apply, then add the signing step from the guide (Stage 4) to `release.yml`. Until then, users with Smart App Control on are blocked, and others see *More info → Run anyway*.
2. **winget.** After the first published release: `wingetcreate new <release asset URL>` creates `AnnasMustafaDev.Dolmi` (InstallerType `inno`, Scope `user`). Then add the `winget-releaser` workflow with a classic `WINGET_TOKEN`.
