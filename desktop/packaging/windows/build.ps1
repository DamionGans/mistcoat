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

# Headless command line, the way an add-on host runs it: no window, output
# captured through redirected handles, results read back as JSON.
$sample = Resolve-Path ..\samples\test_painting.png
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("mistcoat-cli-" + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
try {
    foreach ($exe in @(".\dist\mistcoat-cli.exe", ".\dist\Mistcoat.exe")) {
        $name = [IO.Path]::GetFileNameWithoutExtension($exe)
        $out = Join-Path $tmp "$name.json"
        $err = Join-Path $tmp "$name.err"
        $dest = Join-Path $tmp "$name-out.png"
        $p = Start-Process $exe -ArgumentList @("protect", "`"$sample`"", "--output-file", "`"$dest`"", "--json", "--quick") `
            -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru -WindowStyle Hidden
        $doc = Get-Content $out -Raw | ConvertFrom-Json
        if ($p.ExitCode -ne 0 -or -not $doc.ok -or -not (Test-Path $dest)) {
            Get-Content $err | Write-Host
            throw "$name headless protect failed (exit $($p.ExitCode))"
        }
        Write-Host "$name headless protect: ok -> $($doc.results[0].output) ($($doc.compute))"
    }
    # Results without any stdout at all: --json-file.
    $jf = Join-Path $tmp "report.json"
    $p = Start-Process .\dist\Mistcoat.exe -ArgumentList @("info", "--json-file", "`"$jf`"", "--quiet") -Wait -PassThru
    if ($p.ExitCode -ne 0 -or -not (Get-Content $jf -Raw | ConvertFrom-Json).ok) { throw "--json-file failed" }
    Write-Host "Mistcoat.exe info --json-file: ok"
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
