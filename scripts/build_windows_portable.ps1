# Build the one-click Windows app: CineVision-Windows-x64.zip
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows_portable.ps1 [-OutDir dist]
#
# Needs (on the BUILD machine only): 64-bit Python 3.13 on PATH (the same minor version as the embedded one),
# Node.js 20+ (only if frontend\dist is missing or -BuildFrontend is given), internet access.
# The person who downloads the zip needs nothing: Python, the backend packages, the web interface and FFmpeg
# are all inside. Their data, keys and logs go to %LOCALAPPDATA%\CineVision.
#
# Layout of the zip:
#   CineVision\CineVision.cmd           start (console window; close it to stop)
#   CineVision\Start CineVision.vbs     start with the window minimised
#   CineVision\README.txt
#   CineVision\python\                  official python.org embeddable package + site-packages
#   CineVision\app\backend\cinevision\  the server
#   CineVision\app\frontend\dist\       the prebuilt web interface
#   CineVision\ffmpeg\bin\              ffmpeg.exe, ffprobe.exe (gyan.dev "essentials" release build)
[CmdletBinding()]
param(
  [string]$OutDir = "dist",
  [string]$PythonVersion = "",
  [string]$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
  [switch]$BuildFrontend
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar on
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Invoke-Checked {
  param([string]$Exe, [string[]]$Arguments)
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) { Fail "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}
function Download($url, $dest) {
  Step "Downloading $url"
  for ($i = 1; $i -le 3; $i++) {
    try { Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing; return }
    catch { if ($i -eq 3) { throw } ; Write-Host "  retrying ($i)..."; Start-Sleep -Seconds (5 * $i) }
  }
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not [System.IO.Path]::IsPathRooted($OutDir)) { $OutDir = Join-Path $root $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
$stage = Join-Path $OutDir "CineVision"
$work = Join-Path $OutDir "work"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force -Path $stage, $work | Out-Null

# ---------------------------------------------------------------- host Python (decides the embedded version)
$hostPy = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $hostPy) { Fail "Python 3.13 (64-bit) must be on PATH to build." }
$hostPy = $hostPy.Source
$info = (& $hostPy -c "import platform,struct;print(platform.python_version(), struct.calcsize('P')*8)").Trim().Split(" ")
if ($LASTEXITCODE -ne 0) { Fail "Could not run $hostPy" }
if ($info[1] -ne "64") { Fail "The build Python must be 64-bit." }
if (-not $PythonVersion) { $PythonVersion = $info[0] }
if ($PythonVersion -notmatch '^3\.13\.\d+$') { Fail "Python 3.13.x is required (found $PythonVersion)." }
if ($info[0] -ne $PythonVersion) { Fail "Build Python is $($info[0]) but -PythonVersion is $PythonVersion; they must match." }
$tag = "3" + $PythonVersion.Split(".")[1]   # e.g. 313

# ---------------------------------------------------------------- embedded Python
$pyZip = Join-Path $work "python-embed.zip"
Download "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $pyZip
$pyDir = Join-Path $stage "python"
Expand-Archive -Path $pyZip -DestinationPath $pyDir -Force
$pth = Join-Path $pyDir "python$tag._pth"
if (-not (Test-Path $pth)) { Fail "python$tag._pth not found in the embeddable package." }
# sys.path of the embedded interpreter is exactly this list (PYTHONPATH and the user's site-packages are ignored)
$pthLines = @("python$tag.zip", ".", "Lib\site-packages", "..\app\backend", "import site")
[System.IO.File]::WriteAllLines($pth, $pthLines)
$site = Join-Path $pyDir "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $site | Out-Null

Step "Installing the backend packages into the embedded Python"
Invoke-Checked $hostPy @("-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--prefer-binary",
  "--no-warn-script-location", "--target", $site, "-r", (Join-Path $root "backend\requirements.txt"))
# yt-dlp (pure Python, Unlicense): lets "paste a link" take YouTube/Vimeo/Instagram links, not only video files
Invoke-Checked $hostPy @("-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
  "--no-warn-script-location", "--target", $site, "yt-dlp")
# console-script launchers point at the build machine's python.exe: useless (and confusing) in the zip
$bin = Join-Path $site "bin"
if (Test-Path $bin) { Remove-Item -Recurse -Force $bin }
# OpenCV and friends need the Microsoft C++ runtime. The embeddable package ships vcruntime140*.dll but not
# msvcp140*.dll, which a fresh Windows may lack. Microsoft allows app-local copies, so put them next to python.exe.
foreach ($dll in @("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "concrt140.dll",
                   "vcruntime140.dll", "vcruntime140_1.dll")) {
  $src = Join-Path $env:SystemRoot "System32\$dll"
  if ((Test-Path $src) -and -not (Test-Path (Join-Path $pyDir $dll))) { Copy-Item $src -Destination $pyDir }
}

# ---------------------------------------------------------------- application
Step "Copying the application"
$appBackend = Join-Path $stage "app\backend"
New-Item -ItemType Directory -Force -Path $appBackend | Out-Null
Copy-Item -Recurse -Path (Join-Path $root "backend\cinevision") -Destination $appBackend
Copy-Item -Path (Join-Path $root "backend\requirements.txt") -Destination $appBackend
Get-ChildItem -Path $appBackend -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
$appScripts = Join-Path $stage "app\scripts"
New-Item -ItemType Directory -Force -Path $appScripts | Out-Null
foreach ($f in @("gen_keys.py", "backup.py")) { Copy-Item -Path (Join-Path $root "scripts\$f") -Destination $appScripts }
foreach ($f in @("LICENSE", "LICENSE.md", "LICENSE.txt")) {
  $p = Join-Path $root $f
  if (Test-Path $p) { Copy-Item -Path $p -Destination (Join-Path $stage "LICENSE.txt") }
}

$dist = Join-Path $root "frontend\dist"
if ($BuildFrontend -or -not (Test-Path (Join-Path $dist "index.html"))) {
  Step "Building the web interface"
  Push-Location (Join-Path $root "frontend")
  try {
    Invoke-Checked "npm" @("install", "--no-audit", "--no-fund")
    Invoke-Checked "npm" @("run", "build")
  } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $dist "index.html"))) { Fail "frontend\dist\index.html is missing." }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "app\frontend") | Out-Null
Copy-Item -Recurse -Path $dist -Destination (Join-Path $stage "app\frontend\dist")

# ---------------------------------------------------------------- FFmpeg (static build, checksum verified)
$ffZip = Join-Path $work "ffmpeg.zip"
Download $FfmpegUrl $ffZip
$expected = ""
try {
  $shaFile = Join-Path $work "ffmpeg.zip.sha256"
  Download "$FfmpegUrl.sha256" $shaFile
  $expected = ((Get-Content -Raw $shaFile).Trim() -split '\s+')[0].ToLowerInvariant()
} catch { Write-Host "  (no published checksum found next to $FfmpegUrl)" -ForegroundColor Yellow }
if ($expected) {
  $actual = (Get-FileHash -Algorithm SHA256 $ffZip).Hash.ToLowerInvariant()
  if ($actual -ne $expected) { Fail "FFmpeg download checksum mismatch ($actual vs $expected)." }
  Write-Host "  FFmpeg checksum verified."
} elseif ($FfmpegUrl -like "https://www.gyan.dev/*") {
  Fail "Could not verify the FFmpeg download (checksum file missing)."
}
$ffTmp = Join-Path $work "ffmpeg"
Expand-Archive -Path $ffZip -DestinationPath $ffTmp -Force
$ffExe = Get-ChildItem -Path $ffTmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
$fpExe = Get-ChildItem -Path $ffTmp -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
if (-not $ffExe -or -not $fpExe) { Fail "ffmpeg.exe / ffprobe.exe not found in the FFmpeg download." }
$ffBin = Join-Path $stage "ffmpeg\bin"
New-Item -ItemType Directory -Force -Path $ffBin | Out-Null
Copy-Item $ffExe.FullName, $fpExe.FullName -Destination $ffBin
$ffRoot = $ffExe.Directory.Parent.FullName
foreach ($f in @("LICENSE", "LICENSE.txt", "README.txt")) {
  $p = Join-Path $ffRoot $f
  if (Test-Path $p) { Copy-Item $p -Destination (Join-Path $stage "ffmpeg\$f") }
}
$ffVersion = (& (Join-Path $ffBin "ffmpeg.exe") -hide_banner -version | Select-Object -First 1)
Set-Content -Path (Join-Path $stage "ffmpeg\SOURCE.txt") -Encoding ASCII -Value @(
  "FFmpeg is free software (GPL v3 for this build), included unmodified.",
  "Build: $ffVersion",
  "Downloaded from: $FfmpegUrl",
  "Source code: https://ffmpeg.org/download.html and https://www.gyan.dev/ffmpeg/builds/")

# ---------------------------------------------------------------- launchers and read-me
Step "Writing the launchers"
$cmd = @(
  '@echo off',
  'rem CineVision Studio - double-click to start. Close this window to stop.',
  'setlocal',
  'title CineVision Studio',
  'cd /d "%~dp0"',
  'set "PYTHONUTF8=1"',
  'set "PYTHONNOUSERSITE=1"',
  'set "PYTHONDONTWRITEBYTECODE=1"',
  '"%~dp0python\python.exe" -m cinevision.launcher %*',
  'if errorlevel 1 (',
  '  echo.',
  '  echo CineVision stopped because of a problem. The message above says why.',
  '  echo Logs: %LOCALAPPDATA%\CineVision\logs',
  '  pause',
  ')',
  'endlocal')
[System.IO.File]::WriteAllText((Join-Path $stage "CineVision.cmd"), (($cmd -join "`r`n") + "`r`n"), [System.Text.Encoding]::ASCII)
$vbs = @(
  "' Starts CineVision Studio with its window minimised. Close that window (taskbar) to stop CineVision.",
  'Set fso = CreateObject("Scripting.FileSystemObject")',
  'here = fso.GetParentFolderName(WScript.ScriptFullName)',
  'Set sh = CreateObject("WScript.Shell")',
  'sh.Run """" & here & "\CineVision.cmd""", 7, False')
[System.IO.File]::WriteAllText((Join-Path $stage "Start CineVision.vbs"), (($vbs -join "`r`n") + "`r`n"), [System.Text.Encoding]::ASCII)
$readme = @(
  "CineVision Studio for Windows",
  "=============================",
  "",
  "Start:  double-click  CineVision.cmd   (or  'Start CineVision.vbs'  for a minimised window).",
  "        Your browser opens CineVision at http://127.0.0.1:8000 after a few seconds.",
  "Stop:   close the CineVision window.",
  "",
  "The first account you create is the owner. Nothing else needs installing: Python, FFmpeg and the",
  "web interface are inside this folder. CineVision only listens on this computer (127.0.0.1).",
  "",
  "Your data:  %LOCALAPPDATA%\CineVision\data   (projects, encrypted)",
  "Your keys:  %LOCALAPPDATA%\CineVision\keys   (copy this folder somewhere safe - without",
  "            master_key your projects cannot be opened)",
  "Logs:       %LOCALAPPDATA%\CineVision\logs",
  "",
  "Updating: download the new zip, unzip it, start the new CineVision.cmd. Your data and keys stay.",
  "Removing: delete this folder. To delete your projects too, delete %LOCALAPPDATA%\CineVision.",
  "",
  "Windows may say 'Windows protected your PC' because this download is not code-signed.",
  "Click 'More info' and 'Run anyway' only if you downloaded it from the project's GitHub Releases page.",
  "",
  "Optional: reading on-screen text (titles, credits) works better with Tesseract OCR. Install it with",
  "  winget install UB-Mannheim.TesseractOCR",
  "CineVision finds it automatically in C:\Program Files\Tesseract-OCR. Everything else works without it.",
  "",
  "Advanced settings (environment variables, set before starting):",
  "  CINEVISION_PORT=8001         use another port",
  "  CINEVISION_SIGNUP=owner_only only the owner, plus people with an invite code, can create accounts",
  "  CINEVISION_HOST=0.0.0.0      let other computers on your network connect (NOT recommended: plain",
  "                               http, a warning is shown at start-up)",
  "",
  "Python $PythonVersion (python.org embeddable package), FFmpeg: see ffmpeg\SOURCE.txt.")
[System.IO.File]::WriteAllText((Join-Path $stage "README.txt"), (($readme -join "`r`n") + "`r`n"), [System.Text.Encoding]::ASCII)

# ---------------------------------------------------------------- pre-compile and self-check
Step "Pre-compiling and checking the embedded Python"
$embPy = Join-Path $pyDir "python.exe"
& $embPy -m compileall -q -j 0 (Join-Path $stage "app\backend") | Out-Null
$env:PYTHONPATH = ""   # the ._pth file must make the embedded interpreter self-contained
Invoke-Checked $embPy @("-c", "import sys, sqlite3, ssl, ctypes, numpy, cv2, uvicorn, starlette, cryptography, reportlab, pydantic, python_multipart; import cinevision.app, cinevision.launcher, cinevision.worker; assert sys.version_info[:2] == (3, 13); print('embedded python ok:', sys.version.split()[0], 'opencv', cv2.__version__)")
Invoke-Checked $embPy @("-m", "cinevision.launcher", "--check", "--home", (Join-Path $work "check-home"))

# ---------------------------------------------------------------- zip
$zip = Join-Path $OutDir "CineVision-Windows-x64.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Step "Creating $zip"
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
$hash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLowerInvariant()
Set-Content -Path "$zip.sha256" -Encoding ASCII -Value "$hash  CineVision-Windows-x64.zip"
Remove-Item -Recurse -Force $work
$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $zip ($mb MB, sha256 $hash)" -ForegroundColor Green
