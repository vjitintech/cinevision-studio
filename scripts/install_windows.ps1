# CineVision Studio - install or update the SOURCE version on Windows, then start it.
# (Most people should use the one-click zip from the Releases page instead - it needs nothing installed.)
#
# One line, in PowerShell:
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/vjitintech/cinevision-studio/main/scripts/install_windows.ps1 | iex"
#
# What it does (safe to run again any time - it only adds what is missing):
#   1. installs Git, Python 3.13, Node.js LTS and FFmpeg with winget if they are not already there
#   2. downloads CineVision into  %USERPROFILE%\Documents\cinevision-studio  (or updates it if it is already there)
#   3. puts a "CineVision (source)" shortcut on your desktop and starts CineVision
#
# Optional environment variables: CINEVISION_DIR (install folder), CINEVISION_REPO_URL (git URL),
# CINEVISION_INSTALL_NORUN=1 (install/update only, don't start).
# No "param()" block and no top-level "exit": this file must also work when piped into Invoke-Expression.

function Install-CineVision {
  $ErrorActionPreference = "Stop"
  $repoUrl = if ($env:CINEVISION_REPO_URL) { $env:CINEVISION_REPO_URL } else { "https://github.com/vjitintech/cinevision-studio.git" }
  $dest = if ($env:CINEVISION_DIR) { $env:CINEVISION_DIR } else { Join-Path $env:USERPROFILE "Documents\cinevision-studio" }

  function Say($msg) { Write-Host $msg -ForegroundColor Cyan }
  function Ok($msg) { Write-Host "  OK  $msg" -ForegroundColor Green }
  function Problem($msg) { Write-Host ""; Write-Host $msg -ForegroundColor Red; Write-Host "" }

  function Update-PathFromRegistry {
    # winget changes PATH for new windows only; read it again so this window sees the new programs
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $links = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
    $env:Path = (@($machine, $user, $links, $env:Path) | Where-Object { $_ }) -join ";"
  }

  function Test-Python {
    foreach ($cand in @("py -3.13", "py -3.12", "py -3.11", "python")) {
      $parts = $cand.Split(" ")
      if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
      try {
        $rest = @($parts | Select-Object -Skip 1)
        $v = & $parts[0] @rest -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
        if ($LASTEXITCODE -eq 0 -and [int]"$v" -ge 311) { return $true }
      } catch { }
    }
    return $false
  }

  function Ensure-Tool($name, $wingetId, [scriptblock]$present) {
    if (& $present) { Ok "$name is installed"; return $true }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
      Problem ("$name is missing and winget (Microsoft's 'App Installer') is not available to install it.`n" +
               "Install 'App Installer' from the Microsoft Store, or install $name yourself, then run this again.")
      return $false
    }
    Say "Installing $name (this can take a few minutes)..."
    & winget install --id $wingetId --exact --source winget --silent --accept-package-agreements --accept-source-agreements | Out-Host
    Update-PathFromRegistry
    if (& $present) { Ok "$name installed"; return $true }
    Problem "$name was installed but this window can't see it yet. Close this window, open a new PowerShell window and run the same command again."
    return $false
  }

  Write-Host ""
  Write-Host "CineVision Studio - source install / update" -ForegroundColor Green
  Write-Host "Folder: $dest"
  Write-Host ""
  Update-PathFromRegistry

  $okAll = $true
  $okAll = (Ensure-Tool "Git" "Git.Git" { [bool](Get-Command git -ErrorAction SilentlyContinue) }) -and $okAll
  $okAll = (Ensure-Tool "Python 3.13" "Python.Python.3.13" { Test-Python }) -and $okAll
  $okAll = (Ensure-Tool "Node.js" "OpenJS.NodeJS.LTS" { [bool](Get-Command node -ErrorAction SilentlyContinue) }) -and $okAll
  $okAll = (Ensure-Tool "FFmpeg" "Gyan.FFmpeg" { [bool]((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue)) }) -and $okAll
  if (-not $okAll) { return 1 }

  # ---- get or update the code
  if (Test-Path (Join-Path $dest ".git")) {
    Say "Updating CineVision in $dest ..."
    & git -C $dest pull --ff-only | Out-Host
    if ($LASTEXITCODE -ne 0) {
      Write-Host "  Could not update automatically (files in the folder were changed?). Starting the version you have." -ForegroundColor Yellow
    } else { Ok "up to date" }
  } elseif (Test-Path (Join-Path $dest "scripts\run_local.ps1")) {
    Write-Host "  $dest is not a git download, so it can't be updated automatically. Starting it as it is." -ForegroundColor Yellow
  } elseif ((Test-Path $dest) -and @(Get-ChildItem -Force $dest).Count -gt 0) {
    Problem "$dest already exists and is not CineVision. Move it away or set CINEVISION_DIR to another folder."
    return 1
  } else {
    Say "Downloading CineVision into $dest ..."
    $parent = Split-Path -Parent $dest
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    & git clone $repoUrl $dest | Out-Host
    if ($LASTEXITCODE -ne 0) { Problem "Downloading failed. Check your internet connection and run this again."; return 1 }
    Ok "downloaded"
  }

  $runner = Join-Path $dest "scripts\run_windows.cmd"
  if (-not (Test-Path $runner)) { Problem "$runner is missing - the download looks incomplete."; return 1 }

  # ---- desktop shortcut (once)
  try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desktop "CineVision (source).lnk"
    if ($desktop -and -not (Test-Path $lnk)) {
      $sh = New-Object -ComObject WScript.Shell
      $s = $sh.CreateShortcut($lnk)
      $s.TargetPath = $runner
      $s.WorkingDirectory = $dest
      $s.Description = "Start CineVision Studio"
      $s.Save()
      Ok "desktop shortcut 'CineVision (source)' created"
    }
  } catch { Write-Host "  (could not create a desktop shortcut: $($_.Exception.Message))" -ForegroundColor Yellow }

  if ($env:CINEVISION_INSTALL_NORUN -eq "1") { Ok "installed (not started because CINEVISION_INSTALL_NORUN=1)"; return 0 }

  Write-Host ""
  Say "Starting CineVision. The first start takes a few minutes (it prepares Python packages and the web interface)."
  Say "Then open  http://127.0.0.1:8000  in your browser. Close the CineVision window to stop it."
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dest "scripts\run_local.ps1") | Out-Host
  return $LASTEXITCODE
}

$cvResult = Install-CineVision
if ($cvResult -is [array]) { $cvResult = $cvResult[-1] }
if ($PSCommandPath) {
  # run as a file (powershell -File ...): report success or failure through the exit code
  exit ([int]$cvResult)
}
