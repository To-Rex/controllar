# Builds FlowLink for Windows (release) and prints the output path.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "==> cargo build --release -p flowlink-win"
cargo build --release --manifest-path "$root\rust\Cargo.toml" -p flowlink-win

$exe = Join-Path $root "rust\target\release\flowlink-win.exe"
if (Test-Path $exe) {
    Write-Host "==> done: $exe"
    Write-Host "    run it directly, or enable "Launch at login" in FlowLink's settings."
} else {
    Write-Error "build did not produce $exe"
}
