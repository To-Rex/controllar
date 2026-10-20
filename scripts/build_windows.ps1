# Build ToRexLink for Windows. Run from a PowerShell prompt:
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\scripts\build_windows.ps1
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = $env:PYTHON_BIN
if (-not $Python) { $Python = "py" }
if ($Python -eq "py") {
    & py -3.12 -m venv .venv
} else {
    & $Python -m venv .venv
}
if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment. Install Python 3.12+ x64." }

& .\.venv\Scripts\python.exe -m pip install --upgrade pip wheel
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

& .\.venv\Scripts\python.exe scripts\generate_icons.py
& .\.venv\Scripts\python.exe -m PyInstaller packaging\ToRexLink.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$Exe = Join-Path $Root "dist\ToRexLink\ToRexLink.exe"
if (-not (Test-Path $Exe)) { throw "Build did not produce $Exe" }

$selfTest = Start-Process -FilePath $Exe -ArgumentList "--self-test" -NoNewWindow -Wait -PassThru
if ($selfTest.ExitCode -ne 0) { throw "Packaged dependency self-test failed." }

Write-Host "Built: $Exe"
Write-Host "Run:   $Exe"

# Optional installer (Inno Setup 6+).
if (Get-Command iscc.exe -ErrorAction SilentlyContinue) {
    & iscc.exe packaging\ToRexLink.iss
    if ($LASTEXITCODE -eq 0) { Write-Host "Installer: dist\ToRexLink-Setup.exe" }
} else {
    Write-Host "Inno Setup not found; portable build is ready."
}
