# Parse every PowerShell script in scripts\ and fail on syntax errors (used by CI on Windows).
$bad = 0
Get-ChildItem -Path $PSScriptRoot -Filter *.ps1 | ForEach-Object {
  $tokens = $null; $errs = $null
  [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errs) | Out-Null
  foreach ($e in $errs) {
    Write-Host "::error file=scripts/$($_.Name),line=$($e.Extent.StartLineNumber)::$($e.Message)"
    $bad++
  }
  if (-not $errs) { Write-Host "ok  $($_.Name)" }
}
if ($bad -gt 0) { exit 1 }
