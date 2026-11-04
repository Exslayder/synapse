param(
    [string]$Version = "26.10.31",
    [string]$ZipSha256 = "2e04a9995a6f2ad1b28c0bb25c708ecb2695e43a1c9c7889c4b6ac2b48cf7a6a",
    [string]$ExeSha256 = "d5b558191cccb1e92a5772b68b8cab67717b26f634b0f15153215f65ae982a7e"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$binDir = Join-Path $root "src-tauri/binaries"
$target = Join-Path $binDir "xray-x86_64-pc-windows-msvc.exe"

if (Test-Path $target) {
    $existing = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
    if ($existing -eq $ExeSha256.ToLower()) {
        Write-Host "Xray $Version already present"
        exit 0
    }
}

New-Item -ItemType Directory -Force $binDir | Out-Null
$tmp = Join-Path $env:TEMP "synapse-xray-$Version"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp "xray.zip"
$url = "https://github.com/XTLS/Xray-core/releases/download/v$Version/Xray-windows-64.zip"

Write-Host "Downloading Xray $Version"
Invoke-WebRequest -Uri $url -OutFile $zip
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $ZipSha256.ToLower()) {
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    throw "Xray archive SHA-256 mismatch: $actual"
}

Expand-Archive $zip -DestinationPath $tmp -Force
$exe = Join-Path $tmp "xray.exe"
$exeHash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
if ($exeHash -ne $ExeSha256.ToLower()) {
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    throw "xray.exe SHA-256 mismatch: $exeHash"
}
Copy-Item $exe $target -Force
$license = Join-Path $root "src-tauri/resources/xray-LICENSE.txt"
if ((Test-Path (Join-Path $tmp "LICENSE")) -and (Test-Path (Split-Path $license -Parent))) {
    Copy-Item (Join-Path $tmp "LICENSE") $license -Force
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Xray $Version installed to $target"
