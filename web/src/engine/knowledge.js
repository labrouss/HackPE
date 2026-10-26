// OpenCore knowledge used to explain and rank config differences.
import { PData, PInt, leInt } from './plist.js';

// How much a difference matters.
export const SEVERITY = {
  critical: { label: 'Boot-critical', rank: 0, blurb: 'Decides whether macOS boots at all.' },
  hardware: { label: 'Hardware-specific', rank: 1, blurb: 'Depends on the exact devices in each PC - check before copying.' },
  identity: { label: 'This PC only', rank: 2, blurb: 'Serial numbers and IDs - each PC keeps its own, never copy them.' },
  minor: { label: 'Cosmetic / debug', rank: 3, blurb: 'Boot picker, logging and similar. Safe either way.' },
  inactive: { label: 'Inactive', rank: 4, blurb: 'Disabled entries - they have no effect.' },
};

export const SECTION_INFO = {
  ACPI: 'SSDTs and ACPI patches that make the firmware tables look like a Mac.',
  Booter: 'Memory-map fixes applied before macOS starts.',
  DeviceProperties: 'Per-device properties: graphics framebuffer, audio layout, device spoofs.',
  Kernel: 'Kexts, kernel patches and quirks.',
  Misc: 'Boot picker, security and debug settings.',
  NVRAM: 'boot-args, SIP (csr-active-config) and other NVRAM variables.',
  PlatformInfo: 'The Mac model (SMBIOS) and this PC\'s serial numbers.',
  UEFI: 'Firmware drivers, input/output and UEFI quirks.',
};

// --------------------------------------------------------------- quirks ----
export const QUIRKS = {
  'ACPI/Quirks/FadtEnableReset': 'Enables the FADT reset register - fixes reboot/shutdown on some laptops.',
  'ACPI/Quirks/RebaseRegions': 'Relocates ACPI memory regions. Only needed for some old or custom DSDT setups; usually off.',
  'ACPI/Quirks/NormalizeHeaders': 'Cleans ACPI table headers - only needed for Mojave and older on some boards.',
  'ACPI/Quirks/ResetLogoStatus': 'Clears the BGRT boot-logo status.',
  'ACPI/Quirks/SyncTableIds': 'Keeps patched table IDs in sync with the originals.',
  'Booter/Quirks/AvoidRuntimeDefrag': 'Protects UEFI runtime services - needed on nearly every PC.',
  'Booter/Quirks/DevirtualiseMmio': 'Frees MMIO regions for the kernel slide. Needed on many Ice/Tiger Lake laptops and HEDT boards.',
  'Booter/Quirks/EnableSafeModeSlide': 'Lets the kernel slide work in safe mode.',
  'Booter/Quirks/EnableWriteUnprotector': 'Old memory-protection workaround - replaced by RebuildAppleMemoryMap + SyncRuntimePermissions on modern firmware.',
  'Booter/Quirks/ProtectUefiServices': 'Protects UEFI services from firmware overrides - needed on Z390 and some Ice/Tiger Lake laptops.',
  'Booter/Quirks/ProvideCustomSlide': 'Picks a free kernel slide - fixes "Couldn\'t allocate runtime area".',
  'Booter/Quirks/RebuildAppleMemoryMap': 'Builds a memory map macOS accepts - modern firmware needs it.',
  'Booter/Quirks/SetupVirtualMap': 'Virtual memory map fix-up - on for most desktops, off for many 10th-gen+ laptops.',
  'Booter/Quirks/SyncRuntimePermissions': 'Fixes runtime memory permissions - needed on modern firmware.',
  'Booter/Quirks/ProtectMemoryRegions': 'Protects firmware memory regions - Chromebooks/Coreboot.',
  'Booter/Quirks/ForceExitBootServices': 'Retries ExitBootServices - old firmware only.',
  'Booter/Quirks/ResizeAppleGpuBars': 'Resizable BAR for macOS (-1 = off).',
  'Booter/Quirks/ClearTaskSwitchBit': 'Workaround for some 32-bit firmware (newer OpenCore key).',
  'Booter/Quirks/FixupAppleEfiImages': 'Fixes old Apple EFI images (newer OpenCore key).',
  'Kernel/Quirks/AppleCpuPmCfgLock': 'CFG-Lock workaround for Ivy Bridge and older. Should be off on Haswell+.',
  'Kernel/Quirks/AppleXcpmCfgLock': 'CFG-Lock workaround for Haswell+ - needed when the BIOS cannot unlock CFG-Lock.',
  'Kernel/Quirks/AppleXcpmExtraMsrs': 'XCPM on unsupported CPUs (HEDT, Pentium/Celeron).',
  'Kernel/Quirks/CustomSMBIOSGuid': 'Pairs with UpdateSMBIOSMode=Custom (Dell/Sony/VAIO firmware).',
  'Kernel/Quirks/DisableIoMapper': 'Stops macOS using VT-d. Alternative: keep VT-d and drop DMAR reserved regions.',
  'Kernel/Quirks/DisableIoMapperMapping': 'Disables VT-d mapping only (newer OpenCore key).',
  'Kernel/Quirks/DisableLinkeditJettison': 'Lets Lilu work reliably on macOS 11+.',
  'Kernel/Quirks/DisableRtcChecksum': 'Stops AppleRTC writing the checksum - fixes BIOS reset on some boards.',
  'Kernel/Quirks/LapicKernelPanic': 'Fixes LAPIC panics - common on HP laptops.',
  'Kernel/Quirks/PanicNoKextDump': 'Shorter panic logs.',
  'Kernel/Quirks/PowerTimeoutKernelPanic': 'Fixes power-change timeout panics (audio/macOS 10.15+).',
  'Kernel/Quirks/ProvideCurrentCpuInfo': 'Correct CPU info for AMD / VMs.',
  'Kernel/Quirks/ThirdPartyDrives': 'TRIM for non-Apple SATA SSDs.',
  'Kernel/Quirks/XhciPortLimit': 'Lifts the 15-port USB limit - temporary, only while mapping USB.',
  'Kernel/Quirks/SetApfsTrimTimeout': 'APFS trim timeout (-1 = default).',
  'UEFI/Quirks/IgnoreInvalidFlexRatio': 'Ignores an invalid FLEX_RATIO MSR - Sandy Bridge to Coffee Lake.',
  'UEFI/Quirks/ReleaseUsbOwnership': 'Takes USB from the firmware - fixes USB on some boards.',
  'UEFI/Quirks/RequestBootVarRouting': 'Keeps macOS boot-disk choice inside OpenCore - recommended.',
  'UEFI/Quirks/UnblockFsConnect': 'Lets OpenCore see disks on HP firmware - needed on most HP laptops.',
  'UEFI/Quirks/EnableVectorAcceleration': 'Faster SHA hashing with AVX.',
  'UEFI/Quirks/ResizeGpuBars': 'Resizable BAR for the firmware (-1 = off).',
  'UEFI/Quirks/ExitBootServicesDelay': 'Delay before ExitBootServices - for some ASUS boards.',
  'UEFI/ProtocolOverrides/FirmwareVolume': 'Needed for FileVault on most PCs.',
  'UEFI/APFS/MinDate': 'Minimum APFS driver date (-1 = any) - needed for macOS older than Big Sur.',
  'UEFI/APFS/MinVersion': 'Minimum APFS driver version (-1 = any).',
  'Misc/Security/SecureBootModel': 'Apple Secure Boot. "Disabled" is the safe choice for most hackintoshes.',
  'Misc/Security/Vault': 'OpenCore vault signing - "Optional" unless you signed your EFI.',
  'Misc/Security/DmgLoading': 'Which DMGs OpenCore may boot - "Signed" is fine for macOS recovery.',
  'Misc/Security/ScanPolicy': 'Which disks/file systems the picker shows (0 = everything).',
  'Misc/Security/ExposeSensitiveData': 'What OpenCore exposes to macOS (version, boot path).',
  'Misc/Security/AllowSetDefault': 'Ctrl+Enter sets the default boot entry.',
  'Misc/Security/HaltLevel': 'Debug halt level.',
  'Misc/Boot/PickerMode': 'Builtin = text picker; External = OpenCanopy GUI (needs OpenCanopy.efi + Resources).',
  'Misc/Boot/HideAuxiliary': 'Hides recovery/tools entries until Space is pressed.',
  'Misc/Boot/LauncherOption': 'Registers OpenCore in the firmware boot menu ("Full" adds a BOOTx64 entry).',
  'Misc/Debug/Target': 'Where OpenCore logs (0 = nowhere, 3 = screen + log, 67 = file too).',
  'Misc/Debug/AppleDebug': 'Writes boot.efi log to the kernel log.',
  'NVRAM/WriteFlash': 'Saves NVRAM variables (boot-args, SIP) to firmware flash.',
  'PlatformInfo/UpdateSMBIOSMode': '"Create" for most PCs; "Custom" for Dell/Sony/VAIO (with CustomSMBIOSGuid).',
  'PlatformInfo/Automatic': 'Build SMBIOS from Generic only. With true, DataHub/PlatformNVRAM/SMBIOS are ignored.',
  'PlatformInfo/Generic/SystemProductName': 'The Mac model (SMBIOS) - decides which macOS versions and power management you get.',
  'PlatformInfo/Generic/SpoofVendor': 'Reports "Acidanthera" as vendor instead of the PC maker.',
  'Kernel/Emulate/Cpuid1Data': 'CPUID spoof: makes an unsupported CPU look like a supported generation. Must fit the target\'s real CPU.',
  'Kernel/Emulate/Cpuid1Mask': 'Which CPUID bits the spoof replaces.',
  'Kernel/Emulate/DummyPowerManagement': 'Disables CPU power management - AMD and some unsupported CPUs.',
  'NVRAM/Add/7C436110-AB2A-4BBB-A880-FE41995C9F82/csr-active-config': 'System Integrity Protection. 0x803 is typical for root patching; 0 keeps SIP on.',
  'NVRAM/Add/7C436110-AB2A-4BBB-A880-FE41995C9F82/prev-lang:kbd': 'Language and keyboard of the installer/recovery.',
  'UEFI/Audio/AudioSupport': 'Boot chime / picker audio (needs AudioDxe.efi).',
  'UEFI/Output/ProvideConsoleGop': 'Needed to see the picker on most PCs.',
};

// ------------------------------------------------------------- decoders ----
const CSR_FLAGS = [
  [0x1, 'untrusted kexts'], [0x2, 'unrestricted FS'], [0x4, 'task_for_pid'], [0x8, 'kernel debugger'],
  [0x10, 'Apple internal'], [0x20, 'unrestricted dtrace'], [0x40, 'unrestricted NVRAM'], [0x80, 'device config'],
  [0x100, 'any recovery OS'], [0x200, 'unapproved kexts'], [0x400, 'executable policy override'], [0x800, 'unauthenticated root'],
];
export function decodeCsr(v) {
  const n = v instanceof PData ? Number(leInt(v)) : null;
  if (n === null) return null;
  if (n === 0) return 'SIP fully enabled (0x0)';
  const on = CSR_FLAGS.filter(([b]) => n & b).map(([, s]) => s);
  const known = n === 0x803 ? ' - typical for root patching (OCLP)' : n === 0xFFF || n === 0xFEF ? ' - SIP fully disabled' : '';
  return `SIP 0x${n.toString(16).toUpperCase()}${known}: ${on.join(', ')}`;
}

const SCAN = [[0x1, 'FS lock'], [0x2, 'device lock'], [0x100, 'APFS'], [0x200, 'HFS'], [0x400, 'ESP'], [0x800, 'NTFS'],
  [0x1000, 'Linux root'], [0x2000, 'Linux data'], [0x4000, 'XBOOTLDR'], [0x10000, 'SATA'], [0x20000, 'SAS'], [0x40000, 'SCSI'],
  [0x80000, 'NVMe'], [0x100000, 'ATAPI'], [0x200000, 'USB'], [0x400000, 'FireWire'], [0x800000, 'SD card'], [0x1000000, 'PCI']];
export function decodeScanPolicy(v) {
  if (!(v instanceof PInt)) return null;
  const n = Number(v.v);
  if (n === 0) return 'scan everything (0)';
  return `0x${n.toString(16).toUpperCase()}: ` + SCAN.filter(([b]) => n & b).map(([, s]) => s).join(', ');
}

const CPUID = {
  0x206A7: 'Sandy Bridge', 0x306A9: 'Ivy Bridge', 0x306C3: 'Haswell', 0x40661: 'Haswell (Crystal Well)', 0x306D4: 'Broadwell',
  0x506E3: 'Skylake', 0x406E3: 'Skylake (mobile)', 0x906E9: 'Kaby Lake', 0x806E9: 'Kaby Lake (mobile)', 0x906EA: 'Coffee Lake',
  0x906EB: 'Coffee Lake', 0x906EC: 'Coffee Lake', 0x906ED: 'Coffee Lake Refresh', 0x806EA: 'Coffee Lake (mobile)',
  0xA0655: 'Comet Lake', 0xA0653: 'Comet Lake', 0xA0660: 'Comet Lake (mobile)', 0x706E5: 'Ice Lake', 0x806C1: 'Tiger Lake',
  0x806C2: 'Tiger Lake', 0x90672: 'Alder Lake', 0xB0671: 'Raptor Lake', 0x306F2: 'Haswell-E', 0x50654: 'Skylake-X',
};
export function decodeCpuid(v) {
  if (!(v instanceof PData) || v.bytes.length < 4) return null;
  const eax = Number(leInt(new PData(v.bytes.slice(0, 4))));
  if (!eax) return 'no CPUID spoof';
  return `CPUID spoof as 0x${eax.toString(16).toUpperCase()}${CPUID[eax] ? ' (' + CPUID[eax] + ')' : ''}`;
}

const IGPU = {
  0x0166: 'Ivy Bridge', 0x0412: 'Haswell', 0x0416: 'Haswell', 0x1616: 'Broadwell', 0x1912: 'Skylake', 0x1916: 'Skylake',
  0x5912: 'Kaby Lake', 0x5916: 'Kaby Lake', 0x591B: 'Kaby Lake', 0x3E92: 'Coffee Lake', 0x3E9B: 'Coffee Lake', 0x3EA5: 'Coffee Lake',
  0x9BC8: 'Comet Lake', 0x9B41: 'Comet Lake', 0x8A52: 'Ice Lake', 0x8A5C: 'Ice Lake', 0x9A49: 'Tiger Lake (Xe)', 0x9A40: 'Tiger Lake (Xe)',
  0x9A60: 'Tiger Lake (Xe)', 0x9A78: 'Tiger Lake (Xe)', 0x4680: 'Alder Lake', 0x46A6: 'Alder Lake (Xe)',
};
const AUDIO_IDS = { 0x34C8: 'Ice Lake-LP HDA', 0xA0C8: 'Tiger Lake-LP HDA', 0xA348: 'Cannon Lake HDA', 0xA170: '100-series HDA' };
export function decodeProperty(name, v) {
  const n = v instanceof PData ? leInt(v) : v instanceof PInt ? BigInt(v.v) : null;
  if (n === null) return null;
  const hex = (x, w) => '0x' + x.toString(16).toUpperCase().padStart(w, '0');
  if (name === 'AAPL,ig-platform-id' || name === 'AAPL,snb-platform-id') {
    const dev = Number((n >> 16n) & 0xFFFFn);
    return `framebuffer ${hex(n, 8)}${IGPU[dev] ? ' - ' + IGPU[dev] : ''}`;
  }
  if (name === 'device-id') {
    const d = Number(n & 0xFFFFn);
    return `device-id ${hex(d, 4)}${IGPU[d] ? ' - ' + IGPU[d] + ' iGPU' : AUDIO_IDS[d] ? ' - ' + AUDIO_IDS[d] : ''}`;
  }
  if (name === 'layout-id') return `audio layout ${n}`;
  if (name === 'framebuffer-stolenmem' || name === 'framebuffer-fbmem') return `${Number(n) / 1048576} MB`;
  if (name === 'framebuffer-unifiedmem') return `${Number(n) / 1048576} MB VRAM`;
  if (name === 'built-in' || name === 'RMVP') return `${n}`;
  return null;
}

export const BOOT_ARGS = {
  '-v': 'verbose boot', 'debug': 'kernel debug flags', 'keepsyms': 'symbols in panics', 'msgbuf': 'larger kernel log',
  'alcid': 'AppleALC layout', 'alc-delay': 'AppleALC start delay', '-wegnoegpu': 'disable the discrete GPU',
  '-igfxdvmt': 'WhateverGreen: DVMT pre-alloc fix', '-disablegfxfirmware': 'iGPU firmware loading off', '-allow3d': 'force 3D acceleration',
  'watchdog': 'watchdog off', '-vi2c-force-polling': 'VoodooI2C polling mode', '-lilubetaall': 'Lilu plugins on unsupported macOS',
  '-llilubetaall': 'Lilu beta (typo of -lilubetaall?)', '-amfipassbeta': 'AMFIPass on newer macOS', 'ipc_control_port_options': 'fixes app crashes with SIP off',
  '-ibtcompatbeta': 'IntelBTPatcher on newer macOS', '-wexbeta': 'WhateverXe beta', '-wexdbg': 'WhateverXe debug', '-wex-noaccel': 'WhateverXe without acceleration',
  '-tb2pciout': 'Thunderbolt PCI option', 'agdpmod': 'GPU power-management fix', '-radvesa': 'AMD GPU in VESA mode', 'revpatch': 'RestrictEvents patches',
  '-no_compat_check': 'boot unsupported SMBIOS', 'brcmfx-country': 'Broadcom Wi-Fi country',
};
export const DEBUG_ARGS = new Set(['-v', 'debug', 'keepsyms', 'msgbuf', '-wexdbg', 'watchdog']);
export function bootArgTokens(s) {
  return String(s || '').trim().split(/\s+/).filter(Boolean);
}
export function argName(t) { return t.split('=')[0]; }

// ---------------------------------------------------------------- kexts ----
// Hardware-dependent kexts: copying them only makes sense if the target PC has that device.
export const HW_KEXTS = [
  [/^(airportitlwm|itlwm)/i, 'Intel Wi-Fi'], [/^airportbrcmfixup|^brcm/i, 'Broadcom Wi-Fi/BT'],
  [/^intelbt|^intelbluetooth|^bluetoolfixup|^ibt/i, 'Intel Bluetooth'], [/^voodooi2c|^voodoogpio|^voodooinput/i, 'I2C touchpad'],
  [/^voodoops2|^voodoormi|^voodoosmbus/i, 'PS/2 keyboard/touchpad'], [/^(usbmap|utbmap|utbdefault|usbtoolbox|usbinjectall)/i, 'USB port map'],
  [/^(intelmausi|realtekrtl8111|lucyrtl8125|appleigb|appleigc|atherose2200|intellucy|nullethernet)/i, 'Ethernet'],
  [/^(smcbatterymanager|ecenabler|acpibatterymanager)/i, 'laptop battery'], [/^(smclightsensor|brightnesskeys)/i, 'laptop display'],
  [/^(voodoohda|applealc)/i, 'audio'], [/^(whateverxe|whatevergreen|nootedred|nootrx|radeonsensor)/i, 'graphics'],
  [/^nvmefix/i, 'NVMe'], [/^cputscsync|^voodootscsync/i, 'CPU TSC sync'], [/^smcprocessor|^smcsuperio|^smcdellsensors/i, 'sensors'],
];
export const CORE_KEXTS = /^(lilu|virtualsmc|whatevergreen|applealc|restrictevents|amfipass|cryptexfixup)\.kext$/i;
export const CONFLICTS = [
  [/^applealc\.kext$/i, /^voodoohda\.kext$/i, 'AppleALC and VoodooHDA both drive the audio codec - keep one.'],
  [/^itlwm\.kext$/i, /^airportitlwm/i, 'itlwm and AirportItlwm are alternatives for Intel Wi-Fi - enable one per macOS version.'],
  [/^(utbmap|usbmap)\.kext$/i, /^utbdefault\.kext$/i, 'UTBDefault is a generic USB map - remove it once you have your own UTBMap/USBMap.'],
  [/^hfsplus\.efi$/i, /^openhfsplus\.efi$/i, 'HfsPlus.efi and OpenHfsPlus.efi do the same job - load one.'],
  [/^intelbtpatcher\.kext$/i, /^bluetoolfixup\.kext$/i, 'IntelBTPatcher and BlueToolFixup overlap on newer macOS - check the IntelBluetoothFirmware docs.'],
];

export function kextLeaf(bundlePath) {
  const parts = String(bundlePath || '').split('/');
  return parts[parts.length - 1];
}
export function kextParent(bundlePath) {
  const m = String(bundlePath || '').match(/^(.+?\.kext)\/Contents\/Plug[Ii]ns\//);
  return m ? m[1] : null;
}
export function kextHardware(bundlePath) {
  const leaf = kextLeaf(bundlePath);
  const hit = HW_KEXTS.find(([re]) => re.test(leaf));
  return hit ? hit[1] : null;
}

// SSDTs dumped from or written for one machine (vs generic Dortania/SSDTTime ones)
export function ssdtIsMachineSpecific(path) {
  return /^(dsdt|dmar|ssdt-(tbt|alc|als0|dmac|gpi|tpd|i2c|sbus|mchc|usb-reset|xhc|gprw|uprw|lan|nvme|gpu|dgpu|igpu|bat|batt)|ssdt-[0-9a-f]+)/i.test(path);
}

export const isIdentityPath = (p) =>
  /^PlatformInfo\/(Generic|DataHub|PlatformNVRAM|SMBIOS)\/(SystemSerialNumber|MLB|SystemUUID|ROM|BoardSerialNumber|ChassisSerialNumber)$/.test(p);

export function decodeValue(path, v) {
  if (/csr-active-config$/.test(path)) return decodeCsr(v);
  if (path === 'Misc/Security/ScanPolicy') return decodeScanPolicy(v);
  if (path === 'Kernel/Emulate/Cpuid1Data') return decodeCpuid(v);
  const m = path.match(/^DeviceProperties\/Add\/.+\/([^/]+)$/);   // device paths contain '/' themselves
  if (m) return decodeProperty(m[1], v);
  if (/^Misc\/Debug\/(DisplayLevel|HaltLevel)$|HaltLevel$/.test(path) && v instanceof PInt) return '0x' + Number(v.v).toString(16).toUpperCase();
  return null;
}
