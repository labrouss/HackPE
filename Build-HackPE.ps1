<#
.SYNOPSIS
  Builds the HackPE bootable WinPE 11 ISO / USB stick (or a portable folder).

.DESCRIPTION
  1. Stages the toolkit (engine, GUI, embeddable Python, CA bundle)
  2. Pre-downloads OpenCore, kexts, SSDTs, AMD patches, tools  -> works offline later
  3. Creates WinPE (ADK copype), adds WMI/NetFX/PowerShell/Storage components,
     injects optional drivers and the toolkit, sets the auto-start
  4. Produces HackPE.iso and/or writes a bootable USB stick

  Requirements (host): Windows 10/11 x64, Administrator, internet,
  Windows ADK + "Windows PE add-on for the ADK" (not needed for -PortableOnly).

.EXAMPLE
  .\Build-HackPE.ps1                                  # ISO in C:\HackPE-Build\HackPE.iso
.EXAMPLE
  .\Build-HackPE.ps1 -UsbDrive E: -DriversPath D:\Drivers\NIC
.EXAMPLE
  .\Build-HackPE.ps1 -PortableOnly                    # run the toolkit on a normal Windows PC
#>
[CmdletBinding()]
param(
    [string]$WorkDir = 'C:\HackPE-Build',
    [string]$IsoPath = '',
    [string]$UsbDrive = '',
    [string]$DriversPath = '',
    [string]$PythonVersion = '3.12.10',
    [string]$GitHubToken = '',
    [string]$Language = 'en-us',
    [string]$Version = 'dev',
    [switch]$SkipFetch,
    [switch]$PortableOnly,
    [switch]$NoIso
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Src = $PSScriptRoot

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Run($exe, [string[]]$a) {
    Write-Host "    $exe $($a -join ' ')" -ForegroundColor DarkGray
    & $exe @a
    if ($LASTEXITCODE -ne 0) { throw "$exe failed with exit code $LASTEXITCODE" }
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this script from an elevated PowerShell (Run as Administrator).' }
if (-not $IsoPath) { $IsoPath = Join-Path $WorkDir 'HackPE.iso' }
if (-not $GitHubToken -and $env:GITHUB_TOKEN) { $GitHubToken = $env:GITHUB_TOKEN }   # CI
$Dl = Join-Path $WorkDir 'downloads'
$Stage = Join-Path $WorkDir 'stage\HackPE'
$Cache = Join-Path $WorkDir 'cache'
New-Item -ItemType Directory -Force -Path $Dl, $Stage, $Cache | Out-Null

# ----------------------------------------------------------------------------
Step 'Staging toolkit files'
foreach ($d in 'engine', 'gui', 'data') {
    Remove-Item (Join-Path $Stage $d) -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $Src $d) (Join-Path $Stage $d) -Recurse -Force
}
Copy-Item (Join-Path $Src 'Launch.cmd'), (Join-Path $Src 'README.md') $Stage -Force
New-Item -ItemType Directory -Force (Join-Path $Stage 'usbmap'), (Join-Path $Stage 'tools') | Out-Null
"HackPE $Version  built $(Get-Date -Format 'yyyy-MM-dd HH:mm') UTC$(if ($env:GITHUB_SHA) { '  commit ' + $env:GITHUB_SHA.Substring(0, 7) })" |
    Set-Content -Encoding ASCII (Join-Path $Stage 'VERSION.txt')

Step "Python $PythonVersion embeddable"
$pyZip = Join-Path $Dl "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $pyZip)) {
    Invoke-WebRequest -UseBasicParsing "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $pyZip
}
$pyDir = Join-Path $Stage 'python'
Remove-Item $pyDir -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive $pyZip $pyDir -Force
# The ._pth file puts embeddable Python in isolated path mode: the running script's folder is
# not importable, which breaks tools like SSDTTime (from Scripts import ...). Remove it.
Get-ChildItem $pyDir -Filter 'python*._pth' | Remove-Item -Force
$py = Join-Path $pyDir 'python.exe'
& $py -c "import ssl, zipfile, plistlib, json, urllib.request; print('python ok', ssl.OPENSSL_VERSION)"
if ($LASTEXITCODE -ne 0) { throw 'embedded python does not run' }

Step 'CA certificate bundle (WinPE has no Windows root store updates)'
$ca = Join-Path $Stage 'data\cacert.pem'
Invoke-WebRequest -UseBasicParsing 'https://curl.se/ca/cacert.pem' -OutFile $ca

# ----------------------------------------------------------------------------
if (-not $SkipFetch) {
    Step 'Downloading OpenCore, kexts, SSDTs and tools into the offline cache'
    if ($GitHubToken) { $env:GITHUB_TOKEN = $GitHubToken }
    & $py (Join-Path $Stage 'engine\hackpe.py') --cache $Cache fetch
    if ($LASTEXITCODE -ne 0) { throw 'fetch failed (OpenCorePkg is mandatory). GitHub API rate limit? Use -GitHubToken.' }
}
& $py (Join-Path $Stage 'engine\hackpe.py') --cache $Cache status

# ----------------------------------------------------------------------------
if ($PortableOnly) {
    Step 'Creating portable toolkit'
    Remove-Item (Join-Path $Stage 'cache') -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item $Cache (Join-Path $Stage 'cache') -Recurse -Force
    $starter = Join-Path (Split-Path $Stage) 'Start-HackPE.cmd'
    @'
@echo off
rem Runs the HackPE GUI on a normal Windows PC (elevation needed for disk operations)
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs" & exit /b)
set "HACKPE_CACHE=%~dp0HackPE\cache"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0HackPE\gui\HackPE-Assistant.ps1"
'@ | Set-Content -Encoding ASCII $starter
    Write-Host "`nPortable toolkit ready: $(Split-Path $Stage)   ->  run Start-HackPE.cmd" -ForegroundColor Green
    return
}

# ----------------------------------------------------------------------------
Step 'Locating Windows ADK + WinPE add-on'
$kits = (Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows Kits\Installed Roots' -ErrorAction SilentlyContinue).KitsRoot10
if (-not $kits) { $kits = "${env:ProgramFiles(x86)}\Windows Kits\10\" }
$Adk = Join-Path $kits 'Assessment and Deployment Kit'
$DandI = Join-Path $Adk 'Deployment Tools\DandISetEnv.bat'
$PeRoot = Join-Path $Adk 'Windows Preinstallation Environment\amd64'
$OCs = Join-Path $PeRoot 'WinPE_OCs'
if (-not (Test-Path $DandI)) { throw "Windows ADK not found ($DandI). Install 'Windows ADK' (Deployment Tools)." }
if (-not (Test-Path $OCs)) { throw "WinPE add-on not found ($OCs). Install 'Windows PE add-on for the ADK'." }

$Pe = Join-Path $WorkDir 'winpe'
$Mount = Join-Path $Pe 'mount'
if (Test-Path $Mount) {
    if (Get-WindowsImage -Mounted | Where-Object { $_.Path -eq $Mount }) {
        Write-Warning 'Discarding a previously mounted image'
        Run dism.exe @('/Unmount-Image', "/MountDir:$Mount", '/Discard')
    }
}
Remove-Item $Pe -Recurse -Force -ErrorAction SilentlyContinue

Step 'copype amd64'
cmd.exe /c "call `"$DandI`" >nul && copype amd64 `"$Pe`""
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$Pe\media\sources\boot.wim")) { throw 'copype failed' }

Step 'Mounting boot.wim'
Run dism.exe @('/Mount-Image', "/ImageFile:$Pe\media\sources\boot.wim", '/Index:1', "/MountDir:$Mount")
$committed = $false
try {
    Step 'Adding WinPE optional components'
    $components = 'WinPE-WMI', 'WinPE-NetFX', 'WinPE-Scripting', 'WinPE-PowerShell', 'WinPE-StorageWMI',
                  'WinPE-DismCmdlets', 'WinPE-SecureBootCmdlets', 'WinPE-EnhancedStorage', 'WinPE-FMAPI'
    foreach ($c in $components) {
        $cab = Join-Path $OCs "$c.cab"
        if (-not (Test-Path $cab)) { Write-Warning "$c not found - skipped"; continue }
        Run dism.exe @("/Image:$Mount", '/Add-Package', "/PackagePath:$cab")
        $lp = Join-Path $OCs "$Language\$($c)_$Language.cab"
        if (Test-Path $lp) { Run dism.exe @("/Image:$Mount", '/Add-Package', "/PackagePath:$lp") }
    }

    if ($DriversPath) {
        Step "Injecting drivers from $DriversPath"
        Run dism.exe @("/Image:$Mount", '/Add-Driver', "/Driver:$DriversPath", '/Recurse', '/ForceUnsigned')
    }

    Step 'Copying toolkit into the image (X:\HackPE)'
    Copy-Item $Stage (Join-Path $Mount 'HackPE') -Recurse -Force

    Step 'Configuring auto-start'
    @'
@echo off
wpeinit
powercfg /s 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c >nul 2>&1
call X:\HackPE\Launch.cmd
'@ | Set-Content -Encoding ASCII (Join-Path $Mount 'Windows\System32\startnet.cmd')
    Run dism.exe @("/Image:$Mount", '/Set-ScratchSpace:512')

    Step 'Committing image'
    Run dism.exe @('/Unmount-Image', "/MountDir:$Mount", '/Commit')
    $committed = $true
} finally {
    if (-not $committed) {
        Write-Warning 'Build failed - discarding mounted image'
        & dism.exe /Unmount-Image "/MountDir:$Mount" /Discard | Out-Null
    }
}

Step 'Optimizing boot.wim'
$wim = "$Pe\media\sources\boot.wim"
Run dism.exe @('/Export-Image', "/SourceImageFile:$wim", '/SourceIndex:1', "/DestinationImageFile:$wim.new", '/Compress:max')
Move-Item "$wim.new" $wim -Force

Step 'Placing offline cache on the boot media (\HackPE\cache)'
Copy-Item $Cache (Join-Path $Pe 'media\HackPE\cache') -Recurse -Force
@"
HackPE $Version boot media. \HackPE\cache holds OpenCore/kexts downloaded $(Get-Date -Format 'yyyy-MM-dd').
On a USB stick this folder is writable: 'Update downloads' inside HackPE refreshes it.
Put a UTBMap.kext in \HackPE\usbmap to have it included in builds.
"@ | Set-Content (Join-Path $Pe 'media\HackPE\README.txt')
New-Item -ItemType Directory -Force (Join-Path $Pe 'media\HackPE\usbmap') | Out-Null

if (-not $NoIso) {
    Step "Creating ISO $IsoPath"
    cmd.exe /c "call `"$DandI`" >nul && MakeWinPEMedia /ISO /F `"$Pe`" `"$IsoPath`""
    if ($LASTEXITCODE -ne 0) { throw 'MakeWinPEMedia /ISO failed' }
}
if ($UsbDrive) {
    $UsbDrive = $UsbDrive.TrimEnd('\').TrimEnd(':') + ':'
    $vol = Get-Volume -DriveLetter $UsbDrive[0]
    $disk = $vol | Get-Partition | Get-Disk
    if ($disk.BusType -ne 'USB') { throw "$UsbDrive is not a USB disk ($($disk.BusType))." }
    Write-Warning "ALL DATA on disk $($disk.Number) ($($disk.FriendlyName)) will be erased."
    if ((Read-Host 'Type YES to continue') -ne 'YES') { throw 'aborted' }
    Step "Writing bootable USB $UsbDrive"
    cmd.exe /c "call `"$DandI`" >nul && echo Y | MakeWinPEMedia /UFD /F `"$Pe`" $UsbDrive"
    if ($LASTEXITCODE -ne 0) { throw 'MakeWinPEMedia /UFD failed' }
}

Write-Host "`nDone." -ForegroundColor Green
if (-not $NoIso) { Write-Host "  ISO : $IsoPath  ($([math]::Round((Get-Item $IsoPath).Length / 1MB)) MB)  - write it with Rufus/Ventoy or boot it in a VM" }
if ($UsbDrive) { Write-Host "  USB : $UsbDrive" }
Write-Host '  Boot the target PC from it (UEFI, Secure Boot off) - HackPE starts automatically.'
