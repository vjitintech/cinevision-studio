# Installing CineVision on Windows

There are three ways. Pick the first unless you want to change the code.

## 1. One-click app (no Python, Node, Git or FFmpeg needed)

1. Open the project's **Releases** page on GitHub and download `CineVision-Windows-x64.zip`
   (and, if you like, check it against `CineVision-Windows-x64.zip.sha256`).
2. Right-click the zip → **Extract All**. Put the folder anywhere, for example in `Documents`.
3. Double-click **CineVision.cmd** (or **Start CineVision.vbs** to keep the window minimised).
4. Your browser opens `http://127.0.0.1:8000`. Create your account — the first one is the owner.
5. To stop CineVision, close its window.

Where things are kept (the same for every version you unzip):

| What | Where |
|---|---|
| Projects (encrypted) | `%LOCALAPPDATA%\CineVision\data` |
| Encryption keys | `%LOCALAPPDATA%\CineVision\keys` — **copy this folder somewhere safe**; without `master_key` your projects can't be opened |
| Logs | `%LOCALAPPDATA%\CineVision\logs` |

**Update:** unzip the new version and start its `CineVision.cmd`; delete the old folder. Your data stays.
**Uninstall:** delete the folder. To also delete your projects, delete `%LOCALAPPDATA%\CineVision`.

The zip is not code-signed yet, so Windows SmartScreen may say "Windows protected your PC". Click
**More info → Run anyway** only if you downloaded it from the project's Releases page.

What is inside: the official python.org *embeddable* Python 3.13 with the backend packages, the prebuilt web
interface, FFmpeg (gyan.dev "essentials" build, GPL — see `ffmpeg\SOURCE.txt`) and the Microsoft C++ runtime DLLs.
Optional: install Tesseract OCR (`winget install UB-Mannheim.TesseractOCR`); CineVision finds it in
`C:\Program Files\Tesseract-OCR` automatically.

The zip is built by `scripts\build_windows_portable.ps1` in the `release-windows` GitHub workflow, which also starts
the unzipped app with an empty PATH, checks the web interface and a sign-up, and starts it a second time.

## 2. Source version with one line

In PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/vjitintech/cinevision-studio/main/scripts/install_windows.ps1 | iex"
```

`scripts\install_windows.ps1` installs whatever is missing of Git, Python 3.13, Node.js LTS and FFmpeg with
**winget**, clones the repository into `%USERPROFILE%\Documents\cinevision-studio` (or runs `git pull` if it is
already there), creates a **CineVision (source)** desktop shortcut and starts `scripts\run_local.ps1`. Running it
again is safe. Options (environment variables): `CINEVISION_DIR`, `CINEVISION_REPO_URL`, `CINEVISION_INSTALL_NORUN=1`.

The source version keeps its data in `data\` and its keys in `deploy\secrets\` inside the repository folder.

## 3. Manual

```powershell
winget install Python.Python.3.13
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
winget install Git.Git
git clone https://github.com/vjitintech/cinevision-studio.git $HOME\Documents\cinevision-studio
```

Open a **new** PowerShell window, then double-click `scripts\run_windows.cmd` in that folder.

## Who can sign in

CineVision listens on `127.0.0.1` only, so nobody else on your network can reach it. Sign-up is controlled by
`CINEVISION_SIGNUP`:

| Value | Meaning |
|---|---|
| `open` (default) | anyone who can open the page can create an account with email + password (no email check) |
| `owner_only` | the first account, then only people with an invite code (Security & privacy → Invite codes) |
| `closed` | the first account only |

The owner can make other accounts **admins** (Security & privacy → Accounts). Admins can change the AI settings and
create invite codes; only the owner chooses admins.

If you really want other computers to connect, set `CINEVISION_HOST=0.0.0.0` before starting. CineVision then
prints a large warning, and the Security page shows a banner while sign-up is open over plain http. For network use,
prefer the Docker set-up (HTTPS) with `CINEVISION_SIGNUP=owner_only`.
