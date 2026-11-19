# Build dist\Mistcoat.exe on Windows.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path .venv)) { py -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt pyinstaller
& $py tools\make_icons.py
& $py -m PyInstaller packaging\mistcoat.spec --noconfirm --distpath dist --workpath build

# Prove the build works before shipping it.
& .\dist\Mistcoat.exe --selftest dist\selftest.json
$report = Get-Content dist\selftest.json -Raw | ConvertFrom-Json
if (-not $report.ok) { throw "Self-test failed: $($report.error)" }
Write-Host "Built dist\Mistcoat.exe - self-test passed (durability $($report.durability))"
