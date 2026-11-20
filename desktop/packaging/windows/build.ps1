# Build desktop\dist\Mistcoat.exe on Windows.
#
#   powershell -ExecutionPolicy Bypass -File desktop\packaging\windows\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")   # desktop\

if (-not (Test-Path .venv)) { py -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -e ".[dev]"
& $py -m PyInstaller packaging\pyinstaller\mistcoat.spec --noconfirm --distpath dist --workpath build

# Prove the build works before shipping it.
# It is a windowed exe, so PowerShell would not wait for it on its own.
Remove-Item dist\selftest.json -ErrorAction SilentlyContinue
$proc = Start-Process .\dist\Mistcoat.exe -ArgumentList "--selftest", "dist\selftest.json" -Wait -PassThru
if (-not (Test-Path dist\selftest.json)) { throw "Self-test wrote no report (exit code $($proc.ExitCode))" }
$report = Get-Content dist\selftest.json -Raw | ConvertFrom-Json
if (-not $report.ok) { throw "Self-test failed: $($report.error)" }
Write-Host "Built dist\Mistcoat.exe ($($report.arch)) - self-test passed (durability $($report.durability), $($report.compute))"
