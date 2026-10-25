<#
  HackPE hardware probe. Works in WinPE (needs WinPE-WMI + WinPE-PowerShell)
  and on full Windows. Does not depend on vendor drivers: devices are
  identified by PCI vendor/device/class ids, which exist even when Windows
  has no driver for them (common in WinPE).
#>
param([string]$Out = "")
$ErrorActionPreference = 'SilentlyContinue'

function HexInt($s) { if ($s) { [Convert]::ToInt32($s, 16) } else { $null } }

$r = [ordered]@{ Version = 1; Collected = (Get-Date).ToString('s'); IsWinPE = $false }
if (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\MiniNT') { $r.IsWinPE = $true }

# ---- CPU ------------------------------------------------------------------
$p = Get-CimInstance Win32_Processor | Select-Object -First 1
$fam = $null; $mod = $null; $step = $null
if ($p.Caption -match 'Family (\d+) Model (\d+) Stepping (\d+)') { $fam = [int]$Matches[1]; $mod = [int]$Matches[2]; $step = [int]$Matches[3] }
$sockets = @(Get-CimInstance Win32_Processor).Count
$r.Cpu = [ordered]@{
    Name = ($p.Name -replace '\s+', ' ').Trim(); Manufacturer = $p.Manufacturer
    Cores = [int]$p.NumberOfCores; Threads = [int]$p.NumberOfLogicalProcessors; Sockets = $sockets
    Family = $fam; Model = $mod; Stepping = $step; VirtualizationFirmware = $p.VirtualizationFirmwareEnabled
}

# ---- System / board / firmware -------------------------------------------
$cs = Get-CimInstance Win32_ComputerSystem
$bb = Get-CimInstance Win32_BaseBoard
$bios = Get-CimInstance Win32_BIOS
$enc = Get-CimInstance Win32_SystemEnclosure | Select-Object -First 1
$bat = @(Get-CimInstance Win32_Battery)
$fw = 'Unknown'
try {
    if ($r.IsWinPE) { & wpeutil UpdateBootInfo | Out-Null }
    $t = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name PEFirmwareType).PEFirmwareType
    if ($t -eq 2) { $fw = 'UEFI' } elseif ($t -eq 1) { $fw = 'BIOS' }
} catch {}
if ($fw -eq 'Unknown' -and $env:firmware_type) { $fw = $env:firmware_type }
$sb = $null; try { $sb = Confirm-SecureBootUEFI } catch {}
$r.System = [ordered]@{
    Manufacturer = $cs.Manufacturer; Model = $cs.Model; PCSystemType = [int]$cs.PCSystemType
    ChassisTypes = @($enc.ChassisTypes); HasBattery = ($bat.Count -gt 0)
    BoardManufacturer = $bb.Manufacturer; BoardProduct = $bb.Product
    BiosVendor = $bios.Manufacturer; BiosVersion = $bios.SMBIOSBIOSVersion
    Firmware = $fw; SecureBoot = $sb; RamGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
}

# ---- PCI devices (driver-independent) -------------------------------------
$all = Get-CimInstance Win32_PnPEntity
$pci = @()
foreach ($d in ($all | Where-Object { $_.DeviceID -like 'PCI\*' })) {
    if ($d.DeviceID -notmatch 'VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})') { continue }
    $ven = $Matches[1]; $dev = $Matches[2]
    $sub = $null; if ($d.DeviceID -match 'SUBSYS_([0-9A-F]{8})') { $sub = $Matches[1] }
    $cc = $null
    foreach ($c in @($d.CompatibleID) + @($d.HardwareID)) { if ($c -match 'CC_([0-9A-F]{4,6})') { $cc = $Matches[1]; break } }
    $loc = $null
    if ($cc -like '03*') {
        # PCI path for DeviceProperties, e.g. PCIROOT(0)#PCI(0100)#PCI(0000)
        try {
            $res = Invoke-CimMethod -InputObject $d -MethodName GetDeviceProperties -Arguments @{ devicePropertyKeys = @('DEVPKEY_Device_LocationPaths') }
            $loc = @($res.deviceProperties | Where-Object { $_.KeyName -eq 'DEVPKEY_Device_LocationPaths' } | ForEach-Object { $_.Data })[0]
        } catch {}
    }
    $pci += [ordered]@{ Ven = (HexInt $ven); Dev = (HexInt $dev); VenHex = $ven; DevHex = $dev; SubSys = $sub
                        Class = $cc; Name = $d.Name; DeviceID = $d.DeviceID; Error = [int]$d.ConfigManagerErrorCode
                        LocationPath = $loc }
}
$r.Pci = $pci

# ---- HD-Audio codecs (only visible when the HDA bus driver is present) ----
$r.Hda = @()
foreach ($d in ($all | Where-Object { $_.DeviceID -like 'HDAUDIO\FUNC_01*' })) {
    if ($d.DeviceID -match 'VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})') {
        $r.Hda += [ordered]@{ Ven = (HexInt $Matches[1]); Dev = (HexInt $Matches[2]); Name = $d.Name }
    }
}

# ---- ACPI input devices (laptops) -----------------------------------------
$r.Input = @()
foreach ($d in ($all | Where-Object { $_.DeviceID -like 'ACPI\*' })) {
    $ids = (@($d.HardwareID) + @($d.CompatibleID)) -join ' '
    if ($ids -match 'PNP0C50|PNP0F13|PNP0303|SYN|ELAN|ALPS|ATML|MSFT0001') {
        $r.Input += [ordered]@{ DeviceID = $d.DeviceID; Ids = $ids; Name = $d.Name; I2C = ($ids -match 'PNP0C50') }
    }
}

# ---- Network adapters (MAC for PlatformInfo ROM) --------------------------
$r.Nics = @()
foreach ($n in (Get-CimInstance Win32_NetworkAdapter | Where-Object { $_.PhysicalAdapter -and $_.MACAddress })) {
    $r.Nics += [ordered]@{ Name = $n.Name; Mac = $n.MACAddress; PnpId = $n.PNPDeviceID }
}

# ---- Disks -----------------------------------------------------------------
$r.Disks = @()
try {
    foreach ($d in (Get-Disk)) {
        $r.Disks += [ordered]@{ Number = $d.Number; Model = $d.FriendlyName; BusType = "$($d.BusType)"
                                SizeGB = [math]::Round($d.Size / 1GB, 1); Partition = "$($d.PartitionStyle)"; IsBoot = $d.IsBoot }
    }
} catch {
    foreach ($d in (Get-CimInstance Win32_DiskDrive)) {
        $r.Disks += [ordered]@{ Number = $d.Index; Model = $d.Model; BusType = $d.InterfaceType; SizeGB = [math]::Round($d.Size / 1GB, 1) }
    }
}

$json = $r | ConvertTo-Json -Depth 6
if ($Out) { [IO.File]::WriteAllText($Out, $json, (New-Object Text.UTF8Encoding $false)) } else { $json }
