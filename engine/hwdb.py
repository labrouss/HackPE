"""
HackPE hardware knowledge base.

Everything the planner knows about PC hardware vs. macOS lives here, so it can
be reviewed and updated without touching the engine logic.

Sources: Dortania OpenCore Install Guide, Acidanthera docs, AMD-OSX/AMD_Vanilla,
community findings for macOS 26 Tahoe (last Intel release).
"""
import re

# ---------------------------------------------------------------------------
# macOS catalog
# ---------------------------------------------------------------------------
# key, display, version, darwin major, fallback recovery (board, mlb, ostype)
MACOS = [
    ("highsierra", "High Sierra", "10.13", 17, ("Mac-7BA5B2D9E42DDD94", "00000000000J80300", None)),
    ("mojave",     "Mojave",      "10.14", 18, ("Mac-7BA5B2DFE22DDD8C", "00000000000KXPG00", None)),
    ("catalina",   "Catalina",    "10.15", 19, ("Mac-00BE6ED71E35EB86", "00000000000000000", None)),
    ("bigsur",     "Big Sur",     "11",    20, ("Mac-2BD1B31983FE1663", "00000000000000000", None)),
    ("monterey",   "Monterey",    "12",    21, ("Mac-E43C1C25D4880AD6", "00000000000000000", None)),
    ("ventura",    "Ventura",     "13",    22, ("Mac-B4831CEBD52A0C4C", "00000000000000000", None)),
    ("sonoma",     "Sonoma",      "14",    23, ("Mac-827FAC58A8FDFA22", "00000000000000000", None)),
    ("sequoia",    "Sequoia",     "15",    24, ("Mac-7BA5B2D9E42DDD94", "00000000000000000", None)),
    ("tahoe",      "Tahoe",       "26",    25, ("Mac-CFF7D910A743CAAF", "00000000000000000", "latest")),
]
MACOS_BY_KEY = {m[0]: m for m in MACOS}
DARWIN_NAME = {m[3]: m[1] for m in MACOS}
LATEST = "tahoe"


def macos(key):
    if key not in MACOS_BY_KEY:
        raise KeyError("Unknown macOS '%s'. Valid: %s" % (key, ", ".join(MACOS_BY_KEY)))
    k, name, ver, darwin, rec = MACOS_BY_KEY[key]
    return {"key": k, "name": name, "version": ver, "darwin": darwin, "recovery": rec}


# SMBIOS models and the newest Darwin each one is allowed to install.
SMBIOS_MAX = {
    "iMac14,4": 20, "iMac15,1": 20, "iMac16,2": 21, "iMac17,1": 21, "iMac18,1": 22, "iMac18,3": 22,
    "iMac19,1": 24, "iMac20,1": 25, "iMac20,2": 25, "iMacPro1,1": 24, "MacPro7,1": 25,
    "MacBookPro11,1": 20, "MacBookPro11,4": 21, "MacBookPro12,1": 21, "MacBookPro13,1": 21,
    "MacBookPro14,1": 22, "MacBookPro15,1": 24, "MacBookPro15,2": 24, "MacBookPro16,1": 25,
    "MacBookPro16,2": 25, "MacBookPro16,4": 25, "MacBookAir9,1": 24, "Macmini8,1": 24,
}
SMBIOS_MIN = {"iMac20,1": 19, "iMac20,2": 19, "MacPro7,1": 19, "MacBookPro16,1": 19,
              "MacBookPro16,2": 19, "MacBookPro16,4": 19, "iMac19,1": 18, "MacBookPro15,2": 17,
              "MacBookPro15,1": 17, "MacBookAir9,1": 19}

SMBIOS_FRIENDLY = {
    "iMac14,4": "iMac (21.5-inch, Mid 2014)", "iMac15,1": "iMac (Retina 5K, Late 2014)",
    "iMac16,2": "iMac (Retina 4K, Late 2015)", "iMac17,1": "iMac (Retina 5K, Late 2015)",
    "iMac18,1": "iMac (21.5-inch, 2017)", "iMac18,3": "iMac (Retina 5K, 2017)", "iMac19,1": "iMac (Retina 5K, 2019)",
    "iMac20,1": "iMac (Retina 5K, 2020)", "iMac20,2": "iMac (Retina 5K, 2020)", "iMacPro1,1": "iMac Pro (2017)",
    "MacPro7,1": "Mac Pro (2019)", "Macmini8,1": "Mac mini (2018)", "MacBookAir9,1": "MacBook Air (2020)",
    "MacBookPro11,1": "MacBook Pro (13-inch, 2014)", "MacBookPro11,4": "MacBook Pro (15-inch, 2015)",
    "MacBookPro12,1": "MacBook Pro (13-inch, 2015)", "MacBookPro13,1": "MacBook Pro (13-inch, 2016)",
    "MacBookPro14,1": "MacBook Pro (13-inch, 2017)", "MacBookPro15,1": "MacBook Pro (15-inch, 2018)",
    "MacBookPro15,2": "MacBook Pro (13-inch, 2018)", "MacBookPro16,1": "MacBook Pro (16-inch, 2019)",
    "MacBookPro16,2": "MacBook Pro (13-inch, 2020)", "MacBookPro16,4": "MacBook Pro (16-inch, 2019)",
}
MACOS_YEAR = {"highsierra": 2017, "mojave": 2018, "catalina": 2019, "bigsur": 2020, "monterey": 2021,
              "ventura": 2022, "sonoma": 2023, "sequoia": 2024, "tahoe": 2025}

# ---------------------------------------------------------------------------
# CPU platforms
# ---------------------------------------------------------------------------
# igpu_max/cpu_max: newest Darwin that works *natively* (no OCLP root patches)
PLATFORMS = {
    "sandybridge":  dict(name="Sandy Bridge (2nd gen)",  vendor="intel", cpu_max=21, igpu_max=17, status="legacy"),
    "ivybridge":    dict(name="Ivy Bridge (3rd gen)",    vendor="intel", cpu_max=21, igpu_max=20, status="legacy"),
    "haswell":      dict(name="Haswell (4th gen)",       vendor="intel", cpu_max=25, igpu_max=21, status="ok"),
    "broadwell":    dict(name="Broadwell (5th gen)",     vendor="intel", cpu_max=25, igpu_max=21, status="ok"),
    "skylake":      dict(name="Skylake (6th gen)",       vendor="intel", cpu_max=25, igpu_max=25, status="ok"),
    "kabylake":     dict(name="Kaby Lake (7th gen)",     vendor="intel", cpu_max=25, igpu_max=25, status="ok"),
    "coffeelake":   dict(name="Coffee Lake (8/9th gen)", vendor="intel", cpu_max=25, igpu_max=25, status="ok"),
    "cometlake":    dict(name="Comet Lake (10th gen)",   vendor="intel", cpu_max=25, igpu_max=25, status="ok"),
    "icelake":      dict(name="Ice Lake (10th gen mobile)", vendor="intel", cpu_max=25, igpu_max=25, status="ok"),
    "rocketlake":   dict(name="Rocket Lake (11th gen desktop)", vendor="intel", cpu_max=25, igpu_max=0, status="dgpu"),
    "tigerlake":    dict(name="Tiger Lake (11th gen mobile)", vendor="intel", cpu_max=25, igpu_max=0, status="unsupported"),
    "alderlake":    dict(name="Alder Lake (12th gen)",   vendor="intel", cpu_max=25, igpu_max=0, status="dgpu"),
    "raptorlake":   dict(name="Raptor Lake (13/14th gen)", vendor="intel", cpu_max=25, igpu_max=0, status="dgpu"),
    "coreultra":    dict(name="Core Ultra (Meteor/Arrow/Lunar Lake)", vendor="intel", cpu_max=0, igpu_max=0, status="unsupported"),
    "hedt":         dict(name="Intel HEDT / Xeon (X79/X99/X299)", vendor="intel", cpu_max=25, igpu_max=0, status="manual"),
    "amd_zen":      dict(name="AMD Ryzen / Threadripper (Zen 1-4)", vendor="amd", cpu_max=25, igpu_max=0, status="dgpu"),
    "amd_zen5":     dict(name="AMD Ryzen (Zen 5)",       vendor="amd", cpu_max=25, igpu_max=0, status="experimental"),
    "amd_legacy":   dict(name="AMD FX / Bulldozer / Jaguar", vendor="amd", cpu_max=25, igpu_max=0, status="manual"),
    "unknown":      dict(name="Unknown CPU", vendor="?", cpu_max=0, igpu_max=0, status="unsupported"),
}

INTEL_MODEL = {
    0x2A: "sandybridge", 0x3A: "ivybridge",
    0x3C: "haswell", 0x45: "haswell", 0x46: "haswell",
    0x3D: "broadwell", 0x47: "broadwell",
    0x4E: "skylake", 0x5E: "skylake",
    0xA5: "cometlake", 0xA6: "cometlake",
    0x7D: "icelake", 0x7E: "icelake",
    0x8C: "tigerlake", 0x8D: "tigerlake",
    0xA7: "rocketlake",
    0x97: "alderlake", 0x9A: "alderlake",
    0xB7: "raptorlake", 0xBA: "raptorlake", 0xBF: "raptorlake",
    0xAA: "coreultra", 0xAC: "coreultra", 0xBD: "coreultra", 0xC5: "coreultra", 0xC6: "coreultra",
    0x2D: "hedt", 0x3E: "hedt", 0x3F: "hedt", 0x4F: "hedt", 0x56: "hedt", 0x55: "hedt", 0x6A: "hedt", 0x8F: "hedt",
}

GEN_FROM_NUMBER = {2: "sandybridge", 3: "ivybridge", 4: "haswell", 5: "broadwell", 6: "skylake",
                   7: "kabylake", 8: "coffeelake", 9: "coffeelake", 10: "cometlake", 11: "rocketlake",
                   12: "alderlake", 13: "raptorlake", 14: "raptorlake"}


def intel_gen_from_name(name):
    """i7-8700K -> 8, i9-10900K -> 10, i5-1035G1 -> 10 (Ice Lake via suffix)."""
    m = re.search(r"i[3579]-(\d{4,5})([A-Z]*\d?)", name or "")
    if not m:
        return None, ""
    num, suffix = m.group(1), m.group(2)
    gen = int(num[:2]) if len(num) == 5 else int(num[0])
    return gen, suffix


def classify_cpu(cpu):
    """cpu: dict from detect.ps1 -> (platform_key, notes list)"""
    name = cpu.get("Name", "") or ""
    vendor = (cpu.get("Manufacturer") or "").lower()
    family, model = cpu.get("Family"), cpu.get("Model")
    notes = []
    if "amd" in vendor or "AMD" in name:
        if family == 0x17 or family == 0x19:
            return "amd_zen", notes
        if family == 0x1A:
            return "amd_zen5", notes
        if family in (0x15, 0x16):
            return "amd_legacy", notes
        if re.search(r"Ryzen|Threadripper|EPYC", name):
            return "amd_zen", notes
        return "amd_legacy", notes

    gen, suffix = intel_gen_from_name(name)
    plat = INTEL_MODEL.get(model)
    if model in (0x8E, 0x9E):           # Kaby/Coffee/Whiskey/Amber/Comet-U share these
        if gen == 7:
            plat = "kabylake"
        elif gen in (8, 9):
            plat = "coffeelake"
        elif gen == 10:
            plat = "cometlake"
        else:
            stepping = cpu.get("Stepping") or 0
            plat = "kabylake" if stepping <= 9 else "coffeelake"
    if plat is None and gen:
        plat = GEN_FROM_NUMBER.get(gen)
        if gen == 10 and suffix.startswith("G"):
            plat = "icelake"
        if gen == 11 and (suffix.startswith("G") or suffix.startswith("H")):
            plat = "tigerlake"
    if plat is None:
        if re.search(r"Core\(TM\) Ultra|Core Ultra", name):
            plat = "coreultra"
        elif re.search(r"Xeon", name):
            plat = "hedt"
            notes.append("Xeon detected: treated as HEDT/manual. Desktop Xeon E3 v3-v6 can use the matching consumer profile via --platform.")
        elif re.search(r"Pentium|Celeron", name):
            notes.append("Pentium/Celeron: needs CPUID spoofing; treated per microarchitecture where possible.")
            plat = "unknown"
        else:
            plat = "unknown"
    return plat, notes


# ---------------------------------------------------------------------------
# GPUs
# ---------------------------------------------------------------------------
# AMD device-id -> (name, status, notes)  status: native | spoof | nootrx | unsupported
AMD_GPUS = {
    0x67DF: ("Radeon RX 470/480/570/580/590 (Polaris 10/20)", "native", ""),
    0x67EF: ("Radeon RX 460/560 (Polaris 11/21)", "native", ""),
    0x67FF: ("Radeon RX 550/560 (Polaris 21)", "native", ""),
    0x699F: ("Radeon RX 550 'Lexa' (Polaris 12)", "spoof", "Lexa needs a device-id spoof to 67FF; not guaranteed."),
    0x6FDF: ("Radeon RX 580 2048SP", "spoof", "2048SP needs device-id spoof to 67DF."),
    0x687F: ("Radeon RX Vega 56/64 (Vega 10)", "native", ""),
    0x6863: ("Radeon Vega Frontier (Vega 10)", "native", ""),
    0x66AF: ("Radeon VII (Vega 20)", "native", ""),
    0x731F: ("Radeon RX 5600/5700 (Navi 10)", "native", "boot-arg agdpmod=pikera"),
    0x7340: ("Radeon RX 5500/5300 (Navi 14)", "native", "boot-arg agdpmod=pikera"),
    0x7341: ("Radeon Pro W5500 (Navi 14)", "native", "boot-arg agdpmod=pikera"),
    0x73BF: ("Radeon RX 6800/6800 XT/6900 XT (Navi 21)", "native", "boot-arg agdpmod=pikera"),
    0x73A5: ("Radeon RX 6950 XT (Navi 21)", "spoof", "May need device-id spoof to 73BF."),
    0x73AF: ("Radeon RX 6900 XT (Navi 21 XTXH)", "native", "boot-arg agdpmod=pikera"),
    0x73FF: ("Radeon RX 6600/6600 XT (Navi 23)", "native", "boot-arg agdpmod=pikera"),
    0x73EF: ("Radeon RX 6650 XT (Navi 23)", "spoof", "Needs device-id spoof to 73FF."),
    0x73E3: ("Radeon Pro W6600 (Navi 23)", "native", "boot-arg agdpmod=pikera"),
    0x73DF: ("Radeon RX 6700/6700 XT/6750 XT (Navi 22)", "nootrx", "Requires NootRX.kext (community, experimental)."),
    0x743F: ("Radeon RX 6400/6500 XT (Navi 24)", "unsupported", "Navi 24 has no macOS driver."),
    0x744C: ("Radeon RX 7900 (Navi 31)", "unsupported", "RDNA3 has no macOS driver."),
    0x747E: ("Radeon RX 7700/7800 (Navi 32)", "unsupported", "RDNA3 has no macOS driver."),
    0x7480: ("Radeon RX 7600 (Navi 33)", "unsupported", "RDNA3 has no macOS driver."),
}


def classify_nvidia(dev):
    if 0x0FC0 <= dev <= 0x103F or 0x1180 <= dev <= 0x12BF:
        return ("NVIDIA Kepler (GTX 6xx/7xx, GT 710/730)", "kepler",
                "Kepler drivers were removed in macOS 12 Monterey: native max is Big Sur.")
    if 0x1340 <= dev <= 0x17FF or 0x1B00 <= dev <= 0x1DFF:
        return ("NVIDIA Maxwell/Pascal (GTX 9xx/10xx)", "webdriver",
                "Only works with NVIDIA Web Drivers on High Sierra 10.13.6.")
    if dev >= 0x1E00:
        return ("NVIDIA Turing/Ampere/Ada (RTX/GTX 16)", "unsupported", "No macOS driver exists.")
    return ("NVIDIA Fermi/Tesla", "legacy", "High Sierra or older only.")


def classify_gpu(dev):
    """dev: PCI device dict with Ven/Dev ints. returns dict."""
    ven, did = dev["Ven"], dev["Dev"]
    if ven == 0x8086:
        return dict(kind="igpu", name="Intel integrated graphics (0x%04X)" % did, status="igpu", notes="", did=did)
    if ven == 0x1002:
        if did in AMD_GPUS:
            n, s, notes = AMD_GPUS[did]
            return dict(kind="dgpu", name=n, status=s, notes=notes, did=did)
        # AMD APU iGPU device ids (Raven/Renoir/Cezanne/Rembrandt/Raphael...)
        if did in (0x15D8, 0x15DD, 0x1636, 0x1638, 0x164C, 0x1681, 0x164E, 0x15BF, 0x1900, 0x1506):
            return dict(kind="apu", name="AMD Radeon integrated (APU 0x%04X)" % did, status="nootedred",
                        notes="AMD APU graphics only via NootedRed (experimental); not configured automatically.")
        return dict(kind="dgpu", name="AMD Radeon (0x%04X, unknown)" % did, status="unknown",
                    notes="Unknown AMD device id - check Dortania GPU Buyers Guide.", did=did)
    if ven == 0x10DE:
        n, s, notes = classify_nvidia(did)
        return dict(kind="dgpu", name=n, status=s, notes=notes, did=did)
    return dict(kind="dgpu", name="Unknown GPU %04X:%04X" % (ven, did), status="unknown", notes="")


def gpu_max_darwin(status):
    return {"native": 25, "spoof": 25, "nootrx": 25, "kepler": 20, "webdriver": 17, "legacy": 17}.get(status, 0)


# ---------------------------------------------------------------------------
# Intel iGPU framebuffer profiles (Dortania)
# ---------------------------------------------------------------------------
def igpu_properties(platform, laptop, headless):
    """Returns (props dict of name->bytes|str, notes)"""
    h = bytes.fromhex
    notes = []
    p = {}
    if platform in ("sandybridge", "ivybridge"):
        notes.append("Sandy/Ivy iGPU: verify framebuffer in the Dortania guide for your exact board.")
        if platform == "ivybridge":
            p["AAPL,ig-platform-id"] = h("07006201" if headless else ("03006601" if laptop else "0A006601"))
        return p, notes
    table = {
        # platform: (desktop display, desktop headless, laptop)
        "haswell":   ("0300220D", "04001204", "0500260A"),
        "broadwell": ("07002216", "", "06002616"),
        "skylake":   ("00001219", "01001219", "00001619"),
        "kabylake":  ("00001259", "03001259", "00001B59"),
        "coffeelake":("07009B3E", "0300913E", "0900A53E"),
        "cometlake": ("07009B3E", "0300C89B", "0900A53E"),
        "icelake":   ("", "", "0000528A"),
    }
    if platform not in table:
        return p, notes
    disp, hl, lap = table[platform]
    pid = lap if laptop else (hl if headless else disp)
    if not pid:
        return p, notes
    p["AAPL,ig-platform-id"] = h(pid)
    if platform == "skylake":
        # Ventura+ dropped Skylake iGPU drivers -> spoof Kaby Lake (works on older macOS too)
        p["AAPL,ig-platform-id"] = h("03001259" if headless and not laptop else ("00001B59" if laptop else "00001259"))
        p["device-id"] = h("16590000" if laptop else "12590000")
        notes.append("Skylake iGPU spoofed as Kaby Lake (required for Ventura and newer).")
    if not headless and platform in ("coffeelake", "cometlake", "kabylake", "skylake", "icelake"):
        # Avoid DVMT pre-alloc panics on boards that lock DVMT at 32MB
        p["framebuffer-patch-enable"] = h("01000000")
        p["framebuffer-stolenmem"] = h("00003001")
        p["framebuffer-fbmem"] = h("00009000")
    if laptop and platform == "icelake":
        p["framebuffer-patch-enable"] = h("01000000")
        p["framebuffer-stolenmem"] = h("00003001")
    return p, notes


# ---------------------------------------------------------------------------
# SMBIOS selection
# ---------------------------------------------------------------------------
def pick_smbios(platform, laptop, target_darwin, igpu_display, cpu_name=""):
    """Choose the SMBIOS model for a platform/target combination."""
    td = target_darwin
    vendor = PLATFORMS[platform]["vendor"]
    if vendor == "amd" or platform in ("hedt",):
        return "MacPro7,1" if td >= 19 else "iMacPro1,1"
    if platform in ("alderlake", "raptorlake", "rocketlake"):
        return "MacPro7,1" if td >= 19 else "iMacPro1,1"
    if laptop:
        if platform == "icelake":
            return "MacBookPro16,2"
        h_series = bool(re.search(r"\d{4,5}H[QKX]?\b|HK\b", cpu_name or ""))
        if platform in ("coffeelake", "cometlake", "kabylake", "skylake"):
            if td >= 25:
                return "MacBookPro16,1" if h_series else "MacBookPro16,2"
            if td >= 23:
                return "MacBookPro15,1" if h_series else "MacBookPro15,2"
            if platform in ("skylake", "kabylake"):
                return "MacBookPro14,1" if td >= 22 or platform == "kabylake" else "MacBookPro13,1"
            return "MacBookPro15,1" if h_series else "MacBookPro15,2"
        if platform == "broadwell":
            return "MacBookPro12,1"
        if platform == "haswell":
            return "MacBookPro11,4" if td >= 21 else "MacBookPro11,1"
        return "MacBookPro11,1"
    # desktops
    if platform in ("coffeelake", "cometlake"):
        if td >= 25 or platform == "cometlake":
            return "iMac20,1" if td >= 19 else "iMac19,1"
        return "iMac19,1" if td >= 18 else "iMac18,3"
    if platform == "kabylake":
        return "iMac20,1" if td >= 23 else "iMac18,3"
    if platform == "skylake":
        return "iMac20,1" if td >= 23 else ("iMac18,3" if td >= 22 else "iMac17,1")
    if platform in ("haswell", "broadwell"):
        if not igpu_display or td >= 22:
            return "MacPro7,1" if td >= 19 else "iMac17,1"
        return "iMac17,1" if td >= 21 else ("iMac16,2" if platform == "broadwell" else "iMac15,1")
    return "iMac20,1"


# ---------------------------------------------------------------------------
# Booter / Kernel quirk profiles (Dortania "starting point" values)
# ---------------------------------------------------------------------------
BOOTER_BASE = dict(AvoidRuntimeDefrag=True, DevirtualiseMmio=False, EnableSafeModeSlide=True,
                   EnableWriteUnprotector=True, ProtectUefiServices=False, ProvideCustomSlide=True,
                   RebuildAppleMemoryMap=False, SetupVirtualMap=True, SyncRuntimePermissions=False,
                   ResizeAppleGpuBars=-1)
BOOTER_MODERN = dict(BOOTER_BASE, DevirtualiseMmio=True, EnableWriteUnprotector=False,
                     RebuildAppleMemoryMap=True, SyncRuntimePermissions=True)


def booter_quirks(platform, board_name=""):
    b = board_name.upper()
    if platform == "coffeelake":
        q = dict(BOOTER_MODERN)
        if "Z390" in b:
            q["ProtectUefiServices"] = True
        return q
    if platform == "cometlake":
        return dict(BOOTER_MODERN, ProtectUefiServices=True)
    if platform in ("icelake",):
        return dict(BOOTER_MODERN, ProtectMemoryRegions=False, ProtectUefiServices=True)
    if platform in ("rocketlake", "alderlake", "raptorlake"):
        return dict(BOOTER_MODERN, ProtectUefiServices=True, SetupVirtualMap=False)
    if platform in ("amd_zen", "amd_zen5"):
        q = dict(BOOTER_BASE, EnableWriteUnprotector=False, RebuildAppleMemoryMap=True,
                 SyncRuntimePermissions=True)
        if "TRX40" in b:
            q.update(DevirtualiseMmio=True, SetupVirtualMap=False)
        if any(x in b for x in ("B550", "A520", "B650", "X670", "A620", "B850", "X870")):
            q["SetupVirtualMap"] = False
        return q
    if platform == "hedt":
        return dict(BOOTER_MODERN)
    return dict(BOOTER_BASE)


def kernel_quirks(platform, laptop, board_name=""):
    q = dict(PanicNoKextDump=True, PowerTimeoutKernelPanic=True, DisableIoMapper=True,
             DisableLinkeditJettison=True, XhciPortLimit=False)
    vendor = PLATFORMS[platform]["vendor"]
    if vendor == "intel":
        q["AppleXcpmCfgLock"] = True       # safe default when CFG-Lock state is unknown
        if platform in ("sandybridge", "ivybridge"):
            q["AppleCpuPmCfgLock"] = True
        if platform in ("alderlake", "raptorlake", "rocketlake"):
            q["ProvideCurrentCpuInfo"] = True
        if platform == "hedt":
            q["AppleXcpmExtraMsrs"] = True
    else:
        q["ProvideCurrentCpuInfo"] = True
    if "HP" in board_name.upper() or "HEWLETT" in board_name.upper():
        q["LapicKernelPanic"] = True
    return q


# ---------------------------------------------------------------------------
# Download packages: id -> (repo or url, asset regex)
# ---------------------------------------------------------------------------
PACKAGES = {
    "OpenCorePkg":        dict(repo="acidanthera/OpenCorePkg", asset=r"^OpenCore-[\d.]+-RELEASE\.zip$"),
    "Lilu":               dict(repo="acidanthera/Lilu", asset=r"RELEASE\.zip$"),
    "VirtualSMC":         dict(repo="acidanthera/VirtualSMC", asset=r"RELEASE\.zip$"),
    "WhateverGreen":      dict(repo="acidanthera/WhateverGreen", asset=r"RELEASE\.zip$"),
    "AppleALC":           dict(repo="acidanthera/AppleALC", asset=r"RELEASE\.zip$"),
    "RestrictEvents":     dict(repo="acidanthera/RestrictEvents", asset=r"RELEASE\.zip$"),
    "NVMeFix":            dict(repo="acidanthera/NVMeFix", asset=r"RELEASE\.zip$"),
    "CryptexFixup":       dict(repo="acidanthera/CryptexFixup", asset=r"RELEASE\.zip$"),
    "IntelMausi":         dict(repo="acidanthera/IntelMausi", asset=r"RELEASE\.zip$"),
    "VoodooPS2":          dict(repo="acidanthera/VoodooPS2", asset=r"RELEASE\.zip$"),
    "BrightnessKeys":     dict(repo="acidanthera/BrightnessKeys", asset=r"RELEASE\.zip$"),
    "AirportBrcmFixup":   dict(repo="acidanthera/AirportBrcmFixup", asset=r"RELEASE\.zip$"),
    "BrcmPatchRAM":       dict(repo="acidanthera/BrcmPatchRAM", asset=r"RELEASE\.zip$"),
    "ECEnabler":          dict(repo="1Revenger1/ECEnabler", asset=r"RELEASE\.zip$"),
    "VoodooI2C":          dict(repo="VoodooI2C/VoodooI2C", asset=r"^VoodooI2C-.*\.zip$"),
    "RealtekRTL8111":     dict(repo="Mieze/RTL8111_driver_for_OS_X", asset=r"\.zip$"),
    "LucyRTL8125":        dict(repo="Mieze/LucyRTL8125Ethernet", asset=r"\.zip$"),
    "AtherosE2200":       dict(repo="Mieze/AtherosE2200Ethernet", asset=r"\.zip$"),
    "AppleIGC":           dict(repo="SongXiaoXi/AppleIGC", asset=r"\.zip$"),
    "itlwm":              dict(repo="OpenIntelWireless/itlwm", asset=r"^itlwm_.*\.zip$"),
    "IntelBluetoothFirmware": dict(repo="OpenIntelWireless/IntelBluetoothFirmware", asset=r"^IntelBluetooth.*\.zip$"),
    "CpuTopologyRebuild": dict(repo="b00t0x/CpuTopologyRebuild", asset=r"RELEASE\.zip$"),
    "USBToolBoxKext":     dict(repo="USBToolBox/kext", asset=r"RELEASE\.zip$"),
    "USBToolBoxTool":     dict(repo="USBToolBox/tool", asset=r"^Windows\.exe$"),
    "NootRX":             dict(repo="ChefKissInc/NootRX", asset=r"\.zip$"),
    # Intel ACPICA Windows tools for SSDTTime / ACPI dump.
    # Official releases are 32-bit (fine on full Windows, NOT in x64 WinPE which has no WOW64);
    # the 64-bit builds are produced by build/build-acpica-win64.sh and imported by the ISO build.
    "ACPICA_iasl":        dict(repo="open-acpica/acpica", asset=r"^iasl\.exe$"),
    "ACPICA_acpidump":    dict(repo="open-acpica/acpica", asset=r"^acpidump\.exe$"),
    # Windows apps usable inside WinPE (64-bit, portable)
    "ExplorerPP":         dict(repo="derceg/explorerplusplus", asset=r"^explorerpp_x64\.zip$", app="Explorer++.exe"),
    "NotepadPP":          dict(repo="notepad-plus-plus/notepad-plus-plus", asset=r"^npp\.[\d.]+\.portable\.x64\.zip$", app="notepad++.exe"),
    "OCAT":               dict(repo="ic005k/OCAuxiliaryTools", asset=r"^OCAT-Win64\.zip$", app="OCAuxiliaryTools.exe"),
    # Hackintosh EFI Builder (yusufklncc, MIT): profile-based EFI generator + recovery + USB writer
    "EFIBuilder":         dict(repo="yusufklncc/Hackintosh-for-All-Computers", asset=r"^HackintoshEFIBuilder-win-x64\.zip$",
                               app="HackintoshEFIBuilder.exe"),        # Avalonia window + EFIBuilderEngine
    "EFIBuilderConsole":  dict(repo="yusufklncc/Hackintosh-for-All-Computers", asset=r"^HackintoshEFIBuilder-console-win-x64\.zip$",
                               app="EFIBuilderEngine.exe"),            # the same engine, console only
    "ACPICA64_iasl":      dict(local=True, file="iasl.exe"),
    "ACPICA64_acpidump":  dict(local=True, file="acpidump.exe"),
    # direct files
    "AMD_Vanilla":        dict(url="https://raw.githubusercontent.com/AMD-OSX/AMD_Vanilla/master/patches.plist", file="patches.plist"),
    "HfsPlus":            dict(url="https://raw.githubusercontent.com/acidanthera/OcBinaryData/master/Drivers/HfsPlus.efi", file="HfsPlus.efi"),
    "SSDTTime":           dict(url="https://codeload.github.com/corpnewt/SSDTTime/zip/refs/heads/master", file="SSDTTime-master.zip"),
    "OpCoreSimplify":     dict(url="https://codeload.github.com/lzhoang2801/OpCore-Simplify/zip/refs/heads/main", file="OpCore-Simplify-main.zip"),
    "HardwareSniffer":    dict(repo="lzhoang2801/Hardware-Sniffer", asset=r"^Hardware-Sniffer-CLI\.exe$"),
    # Ferramentas TOPS (hnanoto): Windows build lives on release tag 2.0.0 (2.0.1 is macOS-only)
    "FerramentasTops":    dict(repo="hnanoto/Ferramentas-Tops", tag="2.0.0", multi=True,
                               asset=r"(?i)\.(exe|zip|ps1|bat|cmd|sh|7z|py)$"),
    # Portable Git for Windows (bash, git, curl, unzip...) + jq: runs bash-based hackintosh tools
    "PortableGit":        dict(repo="git-for-windows/git", asset=r"^PortableGit-[\d.]+-64-bit\.7z\.exe$"),
    "jq":                 dict(repo="jqlang/jq", asset=r"^jq-windows-amd64\.exe$"),
    "gibMacOS":           dict(url="https://codeload.github.com/corpnewt/gibMacOS/zip/refs/heads/master", file="gibMacOS-master.zip"),
}
SSDT_BASE = "https://raw.githubusercontent.com/dortania/Getting-Started-With-ACPI/master/extra-files/compiled/"
SSDTS = ["SSDT-RTC0-RANGE-HEDT", "SSDT-UNC", "SSDT-PLUG-DRTNIA", "SSDT-EC-USBX-DESKTOP", "SSDT-EC-USBX-LAPTOP", "SSDT-EC-DESKTOP",
         "SSDT-EC-LAPTOP", "SSDT-AWAC", "SSDT-PMC", "SSDT-RHUB", "SSDT-PNLF", "SSDT-CPUR", "SSDT-XOSI"]
for _s in SSDTS:
    PACKAGES["ssdt:" + _s] = dict(url=SSDT_BASE + _s + ".aml", file=_s + ".aml")

# bundle -> package that contains it
KEXT_SOURCE = {
    "Lilu.kext": "Lilu", "VirtualSMC.kext": "VirtualSMC", "SMCProcessor.kext": "VirtualSMC",
    "SMCSuperIO.kext": "VirtualSMC", "SMCBatteryManager.kext": "VirtualSMC",
    "SMCLightSensor.kext": "VirtualSMC", "WhateverGreen.kext": "WhateverGreen", "AppleALC.kext": "AppleALC",
    "RestrictEvents.kext": "RestrictEvents", "NVMeFix.kext": "NVMeFix", "CryptexFixup.kext": "CryptexFixup",
    "IntelMausi.kext": "IntelMausi", "VoodooPS2Controller.kext": "VoodooPS2", "BrightnessKeys.kext": "BrightnessKeys",
    "AirportBrcmFixup.kext": "AirportBrcmFixup", "BrcmPatchRAM3.kext": "BrcmPatchRAM",
    "BrcmFirmwareData.kext": "BrcmPatchRAM", "BlueToolFixup.kext": "BrcmPatchRAM",
    "BrcmBluetoothInjector.kext": "BrcmPatchRAM", "ECEnabler.kext": "ECEnabler",
    "VoodooI2C.kext": "VoodooI2C", "VoodooI2CHID.kext": "VoodooI2C",
    "RealtekRTL8111.kext": "RealtekRTL8111", "LucyRTL8125Ethernet.kext": "LucyRTL8125",
    "AtherosE2200Ethernet.kext": "AtherosE2200", "AppleIGC.kext": "AppleIGC",
    "itlwm.kext": "itlwm", "IntelBluetoothFirmware.kext": "IntelBluetoothFirmware",
    "IntelBTPatcher.kext": "IntelBluetoothFirmware", "IntelBluetoothInjector.kext": "IntelBluetoothFirmware",
    "CpuTopologyRebuild.kext": "CpuTopologyRebuild", "USBToolBox.kext": "USBToolBoxKext",
    "NootRX.kext": "NootRX",
}
KEXT_LIMITS = {   # bundle -> (MinKernel, MaxKernel)
    "BlueToolFixup.kext": ("21.0.0", ""), "BrcmBluetoothInjector.kext": ("", "20.99.99"),
    "IntelBluetoothInjector.kext": ("", "20.99.99"), "CryptexFixup.kext": ("22.0.0", ""),
}
KEXT_COMMENT = {
    "Lilu.kext": "Patch engine", "VirtualSMC.kext": "SMC emulator", "WhateverGreen.kext": "Graphics fixes",
    "AppleALC.kext": "Audio", "USBToolBox.kext": "USB map helper (add UTBMap.kext after mapping)",
}

# ---------------------------------------------------------------------------
# NIC / Wi-Fi / storage
# ---------------------------------------------------------------------------
def classify_ethernet(ven, dev):
    if ven == 0x8086:
        if dev in (0x15F2, 0x15F3, 0x125B, 0x125C, 0x125D, 0x3100, 0x3101, 0x0D9F):
            return "AppleIGC.kext", "Intel I225/I226 2.5GbE"
        if dev in (0x1533, 0x1539, 0x157B, 0x1536, 0x1537, 0x1538):
            return "", "Intel I210/I211 (native AppleIntelI210Ethernet on Catalina+, may need DeviceProperties)"
        if dev in (0x10D3,):
            return "", "Intel 82574L (native)"
        return "IntelMausi.kext", "Intel 1GbE (I217/I218/I219/82579)"
    if ven == 0x10EC:
        if dev in (0x8125, 0x3000, 0x8126):
            return "LucyRTL8125Ethernet.kext", "Realtek RTL8125 2.5GbE"
        if dev in (0x8168, 0x8111, 0x8161):
            return "RealtekRTL8111.kext", "Realtek RTL8111/8168 1GbE"
        return "", "Realtek NIC 0x%04X (check support)" % dev
    if ven == 0x1969:
        return "AtherosE2200Ethernet.kext", "Atheros/Killer Ethernet"
    if ven == 0x1D6A:
        return "", "Aquantia AQC107 (native, needs ForceAquantiaEthernet on Catalina+)"
    if ven == 0x14E4:
        return "", "Broadcom Ethernet (native on many models)"
    return "", "Unknown Ethernet %04X:%04X" % (ven, dev)


def classify_wifi(ven, dev):
    if ven == 0x8086:
        return ["itlwm.kext", "IntelBluetoothFirmware.kext", "IntelBTPatcher.kext", "BlueToolFixup.kext",
                "IntelBluetoothInjector.kext"], "Intel Wi-Fi (itlwm + HeliPort app; experimental on newest macOS)"
    if ven == 0x14E4:
        if dev in (0x43A0, 0x43BA, 0x43A3):
            return ["AirportBrcmFixup.kext", "BrcmPatchRAM3.kext", "BrcmFirmwareData.kext", "BlueToolFixup.kext"], \
                "Broadcom BCM94360/4360-class (native up to Ventura; Sonoma+ needs OCLP root patch)"
        return ["AirportBrcmFixup.kext", "BrcmPatchRAM3.kext", "BrcmFirmwareData.kext", "BlueToolFixup.kext"], \
            "Broadcom Wi-Fi (Sonoma+ needs OCLP root patch)"
    return [], "Wi-Fi %04X:%04X has no macOS driver (Realtek/MediaTek/Qualcomm): replace the card or use USB/Ethernet" % (ven, dev)


BAD_NVME = [r"PM981", r"PM991", r"PM9A1", r"970 EVO Plus.*(2B2QEXM7)", r"600p", r"2200S", r"SM2263", r"BC501"]

# HDA codec vendor ids -> name prefix
CODEC_VENDOR = {0x10EC: "Realtek ALC", 0x14F1: "Conexant", 0x111D: "IDT", 0x1106: "VIA", 0x11D4: "Analog Devices", 0x8086: "Intel HDMI"}
