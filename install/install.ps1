$ErrorActionPreference = "Stop"
$Source = if ($env:TIMONEL_SOURCE) { $env:TIMONEL_SOURCE } else { "timonel" }

Write-Host "Timonel installer"
Write-Host "This script installs Timonel for your user only. It never asks for administrator rights."
Write-Host ""

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $Command = @("uv", "tool", "install", $Source)
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    $Command = @("pipx", "install", $Source)
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    Write-Host "Neither uv nor pipx was found. The safest option is pipx (isolated per-user installs):"
    Write-Host "  py -m pip install --user pipx; py -m pipx ensurepath"
    Write-Host "Then run this script again. Guide: https://pipx.pypa.io/stable/installation/"
    exit 1
} else {
    Write-Host "Python 3.11 or newer was not found. Install it with:"
    Write-Host "  winget install --id Python.Python.3.12 -e"
    Write-Host "or from https://www.python.org/downloads/ and run this script again."
    exit 1
}

Write-Host "It will run:"
Write-Host ("  " + ($Command -join " "))
Write-Host ""
$Answer = Read-Host "Continue? [y/N]"
if ($Answer -notmatch '^(y|yes|s|si|sí)$') {
    Write-Host "Nothing was changed."
    exit 0
}

& $Command[0] $Command[1..($Command.Length - 1)]
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host "Done. Open a new terminal if 'timonel' is not found, then run:"
Write-Host "  timonel start"
