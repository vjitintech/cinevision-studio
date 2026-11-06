# CineVision Studio - full (self-hosted) edition on Windows, without Docker.
# Run from the repository folder in PowerShell:   powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1
# Needs: Python 3.11+ (python.org or winget), Node.js 20+, FFmpeg on PATH. See README "Windows".
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$root = (Get-Location).Path

function Fail($msg) { Write-Host $msg -ForegroundColor Red; exit 1 }
function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Write-Host "Missing: $cmd. $hint" -ForegroundColor Red; exit 1 }
}

# --- pick a Python 3.11+ (py launcher first, then python on PATH) ---
$py = $null
foreach ($cand in @("py -3.13", "py -3.12", "py -3.11", "python")) {
  try {
    $parts = $cand.Split(" ")
    $v = & $parts[0] @($parts[1..9] | Where-Object { $_ }) -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
    if ($LASTEXITCODE -eq 0 -and [int]$v -ge 311) { $py = $parts; break }
  } catch {}
}
if (-not $py) { Write-Host "Python 3.11 or newer not found. Install it with:  winget install Python.Python.3.12" -ForegroundColor Red; exit 1 }
Need "node"    "Install it with:  winget install OpenJS.NodeJS.LTS   (then open a new PowerShell window)"
Need "ffmpeg"  "Install it with:  winget install Gyan.FFmpeg   (then open a new PowerShell window)"
Need "ffprobe" "It comes with FFmpeg:  winget install Gyan.FFmpeg"

# --- keys (kept outside the data folder) ---
if (-not (Test-Path "deploy\secrets\master_key")) {
  & $py[0] @($py[1..9] | Where-Object { $_ }) scripts\gen_keys.py deploy\secrets
  if ($LASTEXITCODE -ne 0) { Fail "Could not create encryption keys." }
}

# --- Python environment ---
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "Creating Python environment (first run only)..."
  & $py[0] @($py[1..9] | Where-Object { $_ }) -m venv .venv
  if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python environment." }
  & .venv\Scripts\python.exe -m pip install --upgrade pip -q
  & .venv\Scripts\python.exe -m pip install -q -r backend\requirements.txt
  if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force .venv; Fail "Installing Python packages failed (see the messages above). Fix the problem and run this script again." }
}

# --- build the web interface (first run, and again whenever the source changed, e.g. after git pull) ---
$needBuild = -not (Test-Path "frontend\dist\index.html")
if (-not $needBuild) {
  $built = (Get-Item "frontend\dist\index.html").LastWriteTime
  $newest = (Get-ChildItem -Recurse -File "frontend\src", "frontend\build.mjs", "frontend\package.json" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
  $needBuild = $newest -gt $built
}
if ($needBuild) {
  Write-Host "Building the web interface..."
  Push-Location frontend
  npm install --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm install failed (see the messages above)." }
  npm run build
  if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Building the web interface failed (see the messages above)." }
  Pop-Location
}

$port = if ($env:CINEVISION_PORT) { $env:CINEVISION_PORT } else { "8000" }
$env:CINEVISION_MASTER_KEY_FILE = Join-Path $root "deploy\secrets\master_key"
if (-not $env:CINEVISION_DATA_DIR) { $env:CINEVISION_DATA_DIR = Join-Path $root "data" }
$env:CINEVISION_PUBLIC_ORIGIN   = "http://127.0.0.1:$port"
$env:CINEVISION_EXTRA_ORIGINS   = "http://localhost:$port"
$env:CINEVISION_PORT            = $port
$env:CINEVISION_COOKIE_SECURE   = "false"     # plain http is acceptable only on 127.0.0.1
$env:CINEVISION_EMBEDDED_WORKER = "true"
$env:CINEVISION_FRONTEND_DIST   = Join-Path $root "frontend\dist"
$env:PYTHONUTF8                 = "1"
$face = Join-Path $root "backend\cinevision\models\face_detection_yunet_2023mar.onnx"
if ((-not $env:CINEVISION_FACE_MODEL) -and (Test-Path $face)) { $env:CINEVISION_FACE_MODEL = $face }
# The server listens on 127.0.0.1 (this computer only). Other computers can reach it only if you set
# CINEVISION_HOST=0.0.0.0 yourself - the server then prints a warning.

Write-Host ""
Write-Host "CineVision Studio is starting -> open  http://127.0.0.1:$port  (Ctrl+C here to stop)" -ForegroundColor Green
Set-Location backend
& ..\.venv\Scripts\python.exe -m cinevision.app
exit $LASTEXITCODE
