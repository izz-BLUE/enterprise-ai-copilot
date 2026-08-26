# Load backend-java/.env.local into current process env (PowerShell)
# Usage: powershell -NoProfile -File .load-env.ps1
$ErrorActionPreference = 'Stop'
$path = Join-Path $PSScriptRoot '.env.local'
if (-not (Test-Path $path)) { throw ".env.local not found at $path" }
Get-Content $path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { return }
    $idx = $line.IndexOf('=')
    if ($idx -le 0) { return }
    $name = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim()
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}
Write-Host "[load-env] applied $( (Get-Content $path | Where-Object { $_ -and -not $_.StartsWith('#') }).Count ) vars from $path"