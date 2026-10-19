# Builds FlowLink for Windows and stages the exe in dist\FlowLink-Windows.
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1 -Configuration debug
#
# Requirements: Rust via rustup (MSVC toolchain) and Visual Studio Build Tools
# with the "Desktop development with C++" workload. See docs/BUILD.md.
param(
    [ValidateSet("release", "debug")]
    [string]$Configuration = "release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$manifest = Join-Path $root "rust\Cargo.toml"
$outDir = Join-Path $root "dist\FlowLink-Windows"

Write-Host "==> cargo build -p flowlink-win ($Configuration)"
if ($Configuration -eq "release") {
    cargo build --release --manifest-path $manifest -p flowlink-win
    $exe = Join-Path $root "rust\target\release\flowlink-win.exe"
} else {
    cargo build --manifest-path $manifest -p flowlink-win
    $exe = Join-Path $root "rust\target\debug\flowlink-win.exe"
}

if (-not (Test-Path $exe)) {
    throw "build did not produce $exe"
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$dest = Join-Path $outDir "FlowLink.exe"
Copy-Item -Force $exe $dest

Write-Host ""
Write-Host "==> done: $dest"
Write-Host "    Run FlowLink.exe, pick Server or Client, and make sure the Mac build is 0.3.0 too."
Write-Host "    First run as a Server: allow the firewall prompt (or use the NETWORK card)."
