<#
  HackPE - graphical front-end (Windows Forms, PowerShell 5.1).
  Runs inside WinPE (WinPE-NetFX + WinPE-PowerShell) and on normal Windows 10/11.
  All real work is done by engine\hackpe.py; long jobs open in a console window
  so progress is always visible.
#>
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------- paths ----
$Root   = Split-Path -Parent $PSScriptRoot
$Py     = Join-Path $Root 'python\python.exe'
if (-not (Test-Path $Py)) { $Py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
$Engine = Join-Path $Root 'engine\hackpe.py'
$IsPE   = Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\MiniNT'
$Work   = if ($IsPE) { 'X:\HackPE-work' } else { Join-Path $env:TEMP 'HackPE-work' }
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$HwJson = Join-Path $Work 'hw.json'
$env:PYTHONIOENCODING = 'utf-8'

if (-not $env:HACKPE_CACHE) {
    $dirs = @()
    foreach ($d in [IO.DriveInfo]::GetDrives()) {
        $c = Join-Path $d.Name 'HackPE\cache'
        if (Test-Path (Join-Path $c 'manifest.json')) { $dirs += $c }
    }
    $dirs = @(Join-Path $Work 'cache') + $dirs + @(Join-Path $Root 'cache')
    $env:HACKPE_CACHE = ($dirs | Select-Object -Unique) -join ';'
}

$Targets = @(
    @('tahoe', 'macOS 26 Tahoe'), @('sequoia', 'macOS 15 Sequoia'), @('sonoma', 'macOS 14 Sonoma'),
    @('ventura', 'macOS 13 Ventura'), @('monterey', 'macOS 12 Monterey'), @('bigsur', 'macOS 11 Big Sur'),
    @('catalina', 'macOS 10.15 Catalina'), @('mojave', 'macOS 10.14 Mojave'), @('highsierra', 'macOS 10.13 High Sierra'))

# -------------------------------------------------------------- helpers ----
function Write-Log([string]$m) {
    $script:LogBox.AppendText(("[{0}] {1}`r`n" -f (Get-Date -Format 'HH:mm:ss'), $m))
}

function Invoke-Engine([string[]]$EngineArgs) {
    $script:Form.Cursor = 'WaitCursor'
    try {
        $out = & $Py $Engine @EngineArgs 2>&1 | Out-String
        return ($out -replace "`r?`n", "`r`n")
    } finally { $script:Form.Cursor = 'Default' }
}

function Quote([string]$s) { if ($s -match '[\s;&]') { '"' + $s + '"' } else { $s } }

function Start-EngineConsole([string[]]$EngineArgs, [string]$Title) {
    $f = Join-Path $Work ("run-{0}.cmd" -f (Get-Date -Format 'HHmmss'))
    $line = (@((Quote $Py), (Quote $Engine)) + ($EngineArgs | ForEach-Object { Quote $_ })) -join ' '
    $body = "@echo off`r`ntitle HackPE - $Title`r`nset HACKPE_CACHE=$env:HACKPE_CACHE`r`nset PYTHONIOENCODING=utf-8`r`n" +
            "$line`r`necho.`r`nif errorlevel 1 (echo *** FAILED with exit code %errorlevel% ***) else (echo *** Finished successfully ***)`r`n" +
            "echo Press any key to close this window.`r`npause >nul`r`n"
    [IO.File]::WriteAllText($f, $body, [Text.Encoding]::ASCII)
    Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"$f`""
    Write-Log "Started: $Title"
}

function Get-Target { $Targets[$script:TargetBox.SelectedIndex][0] }

function Get-PlanArgs {
    $a = @('--target', (Get-Target))
    if (Test-Path $HwJson) { $a += @('--hw', $HwJson) }
    switch ($script:DisplayBox.SelectedIndex) { 1 { $a += @('--display', 'igpu') } 2 { $a += @('--display', 'dgpu') } }
    switch ($script:FormBox.SelectedIndex) { 1 { $a += '--desktop' } 2 { $a += '--laptop' } }
    if ($script:SmbiosBox.Text.Trim()) { $a += @('--smbios', $script:SmbiosBox.Text.Trim()) }
    if ($script:XhciBox.Checked) { $a += '--xhci-port-limit' }
    return $a
}

function Get-EfiOut { Join-Path $Work ("EFI-" + (Get-Target)) }

function Confirm-Danger([string]$what, [string]$token) {
    $f = New-Object Windows.Forms.Form
    $f.Text = 'Confirm'; $f.Size = '460,190'; $f.StartPosition = 'CenterParent'; $f.FormBorderStyle = 'FixedDialog'
    $l = New-Object Windows.Forms.Label; $l.Text = "$what`r`n`r`nType $token to confirm:"; $l.Location = '12,10'; $l.Size = '420,60'
    $t = New-Object Windows.Forms.TextBox; $t.Location = '12,75'; $t.Width = 420
    $ok = New-Object Windows.Forms.Button; $ok.Text = 'Erase'; $ok.Location = '262,110'; $ok.DialogResult = 'OK'
    $ca = New-Object Windows.Forms.Button; $ca.Text = 'Cancel'; $ca.Location = '347,110'; $ca.DialogResult = 'Cancel'
    $f.Controls.AddRange(@($l, $t, $ok, $ca)); $f.AcceptButton = $ok; $f.CancelButton = $ca
    ($f.ShowDialog($script:Form) -eq 'OK') -and ($t.Text.Trim() -eq $token)
}

function New-Btn($text, $x, $y, $w = 170) {
    $b = New-Object Windows.Forms.Button; $b.Text = $text; $b.Location = "$x,$y"; $b.Size = "$w,32"; $b
}

function New-Out($x, $y, $w, $h) {
    $t = New-Object Windows.Forms.TextBox
    $t.Multiline = $true; $t.ScrollBars = 'Both'; $t.WordWrap = $false; $t.ReadOnly = $true
    $t.Font = New-Object Drawing.Font('Consolas', 9); $t.Location = "$x,$y"; $t.Size = "$w,$h"
    $t.BackColor = [Drawing.Color]::FromArgb(24, 24, 28); $t.ForeColor = [Drawing.Color]::FromArgb(220, 220, 220)
    $t
}

function Fill-Disks($lv, [bool]$usbOnly) {
    $lv.Items.Clear()
    $json = & $Py $Engine disks --json 2>$null | Out-String
    try { $disks = $json | ConvertFrom-Json } catch { Write-Log "disk list failed: $json"; return }
    foreach ($d in @($disks)) {
        if ($usbOnly -and $d.BusType -ne 'USB') { continue }
        $it = New-Object Windows.Forms.ListViewItem([string]$d.Number)
        [void]$it.SubItems.Add([string]$d.FriendlyName)
        [void]$it.SubItems.Add([string]$d.BusType)
        [void]$it.SubItems.Add(('{0:N1} GB' -f $d.SizeGB))
        [void]$it.SubItems.Add($(if ($d.IsBoot -or $d.IsSystem) { 'SYSTEM' } elseif ($d.IsHackPE) { 'HackPE' } else { '' }))
        if ($d.IsBoot -or $d.IsSystem -or $d.IsHackPE) { $it.ForeColor = 'Gray' }
        [void]$lv.Items.Add($it)
    }
}

function New-DiskList($x, $y, $w, $h) {
    $lv = New-Object Windows.Forms.ListView
    $lv.View = 'Details'; $lv.FullRowSelect = $true; $lv.MultiSelect = $false; $lv.HideSelection = $false
    $lv.Location = "$x,$y"; $lv.Size = "$w,$h"
    foreach ($c in @(@('Disk', 50), @('Model', 330), @('Bus', 80), @('Size', 90), @('', 70))) { [void]$lv.Columns.Add($c[0], $c[1]) }
    $lv
}

# ------------------------------------------------------------------ form ----
$Form = New-Object Windows.Forms.Form
$Form.Text = 'HackPE - Expert mode (OpenCore, macOS up to 26 Tahoe)'
$Form.Size = '1080,780'; $Form.StartPosition = 'CenterScreen'; $Form.MinimumSize = '1000,700'
$Form.Font = New-Object Drawing.Font('Segoe UI', 9)

$Header = New-Object Windows.Forms.Label
$Header.Location = '12,8'; $Header.Size = '1040,22'; $Header.Font = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Bold)
$Header.Text = 'Detect  ->  Plan  ->  Build EFI  ->  USB installer  ->  boot it  ->  install EFI to disk'
$Form.Controls.Add($Header)

# shared target/options strip
$lt = New-Object Windows.Forms.Label; $lt.Text = 'Target:'; $lt.Location = '12,40'; $lt.AutoSize = $true
$TargetBox = New-Object Windows.Forms.ComboBox; $TargetBox.DropDownStyle = 'DropDownList'; $TargetBox.Location = '62,36'; $TargetBox.Width = 190
$Targets | ForEach-Object { [void]$TargetBox.Items.Add($_[1]) }; $TargetBox.SelectedIndex = 0
$ld = New-Object Windows.Forms.Label; $ld.Text = 'Display:'; $ld.Location = '268,40'; $ld.AutoSize = $true
$DisplayBox = New-Object Windows.Forms.ComboBox; $DisplayBox.DropDownStyle = 'DropDownList'; $DisplayBox.Location = '322,36'; $DisplayBox.Width = 120
'Auto', 'iGPU', 'dGPU' | ForEach-Object { [void]$DisplayBox.Items.Add($_) }; $DisplayBox.SelectedIndex = 0
$lf = New-Object Windows.Forms.Label; $lf.Text = 'Type:'; $lf.Location = '456,40'; $lf.AutoSize = $true
$FormBox = New-Object Windows.Forms.ComboBox; $FormBox.DropDownStyle = 'DropDownList'; $FormBox.Location = '494,36'; $FormBox.Width = 100
'Auto', 'Desktop', 'Laptop' | ForEach-Object { [void]$FormBox.Items.Add($_) }; $FormBox.SelectedIndex = 0
$ls = New-Object Windows.Forms.Label; $ls.Text = 'SMBIOS override:'; $ls.Location = '608,40'; $ls.AutoSize = $true
$SmbiosBox = New-Object Windows.Forms.TextBox; $SmbiosBox.Location = '712,36'; $SmbiosBox.Width = 110
$XhciBox = New-Object Windows.Forms.CheckBox; $XhciBox.Text = 'XhciPortLimit (temp)'; $XhciBox.Location = '836,38'; $XhciBox.AutoSize = $true
$Form.Controls.AddRange(@($lt, $TargetBox, $ld, $DisplayBox, $lf, $FormBox, $ls, $SmbiosBox, $XhciBox))

$Tabs = New-Object Windows.Forms.TabControl
$Tabs.Location = '12,68'; $Tabs.Size = '1040,520'; $Tabs.Anchor = 'Top,Left,Right,Bottom'
$Form.Controls.Add($Tabs)

$LogBox = New-Out 12 594 1040 130
$LogBox.Anchor = 'Left,Right,Bottom'
$Form.Controls.Add($LogBox)

# ---- Tab 1: hardware --------------------------------------------------------
$t1 = New-Object Windows.Forms.TabPage; $t1.Text = '1. Hardware'
$Hout = New-Out 12 52 1004 430
$b = New-Btn 'Detect hardware' 12 10
$b.Add_Click({
    $Hout.Text = 'Detecting...'
    $Hout.Text = Invoke-Engine @('detect', '--out', $HwJson, '--summary')
    Write-Log 'Hardware detected'
})
$b2 = New-Btn 'Load hardware JSON...' 190 10
$b2.Add_Click({
    $d = New-Object Windows.Forms.OpenFileDialog; $d.Filter = 'JSON|*.json'
    if ($d.ShowDialog() -eq 'OK') { Copy-Item $d.FileName $HwJson -Force; $Hout.Text = "Loaded $($d.FileName)`r`n(use this to plan for another PC)"; Write-Log 'Hardware JSON loaded' }
})
$b3 = New-Btn 'Save hardware JSON...' 368 10
$b3.Add_Click({
    if (-not (Test-Path $HwJson)) { Write-Log 'Detect first'; return }
    $d = New-Object Windows.Forms.SaveFileDialog; $d.Filter = 'JSON|*.json'; $d.FileName = 'hardware.json'
    if ($d.ShowDialog() -eq 'OK') { Copy-Item $HwJson $d.FileName -Force; Write-Log "Saved $($d.FileName)" }
})
$t1.Controls.AddRange(@($b, $b2, $b3, $Hout))

# ---- Tab 2: plan ----------------------------------------------------------------
$t2 = New-Object Windows.Forms.TabPage; $t2.Text = '2. Compatibility & plan'
$Pout = New-Out 12 52 1004 430
$b = New-Btn 'Analyze for selected target' 12 10 220
$b.Add_Click({
    if (-not (Test-Path $HwJson)) { $null = Invoke-Engine @('detect', '--out', $HwJson) }
    $Pout.Text = Invoke-Engine (@('plan') + (Get-PlanArgs))
    Write-Log ("Plan created for " + $TargetBox.Text)
})
$t2.Controls.AddRange(@($b, $Pout))

# ---- Tab 3: build EFI -----------------------------------------------------------
$t3 = New-Object Windows.Forms.TabPage; $t3.Text = '3. Build EFI'
$info3 = New-Object Windows.Forms.Label; $info3.Location = '12,52'; $info3.Size = '1000,120'
$info3.Text = "Builds a complete OpenCore EFI (OpenCore, kexts, SSDTs, config.plist with generated SMBIOS) for this PC.`r`n" +
              "Output: $Work\EFI-<target>\EFI   (BUILD-NOTES.txt explains BIOS settings and next steps)`r`n`r`n" +
              "Custom SSDTs: run Tools > SSDTTime first - its Results folder is picked up automatically.`r`n" +
              "USB map: run Tools > USBToolBox, export UTBMap.kext into $Root\usbmap - picked up automatically.`r`n" +
              "Builds with blockers (unsupported GPU/CPU) need 'Force'."
$ForceBox = New-Object Windows.Forms.CheckBox; $ForceBox.Text = 'Force (ignore blockers)'; $ForceBox.Location = '12,180'; $ForceBox.AutoSize = $true
$b = New-Btn 'Build EFI' 12 10
$b.Add_Click({
    $a = @('build-efi') + (Get-PlanArgs) + @('--out', (Get-EfiOut))
    if ($ForceBox.Checked) { $a += '--force' }
    Start-EngineConsole $a 'Build EFI'
})
$b2 = New-Btn 'Edit config.plist' 190 10
$b2.Add_Click({ $c = Join-Path (Get-EfiOut) 'EFI\OC\config.plist'; if (Test-Path $c) { Start-Process notepad.exe $c } else { Write-Log 'Build the EFI first' } })
$b3 = New-Btn 'Validate (ocvalidate)' 368 10
$b3.Add_Click({ $c = Join-Path (Get-EfiOut) 'EFI\OC\config.plist'; if (Test-Path $c) { Write-Log (Invoke-Engine @('validate', $c)) } else { Write-Log 'Build the EFI first' } })
$b4 = New-Btn 'Build notes' 546 10
$b4.Add_Click({ $c = Join-Path (Get-EfiOut) 'BUILD-NOTES.txt'; if (Test-Path $c) { Start-Process notepad.exe $c } else { Write-Log 'Build the EFI first' } })
$b5 = New-Btn 'Copy EFI to folder...' 724 10
$b5.Add_Click({
    $src = Join-Path (Get-EfiOut) 'EFI'
    if (-not (Test-Path $src)) { Write-Log 'Build the EFI first'; return }
    $d = New-Object Windows.Forms.FolderBrowserDialog
    if ($d.ShowDialog() -eq 'OK') { Copy-Item $src (Join-Path $d.SelectedPath 'EFI') -Recurse -Force; Write-Log "EFI copied to $($d.SelectedPath)" }
})
$t3.Controls.AddRange(@($b, $b2, $b3, $b4, $b5, $info3, $ForceBox))

# ---- Tab 4: USB installer -------------------------------------------------------
$t4 = New-Object Windows.Forms.TabPage; $t4.Text = '4. USB installer'
$UsbList = New-DiskList 12 52 640 300
$AnyBus = New-Object Windows.Forms.CheckBox; $AnyBus.Text = 'Show non-USB disks (dangerous)'; $AnyBus.Location = '670,56'; $AnyBus.AutoSize = $true
$NoRec = New-Object Windows.Forms.CheckBox; $NoRec.Text = 'Skip recovery download (EFI only)'; $NoRec.Location = '670,82'; $NoRec.AutoSize = $true
$Force4 = New-Object Windows.Forms.CheckBox; $Force4.Text = 'Force (ignore blockers)'; $Force4.Location = '670,108'; $Force4.AutoSize = $true
$info4 = New-Object Windows.Forms.Label; $info4.Location = '670,140'; $info4.Size = '340,210'
$info4.Text = "Creates the installer on the selected stick (>= 4 GB, it will be ERASED):`r`n`r`n" +
              " 1. GPT + FAT32 partition 'OPENCORE'`r`n 2. EFI built for this PC`r`n" +
              " 3. macOS recovery downloaded from Apple`r`n     (com.apple.recovery.boot, ~1-2 GB)`r`n`r`n" +
              "Needs an internet connection (Ethernet or USB tethering). Then reboot, choose the stick in the boot menu " +
              "and pick 'macOS Base System' in OpenCore."
$b = New-Btn 'Refresh disks' 12 10
$b.Add_Click({ Fill-Disks $UsbList (-not $AnyBus.Checked) })
$AnyBus.Add_CheckedChanged({ Fill-Disks $UsbList (-not $AnyBus.Checked) })
$b2 = New-Btn 'Create USB installer' 190 10 200
$b2.BackColor = [Drawing.Color]::FromArgb(255, 228, 225)
$b2.Add_Click({
    if ($UsbList.SelectedItems.Count -ne 1) { Write-Log 'Select a disk first'; return }
    $n = $UsbList.SelectedItems[0].Text; $m = $UsbList.SelectedItems[0].SubItems[1].Text
    if ($UsbList.SelectedItems[0].SubItems[4].Text) { Write-Log 'Refusing: that is the system disk or the HackPE stick - use a second USB stick'; return }
    if (-not (Confirm-Danger "ALL DATA on disk $n ($m) will be erased and replaced by a $($TargetBox.Text) installer." $n)) { Write-Log 'Cancelled'; return }
    $a = @('make-usb') + (Get-PlanArgs) + @('--disk', $n, '--yes', '--out', (Get-EfiOut))
    if ($AnyBus.Checked) { $a += '--any-bus' }
    if ($NoRec.Checked) { $a += '--no-recovery' }
    if ($Force4.Checked) { $a += '--force' }
    Start-EngineConsole $a 'Create USB installer'
})
$b3 = New-Btn 'Download recovery to folder...' 398 10 220
$b3.Add_Click({
    $d = New-Object Windows.Forms.FolderBrowserDialog
    if ($d.ShowDialog() -eq 'OK') { Start-EngineConsole @('recovery', '--target', (Get-Target), '--out', (Join-Path $d.SelectedPath 'com.apple.recovery.boot')) 'Download recovery' }
})
$t4.Controls.AddRange(@($b, $b2, $b3, $UsbList, $AnyBus, $NoRec, $Force4, $info4))

# ---- Tab 5: post-install & tools ------------------------------------------------
$t5 = New-Object Windows.Forms.TabPage; $t5.Text = '5. Post-install & tools'
$EspList = New-DiskList 12 52 640 200
$b = New-Btn 'Refresh disks' 12 10
$b.Add_Click({ Fill-Disks $EspList $false })
$b2 = New-Btn 'Install EFI to ESP of disk' 190 10 200
$b2.Add_Click({
    if ($EspList.SelectedItems.Count -ne 1) { Write-Log 'Select the macOS disk first'; return }
    $n = $EspList.SelectedItems[0].Text
    $src = Get-EfiOut
    if (-not (Test-Path (Join-Path $src 'EFI\OC\OpenCore.efi'))) {
        $d = New-Object Windows.Forms.FolderBrowserDialog; $d.Description = 'Folder that contains EFI\OC'
        if ($d.ShowDialog() -ne 'OK') { return }; $src = $d.SelectedPath
    }
    if (-not (Confirm-Danger "OpenCore will be installed to the EFI partition of disk $n (EFI\OC and EFI\BOOT replaced, old ones renamed .bak)." $n)) { return }
    Start-EngineConsole @('install-esp', '--efi', $src, '--disk', $n, '--yes') 'Install EFI to disk'
})
$t5.Controls.AddRange(@($b, $b2, $EspList))

$tools = @(
    @('Command prompt', { Start-Process cmd.exe -ArgumentList "/k cd /d `"$Work`"" }),
    @('Notepad', { Start-Process notepad.exe }),
    @('Network status', { Start-Process cmd.exe -ArgumentList '/k ipconfig /all & ping -n 2 www.apple.com' }),
    @('Load driver (.inf)...', {
        $d = New-Object Windows.Forms.OpenFileDialog; $d.Filter = 'Driver|*.inf'
        if ($d.ShowDialog() -eq 'OK') { Write-Log ((& drvload.exe $d.FileName 2>&1) | Out-String); if ($IsPE) { & wpeutil InitializeNetwork | Out-Null } }
    }),
    @('Update downloads (online)', { Start-EngineConsole @('fetch') 'Update OpenCore & kexts' }),
    @('Download cache status', { Write-Log (Invoke-Engine @('status')) }),
    @('SSDTTime (custom SSDTs)', {
        $dst = Join-Path $Root 'tools\SSDTTime'
        if (-not (Test-Path (Join-Path $dst 'SSDTTime.py'))) {
            $zip = $null
            foreach ($c in ($env:HACKPE_CACHE -split ';')) { $z = Get-ChildItem (Join-Path $c 'pkg') -Filter 'SSDTTime*.zip' -ErrorAction SilentlyContinue | Select-Object -First 1; if ($z) { $zip = $z.FullName; break } }
            if (-not $zip) { Write-Log 'SSDTTime not cached - run Update downloads'; return }
            $tmp = Join-Path $Work 'ssdttime-x'; Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp)
            New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
            Move-Item (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName $dst
        }
        $f = Join-Path $Work 'ssdttime.cmd'
        [IO.File]::WriteAllText($f, "@echo off`r`ncd /d `"$dst`"`r`n`"$Py`" SSDTTime.py`r`npause`r`n", [Text.Encoding]::ASCII)
        Start-Process cmd.exe -ArgumentList "/c `"$f`""
        Write-Log 'SSDTTime: dump DSDT, generate FakeEC/PLUG/AWAC/PMC/USBX..., then rebuild the EFI'
    }),
    @('USBToolBox (USB map)', {
        $exe = $null
        foreach ($c in ($env:HACKPE_CACHE -split ';')) { $z = Join-Path $c 'pkg\Windows.exe'; if (Test-Path $z) { $exe = $z; break } }
        if (-not $exe) { Write-Log 'USBToolBox not cached - run Update downloads'; return }
        $map = Join-Path $Root 'usbmap'; New-Item -ItemType Directory -Force $map | Out-Null
        Start-Process -FilePath $exe -WorkingDirectory $map
        Write-Log "USBToolBox: discover ports, select <=15, Build UTBMap.kext; copy it to $map then rebuild"
    }),
    @('Text-mode wizard', { Start-EngineConsole @('wizard') 'Wizard' }),
    @('Reboot', { if ($IsPE) { & wpeutil reboot } else { Write-Log 'Not in WinPE - reboot manually' } }),
    @('Shut down', { if ($IsPE) { & wpeutil shutdown } else { Write-Log 'Not in WinPE' } })
)
$i = 0
foreach ($t in $tools) {
    $x = 670 + ($i % 2) * 180; $y = 52 + [math]::Floor($i / 2) * 40
    $bt = New-Btn $t[0] $x $y; $bt.Add_Click($t[1]); $t5.Controls.Add($bt); $i++
}
$info5 = New-Object Windows.Forms.Label; $info5.Location = '12,262'; $info5.Size = '640,220'
$info5.Text = "After macOS is installed and boots from the USB stick:`r`n" +
              " - Install EFI to the ESP of the macOS disk so it boots without the stick.`r`n" +
              " - Map USB ports (USBToolBox works from Windows) and rebuild the EFI.`r`n" +
              " - Generate custom SSDTs with SSDTTime for best stability (laptops / AMD).`r`n" +
              " - Check the serial in BUILD-NOTES.txt is NOT valid at checkcoverage.apple.com before iCloud login.`r`n`r`n" +
              "Tahoe notes: onboard audio needs AppleHDA restored (root patch) or VoodooHDA; Broadcom/Intel Wi-Fi may need extra patches."
$t5.Controls.Add($info5)

$Tabs.TabPages.AddRange(@($t1, $t2, $t3, $t4, $t5))

# ---------------------------------------------------------------- startup ----
$Form.Add_Shown({
    Write-Log ("HackPE GUI  |  root {0}  |  {1}" -f $Root, $(if ($IsPE) { 'WinPE' } else { 'Windows' }))
    if (-not $Py) { Write-Log 'ERROR: python.exe not found - the engine cannot run.'; return }
    if (-not $IsPE) {
        $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $admin) { Write-Log 'WARNING: not running as Administrator - disk operations will fail.' }
    }
    $st = Invoke-Engine @('status')
    $oc = ($st -split "`r`n" | Where-Object { $_ -match '^\s+OpenCorePkg' }) -join ''
    Write-Log ("Cache: " + $oc.Trim())
    if ($oc -match 'missing') { Write-Log 'Download cache is empty: connect to the internet and use Tools > Update downloads.' }
    Write-Log 'Detecting hardware...'
    $Hout.Text = Invoke-Engine @('detect', '--out', $HwJson, '--summary')
    Fill-Disks $UsbList $true
    Fill-Disks $EspList $false
})

[void]$Form.ShowDialog()
