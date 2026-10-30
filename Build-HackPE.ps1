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
  .\Build-HackPE.ps1 -AcpicaDir .\acpica-win64   # 64-bit iasl/acpidump (build/build-acpica-win64.sh)
.EXAMPLE
  .\Build-HackPE.ps1 -HarvestHostDrivers           # build ON the target laptop: include its touchpad/LAN/Wi-Fi drivers
.EXAMPLE
  .\Build-HackPE.ps1 -WindowsIso D:\Win11_24H2.iso  # WinRE base -> Wi-Fi support inside HackPE
.EXAMPLE
  .\Build-HackPE.ps1 -PortableOnly                    # run the toolkit on a normal Windows PC
#>
[CmdletBinding()]
param(
    [string]$WorkDir = 'C:\HackPE-Build',
    [string]$IsoPath = '',
    [string]$UsbDrive = '',
    [string]$DriversPath = '',
    [string]$PythonVersion = 'latest',
    [string]$AcpicaVersion = '',
    [string]$GitHubToken = '',
    [string]$Language = 'en-us',
    [string]$Version = 'dev',
    [string]$AcpicaDir = '',
    [bool]$HarvestInboxDrivers = $true,
    [switch]$HarvestHostDrivers,
    [string]$WinReWim = '',
    [string]$WindowsIso = '',
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
if (Test-Path (Join-Path $Src 'tools\custom')) {           # your own tools -> Tools menu "Your tools"
    Copy-Item (Join-Path $Src 'tools\custom') (Join-Path $Stage 'tools\custom') -Recurse -Force
}
"HackPE $Version  built $(Get-Date -Format 'yyyy-MM-dd HH:mm') UTC$(if ($env:GITHUB_SHA) { '  commit ' + $env:GITHUB_SHA.Substring(0, 7) })" |
    Set-Content -Encoding ASCII (Join-Path $Stage 'VERSION.txt')

# Embeddable Python. Its ._pth file is KEPT: it isolates Python from the registry and PYTHONPATH of the
# machine it runs on - without it, a Python installed on the build runner (or on a user's PC running the
# portable toolkit) can leak its own .pyd modules in ("_ssl: incompatible internal ABI"). Scripts still
# import their own folder through a one-line site hook (SSDTTime: "from Scripts import ...").
$pyDir = Join-Path $Stage 'python'
$py = Join-Path $pyDir 'python.exe'
$ScriptDirHook = 'import sys, os; p = (os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] not in ("", "-c", "-m") else os.getcwd()); sys.path.insert(0, p) if p not in sys.path else None'

function Install-EmbeddedPython([string]$Ver) {
    $zip = Join-Path $Dl "python-$Ver-embed-amd64.zip"
    if (-not (Test-Path $zip)) {
        Invoke-WebRequest -UseBasicParsing "https://www.python.org/ftp/python/$Ver/python-$Ver-embed-amd64.zip" -OutFile $zip
    }
    Remove-Item $pyDir -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip $pyDir -Force
    $pth = Get-ChildItem $pyDir -Filter 'python*._pth' | Select-Object -First 1
    $stdlib = Get-ChildItem $pyDir -Filter 'python*.zip' | Select-Object -First 1
    if (-not $pth -or -not $stdlib) { Write-Warning "python $Ver embeddable has no ._pth/stdlib zip"; return $false }
    Set-Content -Encoding ASCII -Path $pth.FullName -Value @($stdlib.Name, '.', 'Lib\site-packages', 'import site')
    $sp = Join-Path $pyDir 'Lib\site-packages'
    New-Item -ItemType Directory -Force -Path $sp | Out-Null
    Set-Content -Encoding ASCII -Path (Join-Path $sp 'hackpe_scriptdir.pth') -Value $ScriptDirHook
    # smoke test: stdlib + SSL + the script-folder import, the way the tools use Python
    $t = Join-Path $WorkDir 'pyprobe'
    New-Item -ItemType Directory -Force -Path $t | Out-Null
    Set-Content -Encoding ASCII -Path (Join-Path $t 'hackpe_probe_mod.py') -Value 'OK = True'
    Set-Content -Encoding ASCII -Path (Join-Path $t 'probe.py') -Value @(
        'import ssl, zipfile, plistlib, json, urllib.request, ctypes, xml.parsers.expat, hackpe_probe_mod',
        'print("python ok", ssl.OPENSSL_VERSION)')
    & $py (Join-Path $t 'probe.py') | Out-Host
    return ($LASTEXITCODE -eq 0)
}

if ($PythonVersion -eq 'latest') {
    Step 'Resolving the latest stable Python with a Windows embeddable build'
    $idx = (Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/').Content
    $cands = [regex]::Matches($idx, 'href="(3\.\d+\.\d+)/"') | ForEach-Object { [version]$_.Groups[1].Value } |
             Sort-Object -Descending | Select-Object -First 25
    $ok = $null; $tried = 0
    foreach ($v in $cands) {
        try { Invoke-WebRequest -UseBasicParsing -Method Head "https://www.python.org/ftp/python/$v/python-$v-embed-amd64.zip" | Out-Null } catch { continue }
        $tried++
        Step "Python $v embeddable"
        if (Install-EmbeddedPython "$v") { $ok = "$v"; break }
        Write-Warning "Python $v embeddable failed the smoke test - trying the previous release"
        if ($tried -ge 4) { break }
    }
    if (-not $ok) { throw 'no working Python embeddable build found (tried the newest 4)' }
    $PythonVersion = $ok
} else {
    Step "Python $PythonVersion embeddable"
    if (-not (Install-EmbeddedPython $PythonVersion)) { throw "embedded python $PythonVersion does not run" }
}
Write-Host "Using Python $PythonVersion"

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
if ($AcpicaDir) {
    Step "Importing 64-bit ACPICA tools from $AcpicaDir"
    $tag = if (Test-Path (Join-Path $AcpicaDir 'VERSION.txt')) { (Get-Content (Join-Path $AcpicaDir 'VERSION.txt') -Raw).Trim() } else { 'local' }
    foreach ($t in 'iasl', 'acpidump') {
        $f = Join-Path $AcpicaDir "$t.exe"
        if (-not (Test-Path $f)) { throw "$f not found" }
        & $py (Join-Path $Stage 'engine\hackpe.py') --cache $Cache import --pkg "ACPICA64_$t" --file $f --tag $tag
        if ($LASTEXITCODE -ne 0) { throw "import of $t failed" }
    }
} else {
    Write-Warning 'No -AcpicaDir: SSDT generation inside WinPE needs 64-bit iasl.exe (see build/build-acpica-win64.sh). ACPI dumps still work (built-in fallback).'
}
& $py (Join-Path $Stage 'engine\hackpe.py') --cache $Cache status

Step 'Extracting bundled apps (Explorer++, Notepad++, OCAuxiliaryTools, Hackintosh EFI Builder)'
Remove-Item (Join-Path $Stage 'apps') -Recurse -Force -ErrorAction SilentlyContinue
$appScript = "import sys; sys.path.insert(0, r'$Stage\engine'); import hackpe, tools; c = hackpe.Cache([r'$Cache']); [print(k, tools.app_path(c, k)) for k in tools.APPS]"
& $py -c $appScript

# Portable Git (bash, git, curl, unzip) + jq -> lives on the boot media (not in RAM), used by
# bash-based tools such as Ferramentas TOPS. Extracted here because the archive is a 7z SFX.
$GitDir = Join-Path $WorkDir 'PortableGit'
Remove-Item $GitDir -Recurse -Force -ErrorAction SilentlyContinue
$pgDir = Join-Path $Cache 'pkg'
$pg = Get-ChildItem $pgDir -Filter 'PortableGit-*-64-bit.7z.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pg) {
    Step "Extracting $($pg.Name)"
    $p = Start-Process $pg.FullName -ArgumentList "-o`"$GitDir`"", '-y' -Wait -PassThru -WindowStyle Hidden
    if ($p.ExitCode -ne 0 -or -not (Test-Path "$GitDir\bin\bash.exe")) { Write-Warning 'Portable Git extraction failed - bash tools will not work'; $GitDir = $null }
    else {
        $jq = Get-ChildItem $pgDir -Filter 'jq-windows-amd64.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($jq) { Copy-Item $jq.FullName "$GitDir\usr\bin\jq.exe" -Force }
    }
} else { Write-Warning 'PortableGit not in cache - bash-based tools unavailable'; $GitDir = $null }

# Versions of everything bundled (used for the release changelog)
$verMap = [ordered]@{ HackPE = $Version; Python = $PythonVersion }
if ($AcpicaVersion) { $verMap.ACPICA = $AcpicaVersion }
elseif ($AcpicaDir -and (Test-Path (Join-Path $AcpicaDir 'VERSION.txt'))) { $verMap.ACPICA = (Get-Content (Join-Path $AcpicaDir 'VERSION.txt') -Raw).Trim() }
$pkgJson = & $py (Join-Path $Stage 'engine\hackpe.py') --cache $Cache status --json | Out-String
($pkgJson | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $verMap[$_.Name] = $_.Value }

# ----------------------------------------------------------------------------
if ($PortableOnly) {
    Step 'Creating portable toolkit'
    Remove-Item (Join-Path $Stage 'cache') -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item $Cache (Join-Path $Stage 'cache') -Recurse -Force
    if ($GitDir) { Copy-Item $GitDir (Join-Path $Stage 'apps\PortableGit') -Recurse -Force }
    $verMap | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $Stage 'versions.json')
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
# Driver sources
# ----------------------------------------------------------------------------
# Inbox (Microsoft-shipped) drivers of the build host that WinPE does not include:
# touchpads (HID over I2C/SPI + Intel/AMD Serial-IO I2C & GPIO controllers), Ethernet, USB Ethernet,
# phone USB tethering (RNDIS/NCM) and Wi-Fi adapters.
$InboxDriverPatterns = @(
    'hidi2c.inf_*', 'hidspi*.inf_*', 'hidinterrupt*.inf_*', 'mshidkmdf.inf_*', 'mshidumdf.inf_*',
    'ialpss*.inf_*', 'iagpio*.inf_*', '*i2c*.inf_*', '*gpio*.inf_*', '*serialio*.inf_*', 'amdi2c*.inf_*',
    'rt640x64.inf_*', 'rtcx21x64.inf_*', 'rtux64w10.inf_*', 'rtucx21x64.inf_*', 'netrtwlan*.inf_*', 'rtwlan*.inf_*',
    'netwtw*.inf_*', 'netwew*.inf_*', 'netwns*.inf_*', 'netwbw*.inf_*', 'e1d*.inf_*', 'e1i*.inf_*', 'e1r*.inf_*',
    'e2f*.inf_*', 'net1ic64.inf_*', 'netrndis.inf_*', 'rndiscmp.inf_*', 'wceisvista.inf_*', 'usbncm.inf_*',
    'netax88*.inf_*', 'ax88*.inf_*', 'mrvlpcie*.inf_*', 'netathr*.inf_*', 'athw*.inf_*', 'qcamain*.inf_*',
    'bcmwl*.inf_*', 'netbc*.inf_*', 'b57nd60a.inf_*', 'mtkwl*.inf_*', 'netmtk*.inf_*', 'netr28*.inf_*'
)

function Add-DriverFolder([string]$Folder, [string]$Label) {
    & dism.exe "/Image:$Mount" /Add-Driver "/Driver:$Folder" /Recurse /ForceUnsigned *> $null
    if ($LASTEXITCODE -eq 0) { $script:DriverOk++ } else { $script:DriverFail++; Write-Host "    skipped ($Label): $(Split-Path $Folder -Leaf)" -ForegroundColor DarkGray }
}

function Get-WinReFromIso([string]$Iso) {
    Step "Extracting WinRE from $Iso (Wi-Fi capable base)" | Out-Host
    $img = Mount-DiskImage -ImagePath (Resolve-Path $Iso).Path -PassThru
    $tmp = Join-Path $WorkDir 'install-mount'
    try {
        $letter = ($img | Get-Volume).DriveLetter
        $src = "${letter}:\sources\install.wim"
        if (-not (Test-Path $src)) {
            $esd = "${letter}:\sources\install.esd"
            if (-not (Test-Path $esd)) { throw 'install.wim/install.esd not found on the ISO' }
            $src = Join-Path $WorkDir 'install-1.wim'
            Remove-Item $src -Force -ErrorAction SilentlyContinue
            Run dism.exe @('/Export-Image', "/SourceImageFile:$esd", '/SourceIndex:1', "/DestinationImageFile:$src", '/Compress:max', '/CheckIntegrity') | Out-Host
        }
        New-Item -ItemType Directory -Force $tmp | Out-Null
        Run dism.exe @('/Mount-Image', "/ImageFile:$src", '/Index:1', "/MountDir:$tmp", '/ReadOnly') | Out-Host
        try {
            $dst = Join-Path $WorkDir 'winre'
            New-Item -ItemType Directory -Force $dst | Out-Null
            Copy-Item "$tmp\Windows\System32\Recovery\Winre.wim" "$dst\winre.wim" -Force
            foreach ($f in 'dmcmnutils.dll', 'mdmregistration.dll', 'mdmpostprocessevaluator.dll') {
                if (Test-Path "$tmp\Windows\System32\$f") { Copy-Item "$tmp\Windows\System32\$f" $dst -Force }
            }
            return "$dst\winre.wim"
        } finally { & dism.exe /Unmount-Image "/MountDir:$tmp" /Discard | Out-Null }
    } finally { Dismount-DiskImage -ImagePath (Resolve-Path $Iso).Path | Out-Null }
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
$dismExe = Join-Path $Adk 'Deployment Tools\amd64\DISM\dism.exe'
if (Test-Path $dismExe) { $verMap.ADK = (Get-Item $dismExe).VersionInfo.ProductVersion }

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

$UseWinRE = $false
if ($WindowsIso -and -not $WinReWim) { $WinReWim = Get-WinReFromIso $WindowsIso }
if ($WinReWim) {
    Step "Using WinRE as base image: $WinReWim"
    if (-not (Test-Path $WinReWim)) { throw "$WinReWim not found" }
    Remove-Item "$Pe\media\sources\boot.wim" -Force
    Run dism.exe @('/Export-Image', "/SourceImageFile:$WinReWim", '/SourceIndex:1', "/DestinationImageFile:$Pe\media\sources\boot.wim")
    $UseWinRE = $true
}

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
        if ($UseWinRE) {    # WinRE already contains some components; version must match the ADK
            & dism.exe "/Image:$Mount" /Add-Package "/PackagePath:$cab" *> $null
            if ($LASTEXITCODE -ne 0) { Write-Warning "$c could not be added to WinRE (already present or version mismatch)"; continue }
        } else { Run dism.exe @("/Image:$Mount", '/Add-Package', "/PackagePath:$cab") }
        $lp = Join-Path $OCs "$Language\$($c)_$Language.cab"
        if (Test-Path $lp) { & dism.exe "/Image:$Mount" /Add-Package "/PackagePath:$lp" *> $null }
    }

    if ($UseWinRE) {
        Step 'WinRE: enabling the HackPE shell and Wi-Fi'
        Remove-Item (Join-Path $Mount 'Windows\System32\winpeshl.ini') -Force -ErrorAction SilentlyContinue  # do not start Recovery UI
        $extra = Split-Path $WinReWim
        foreach ($f in 'dmcmnutils.dll', 'mdmregistration.dll', 'mdmpostprocessevaluator.dll') {
            $srcf = Join-Path $extra $f
            if (-not (Test-Path $srcf)) { $srcf = Join-Path $env:SystemRoot "System32\$f" }
            if (Test-Path $srcf) { Copy-Item $srcf (Join-Path $Mount 'Windows\System32') -Force }
        }
    }

    $script:DriverOk = 0; $script:DriverFail = 0
    if ($HarvestInboxDrivers) {
        Step 'Adding inbox touchpad / network / tethering / Wi-Fi drivers from this host'
        $repo = Join-Path $env:SystemRoot 'System32\DriverStore\FileRepository'
        $folders = foreach ($pat in $InboxDriverPatterns) { Get-ChildItem $repo -Directory -Filter $pat -ErrorAction SilentlyContinue }
        $folders = $folders | Sort-Object FullName -Unique
        Write-Host "    $($folders.Count) driver packages found"
        foreach ($f in $folders) { Add-DriverFolder $f.FullName 'inbox' }
    }
    if ($HarvestHostDrivers) {
        Step "Exporting this PC's own drivers (input, network, Wi-Fi, Serial IO)"
        $exp = Join-Path $WorkDir 'hostdrivers'
        Remove-Item $exp -Recurse -Force -ErrorAction SilentlyContinue
        $drv = Export-WindowsDriver -Online -Destination $exp |
               Where-Object { $_.ClassName -in 'Net', 'HIDClass', 'Mouse', 'Keyboard', 'System', 'USB', 'Bluetooth' }
        $drv | Select-Object -ExpandProperty OriginalFileName -Unique | ForEach-Object {
            $leaf = Split-Path (Split-Path $_) -Leaf
            $folder = Join-Path $exp $leaf
            if (Test-Path $folder) { Add-DriverFolder $folder 'host' }
        }
    }
    $packs = Join-Path $Src 'drivers\sources.txt'
    if (Test-Path $packs) {
        Step 'Downloading driver packs listed in drivers\sources.txt'
        $pd = Join-Path $Dl 'driverpacks'
        New-Item -ItemType Directory -Force $pd | Out-Null
        foreach ($u in (Get-Content $packs | Where-Object { $_ -match '^https?://' })) {
            try {
                $f = Join-Path $pd ([IO.Path]::GetFileName(([uri]$u).AbsolutePath))
                if (-not (Test-Path $f)) { Invoke-WebRequest -UseBasicParsing $u -OutFile $f }
                $x = "$f.x"; New-Item -ItemType Directory -Force $x | Out-Null
                if ($f -match '\.zip$') { Expand-Archive $f $x -Force } elseif ($f -match '\.cab$') { & expand.exe -F:* $f $x | Out-Null }
                Add-DriverFolder $x 'pack'
            } catch { Write-Warning "driver pack $u failed: $_" }
        }
    }
    if ($DriversPath) {
        Step "Injecting drivers from $DriversPath"
        Run dism.exe @("/Image:$Mount", '/Add-Driver', "/Driver:$DriversPath", '/Recurse', '/ForceUnsigned')
    }
    if ($script:DriverOk -or $script:DriverFail) { Write-Host "    drivers added: $($script:DriverOk), skipped: $($script:DriverFail)" }

    Step 'Copying toolkit into the image (X:\HackPE)'
    Copy-Item $Stage (Join-Path $Mount 'HackPE') -Recurse -Force
    # Explorer++ in WinPE needs a Desktop folder (folder tree + copy/paste)
    New-Item -ItemType Directory -Force (Join-Path $Mount 'Windows\System32\config\systemprofile\Desktop') | Out-Null

    Step 'Adding Windows DLLs the bundled apps need but WinPE lacks (from this host)'
    $scan = @((Join-Path $Mount 'HackPE\apps'), (Join-Path $Mount 'HackPE\python'))
    if ($GitDir) { $scan += (Join-Path $GitDir 'usr\bin'); $scan += (Join-Path $GitDir 'mingw64\bin') }
    & $py (Join-Path $Stage 'engine\hackpe.py') backfill-dlls --image $Mount @scan

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
if ($GitDir) {
    Step 'Placing Portable Git + jq on the boot media (\HackPE\apps\PortableGit)'
    New-Item -ItemType Directory -Force (Join-Path $Pe 'media\HackPE\apps') | Out-Null
    Copy-Item $GitDir (Join-Path $Pe 'media\HackPE\apps\PortableGit') -Recurse -Force
}
$winpeBuild = (& dism.exe /Get-ImageInfo "/ImageFile:$Pe\media\sources\boot.wim" /Index:1 | Select-String 'Version\s*:' | Select-Object -First 1)
if ($winpeBuild) { $verMap.WinPE = ($winpeBuild.ToString() -split ':')[1].Trim() }
$verMap | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $WorkDir 'versions.json')
Copy-Item (Join-Path $WorkDir 'versions.json') (Join-Path $Pe 'media\HackPE\versions.json') -Force
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
