<#
  HackPE Bar - a taskbar-style launcher docked at the bottom of the screen.
  Replaces the numbered text menu: every tool sits in a drop-down (ACPI, USB, EFI, macOS,
  Apps, Your tools, System, Windows, Power). Console tools open maximized in their own window.
  The screen work area is reduced by the bar height so maximized windows never cover it.

  Exit code 0 = user chose "Classic text menu" (Launch.cmd falls back to the text menu).
  Win32 calls go through engine\hackpe.py (ctypes) so no C# compiler is needed in WinPE.
#>
param([switch]$StartAssistant)

# ---- diagnostics: every start is logged; any error while building the bar is reported with its line
$LogDir  = if (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\MiniNT') { 'X:\HackPE-work' } else { Join-Path $env:TEMP 'HackPE-work' }
$null = New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction SilentlyContinue
$LogFile = Join-Path $LogDir 'bar.log'
function Write-BarLog([string]$m) {
    try { Add-Content -Path $script:LogFile -Value ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding ASCII } catch {}
}
try { Set-Content -Path $LogFile -Value ('[{0}] HackPE Bar starting - PowerShell {1}, {2}' -f (Get-Date -Format 'HH:mm:ss'), $PSVersionTable.PSVersion, [Environment]::OSVersion.VersionString) -Encoding ASCII } catch {}
trap {
    Write-BarLog ('ERROR: {0}' -f $_.Exception.Message)
    if ($_.InvocationInfo) { Write-BarLog ('  at line {0}: {1}' -f $_.InvocationInfo.ScriptLineNumber, $_.InvocationInfo.Line.Trim()) }
    try { $null = [Windows.Forms.MessageBox]::Show("The HackPE Bar could not start:`n`n$($_.Exception.Message)`n`nDetails: $script:LogFile`nThe text menu is shown instead.", 'HackPE') } catch {}
    exit 1
}
$ErrorActionPreference = 'Stop'        # while building the bar: every problem is fatal and logged

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
Write-BarLog 'WinForms loaded'

# ---------------------------------------------------------------- paths ----
$Root   = Split-Path -Parent $PSScriptRoot
$Py     = Join-Path $Root 'python\python.exe'
if (-not (Test-Path $Py)) { $Py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
$Engine = Join-Path $Root 'engine\hackpe.py'
$GuiDir = $PSScriptRoot
$IsPE   = Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\MiniNT'
$Work   = if ($IsPE) { 'X:\HackPE-work' } else { Join-Path $env:TEMP 'HackPE-work' }
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:HACKPE_CACHE) {
    $dirs = @()
    foreach ($d in [IO.DriveInfo]::GetDrives()) {
        $c = Join-Path $d.Name 'HackPE\cache'
        if (Test-Path (Join-Path $c 'manifest.json')) { $dirs += $c }
    }
    $env:HACKPE_CACHE = ((@($dirs) + @((Join-Path $Work 'cache'), (Join-Path $Root 'cache'))) | Select-Object -Unique) -join ';'
}

$BarHeight = 46
$script:ExitToText = $false

# -------------------------------------------------------------- helpers ----
function Quote([string]$s) { if ($s -match '[\s;&()]') { '"' + $s + '"' } else { $s } }

function Invoke-Engine([string[]]$EngineArgs) {
    try { return (& $Py $Engine @EngineArgs 2>$null | Out-String) } catch { return '' }
}

# Run an engine command in its own maximized console; keeps it open at the end.
function Start-EngineConsole([string[]]$EngineArgs, [string]$Title, [switch]$NoPause) {
    $f = Join-Path $Work ("bar-{0}.cmd" -f ([guid]::NewGuid().ToString('N').Substring(0, 8)))
    $line = (@((Quote $Py), (Quote $Engine)) + ($EngineArgs | ForEach-Object { Quote $_ })) -join ' '
    $body = "@echo off`r`ntitle HackPE - $Title`r`nset HACKPE_CACHE=$env:HACKPE_CACHE`r`nset PYTHONIOENCODING=utf-8`r`n$line`r`n"
    if (-not $NoPause) {
        $body += "echo.`r`nif errorlevel 1 (echo *** FAILED with exit code %errorlevel% ***) else (echo *** Finished ***)`r`n" +
                 "echo Press any key to close this window.`r`npause >nul`r`n"
    }
    $body += "del `"%~f0`" >nul 2>&1`r`n"
    [IO.File]::WriteAllText($f, $body, [Text.Encoding]::ASCII)
    Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"$f`"" -WindowStyle Maximized
    Set-Status "Started: $Title"
}

# A Tools-menu action (same keys as the classic menu) in its own maximized console
function Start-Tool([string]$Key, [string]$Title) { Start-EngineConsole @('tools', '--run', $Key) $Title -NoPause }

function Start-Gui([string]$Name) {
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-File', (Quote (Join-Path $GuiDir $Name)))
}

# GUI apps (Explorer++, Notepad++, OCAT) - the engine maximizes them
function Start-App([string]$Name, [string[]]$AppArgs) {
    $a = @($Engine, 'app', $Name) + @($AppArgs | Where-Object { $_ })
    Start-Process -FilePath $Py -ArgumentList ($a | ForEach-Object { Quote $_ }) -WindowStyle Hidden
    Set-Status "Started: $Name"
}

# This PC's results: <stick>\HackPE\output\<PC>\  (hardware, acpi, ssdt, usb, efi, logs)
function Open-Output {
    Start-Process -FilePath $Py -ArgumentList @((Quote $Engine), 'open-output') -WindowStyle Hidden
    Set-Status 'Opening the output folder'
}

function Start-Shell([string]$Exe, [string[]]$ShellArgs) {
    Start-Process -FilePath $Exe -ArgumentList $ShellArgs -WorkingDirectory $Work -WindowStyle Maximized
}

function Invoke-Power([string]$What) {
    $verb = if ($What -eq 'reboot') { 'restart' } else { 'shut down' }
    $r = [Windows.Forms.MessageBox]::Show("Really $verb this PC?", 'HackPE', 'YesNo', 'Question')
    if ($r -ne 'Yes') { return }
    if (Get-Command wpeutil.exe -ErrorAction SilentlyContinue) {
        & wpeutil.exe $What
    } elseif ($What -eq 'reboot') { & shutdown.exe /r /t 0 } else { & shutdown.exe /s /t 0 }
}

function Get-IpText {
    try {
        $ips = foreach ($n in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
            if ($n.OperationalStatus -ne 'Up' -or $n.NetworkInterfaceType -eq 'Loopback') { continue }
            foreach ($u in $n.GetIPProperties().UnicastAddresses) {
                if ($u.Address.AddressFamily -eq 'InterNetwork' -and -not $u.Address.ToString().StartsWith('169.254.')) {
                    $kind = if ($n.NetworkInterfaceType -eq 'Wireless80211') { 'Wi-Fi' } else { 'LAN' }
                    "$kind $($u.Address)"
                }
            }
        }
        if ($ips) { return 'Net: ' + (@($ips)[0]) }
    } catch {}
    return 'Net: offline'
}

# ---------------------------------------------------------------- form -----
# WinPE: whole screen (we reserve our strip). Full Windows: sit above Explorer's taskbar.
$Scr  = if ($IsPE) { [Windows.Forms.Screen]::PrimaryScreen.Bounds } else { [Windows.Forms.Screen]::PrimaryScreen.WorkingArea }
$Form = New-Object Windows.Forms.Form
$Form.Text = 'HackPE Bar'
$Form.FormBorderStyle = 'None'
$Form.StartPosition = 'Manual'
$Form.Bounds = New-Object Drawing.Rectangle($Scr.Left, ($Scr.Bottom - $BarHeight), $Scr.Width, $BarHeight)
$Form.TopMost = $true
$Form.ShowInTaskbar = $true
$Form.BackColor = [Drawing.Color]::FromArgb(32, 34, 40)
$Form.KeyPreview = $true

$Strip = New-Object Windows.Forms.ToolStrip
$Strip.Dock = 'Fill'
$Strip.GripStyle = 'Hidden'
$Strip.RenderMode = 'System'
$Strip.BackColor = $Form.BackColor
$Strip.ForeColor = [Drawing.Color]::White
$Strip.Font = New-Object Drawing.Font('Segoe UI', 11)
$Strip.Padding = New-Object Windows.Forms.Padding(6, 4, 6, 4)
$Strip.ImageScalingSize = New-Object Drawing.Size(20, 20)
$Strip.ShowItemToolTips = $true
$Form.Controls.Add($Strip)

$MenuBack = [Drawing.Color]::FromArgb(45, 48, 56)

# A top-level drop-down button. $Items: @( @('Text', { action }, 'tooltip', arg), 'HEADER', '-', ... )
# The action receives arg as its first parameter.
function New-BarMenu([string]$Text, [object[]]$Items, [switch]$Right) {
    $b = New-Object Windows.Forms.ToolStripDropDownButton
    $b.Text = $Text
    $b.ForeColor = [Drawing.Color]::White
    $b.ShowDropDownArrow = $false
    $b.Margin = New-Object Windows.Forms.Padding(2, 0, 2, 0)
    $b.Padding = New-Object Windows.Forms.Padding(8, 0, 8, 0)
    $b.DropDownDirection = if ($Right) { 'AboveLeft' } else { 'AboveRight' }
    $b.DropDown.BackColor = $MenuBack
    $b.DropDown.Font = New-Object Drawing.Font('Segoe UI', 11)
    if ($Right) { $b.Alignment = 'Right' }
    Add-MenuItems $b $Items
    [void]$Strip.Items.Add($b)
    return $b
}

function Add-MenuItems($Btn, [object[]]$Items) {
    foreach ($it in $Items) {
        if ($it -is [string] -and $it -eq '-') {
            [void]$Btn.DropDownItems.Add((New-Object Windows.Forms.ToolStripSeparator))
            continue
        }
        if ($it -is [string]) {       # section header
            $h = New-Object Windows.Forms.ToolStripLabel($it)
            $h.ForeColor = [Drawing.Color]::FromArgb(140, 180, 255)
            $h.Font = New-Object Drawing.Font('Segoe UI', 9, [Drawing.FontStyle]::Bold)
            [void]$Btn.DropDownItems.Add($h)
            continue
        }
        $mi = New-Object Windows.Forms.ToolStripMenuItem($it[0])
        $mi.ForeColor = [Drawing.Color]::White
        $mi.BackColor = $MenuBack
        if ($it.Count -gt 2 -and $it[2]) { $mi.ToolTipText = $it[2] }
        # action + optional argument travel in Tag (GetNewClosure would hide this script's functions)
        $mi.Tag = @{ Act = $it[1]; Arg = $(if ($it.Count -gt 3) { $it[3] } else { $null }) }
        $mi.Add_Click({
            param($src, $ev)
            try { & $src.Tag.Act $src.Tag.Arg } catch { Set-Status ("Error: " + $_.Exception.Message) }
        })
        [void]$Btn.DropDownItems.Add($mi)
    }
}

function Set-Status([string]$m) {
    if ($script:StatusLbl) { $script:StatusLbl.Text = $m; $script:StatusTimer.Stop(); $script:StatusTimer.Start() }
}

# ---------------------------------------------------------------- menus ----
$Start = New-BarMenu '  HackPE  ' @(
    'GUIDED',
    @('HackPE Assistant (recommended)', { Start-Gui 'HackPE-Assistant.ps1' }, 'Step-by-step: check PC, pick macOS, make the USB, install EFI'),
    @('Expert mode (all options)', { Start-Gui 'HackPE-Expert.ps1' }),
    @('Text-mode wizard', { Start-EngineConsole @('wizard') 'Wizard' }),
    '-',
    'INFO',
    @('Hardware report', { Start-Tool 'h' 'Hardware report' }),
    @('Output folder of this PC (all results)', { Open-Output }, 'Hardware report, ACPI dumps, SSDTs, USB map, EFIs and logs - one folder per PC on the stick'),
    @('Bundled versions', { Start-EngineConsole @('versions') 'Versions' }),
    @('Pre-flight check', { Start-EngineConsole @('preflight') 'Pre-flight' }),
    '-',
    @('Update OpenCore / kexts / tools (internet)', { Start-EngineConsole @('fetch') 'Update downloads' }),
    @('Classic text menu', { $script:ExitToText = $true; $Form.Close() }, 'Closes the bar and shows the numbered text menu')
)
$Start.Font = New-Object Drawing.Font('Segoe UI', 11, [Drawing.FontStyle]::Bold)
$Start.BackColor = [Drawing.Color]::FromArgb(0, 103, 192)
[void]$Strip.Items.Add((New-Object Windows.Forms.ToolStripSeparator))

$null = New-BarMenu 'ACPI' @(
    @('Dump ACPI tables of this PC  (acpidump)', { Start-Tool '1' 'ACPI dump' }),
    @('Auto-generate SSDTs for this PC  (dump + SSDTTime)', { Start-Tool '2' 'SSDT auto' }),
    @('SSDTTime - interactive, all options', { Start-Tool '3' 'SSDTTime' }),
    @('Show generated SSDTs / ACPI patches', { Start-Tool '4' 'SSDT results' })
)
$null = New-BarMenu 'USB' @(
    @('Prepare this HackPE stick for macOS (flashed image)', { Start-Tool 'u' 'Prepare stick' }, 'Builds OpenCore for this PC onto the stick and downloads macOS recovery - nothing is erased'),
    @('USBToolBox - map USB ports -> UTBMap.kext', { Start-Tool '5' 'USBToolBox' }),
    '-',
    @('Make a macOS USB installer (Assistant)', { Start-Gui 'HackPE-Assistant.ps1' }),
    @('Add HackPE to the OpenCore menu of a stick', { Start-Tool 'a' 'Add HackPE to OpenCore' })
)
$null = New-BarMenu 'EFI' @(
    'BUILD',
    @('Rebuild EFI with generated SSDTs + USB map', { Start-Tool '6' 'Rebuild EFI' }),
    @('OpCore-Simplify (alternative generator, online)', { Start-Tool 's' 'OpCore-Simplify' }),
    @('Hackintosh EFI Builder (179 device profiles)', { Start-App 'efibuilder' }, 'EFI generator by yusufklncc - also downloads recovery and writes the USB stick'),
    @('Hackintosh EFI Builder - console version', { Start-Tool 'j' 'Hackintosh EFI Builder' }, 'Text-mode build of the same tool - use it if the window version does not start in WinPE'),
    @('Hardware-Sniffer (report for OpCore-Simplify)', { Start-Tool 'k' 'Hardware-Sniffer' }),
    @('Ferramentas TOPS (tool collection, online)', { Start-Tool 'f' 'Ferramentas TOPS' }),
    '-',
    'EDIT / CHECK',
    @('OCAuxiliaryTools - config.plist editor', { Start-App 'ocat' }),
    @('Validate a config.plist  (ocvalidate)', { Start-Tool '7' 'ocvalidate' }),
    @('Generate SMBIOS serial  (macserial)', { Start-Tool '8' 'macserial' }),
    @('Open the EFI partition of a disk', { Start-Tool '9' 'Mount EFI' })
)
$null = New-BarMenu 'macOS' @(
    @('Prepare this HackPE stick for macOS (flashed image)', { Start-Tool 'u' 'Prepare stick' }),
    @('Download full OFFLINE installer (gibMacOS, Big Sur+)', { Start-Tool 'm' 'Offline installer' }),
    @('gibMacOS - interactive (all versions / catalogs)', { Start-Tool 'g' 'gibMacOS' }),
    '-',
    @('Make a USB installer (online recovery / offline)', { Start-Gui 'HackPE-Assistant.ps1' }),
    @('Install EFI to the internal disk (post-install)', { Start-Gui 'HackPE-Assistant.ps1' })
)
$null = New-BarMenu 'Apps' @(
    @('File explorer  (Explorer++)', { Start-App 'explorer' }),
    @('Output folder of this PC', { Open-Output }),
    @('Hackintosh EFI Builder', { Start-App 'efibuilder' }),
    @('Text editor  (Notepad++)', { Start-App 'notepad' }),
    @('OCAuxiliaryTools', { Start-App 'ocat' }),
    '-',
    @('Command prompt', { Start-Shell 'cmd.exe' @('/k', 'title HackPE shell') }),
    @('PowerShell', { Start-Shell 'powershell.exe' @('-NoExit', '-NoProfile') }),
    @('Git Bash', { Start-EngineConsole @('bash') 'Git Bash' -NoPause }),
    @('DiskPart', { Start-Shell 'diskpart.exe' @() }),
    '-',
    @('Run a program...', {
        $d = New-Object Windows.Forms.OpenFileDialog
        $d.Title = 'Run a program'
        $d.Filter = 'Programs and scripts|*.exe;*.py;*.cmd;*.bat;*.ps1;*.sh|All files|*.*'
        if ($d.ShowDialog() -eq 'OK') { Start-EngineConsole @('run', $d.FileName) ([IO.Path]::GetFileName($d.FileName)) }
    })
)
$Mine = New-BarMenu 'Your tools' @()
$Mine.ToolTipText = 'Programs in \HackPE\tools on the stick'
$Mine.DropDown.Add_Opening({
    $Mine.DropDownItems.Clear()
    $list = @()
    try { $j = (Invoke-Engine @('custom-tools', '--json')) | ConvertFrom-Json; $list = @($j | ForEach-Object { $_ }) } catch {}
    if ($list.Count -eq 0) {
        Add-MenuItems $Mine @(, @('(none - put programs in \HackPE\tools on the stick)', { Start-App 'explorer' }))
        return
    }
    $i = 0
    foreach ($p in $list) {
        $i++
        Add-MenuItems $Mine @(, @([IO.Path]::GetFileName($p), { param($k) Start-Tool $k "Tool $k" }, $p, "t$i"))
    }
})
$null = New-BarMenu 'System' @(
    @('Network status / IP configuration', { Start-Tool 'n' 'Network' }),
    @('Connect to Wi-Fi', { Start-Tool 'w' 'Wi-Fi' }),
    @('Load drivers from a folder (.inf)...', {
        $d = New-Object Windows.Forms.FolderBrowserDialog
        $d.Description = 'Folder with drivers (.inf) - touchpad, LAN, Wi-Fi... (searched recursively)'
        if ($d.ShowDialog() -eq 'OK') { Start-EngineConsole @('load-drivers', '--folder', $d.SelectedPath) 'Load drivers' }
    }),
    @('Load drivers from \HackPE\drivers on sticks', { Start-EngineConsole @('load-drivers', '--auto') 'Load drivers' }),
    '-',
    @('Hardware report', { Start-Tool 'h' 'Hardware report' }),
    @('Classic text menu', { $script:ExitToText = $true; $Form.Close() })
)
$Win = New-BarMenu 'Windows' @()
$Win.ToolTipText = 'Switch between open windows'
$Win.DropDown.Add_Opening({
    $Win.DropDownItems.Clear()
    $list = @()
    try { $j = (Invoke-Engine @('windows', '--json')) | ConvertFrom-Json; $list = @($j | ForEach-Object { $_ }) } catch {}
    if ($list.Count -eq 0) { Add-MenuItems $Win @(, @('(no open windows)', {})); return }
    foreach ($w in $list) {
        $t = [string]$w.title
        if ($t.Length -gt 70) { $t = $t.Substring(0, 67) + '...' }
        Add-MenuItems $Win @(, @($t, { param($hw) $null = Invoke-Engine @('focus', '--hwnd', $hw) }, [string]$w.title, [string]$w.hwnd))
    }
})

# ---- right side: power, clock, network, status
$null = New-BarMenu 'Power' @(
    @('Restart', { Invoke-Power 'reboot' }),
    @('Shut down', { Invoke-Power 'shutdown' }),
    '-',
    @('Close the bar (classic text menu)', { $script:ExitToText = $true; $Form.Close() })
) -Right
$Clock = New-Object Windows.Forms.ToolStripLabel
$Clock.Alignment = 'Right'; $Clock.ForeColor = [Drawing.Color]::White
$Clock.Margin = New-Object Windows.Forms.Padding(8, 0, 8, 0)
[void]$Strip.Items.Add($Clock)
$Net = New-Object Windows.Forms.ToolStripLabel
$Net.Alignment = 'Right'; $Net.ForeColor = [Drawing.Color]::FromArgb(190, 200, 215)
$Net.ToolTipText = 'Click: network status'
$Net.IsLink = $false
$Net.Add_Click({ Start-Tool 'n' 'Network' })
[void]$Strip.Items.Add($Net)
$script:StatusLbl = New-Object Windows.Forms.ToolStripLabel
$StatusLbl.Alignment = 'Right'; $StatusLbl.ForeColor = [Drawing.Color]::FromArgb(150, 220, 150)
$StatusLbl.Font = New-Object Drawing.Font('Segoe UI', 9)
[void]$Strip.Items.Add($StatusLbl)

$script:StatusTimer = New-Object Windows.Forms.Timer
$StatusTimer.Interval = 6000
$StatusTimer.Add_Tick({ $script:StatusLbl.Text = ''; $script:StatusTimer.Stop() })

$Tick = New-Object Windows.Forms.Timer
$Tick.Interval = 1000
$script:TickN = 0
$Tick.Add_Tick({
    $Clock.Text = (Get-Date -Format 'HH:mm')
    if (($script:TickN++ % 5) -eq 0) { $Net.Text = Get-IpText }
})

# Keyboard: F1..F9 open the menus (the bar has focus after a click); Esc closes a menu
$Form.Add_KeyDown({
    param($s, $e)
    if ($e.KeyCode -ge [Windows.Forms.Keys]::F1 -and $e.KeyCode -le [Windows.Forms.Keys]::F9) {
        $n = [int]$e.KeyCode - [int][Windows.Forms.Keys]::F1
        $menus = @($Strip.Items | Where-Object { $_ -is [Windows.Forms.ToolStripDropDownButton] -and $_.Alignment -eq 'Left' })
        if ($n -lt $menus.Count) { $menus[$n].ShowDropDown(); $e.Handled = $true }
    }
})

$Form.Add_Shown({
    try {
        Write-BarLog ('Bar shown at {0}' -f $Form.Bounds)
        # Reserve the strip so maximized windows stop above the bar, then hide our console
        $null = Invoke-Engine @('workarea', '--bottom', [string]$BarHeight)
        $null = Invoke-Engine @('console', '--hide')
        $Clock.Text = (Get-Date -Format 'HH:mm'); $Net.Text = Get-IpText
        $Tick.Start()
        if ($StartAssistant) { Start-Gui 'HackPE-Assistant.ps1' }
        $Form.Activate()
    } catch { Write-BarLog ('Shown handler: ' + $_.Exception.Message) }
})
$Form.Add_FormClosing({
    param($s, $e)
    if (-not $script:ExitToText) {
        # Alt+F4 etc.: the bar is the shell of WinPE - confirm
        $r = [Windows.Forms.MessageBox]::Show("Close the HackPE Bar and go to the classic text menu?", 'HackPE', 'YesNo', 'Question')
        if ($r -ne 'Yes') { $e.Cancel = $true; return }
    }
    $Tick.Stop()
    $null = Invoke-Engine @('workarea', '--bottom', '0')
    $null = Invoke-Engine @('console', '--show')
})

Write-BarLog ('Python: {0}  Engine: {1}  Screen: {2}' -f $Py, $Engine, $Scr)
if (-not $Py -or -not (Test-Path $Py)) { throw "python.exe not found ($Py)" }
$ErrorActionPreference = 'Continue'    # running: a failing menu action must not close the bar
[void]$Form.ShowDialog()
Write-BarLog ('Bar closed (classic text menu: {0})' -f $script:ExitToText)
exit 0
