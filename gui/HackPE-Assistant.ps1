<#
  HackPE Assistant - step-by-step guided hackintosh installer (Windows Forms, PowerShell 5.1).
  Plain-language pages, live checks, colour-coded verdicts, in-window progress and
  friendly error explanations. Engine: engine\hackpe.py. Expert mode: HackPE-Expert.ps1.
#>
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$ErrorActionPreference = 'Continue'

# =============================================================== paths ====
$Root   = Split-Path -Parent $PSScriptRoot
$Py     = Join-Path $Root 'python\python.exe'
if (-not (Test-Path $Py)) { $c = Get-Command python.exe -ErrorAction SilentlyContinue; if ($c) { $Py = $c.Source } }
$Engine = Join-Path $Root 'engine\hackpe.py'
$IsPE   = Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\MiniNT'
$Work   = if ($IsPE) { 'X:\HackPE-work' } else { Join-Path $env:TEMP 'HackPE-work' }
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$HwJson = Join-Path $Work 'hw.json'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
if (-not $env:HACKPE_CACHE) {
    $dirs = @()
    foreach ($d in [IO.DriveInfo]::GetDrives()) {
        $c = Join-Path $d.Name 'HackPE\cache'
        if (Test-Path (Join-Path $c 'manifest.json')) { $dirs += $c }
    }
    $env:HACKPE_CACHE = ((@($dirs) + @((Join-Path $Work 'cache'), (Join-Path $Root 'cache'))) | Select-Object -Unique) -join ';'
}

# =============================================================== theme ====
$Col = @{
    Ok = [Drawing.Color]::FromArgb(46, 125, 50); Warn = [Drawing.Color]::FromArgb(230, 126, 0)
    Bad = [Drawing.Color]::FromArgb(198, 40, 40); Info = [Drawing.Color]::FromArgb(21, 101, 192)
    Wait = [Drawing.Color]::FromArgb(150, 150, 150); Run = [Drawing.Color]::FromArgb(21, 101, 192)
    Side = [Drawing.Color]::FromArgb(28, 33, 48); SideText = [Drawing.Color]::FromArgb(150, 160, 180)
    Accent = [Drawing.Color]::FromArgb(0, 122, 255); Bg = [Drawing.Color]::White
    Sub = [Drawing.Color]::FromArgb(95, 99, 104); RowBg = [Drawing.Color]::FromArgb(246, 247, 249)
}
$Fnt = @{
    Title = New-Object Drawing.Font('Segoe UI', 17, [Drawing.FontStyle]::Bold)
    Sub = New-Object Drawing.Font('Segoe UI', 10)
    Body = New-Object Drawing.Font('Segoe UI', 10)
    Bold = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Bold)
    Big = New-Object Drawing.Font('Segoe UI', 13, [Drawing.FontStyle]::Bold)
    Side = New-Object Drawing.Font('Segoe UI', 10.5)
    SideB = New-Object Drawing.Font('Segoe UI', 10.5, [Drawing.FontStyle]::Bold)
    Badge = New-Object Drawing.Font('Segoe UI', 8, [Drawing.FontStyle]::Bold)
    Mono = New-Object Drawing.Font('Consolas', 9)
}

# =============================================================== state ====
$State = @{
    Mode = 'install'; Pre = $null; Hw = $null; Plans = @{}; Rec = $null; Target = $null
    Disk = $null; DiskName = ''; UsbRoot = $null; EspDisk = $null; Force = $false
    Busy = $false; Failed = $null; DiskSig = ''; Started = $null; LastJob = $null
}
$Versions = @(
    @('tahoe', 'macOS 26 Tahoe', 2025), @('sequoia', 'macOS 15 Sequoia', 2024), @('sonoma', 'macOS 14 Sonoma', 2023),
    @('ventura', 'macOS 13 Ventura', 2022), @('monterey', 'macOS 12 Monterey', 2021), @('bigsur', 'macOS 11 Big Sur', 2020),
    @('catalina', 'macOS 10.15 Catalina', 2019), @('mojave', 'macOS 10.14 Mojave', 2018), @('highsierra', 'macOS 10.13 High Sierra', 2017))
$Flows = @{
    install = @('welcome', 'pc', 'version', 'usb', 'progress', 'done')
    post    = @('welcome', 'espdisk', 'progress', 'done')
}
$StepNames = @{ welcome = 'Welcome'; pc = 'Your PC'; version = 'Choose macOS'; usb = 'USB stick'
                progress = 'Create'; done = 'Next steps'; espdisk = 'Choose disk' }

# ============================================================= helpers ====
function Quote([string]$s) { if ($s -match '[\s&;()]') { '"' + $s + '"' } else { $s } }

function Invoke-Engine([string[]]$EngineArgs) {
    $Form.Cursor = 'WaitCursor'; [Windows.Forms.Application]::DoEvents()
    try { (& $Py $Engine @EngineArgs 2>$null | Out-String) } finally { $Form.Cursor = 'Default' }
}

function Measure-H([string]$text, $font, [int]$w) {
    [Windows.Forms.TextRenderer]::MeasureText($text, $font, (New-Object Drawing.Size($w, 0)),
        [Windows.Forms.TextFormatFlags]::WordBreak).Height
}

function New-Label([string]$text, $x, $y, $w, $font = $Fnt.Body, $color = [Drawing.Color]::Black) {
    $l = New-Object Windows.Forms.Label
    $l.Text = $text; $l.Font = $font; $l.ForeColor = $color; $l.Location = "$x,$y"
    $l.Size = New-Object Drawing.Size($w, ((Measure-H $text $font $w) + 4)); $l
}

function New-Button([string]$text, $x, $y, $w = 150, $h = 34) {
    $b = New-Object Windows.Forms.Button
    $b.Text = $text; $b.Location = "$x,$y"; $b.Size = "$w,$h"; $b.Font = $Fnt.Body; $b.FlatStyle = 'System'; $b
}

function Get-StatusColor([string]$st) {
    switch ($st) { 'ok' { $Col.Ok } 'warn' { $Col.Warn } 'bad' { $Col.Bad } 'run' { $Col.Run } 'wait' { $Col.Wait } default { $Col.Info } }
}
function Get-StatusGlyph([string]$st) {
    switch ($st) { 'ok' { 'OK' } 'warn' { '!' } 'bad' { 'X' } 'run' { '>' } 'wait' { '-' } default { 'i' } }
}

# A coloured status row: [badge] Title / explanation
function New-Row([string]$status, [string]$RowTitle, [string]$note, [int]$w) {
    $p = New-Object Windows.Forms.Panel
    $p.BackColor = $Col.RowBg; $p.Margin = New-Object Windows.Forms.Padding(0, 0, 0, 6)
    $badge = New-Object Windows.Forms.Label
    $badge.Text = Get-StatusGlyph $status; $badge.TextAlign = 'MiddleCenter'; $badge.Font = $Fnt.Badge
    $badge.ForeColor = [Drawing.Color]::White; $badge.BackColor = Get-StatusColor $status
    $badge.Location = '10,10'; $badge.Size = '30,24'
    $t = New-Label $RowTitle 52 8 ($w - 64) $Fnt.Bold
    $p.Controls.AddRange(@($badge, $t))
    $h = $t.Bottom
    if ($note) { $n = New-Label $note 52 ($t.Bottom) ($w - 64) $Fnt.Body $Col.Sub; $p.Controls.Add($n); $h = $n.Bottom }
    $p.Size = New-Object Drawing.Size($w, [math]::Max(44, $h + 8))
    $p.Tag = @{ Badge = $badge; Title = $t }
    $p
}
function Set-RowStatus($row, [string]$status) {
    $row.Tag.Badge.BackColor = Get-StatusColor $status; $row.Tag.Badge.Text = Get-StatusGlyph $status
}

function New-List($x, $y, $w, $h) {
    $f = New-Object Windows.Forms.FlowLayoutPanel
    $f.FlowDirection = 'TopDown'; $f.WrapContents = $false; $f.AutoScroll = $true
    $f.Location = "$x,$y"; $f.Size = "$w,$h"; $f
}
function Clear-List($list) { foreach ($c in @($list.Controls)) { $c.Dispose() }; $list.Controls.Clear() }
function RowW($list) { $list.ClientSize.Width - 22 }

function Show-Help([string]$text) {
    [void][Windows.Forms.MessageBox]::Show($Form, $text, 'HackPE help', 'OK', 'Information')
}

# ======================================================= async engine jobs ====
# Output goes to a file (cmd redirect, stdout+stderr merged); a UI timer tails it.
$State.Job = $null
$JobTimer = New-Object Windows.Forms.Timer
$JobTimer.Interval = 250

function Start-EngineJob([string[]]$EngineArgs, [scriptblock]$OnLine, [scriptblock]$OnExit) {
    $out = Join-Path $Work ("job-{0}.log" -f (Get-Date -Format 'HHmmssfff'))
    $line = ((@($Py, $Engine) + $EngineArgs) | ForEach-Object { Quote $_ }) -join ' '
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = "/d /s /c `"$line > `"$out`" 2>&1`""
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $p = [Diagnostics.Process]::Start($psi)
    $State.Job = @{ P = $p; Out = $out; Pos = 0; Partial = ''; OnLine = $OnLine; OnExit = $OnExit }
    $State.LastJob = $out
    $JobTimer.Start()
}

function Stop-EngineJob {
    if ($State.Job) { & taskkill.exe /PID $State.Job.P.Id /T /F 2>&1 | Out-Null }
}

function Read-JobOutput($j) {
    if (-not (Test-Path $j.Out)) { return }
    $n = 0; $buf = $null
    $fs = [IO.File]::Open($j.Out, 'Open', 'Read', 'ReadWrite')
    try {
        if ($fs.Length -gt $j.Pos) {
            [void]$fs.Seek($j.Pos, 'Begin')
            $buf = New-Object byte[] ($fs.Length - $j.Pos)
            $n = $fs.Read($buf, 0, $buf.Length); $j.Pos += $n
        }
    } finally { $fs.Close() }
    if ($n -eq 0) { return }
    $text = $j.Partial + [Text.Encoding]::UTF8.GetString($buf, 0, $n)
    $parts = $text -split "`n"
    $j.Partial = $parts[-1]
    for ($i = 0; $i -lt $parts.Count - 1; $i++) {
        $l = ($parts[$i].TrimEnd("`r") -split "`r")[-1]
        & $j.OnLine $l $false
    }
    if ($j.Partial -match "`r") { & $j.OnLine (($j.Partial -split "`r")[-1]) $true }   # live progress
}

$JobTimer.Add_Tick({
    $j = $State.Job
    if (-not $j) { $JobTimer.Stop(); return }
    Read-JobOutput $j
    if ($j.P.HasExited) {
        Read-JobOutput $j
        if ($j.Partial) { & $j.OnLine (($j.Partial -split "`r")[-1]) $false }
        $JobTimer.Stop(); $State.Job = $null
        & $j.OnExit $j.P.ExitCode
    }
})

# ========================================================== hardware/data ====
function Get-Plan([string]$target) {
    if ($State.Plans.ContainsKey($target)) { return $State.Plans[$target] }
    $a = @('plan', '--target', $target, '--hw', $HwJson, '--json')
    switch ($TypeBox.SelectedIndex) { 1 { $a += '--desktop' } 2 { $a += '--laptop' } }
    $txt = Invoke-Engine $a
    try { $p = $txt | ConvertFrom-Json } catch { return $null }
    $State.Plans[$target] = $p
    $p
}

function Get-UsbDisks {
    # Native & fast (called every few seconds while the USB page is open)
    $res = @()
    try { $disks = @(Get-Disk -ErrorAction Stop) } catch { return $res }
    $parts = @(Get-Partition -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter })
    foreach ($d in $disks) {
        $letters = @($parts | Where-Object { $_.DiskNumber -eq $d.Number } | ForEach-Object { [string]$_.DriveLetter })
        $hack = $false
        foreach ($l in $letters) { if ((Test-Path "${l}:\sources\boot.wim") -and (Test-Path "${l}:\HackPE")) { $hack = $true } }
        $res += [pscustomobject]@{ Number = $d.Number; Name = $d.FriendlyName; Bus = "$($d.BusType)"
            GB = [math]::Round($d.Size / 1GB, 1); System = ($d.IsBoot -or $d.IsSystem); HackPE = $hack; Letters = $letters }
    }
    $res
}

function Get-VendorKeys {
    $m = ''
    if ($State.Hw) { $m = ("{0} {1}" -f $State.Hw.System.Manufacturer, $State.Hw.System.BoardManufacturer) }
    $laptop = $false; $p = $State.Plans[$State.Target]; if ($p) { $laptop = $p.laptop }
    switch -Regex ($m) {
        'ASUS'                 { return @{ Bios = 'Del (or F2)'; Boot = 'F8'; Brand = 'ASUS' } }
        'Gigabyte'             { return @{ Bios = 'Del'; Boot = 'F12'; Brand = 'Gigabyte' } }
        'Micro-Star|MSI'       { return @{ Bios = 'Del'; Boot = 'F11'; Brand = 'MSI' } }
        'ASRock'               { return @{ Bios = 'F2 or Del'; Boot = 'F11'; Brand = 'ASRock' } }
        'Dell'                 { return @{ Bios = 'F2'; Boot = 'F12'; Brand = 'Dell' } }
        'HP|Hewlett'           { return @{ Bios = 'F10 (or Esc, then F10)'; Boot = 'F9 (or Esc, then F9)'; Brand = 'HP' } }
        'LENOVO'               { if ($laptop) { return @{ Bios = 'F2 (or Fn+F2 / Novo button)'; Boot = 'F12 (or Fn+F12)'; Brand = 'Lenovo' } }
                                 return @{ Bios = 'F1'; Boot = 'F12'; Brand = 'Lenovo' } }
        'Acer'                 { return @{ Bios = 'F2'; Boot = 'F12 (enable "F12 Boot Menu" in BIOS first)'; Brand = 'Acer' } }
        'Intel'                { return @{ Bios = 'F2'; Boot = 'F10'; Brand = 'Intel' } }
    }
    @{ Bios = 'Del, F2 or F10'; Boot = 'F8, F11 or F12'; Brand = 'your PC' }
}

function Get-BiosChecklist {
    $p = $State.Plans[$State.Target]
    $l = @('Secure Boot: Disabled', 'Fast Boot: Disabled', 'CSM / Legacy boot: Disabled (UEFI only)',
           'Above 4G Decoding: Enabled', 'Resizable BAR (Re-Size BAR): Disabled', 'XHCI Hand-off: Enabled',
           'SATA mode: AHCI', 'VT-d / IOMMU: Disabled (optional - already handled in the EFI)',
           'CFG Lock: Disabled (only if your BIOS has this option)', 'Intel SGX / Platform Trust: Disabled (if present)',
           'OS type: "Windows 10/11 WHQL" or "Other OS" (UEFI)')
    if ($p -and $p.display -eq 'igpu') { $l += @('Integrated graphics: Enabled and set as primary display', 'DVMT Pre-Allocated: 64M (if available)') }
    elseif ($p -and ($p.gpus | Where-Object { $_.kind -eq 'igpu' })) { $l += 'Integrated graphics: Enabled (multi-monitor) - recommended' }
    $l
}

# ================================================================= form ====
$Form = New-Object Windows.Forms.Form
$Form.Text = 'HackPE Assistant'
$Form.ClientSize = '1000,680'; $Form.StartPosition = 'CenterScreen'
$Form.FormBorderStyle = 'FixedSingle'; $Form.MaximizeBox = $false; $Form.BackColor = $Col.Bg; $Form.Font = $Fnt.Body

$Side = New-Object Windows.Forms.Panel
$Side.Location = '0,0'; $Side.Size = '220,680'; $Side.BackColor = $Col.Side
$Logo = New-Label 'HackPE' 22 22 180 (New-Object Drawing.Font('Segoe UI', 20, [Drawing.FontStyle]::Bold)) ([Drawing.Color]::White)
$Tag = New-Label 'Hackintosh assistant' 24 64 180 $Fnt.Sub $Col.SideText
$Side.Controls.AddRange(@($Logo, $Tag))
$SideSteps = New-Object Windows.Forms.Panel; $SideSteps.Location = '0,110'; $SideSteps.Size = '220,400'; $SideSteps.BackColor = $Col.Side
$Side.Controls.Add($SideSteps)
$SideFoot = New-Label "OpenCore + macOS`r`nHigh Sierra ... Tahoe 26" 24 600 180 $Fnt.Badge $Col.SideText
$Side.Controls.Add($SideFoot)
$Form.Controls.Add($Side)

$Title = New-Label '' 250 22 720 $Fnt.Title; $Title.Height = 36
$Subtitle = New-Label '' 252 60 720 $Fnt.Sub $Col.Sub; $Subtitle.Height = 40
$Form.Controls.AddRange(@($Title, $Subtitle))

$Host_ = New-Object Windows.Forms.Panel; $Host_.Location = '250,104'; $Host_.Size = '730,500'
$Form.Controls.Add($Host_)

$Sep = New-Object Windows.Forms.Label; $Sep.Location = '220,612'; $Sep.Size = '780,1'; $Sep.BackColor = [Drawing.Color]::Gainsboro
$Form.Controls.Add($Sep)
$HelpLink = New-Object Windows.Forms.LinkLabel; $HelpLink.Text = 'Need help with this step?'; $HelpLink.Location = '250,634'; $HelpLink.AutoSize = $true
$ExpertLink = New-Object Windows.Forms.LinkLabel; $ExpertLink.Text = 'Expert mode'; $ExpertLink.Location = '440,634'; $ExpertLink.AutoSize = $true
$BackBtn = New-Button 'Back' 720 626 120 38
$NextBtn = New-Button 'Next' 850 626 130 38
$NextBtn.Font = $Fnt.Bold
$Form.Controls.AddRange(@($HelpLink, $ExpertLink, $BackBtn, $NextBtn))
$Form.AcceptButton = $NextBtn
$Tips = New-Object Windows.Forms.ToolTip

$ExpertLink.Add_LinkClicked({
    Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -STA -File `"$(Join-Path $PSScriptRoot 'HackPE-Expert.ps1')`""
})

$Pages = @{}
function New-Page([string]$key) {
    $p = New-Object Windows.Forms.Panel; $p.Location = '0,0'; $p.Size = $Host_.Size; $p.Visible = $false
    $Host_.Controls.Add($p); $Pages[$key] = $p; $p
}

# ============================================================ page: welcome ====
$pg = New-Page 'welcome'
$pg.Controls.Add((New-Label ("This assistant turns this PC into a hackintosh. It checks your hardware, picks the right " +
    "settings, builds the OpenCore boot loader and creates a macOS installer on a USB stick. Nothing on your " +
    "internal drives is changed until you install macOS yourself.") 0 0 720))
$pg.Controls.Add((New-Label 'Before we start' 0 70 400 $Fnt.Big))
$WelcomeList = New-List 0 102 720 236
$pg.Controls.Add($WelcomeList)
$RecheckBtn = New-Button 'Check again' 0 344 130 32
$NetDrvBtn = New-Button 'Load network driver...' 138 344 180 32
$FetchBtn = New-Button 'Download toolkit files' 326 344 190 32; $FetchBtn.Visible = $false
$FetchLbl = New-Label '' 524 350 200 $Fnt.Body $Col.Sub
$pg.Controls.AddRange(@($RecheckBtn, $NetDrvBtn, $FetchBtn, $FetchLbl))
$pg.Controls.Add((New-Label 'What would you like to do?' 0 390 400 $Fnt.Big))
$ModeInstall = New-Object Windows.Forms.RadioButton; $ModeInstall.Text = 'Create a macOS installer USB stick for this PC'
$ModeInstall.Location = '4,424'; $ModeInstall.Size = '700,26'; $ModeInstall.Checked = $true; $ModeInstall.Font = $Fnt.Bold
$ModePost = New-Object Windows.Forms.RadioButton; $ModePost.Text = 'macOS is already installed - make it start without the USB stick'
$ModePost.Location = '4,456'; $ModePost.Size = '700,26'; $ModePost.Font = $Fnt.Bold
$pg.Controls.AddRange(@($ModeInstall, $ModePost))

function Update-Welcome {
    Clear-List $WelcomeList
    $w = RowW $WelcomeList
    $pre = $State.Pre
    if (-not $pre) { $WelcomeList.Controls.Add((New-Row 'run' 'Checking this PC and the network...' 'This takes a few seconds.' $w)); return }
    if ($IsPE) { $WelcomeList.Controls.Add((New-Row 'ok' 'Running from the HackPE stick' 'Good - nothing is installed on this PC.' $w)) }
    elseif ($pre.admin) { $WelcomeList.Controls.Add((New-Row 'ok' 'Running on Windows as Administrator' 'Disk operations are allowed.' $w)) }
    else { $WelcomeList.Controls.Add((New-Row 'bad' 'Not running as Administrator' 'Close this window and start it with "Run as administrator".' $w)) }
    if ($pre.opencore) {
        $WelcomeList.Controls.Add((New-Row 'ok' ("Toolkit ready - OpenCore {0}" -f $pre.opencore) ("{0} of {1} components available offline." -f $pre.packages, $pre.packages_total) $w))
    } else {
        $WelcomeList.Controls.Add((New-Row 'bad' 'Toolkit files are missing' 'Connect to the internet and click "Download toolkit files".' $w))
    }
    if ($pre.net_apple) { $WelcomeList.Controls.Add((New-Row 'ok' 'Internet connection works' 'Needed to download macOS from Apple.' $w)) }
    else { $WelcomeList.Controls.Add((New-Row 'warn' 'No internet connection' ("macOS is downloaded from Apple, so the installer step needs internet. Plug in an Ethernet " +
        "cable or connect your phone by USB with tethering on, then click Check again. If your network card isn't recognised, use Load network driver.") $w)) }
    $WelcomeList.Controls.Add((New-Row 'info' 'You will need a second USB stick (8 GB or more recommended)' 'It will be erased. You choose it in step 4 - not the HackPE stick.' $w))
    $WelcomeList.Controls.Add((New-Row 'info' 'Plan about 30-60 minutes and back up your data' 'Installing macOS requires erasing the drive you install it to.' $w))
    $FetchBtn.Visible = ($pre.missing.Count -gt 0 -and $pre.net_github)
}

function Start-Preflight {
    $State.Pre = $null; Update-Welcome
    $State.PreText = ''
    Start-EngineJob @('preflight', '--json') { param($l, $live) if ($l -like '{*') { $State.PreText = $l } } {
        param($code)
        try { $State.Pre = $State.PreText | ConvertFrom-Json } catch { $State.Pre = [pscustomobject]@{ admin = $true; opencore = $null; packages = 0; packages_total = 0; missing = @('?'); net_apple = $false; net_github = $false } }
        Update-Welcome; Update-Nav
    }
}
$RecheckBtn.Add_Click({ if (-not $State.Job) { Start-Preflight } })
$NetDrvBtn.Add_Click({
    $d = New-Object Windows.Forms.OpenFileDialog; $d.Filter = 'Driver (*.inf)|*.inf'; $d.Title = 'Choose the .inf file of your network driver'
    if ($d.ShowDialog() -eq 'OK') {
        & drvload.exe $d.FileName | Out-Null
        if ($IsPE) { & wpeutil.exe InitializeNetwork | Out-Null }
        Start-Sleep 3; Start-Preflight
    }
})
$FetchBtn.Add_Click({
    if ($State.Job) { return }
    $FetchBtn.Enabled = $false; $FetchLbl.Text = 'Downloading...'
    Start-EngineJob @('fetch') { param($l, $live) if ($l -match '^\s+[+=!]\s+(\S+)') { $FetchLbl.Text = "Downloading $($Matches[1])..." } } {
        param($code); $FetchBtn.Enabled = $true; $FetchLbl.Text = $(if ($code -eq 0) { 'Done.' } else { 'Some downloads failed.' }); Start-Preflight
    }
})

# ================================================================ page: pc ====
$pg = New-Page 'pc'
$Banner = New-Object Windows.Forms.Panel; $Banner.Location = '0,0'; $Banner.Size = '720,74'; $Banner.BackColor = $Col.Info
$BannerText = New-Label '' 16 10 690 $Fnt.Big ([Drawing.Color]::White); $BannerText.Height = 28
$BannerSub = New-Label '' 16 40 690 $Fnt.Body ([Drawing.Color]::White); $BannerSub.Height = 30
$Banner.Controls.AddRange(@($BannerText, $BannerSub))
$PcList = New-List 0 84 720 368
$RedetectBtn = New-Button 'Detect again' 0 462 130 32
$SaveRepBtn = New-Button 'Save report...' 138 462 130 32
$TypeLbl = New-Label 'PC type:' 300 469 60; $TypeLbl.Height = 22
$TypeBox = New-Object Windows.Forms.ComboBox; $TypeBox.DropDownStyle = 'DropDownList'; $TypeBox.Location = '362,466'; $TypeBox.Width = 150
'Detect automatically', 'Desktop', 'Laptop' | ForEach-Object { [void]$TypeBox.Items.Add($_) }; $TypeBox.SelectedIndex = 0
$Tips.SetToolTip($TypeBox, 'Only change this if HackPE guessed wrong (e.g. an all-in-one detected as a laptop).')
$AnywayBox = New-Object Windows.Forms.CheckBox; $AnywayBox.Text = 'Continue anyway (advanced)'; $AnywayBox.Location = '530,468'; $AnywayBox.AutoSize = $true; $AnywayBox.Visible = $false
$pg.Controls.AddRange(@($Banner, $PcList, $RedetectBtn, $SaveRepBtn, $TypeLbl, $TypeBox, $AnywayBox))

function Set-Banner([string]$st, [string]$t, [string]$s) {
    $Banner.BackColor = Get-StatusColor $st; $BannerText.Text = $t; $BannerSub.Text = $s
}

function Update-PcPage {
    Clear-List $PcList
    $w = RowW $PcList
    if (-not $State.Rec) { return }
    $p = $State.Plans[$State.Rec]
    $yes = @($Versions | Where-Object { $p.compat.($_[0]) -eq 'yes' })
    $exp = @($Versions | Where-Object { $p.compat.($_[0]) -eq 'experimental' })
    if ($yes.Count -gt 0) {
        Set-Banner 'ok' ("Good news! This PC can run up to {0}." -f $yes[0][1]) ("It will identify itself as a {0}. Details for each part are below." -f $p.smbios_friendly)
    } elseif ($exp.Count -gt 0) {
        Set-Banner 'warn' ("This PC may run up to {0} - experimental" -f $exp[0][1]) 'It can work, but expect manual tuning. Read the yellow items below.'
    } else {
        $why = ($p.components | Where-Object { $_.status -eq 'bad' } | Select-Object -First 1)
        Set-Banner 'bad' "This PC can't run macOS as it is" $(if ($why) { "$($why.kind): $($why.note)" } else { 'See the red items below.' })
    }
    $AnywayBox.Visible = ($yes.Count -eq 0)
    foreach ($c in $p.components) {
        $PcList.Controls.Add((New-Row $c.status ("{0}:  {1}" -f $c.kind, $c.name) $c.note $w))
    }
}

function Start-Detect {
    $State.Plans = @{}; $State.Rec = $null; $State.Hw = $null
    Clear-List $PcList
    Set-Banner 'info' 'Looking at your hardware...' 'Reading processor, graphics, network, audio and storage information.'
    Start-EngineJob @('detect', '--out', $HwJson) { param($l, $live) } {
        param($code)
        if (-not (Test-Path $HwJson)) { Set-Banner 'bad' 'Hardware detection failed' 'Click "Detect again". If it keeps failing, use Expert mode.'; Update-Nav; return }
        try { $State.Hw = Get-Content $HwJson -Raw | ConvertFrom-Json } catch {}
        $first = Get-Plan 'tahoe'
        $rec = 'tahoe'
        if ($first) {
            $y = @($Versions | Where-Object { $first.compat.($_[0]) -eq 'yes' })
            $e = @($Versions | Where-Object { $first.compat.($_[0]) -eq 'experimental' })
            if ($y.Count) { $rec = $y[0][0] } elseif ($e.Count) { $rec = $e[0][0] }
        }
        [void](Get-Plan $rec); $State.Rec = $rec
        if (-not $State.Target) { $State.Target = $rec }
        Update-PcPage; Update-Nav
    }
}
$RedetectBtn.Add_Click({ if (-not $State.Job) { Start-Detect } })
$TypeBox.Add_SelectedIndexChanged({ if ($State.Rec -and -not $State.Job) { $State.Plans = @{}; [void](Get-Plan 'tahoe'); [void](Get-Plan $State.Rec); Update-PcPage } })
$AnywayBox.Add_CheckedChanged({ Update-Nav })
$SaveRepBtn.Add_Click({
    if (-not $State.Rec) { return }
    $d = New-Object Windows.Forms.SaveFileDialog; $d.Filter = 'Text|*.txt'; $d.FileName = 'hackpe-report.txt'
    if ($d.ShowDialog() -eq 'OK') {
        $txt = Invoke-Engine @('plan', '--target', $State.Rec, '--hw', $HwJson)
        Set-Content -Path $d.FileName -Value $txt -Encoding UTF8
        Copy-Item $HwJson ([IO.Path]::ChangeExtension($d.FileName, '.json')) -Force
    }
})

# =========================================================== page: version ====
$pg = New-Page 'version'
$pg.Controls.Add((New-Label 'macOS version' 0 0 300 $Fnt.Bold))
$VerPanel = New-Object Windows.Forms.Panel; $VerPanel.Location = '0,26'; $VerPanel.Size = '300,420'
$pg.Controls.Add($VerPanel)
$ShowAll = New-Object Windows.Forms.CheckBox; $ShowAll.Text = 'Allow unsupported versions (advanced)'; $ShowAll.Location = '0,452'; $ShowAll.AutoSize = $true
$pg.Controls.Add($ShowAll)
$pg.Controls.Add((New-Label 'What to expect' 320 0 400 $Fnt.Bold))
$ExpectList = New-List 320 26 400 420
$pg.Controls.Add($ExpectList)
$ForceBox = New-Object Windows.Forms.CheckBox; $ForceBox.Text = "I understand it may not work - build anyway"; $ForceBox.Location = '320,452'; $ForceBox.AutoSize = $true; $ForceBox.Visible = $false
$pg.Controls.Add($ForceBox)
$VerRadios = @()
$y = 0
foreach ($v in $Versions) {
    $r = New-Object Windows.Forms.RadioButton
    $r.Text = "{0}  ({1})" -f $v[1], $v[2]; $r.Tag = $v[0]; $r.Location = "4,$y"; $r.Size = '200,26'; $r.Font = $Fnt.Body
    $b = New-Object Windows.Forms.Label; $b.Location = "206,$($y + 3)"; $b.Size = '90,20'; $b.TextAlign = 'MiddleCenter'
    $b.Font = $Fnt.Badge; $b.ForeColor = [Drawing.Color]::White
    $r.Add_CheckedChanged({ if ($this.Checked) { $State.Target = $this.Tag; Update-Expect; Update-Nav } })
    $VerPanel.Controls.AddRange(@($r, $b))
    $VerRadios += , @($r, $b)
    $y += 44
}

function Update-VersionPage {
    $p = $State.Plans[$State.Rec]; if (-not $p) { return }
    foreach ($pair in $VerRadios) {
        $r = $pair[0]; $b = $pair[1]; $st = $p.compat.($r.Tag)
        if ($r.Tag -eq $State.Rec) { $b.Text = 'RECOMMENDED'; $b.BackColor = $Col.Ok; $r.Enabled = $true }
        elseif ($st -eq 'yes') { $b.Text = 'SUPPORTED'; $b.BackColor = [Drawing.Color]::FromArgb(102, 160, 106); $r.Enabled = $true }
        elseif ($st -eq 'experimental') { $b.Text = 'EXPERIMENTAL'; $b.BackColor = $Col.Warn; $r.Enabled = $true }
        else { $b.Text = $(if ($st -like '*CPU*') { 'NO: CPU' } else { 'NO: GRAPHICS' }); $b.BackColor = $Col.Wait; $r.Enabled = $ShowAll.Checked }
        $Tips.SetToolTip($r, $(if ($st -eq 'yes') { 'Works natively on this PC' } elseif ($st -eq 'experimental') { 'May work with tuning' } else { "Not supported natively ($st)" }))
    }
    foreach ($pair in $VerRadios) { if ($pair[0].Tag -eq $State.Target -and $pair[0].Enabled) { $pair[0].Checked = $true } }
    $any = $false; foreach ($pair in $VerRadios) { if ($pair[0].Checked) { $any = $true } }
    if (-not $any) { foreach ($pair in $VerRadios) { if ($pair[0].Tag -eq $State.Rec) { $pair[0].Checked = $true } } }
    Update-Expect
}
$ShowAll.Add_CheckedChanged({ Update-VersionPage })

function Update-Expect {
    Clear-List $ExpectList
    $w = RowW $ExpectList
    $p = Get-Plan $State.Target
    if (-not $p) { $ExpectList.Controls.Add((New-Row 'bad' 'Could not analyse this version' '' $w)); return }
    $ExpectList.Controls.Add((New-Row 'info' ("Your PC will appear as: {0}" -f $p.smbios_friendly) ("Model identifier {0}. A unique serial number is generated for you." -f $p.smbios) $w))
    foreach ($b in $p.blockers) { $ExpectList.Controls.Add((New-Row 'bad' 'Not supported' $b $w)) }
    foreach ($c in $p.components) { if ($c.status -ne 'ok') { $ExpectList.Controls.Add((New-Row $c.status ("{0}: {1}" -f $c.kind, $c.name) $c.note $w)) } }
    if ($State.Target -eq 'tahoe') { $ExpectList.Controls.Add((New-Row 'info' 'Tahoe is the final macOS for Intel/x86' 'Best long-term choice - it will receive security updates for a few years.' $w)) }
    if (-not $p.blockers -and -not ($p.components | Where-Object { $_.status -ne 'ok' })) {
        $ExpectList.Controls.Add((New-Row 'ok' 'Everything detected is supported' 'This is an excellent match.' $w))
    }
    $ForceBox.Visible = [bool]$p.blockers
    if (-not $ForceBox.Visible) { $ForceBox.Checked = $false }
}
$ForceBox.Add_CheckedChanged({ Update-Nav })

# =============================================================== page: usb ====
$pg = New-Page 'usb'
$pg.Controls.Add((New-Label ("Plug a USB stick into a port on the back of the PC (directly on the motherboard). It appears below " +
    "automatically. Use at least 8 GB - the macOS download needs 1-3 GB.") 0 0 720))
$UsbList = New-List 0 56 720 300
$EraseBox = New-Object Windows.Forms.CheckBox; $EraseBox.Location = '0,368'; $EraseBox.Size = '720,40'; $EraseBox.Font = $Fnt.Bold
$EraseBox.Text = 'Select a USB stick above'; $EraseBox.Enabled = $false
$UsbHint = New-Label 'Tip: avoid USB hubs and front-panel ports - they are a common cause of boot problems.' 0 416 720 $Fnt.Body $Col.Sub
$pg.Controls.AddRange(@($UsbList, $EraseBox, $UsbHint))
$UsbTimer = New-Object Windows.Forms.Timer; $UsbTimer.Interval = 3000

function Update-UsbPage([bool]$force = $false) {
    $disks = @(Get-UsbDisks | Where-Object { $_.Bus -eq 'USB' })
    $sig = ($disks | ForEach-Object { "$($_.Number)|$($_.GB)|$($_.HackPE)" }) -join ';'
    if (-not $force -and $sig -eq $State.DiskSig) { return }
    $State.DiskSig = $sig
    Clear-List $UsbList
    $w = RowW $UsbList
    $usable = 0
    foreach ($d in $disks) {
        $why = $null
        if ($d.HackPE) { $why = 'This is the HackPE stick you started from - use a different one.' }
        elseif ($d.System) { $why = 'This disk is in use by the running system.' }
        elseif ($d.GB -lt 3.5) { $why = ('Too small ({0} GB) - at least 4 GB is needed.' -f $d.GB) }
        $row = New-Object Windows.Forms.Panel; $row.Size = New-Object Drawing.Size($w, 52); $row.BackColor = $Col.RowBg
        $row.Margin = New-Object Windows.Forms.Padding(0, 0, 0, 6)
        $rb = New-Object Windows.Forms.RadioButton
        $rb.Text = "{0}   -   {1} GB" -f $d.Name, $d.GB; $rb.Font = $Fnt.Bold; $rb.Location = '12,6'; $rb.Size = New-Object Drawing.Size(($w - 24), 22)
        $rb.Tag = $d; $rb.Enabled = (-not $why)
        $sub = New-Label $(if ($why) { $why } else { "Disk $($d.Number)" + $(if ($d.Letters) { "  (currently " + (($d.Letters | ForEach-Object { "${_}:" }) -join ' ') + ")" } else { '' }) }) 30 28 ($w - 40) $Fnt.Body $(if ($why) { $Col.Bad } else { $Col.Sub })
        $rb.Add_CheckedChanged({
            if ($this.Checked) {
                $State.Disk = $this.Tag.Number; $State.DiskName = "$($this.Tag.Name) ($($this.Tag.GB) GB)"
                $EraseBox.Enabled = $true; $EraseBox.Checked = $false
                $EraseBox.Text = "I understand that ALL files on $($State.DiskName) will be permanently erased."
                Update-Nav
            }
        })
        if ($d.Number -eq $State.Disk -and -not $why) { $rb.Checked = $true }
        $row.Controls.AddRange(@($rb, $sub))
        $UsbList.Controls.Add($row)
        if (-not $why) { $usable++ }
    }
    if ($disks.Count -eq 0) {
        $UsbList.Controls.Add((New-Row 'wait' 'No USB stick found yet' 'Insert one now - this list refreshes every few seconds.' $w))
    } elseif ($usable -eq 0) {
        $UsbList.Controls.Add((New-Row 'warn' 'Insert another USB stick' 'None of the connected sticks can be used (see the reasons above).' $w))
    }
    if (-not ($disks | Where-Object { $_.Number -eq $State.Disk -and -not $_.HackPE })) {
        $State.Disk = $null; $EraseBox.Enabled = $false; $EraseBox.Checked = $false; $EraseBox.Text = 'Select a USB stick above'
    }
    Update-Nav
}
$UsbTimer.Add_Tick({ if (-not $State.Job) { Update-UsbPage } })
$EraseBox.Add_CheckedChanged({ Update-Nav })

# =========================================================== page: espdisk ====
$pg = New-Page 'espdisk'
$pg.Controls.Add((New-Label ("Choose the drive you installed macOS on. HackPE copies OpenCore to its hidden EFI partition so the " +
    "PC starts macOS without the USB stick. Your files and Windows' boot loader are not touched.") 0 0 720))
$EspList = New-List 0 56 720 220
$pg.Controls.Add($EspList)
$pg.Controls.Add((New-Label 'Which OpenCore should be installed?' 0 286 720 $Fnt.Bold))
$SrcStick = New-Object Windows.Forms.RadioButton; $SrcStick.Location = '4,312'; $SrcStick.Size = '716,26'
$SrcBuild = New-Object Windows.Forms.RadioButton; $SrcBuild.Location = '4,342'; $SrcBuild.Size = '420,26'; $SrcBuild.Text = 'Build a fresh one for this PC, for:'
$SrcTarget = New-Object Windows.Forms.ComboBox; $SrcTarget.DropDownStyle = 'DropDownList'; $SrcTarget.Location = '428,342'; $SrcTarget.Width = 220
$Versions | ForEach-Object { [void]$SrcTarget.Items.Add($_[1]) }; $SrcTarget.SelectedIndex = 0
$EspConfirm = New-Object Windows.Forms.CheckBox; $EspConfirm.Location = '0,390'; $EspConfirm.Size = '720,40'; $EspConfirm.Font = $Fnt.Bold
$EspConfirm.Text = 'Select a disk above'; $EspConfirm.Enabled = $false
$pg.Controls.AddRange(@($SrcStick, $SrcBuild, $SrcTarget, $EspConfirm))

function Find-InstallerStick {
    foreach ($d in [IO.DriveInfo]::GetDrives()) {
        $r = $d.Name
        if ((Test-Path (Join-Path $r 'EFI\OC\OpenCore.efi')) -and -not (Test-Path (Join-Path $r 'sources\boot.wim'))) { return $r }
    }
    $null
}

function Update-EspPage {
    Clear-List $EspList
    $w = RowW $EspList
    $stick = Find-InstallerStick
    if ($stick) { $SrcStick.Text = "Copy it from the installer USB stick ($stick) - recommended, it is the one that already boots"; $SrcStick.Enabled = $true; $SrcStick.Tag = $stick; $SrcStick.Checked = $true }
    else { $SrcStick.Text = 'Copy it from the installer USB stick (not found - plug it in and come back)'; $SrcStick.Enabled = $false; $SrcBuild.Checked = $true }
    $disks = @(Get-UsbDisks | Sort-Object @{ e = { $_.Bus -eq 'USB' } }, @{ e = { -$_.GB } })
    foreach ($d in $disks) {
        if ($d.HackPE) { continue }
        $row = New-Object Windows.Forms.Panel; $row.Size = New-Object Drawing.Size($w, 52); $row.BackColor = $Col.RowBg
        $row.Margin = New-Object Windows.Forms.Padding(0, 0, 0, 6)
        $rb = New-Object Windows.Forms.RadioButton; $rb.Text = "{0}   -   {1} GB" -f $d.Name, $d.GB; $rb.Font = $Fnt.Bold
        $rb.Location = '12,6'; $rb.Size = New-Object Drawing.Size(($w - 24), 22); $rb.Tag = $d
        $sub = New-Label ("Disk {0}, {1}{2}" -f $d.Number, $d.Bus, $(if ($d.Bus -eq 'USB') { ' - usually NOT the right one' } else { '' })) 30 28 ($w - 40) $Fnt.Body $Col.Sub
        $rb.Add_CheckedChanged({
            if ($this.Checked) {
                $State.EspDisk = $this.Tag.Number; $EspConfirm.Enabled = $true; $EspConfirm.Checked = $false
                $EspConfirm.Text = "Install OpenCore to the EFI partition of $($this.Tag.Name) (disk $($this.Tag.Number))."
                Update-Nav
            }
        })
        $row.Controls.AddRange(@($rb, $sub)); $EspList.Controls.Add($row)
    }
    Update-Nav
}
$EspConfirm.Add_CheckedChanged({ Update-Nav })

# ========================================================== page: progress ====
$pg = New-Page 'progress'
$ProgList = New-List 0 0 720 230
$ProgBar = New-Object Windows.Forms.ProgressBar; $ProgBar.Location = '0,242'; $ProgBar.Size = '720,22'; $ProgBar.Style = 'Marquee'
$ProgText = New-Label '' 0 270 560 $Fnt.Body $Col.Sub; $ProgText.Height = 22
$ProgTime = New-Label '' 600 270 120 $Fnt.Body $Col.Sub; $ProgTime.TextAlign = 'TopRight'; $ProgTime.Height = 22
$ErrPanel = New-Object Windows.Forms.Panel; $ErrPanel.Location = '0,298'; $ErrPanel.Size = '720,110'; $ErrPanel.BackColor = [Drawing.Color]::FromArgb(253, 236, 234); $ErrPanel.Visible = $false
$ErrTitle = New-Label 'Something went wrong' 14 10 690 $Fnt.Big $Col.Bad; $ErrTitle.Height = 26
$ErrText = New-Label '' 14 40 690 $Fnt.Body; $ErrText.Height = 64
$ErrPanel.Controls.AddRange(@($ErrTitle, $ErrText))
$DetailsBox = New-Object Windows.Forms.CheckBox; $DetailsBox.Text = 'Show details'; $DetailsBox.Location = '0,416'; $DetailsBox.AutoSize = $true
$Details = New-Object Windows.Forms.TextBox; $Details.Multiline = $true; $Details.ReadOnly = $true; $Details.ScrollBars = 'Vertical'
$Details.Font = $Fnt.Mono; $Details.Location = '0,298'; $Details.Size = '720,112'; $Details.Visible = $false
$pg.Controls.AddRange(@($ProgList, $ProgBar, $ProgText, $ProgTime, $ErrPanel, $DetailsBox, $Details))
$DetailsBox.Add_CheckedChanged({ $Details.Visible = $DetailsBox.Checked; if ($Details.Visible) { $Details.BringToFront() } })
$ClockTimer = New-Object Windows.Forms.Timer; $ClockTimer.Interval = 1000
$ClockTimer.Add_Tick({ if ($State.Started) { $ProgTime.Text = ((Get-Date) - $State.Started).ToString('mm\:ss') } })
$State.StepRows = @{}

function Set-Steps($steps) {
    Clear-List $ProgList; $State.StepRows = @{}
    $w = RowW $ProgList
    foreach ($s in $steps) { $r = New-Row 'wait' $s[1] '' $w; $r.Height = 44; $ProgList.Controls.Add($r); $State.StepRows[$s[0]] = $r }
}

function Mark-Step([string]$key) {
    $seen = $false
    foreach ($r in $ProgList.Controls) {
        if ($State.StepRows[$key] -eq $r) { Set-RowStatus $r 'run'; $seen = $true }
        elseif (-not $seen) { Set-RowStatus $r 'ok' }
    }
}

function Get-FriendlyError([string]$msg, [string]$log) {
    $all = "$msg`n$log"
    if ($all -match 'macrecovery|osrecovery|urlopen|Name or service|getaddrinfo|timed out|ConnectionReset') {
        return 'macOS could not be downloaded from Apple. Check the internet connection (Ethernet or phone USB tethering) and click Try again. The USB stick already contains OpenCore.' }
    if ($all -match 'not in the cache|OpenCorePkg') { return 'The toolkit files (OpenCore and kexts) are missing. Go back to the Welcome page, connect to the internet and click "Download toolkit files".' }
    if ($all -match 'diskpart|did not appear|access is denied|write.?protect') { return 'The USB stick could not be formatted. Try a different stick or port; make sure it is not write-protected.' }
    if ($all -match 'blockers') { return 'This PC has parts macOS cannot use for the selected version. Go back and pick a supported version, or tick "build anyway".' }
    if ($all -match 'HackPE boot stick') { return 'You selected the HackPE stick itself. Insert a second USB stick and choose that one.' }
    if ($all -match 'EFI System Partition|EFI partition') { return 'No EFI partition was found on that disk. Make sure you picked the drive macOS was installed on (erased as APFS / GUID in Disk Utility).' }
    if ($msg) { return $msg }
    'An unexpected error occurred. Tick "Show details" to see the log, or use Expert mode.'
}

function Start-Task([string]$TaskTitle, [string]$TaskSub, $TaskSteps, [string[]]$EngineArgs, [scriptblock]$OnSuccess) {
    $Title.Text = $TaskTitle; $Subtitle.Text = $TaskSub
    Set-Steps $TaskSteps
    $ErrPanel.Visible = $false; $Details.Text = ''; $State.Failed = $null; $State.FailMsg = ''
    $ProgBar.Style = 'Marquee'; $ProgText.Text = 'Starting...'
    $State.Started = Get-Date; $ClockTimer.Start(); $State.Busy = $true; $State.OnSuccess = $OnSuccess; $State.TaskArgs = $EngineArgs
    Update-Nav
    $State.TaskOnLine = {
        param($l, $live)
        if ($l -match '([\d.]+)% downloaded') {
            $ProgBar.Style = 'Continuous'; $ProgBar.Value = [math]::Min(100, [int][double]$Matches[1])
            if ($l -match '([\d.]+)/([\d.]+) MB') { $ProgText.Text = "Downloading macOS: $($Matches[1]) of $($Matches[2]) MB" }
            return
        }
        if ($live) { return }
        if ($l -match '^@@STEP (\w+) ?(.*)$') {
            $k = $Matches[1]; $m = $Matches[2]
            if ($k -eq 'done') { $State.DoneInfo = $m; return }
            Mark-Step $k; $ProgText.Text = $m + '...'; $ProgBar.Style = 'Marquee'
            return
        }
        if ($l -match '^@@FAIL (.*)$') { $State.FailMsg = $Matches[1]; return }
        if ($l -match 'Chunk \d+') { $ProgText.Text = 'Verifying the download...'; return }
        if ($l.Trim()) { $Details.AppendText($l + "`r`n") }
    }
    $State.TaskOnExit = {
        param($code)
        $State.Busy = $false
        if ($code -eq 0 -and -not $State.Cancelled) {
            & $State.OnSuccess                      # may chain another job (sets Busy again)
            if (-not $State.Busy) {
                $ClockTimer.Stop()
                foreach ($r in $ProgList.Controls) { Set-RowStatus $r 'ok' }
                $ProgBar.Style = 'Continuous'; $ProgBar.Value = 100; $ProgText.Text = 'Finished.'
            }
        } else {
            $ClockTimer.Stop()
            foreach ($r in $ProgList.Controls) { if ($r.Tag.Badge.Text -eq '>') { Set-RowStatus $r 'bad' } }
            $ProgBar.Style = 'Continuous'; $ProgBar.Value = 0; $ProgText.Text = 'Stopped.'
            $State.Failed = $true
            $ErrText.Text = Get-FriendlyError $State.FailMsg $Details.Text
            $ErrPanel.Visible = $true; $ErrPanel.BringToFront()
            if ($State.Cancelled) { $ErrTitle.Text = 'Cancelled'; $ErrText.Text = 'The operation was cancelled. Click Try again to restart it.' } else { $ErrTitle.Text = 'Something went wrong' }
        }
        $State.Cancelled = $false
        Update-Nav
    }
    Start-EngineJob $EngineArgs $State.TaskOnLine $State.TaskOnExit
}

function Start-CreateInstaller {
    $a = @('make-usb', '--target', $State.Target, '--hw', $HwJson, '--disk', [string]$State.Disk, '--yes', '--out', (Join-Path $Work "EFI-$($State.Target)"))
    switch ($TypeBox.SelectedIndex) { 1 { $a += '--desktop' } 2 { $a += '--laptop' } }
    if ($ForceBox.Checked -or $AnywayBox.Checked) { $a += '--force' }
    $name = ($Versions | Where-Object { $_[0] -eq $State.Target })[0][1]
    Start-Task 'Creating your installer' "Writing $name to $($State.DiskName). Keep the stick plugged in." `
        @(@('check', 'Check the USB stick'), @('build', 'Build OpenCore for this PC'), @('format', 'Erase and format the stick'),
          @('copy', 'Copy OpenCore to the stick'), @('recovery', 'Download macOS from Apple (1-3 GB)')) $a {
        $State.UsbRoot = $State.DoneInfo; Show-Page 'done'
    }
}

function Start-InstallEsp {
    if ($SrcStick.Checked -and $SrcStick.Tag) {
        Start-Task 'Installing OpenCore' 'Copying OpenCore to the EFI partition of your macOS drive.' `
            @(@('mount', 'Open the EFI partition'), @('copy', 'Install OpenCore')) @('install-esp', '--efi', $SrcStick.Tag, '--disk', [string]$State.EspDisk, '--yes') { Show-Page 'done' }
    } else {
        $t = $Versions[$SrcTarget.SelectedIndex][0]; $out = Join-Path $Work "EFI-post-$t"
        if (-not (Test-Path $HwJson)) { & $Py $Engine detect --out $HwJson | Out-Null }
        $State.PostChain = @('install-esp', '--efi', $out, '--disk', [string]$State.EspDisk, '--yes')
        Start-Task 'Installing OpenCore' 'Building a fresh OpenCore and installing it to your macOS drive.' `
            @(@('build', 'Build OpenCore for this PC'), @('mount', 'Open the EFI partition'), @('copy', 'Install OpenCore')) `
            @('build-efi', '--target', $t, '--hw', $HwJson, '--out', $out, '--force') {
                $State.OnSuccess = { Show-Page 'done' }
                $State.Busy = $true; Update-Nav
                Start-EngineJob $State.PostChain $State.TaskOnLine $State.TaskOnExit
            }
    }
}

# ============================================================== page: done ====
$pg = New-Page 'done'
$DoneList = New-List 0 0 470 440
$pg.Controls.Add($DoneList)
$pg.Controls.Add((New-Label 'BIOS checklist' 490 0 230 $Fnt.Bold))
$BiosList = New-Object Windows.Forms.CheckedListBox; $BiosList.Location = '490,26'; $BiosList.Size = '230,330'; $BiosList.CheckOnClick = $true
$BiosList.HorizontalScrollbar = $true; $BiosList.Font = New-Object Drawing.Font('Segoe UI', 9)
$SaveStepsBtn = New-Button 'Save steps to the USB stick' 490 364 230 32
$NotesBtn = New-Button 'Open technical notes' 490 402 230 32
$pg.Controls.AddRange(@($BiosList, $SaveStepsBtn, $NotesBtn))

function Get-NextStepsText {
    $k = Get-VendorKeys
    $name = ($Versions | Where-Object { $_[0] -eq $State.Target })[0][1]
    @(
        @('Restart and open the BIOS', "Restart the PC and keep pressing $($k.Bios) to open the $($k.Brand) BIOS setup."),
        @('Change the BIOS settings', 'Tick through the checklist on the right. Save and exit (usually F10).'),
        @('Start from the macOS stick', "Restart, press $($k.Boot) for the boot menu and choose the UEFI entry of the new stick (not the HackPE stick)."),
        @('Choose "macOS Base System"', "In the OpenCore menu pick the entry with 'macOS Base System' or 'Recovery' in the name. Lots of text scrolling is normal - wait 2-10 minutes."),
        @('Erase the target drive', 'Open Disk Utility > View > Show All Devices. Select the whole drive, click Erase: name "Macintosh HD", format APFS, scheme GUID. This deletes everything on it!'),
        @("Install $name", 'Close Disk Utility and choose Reinstall macOS. The PC restarts several times: each time pick "macOS Installer", later "Macintosh HD", in OpenCore.'),
        @('Make it boot without the stick', 'When macOS works, start HackPE again and choose "macOS is already installed" on the Welcome page.')
    )
}

function Update-DonePage {
    Clear-List $DoneList
    $w = RowW $DoneList
    if ($State.Mode -eq 'install') {
        $Title.Text = 'Your installer is ready!'
        $Subtitle.Text = "Follow these steps. They are also saved on the stick as NEXT-STEPS.txt."
        $i = 1
        foreach ($s in (Get-NextStepsText)) { $DoneList.Controls.Add((New-Row 'info' ("{0}. {1}" -f $i, $s[0]) $s[1] $w)); $i++ }
        $BiosList.Items.Clear(); foreach ($b in (Get-BiosChecklist)) { [void]$BiosList.Items.Add($b) }
        $BiosList.Visible = $true; $SaveStepsBtn.Visible = $true
        Save-NextSteps $true
    } else {
        $Title.Text = 'All set!'
        $Subtitle.Text = 'macOS can now start without the USB stick.'
        $DoneList.Controls.Add((New-Row 'ok' '1. Remove the USB sticks and restart' 'OpenCore starts from the internal drive. If the PC goes straight to Windows, choose "OpenCore" in the BIOS boot order.' $w))
        $DoneList.Controls.Add((New-Row 'info' '2. Map your USB ports (recommended)' 'Expert mode > Post-install & tools > USBToolBox. Fixes sleep and port issues.' $w))
        $DoneList.Controls.Add((New-Row 'info' '3. Fine-tune later' 'Custom SSDTs (SSDTTime), removing verbose boot (-v), audio layout - see the technical notes.' $w))
        $DoneList.Controls.Add((New-Row 'warn' 'Before signing in to iCloud' 'Check the serial number is NOT already registered at checkcoverage.apple.com (see the technical notes).' $w))
        $BiosList.Visible = $false; $SaveStepsBtn.Visible = $false
    }
}

function Save-NextSteps([bool]$quiet) {
    if (-not $State.UsbRoot -or -not (Test-Path $State.UsbRoot)) { if (-not $quiet) { Show-Help 'The installer stick was not found.' }; return }
    $t = @("HackPE - next steps", "====================", '')
    $i = 1; foreach ($s in (Get-NextStepsText)) { $t += "$i. $($s[0])"; $t += "   $($s[1])"; $t += ''; $i++ }
    $t += 'BIOS checklist:'; foreach ($b in (Get-BiosChecklist)) { $t += "  [ ] $b" }
    try { Set-Content -Path (Join-Path $State.UsbRoot 'NEXT-STEPS.txt') -Value $t -Encoding UTF8; if (-not $quiet) { Show-Help "Saved to $($State.UsbRoot)NEXT-STEPS.txt" } } catch {}
}
$SaveStepsBtn.Add_Click({ Save-NextSteps $false })
$NotesBtn.Add_Click({
    $n = $null
    foreach ($c in @((Join-Path $Work "EFI-$($State.Target)\BUILD-NOTES.txt"), $(if ($State.UsbRoot) { Join-Path $State.UsbRoot 'BUILD-NOTES.txt' }))) { if ($c -and (Test-Path $c)) { $n = $c; break } }
    if ($n) { Start-Process notepad.exe $n } else { Show-Help 'Technical notes are created when an EFI is built.' }
})

# ======================================================== navigation/help ====
$Help = @{
    welcome  = "HackPE needs:`n`n- Internet, to download macOS from Apple (Ethernet works best; Android/iPhone USB tethering also works).`n- A second USB stick of 8 GB or more.`n`nNo network? Get the Windows driver (.inf) for your network card on another PC, copy it to a USB stick and use 'Load network driver'."
    pc       = "Green = works, yellow = works with limits, red = not usable.`n`nThe most important part is graphics: macOS needs a supported Intel iGPU or AMD Radeon card. NVIDIA RTX/GTX 16xx and newer have no macOS drivers.`n`nWi-Fi and audio problems do not prevent installation."
    version  = "We recommend the newest version this PC supports.`n`nmacOS 26 Tahoe is the last release for Intel/x86 - the best long-term choice, but onboard audio needs a post-install fix.`nmacOS 15 Sequoia is the safest choice if you need onboard audio immediately."
    usb      = "Any USB 2.0/3.0 stick of 8 GB or more works. It is erased and formatted as FAT32.`n`nIt must NOT be the HackPE stick. Use a port directly on the motherboard."
    progress = "Building and formatting take about a minute. Downloading macOS takes 5-30 minutes depending on your connection.`n`nIf the download fails, click Try again - the stick does not need to be erased again manually."
    done     = "Common first-boot problems:`n- Stuck on text early: re-check the BIOS checklist (Secure Boot, CSM, Above 4G).`n- 'Prohibited' sign: try another USB port (USB 2.0 port if available).`n- Black screen: connect the monitor to the GPU that HackPE said drives the display.`n`nThe technical notes contain everything that was configured."
    espdisk  = "Pick the drive you erased as 'Macintosh HD' during installation. HackPE only writes the EFI\\OC and EFI\\BOOT folders on its EFI partition (old ones are kept as .bak)."
}
$HelpLink.Add_LinkClicked({ Show-Help $Help[$State.Page] })

function Update-Side {
    foreach ($c in @($SideSteps.Controls)) { $c.Dispose() }; $SideSteps.Controls.Clear()
    $flow = $Flows[$State.Mode]; $idx = [array]::IndexOf($flow, $State.Page); $y = 0; $i = 0
    foreach ($k in $flow) {
        $l = New-Object Windows.Forms.Label
        $mark = if ($i -lt $idx) { 'OK' } elseif ($i -eq $idx) { '>' } else { [string]($i + 1) }
        $nm = $StepNames[$k]; if ($k -eq 'progress' -and $State.Mode -eq 'post') { $nm = 'Install' }
        $l.Text = "  $mark   $nm"; $l.Location = "0,$y"; $l.Size = '220,40'; $l.TextAlign = 'MiddleLeft'
        if ($i -eq $idx) { $l.Font = $Fnt.SideB; $l.ForeColor = [Drawing.Color]::White; $l.BackColor = [Drawing.Color]::FromArgb(45, 55, 80) }
        else { $l.Font = $Fnt.Side; $l.ForeColor = $(if ($i -lt $idx) { [Drawing.Color]::FromArgb(129, 199, 132) } else { $Col.SideText }) }
        $SideSteps.Controls.Add($l); $y += 42; $i++
    }
}

function Update-Nav {
    $p = $State.Page
    $BackBtn.Visible = $true; $BackBtn.Enabled = $true; $BackBtn.Text = 'Back'
    $NextBtn.Visible = $true; $NextBtn.Text = 'Next'
    $ok = $true
    switch ($p) {
        'welcome' { $BackBtn.Visible = $false; $ok = [bool]$State.Pre -and -not $State.Job
                    if ($ok -and $ModeInstall.Checked -and -not $State.Pre.opencore) { $ok = $false } }
        'pc'      { $ok = [bool]$State.Rec -and -not $State.Job
                    if ($ok) { $pl = $State.Plans[$State.Rec]; if ($pl -and -not ($Versions | Where-Object { $pl.compat.($_[0]) -notlike 'no*' })) { $ok = $AnywayBox.Checked } } }
        'version' { $pl = $State.Plans[$State.Target]; $ok = [bool]$State.Target -and $pl -and (-not $pl.blockers -or $ForceBox.Checked) }
        'usb'     { $NextBtn.Text = 'Create installer'; $ok = ($null -ne $State.Disk) -and $EraseBox.Checked }
        'espdisk' { $NextBtn.Text = 'Install'; $ok = ($null -ne $State.EspDisk) -and $EspConfirm.Checked -and ($SrcStick.Checked -or $SrcBuild.Checked) }
        'progress' {
            if ($State.Busy) { $BackBtn.Text = 'Cancel'; $NextBtn.Visible = $false }
            elseif ($State.Failed) { $NextBtn.Text = 'Try again'; $ok = $true }
            else { $NextBtn.Visible = $false }
        }
        'done'    { $BackBtn.Visible = $false; $NextBtn.Text = $(if ($IsPE) { 'Restart now' } else { 'Close' }) }
    }
    $NextBtn.Enabled = $ok
}

function Show-Page([string]$key) {
    if ($State.Page -eq 'usb') { $UsbTimer.Stop() }
    foreach ($pp in $Pages.Values) { $pp.Visible = $false }
    $State.Page = $key; $Pages[$key].Visible = $true
    switch ($key) {
        'welcome' { $Title.Text = 'Welcome'; $Subtitle.Text = 'Install macOS on this PC in a few guided steps.'; Update-Welcome }
        'pc'      { $Title.Text = 'Your PC'; $Subtitle.Text = 'What macOS thinks of your hardware.'; if (-not $State.Rec -and -not $State.Job) { Start-Detect } else { Update-PcPage } }
        'version' { $Title.Text = 'Choose your macOS version'; $Subtitle.Text = 'The recommended version is pre-selected for you.'; Update-VersionPage }
        'usb'     { $Title.Text = 'Choose the USB stick'; $Subtitle.Text = 'The macOS installer will be written to it.'; Update-UsbPage $true; $UsbTimer.Start() }
        'espdisk' { $Title.Text = 'Make macOS start on its own'; $Subtitle.Text = 'Install OpenCore to the drive macOS is on.'; Update-EspPage }
        'progress' { }
        'done'    { Update-DonePage }
    }
    Update-Side; Update-Nav
}

$NextBtn.Add_Click({
    switch ($State.Page) {
        'welcome' {
            $State.Mode = $(if ($ModePost.Checked) { 'post' } else { 'install' })
            if ($State.Mode -eq 'install') { Show-Page 'pc' } else { Show-Page 'espdisk' }
        }
        'pc'      { Show-Page 'version' }
        'version' { Show-Page 'usb' }
        'usb'     {
            $msg = "Last check: ALL data on`n`n    $($State.DiskName)`n`nwill be erased. Continue?"
            if ([Windows.Forms.MessageBox]::Show($Form, $msg, 'Erase USB stick?', 'YesNo', 'Warning') -eq 'Yes') {
                Show-Page 'progress'; Start-CreateInstaller
            }
        }
        'espdisk' { Show-Page 'progress'; Start-InstallEsp }
        'progress' { if ($State.Failed) { if ($State.Mode -eq 'install') { Start-CreateInstaller } else { Start-InstallEsp } } }
        'done'    { if ($IsPE) { if ([Windows.Forms.MessageBox]::Show($Form, 'Restart the PC now?', 'Restart', 'YesNo') -eq 'Yes') { & wpeutil.exe reboot } } else { $Form.Close() } }
    }
})
$BackBtn.Add_Click({
    if ($State.Page -eq 'progress' -and $State.Busy) {
        if ([Windows.Forms.MessageBox]::Show($Form, 'Cancel the current operation?', 'Cancel', 'YesNo', 'Question') -eq 'Yes') { $State.Cancelled = $true; Stop-EngineJob }
        return
    }
    $flow = $Flows[$State.Mode]; $i = [array]::IndexOf($flow, $State.Page)
    if ($i -gt 0) { Show-Page $flow[$i - 1] }
})
$ModeInstall.Add_CheckedChanged({ Update-Nav })
$ModePost.Add_CheckedChanged({ Update-Nav })

$Form.Add_FormClosing({
    if ($State.Busy) {
        if ([Windows.Forms.MessageBox]::Show($Form, 'An operation is still running. Stop it and close?', 'HackPE', 'YesNo', 'Warning') -ne 'Yes') { $_.Cancel = $true; return }
        Stop-EngineJob
    }
})

$Form.Add_Shown({
    Show-Page 'welcome'
    if (-not $Py -or -not (Test-Path $Py)) { Show-Help 'Python was not found next to HackPE - the toolkit cannot run. Rebuild the HackPE media.'; return }
    Start-Preflight
})

[void]$Form.ShowDialog()
