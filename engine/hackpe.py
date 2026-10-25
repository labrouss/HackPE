#!/usr/bin/env python3
"""
HackPE engine - hardware detection, OpenCore EFI generation, macOS recovery
download and USB installer creation. Standard library only (runs on the
Python embeddable distribution inside WinPE, or on any Windows/Linux/macOS
Python 3.8+ for the parts that do not touch disks).

  hackpe.py detect      [--out hw.json]
  hackpe.py plan        --target tahoe [--hw hw.json] [--out plan.json]
  hackpe.py fetch       [--all | --only Lilu,VirtualSMC]
  hackpe.py build-efi   --target tahoe [--hw hw.json] --out DIR
  hackpe.py versions
  hackpe.py recovery    --target tahoe --out X:\\com.apple.recovery.boot
  hackpe.py disks
  hackpe.py make-usb    --target tahoe --disk N [--yes]
  hackpe.py install-esp --efi DIR --disk N [--yes]
  hackpe.py wizard
"""
import argparse, datetime, glob, json, os, platform as pyplatform, plistlib, re, shutil, ssl
import string, subprocess, sys, tempfile, time, urllib.request, urllib.error, uuid, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hwdb  # noqa: E402
import tools  # noqa: E402

VERSION = "1.0.0"
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE_DIR)
IS_WIN = os.name == "nt"
UA = "HackPE/%s (+OpenCore helper)" % VERSION


def log(msg=""):
    print(msg, flush=True)


def die(msg, code=1):
    log("ERROR: " + msg)
    log("@@FAIL " + msg)
    sys.exit(code)


def stage(key, msg):
    """Machine-readable progress marker for the GUI assistant."""
    log("@@STEP %s %s" % (key, msg))


# ===========================================================================
# Networking
# ===========================================================================
_SSL = None


def ssl_ctx():
    global _SSL
    if _SSL is None:
        _SSL = ssl.create_default_context()
        bundle = os.path.join(ROOT, "data", "cacert.pem")
        if os.path.isfile(bundle):          # WinPE has an almost empty root store
            try:
                _SSL.load_verify_locations(bundle)
            except Exception as e:
                log("warn: could not load %s: %s" % (bundle, e))
    return _SSL


def http(url, dest=None, accept=None, quiet=False, retries=3):
    hdr = {"User-Agent": UA}
    if accept:
        hdr["Accept"] = accept
    tok = os.environ.get("GITHUB_TOKEN")
    if tok and "api.github.com" in url:
        hdr["Authorization"] = "Bearer " + tok
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, context=ssl_ctx(), timeout=60) as r:
                if dest is None:
                    return r.read()
                total = int(r.headers.get("Content-Length") or 0)
                tmp = dest + ".part"
                done, shown = 0, -1
                with open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if not quiet and total:
                            pct = int(done * 100 / total)
                            if pct // 5 != shown:
                                shown = pct // 5
                                log("    %3d%%  %.1f / %.1f MB" % (pct, done / 1e6, total / 1e6))
                os.replace(tmp, dest)
                return dest
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 404, 429) and "api.github.com" in url:
                break
            if e.code == 404:
                break
        except Exception as e:  # network hiccup
            last = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("download failed: %s (%s)" % (url, last))


def gh_release_assets(repo, asset_re, tag=None):
    """All assets matching asset_re of the latest (or a given tag's) release: (tag, [(name, url)])."""
    rx = re.compile(asset_re)
    api = "https://api.github.com/repos/%s/releases/%s" % (repo, ("tags/" + tag) if tag else "latest")
    try:
        data = json.loads(http(api, accept="application/vnd.github+json", quiet=True))
        found = [(a["name"], a["browser_download_url"]) for a in data.get("assets", []) if rx.search(a["name"])]
        return data["tag_name"], found
    except RuntimeError:
        pass
    t = tag
    if not t:
        req = urllib.request.Request("https://github.com/%s/releases/latest" % repo, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, context=ssl_ctx(), timeout=60) as r:
            t = r.geturl().rstrip("/").rsplit("/", 1)[-1]
    html = http("https://github.com/%s/releases/expanded_assets/%s" % (repo, t), quiet=True).decode("utf-8", "replace")
    found = [(h.rsplit("/", 1)[-1], "https://github.com" + h)
             for h in re.findall(r'href="(/[^"]+/releases/download/[^"]+)"', html) if rx.search(h.rsplit("/", 1)[-1])]
    return t, found


def gh_latest(repo, asset_re):
    """Return (tag, asset_name, url). Uses the API, falls back to HTML scraping (rate limits)."""
    rx = re.compile(asset_re)
    try:
        data = json.loads(http("https://api.github.com/repos/%s/releases/latest" % repo,
                               accept="application/vnd.github+json", quiet=True))
        for a in data.get("assets", []):
            if rx.search(a["name"]):
                return data["tag_name"], a["name"], a["browser_download_url"]
        raise RuntimeError("no asset matching %s in %s %s" % (asset_re, repo, data.get("tag_name")))
    except RuntimeError as e:
        if "no asset" in str(e):
            raise
        api_err = e
    # Fallback: releases/latest redirect + expanded_assets fragment
    try:
        req = urllib.request.Request("https://github.com/%s/releases/latest" % repo, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, context=ssl_ctx(), timeout=60) as r:
            tag = r.geturl().rstrip("/").rsplit("/", 1)[-1]
        html = http("https://github.com/%s/releases/expanded_assets/%s" % (repo, tag), quiet=True).decode("utf-8", "replace")
        for href in re.findall(r'href="(/[^"]+/releases/download/[^"]+)"', html):
            name = href.rsplit("/", 1)[-1]
            if rx.search(name):
                return tag, name, "https://github.com" + href
    except Exception:
        pass
    raise RuntimeError("cannot resolve latest release of %s (%s)" % (repo, api_err))


# ===========================================================================
# Cache (downloaded OpenCore, kexts, SSDTs). Several dirs may be layered:
# read-only cache on the boot media + writable cache in RAM/USB.
# ===========================================================================
class Cache:
    def __init__(self, dirs=None):
        env = os.environ.get("HACKPE_CACHE", "")
        self.dirs = [d for d in (dirs or env.split(";")) if d] or [os.path.join(ROOT, "cache")]
        self.manifest = {}
        for d in reversed(self.dirs):
            mf = os.path.join(d, "manifest.json")
            if os.path.isfile(mf):
                try:
                    for k, v in json.load(open(mf, encoding="utf-8")).items():
                        v["_dir"] = d
                        self.manifest[k] = v
                except Exception as e:
                    log("warn: bad manifest %s: %s" % (mf, e))

    def writable(self):
        for d in self.dirs:
            try:
                os.makedirs(d, exist_ok=True)
                probe = os.path.join(d, ".w")
                open(probe, "w").close()
                os.remove(probe)
                return d
            except OSError:
                continue
        d = os.path.join(tempfile.gettempdir(), "hackpe-cache")
        os.makedirs(d, exist_ok=True)
        self.dirs.insert(0, d)
        return d

    def has(self, pkg):
        e = self.manifest.get(pkg)
        return bool(e) and os.path.exists(os.path.join(e["_dir"], e["file"]))

    def path(self, pkg):
        if not self.has(pkg):
            return None
        e = self.manifest[pkg]
        return os.path.join(e["_dir"], e["file"])

    def extracted(self, pkg):
        """Extract a zip package once; returns directory."""
        p = self.path(pkg)
        if not p:
            return None
        if not p.lower().endswith(".zip"):
            return os.path.dirname(p)
        tag = re.sub(r"[^\w.-]", "_", self.manifest[pkg].get("tag", "x"))
        for base in [self.manifest[pkg]["_dir"], self.writable()]:
            out = os.path.join(base, "x", pkg + "-" + tag)
            if os.path.isfile(os.path.join(out, ".ok")):
                return out
        out = os.path.join(self.writable(), "x", pkg + "-" + tag)
        shutil.rmtree(out, ignore_errors=True)
        with zipfile.ZipFile(p) as z:
            for m in z.infolist():
                if m.filename.startswith("__MACOSX") or "/._" in m.filename:
                    continue
                z.extract(m, out)
        open(os.path.join(out, ".ok"), "w").close()
        return out

    def save_entry(self, pkg, d, entry):
        mf = os.path.join(d, "manifest.json")
        data = {}
        if os.path.isfile(mf):
            data = json.load(open(mf, encoding="utf-8"))
        data[pkg] = entry
        json.dump(data, open(mf, "w", encoding="utf-8"), indent=1)
        entry = dict(entry, _dir=d)
        self.manifest[pkg] = entry

    def import_file(self, pkg, src, tag="local"):
        d = self.writable()
        sub = os.path.join(d, "pkg", re.sub(r"[^\w.-]", "_", pkg))
        os.makedirs(sub, exist_ok=True)
        dst = os.path.join(sub, os.path.basename(src))
        shutil.copy2(src, dst)
        self.save_entry(pkg, d, {"tag": tag, "file": os.path.relpath(dst, d), "url": "import:" + src,
                                 "fetched": datetime.datetime.now().isoformat(timespec="seconds")})
        log("  + %-26s %s  <- %s" % (pkg, tag, src))

    def fetch(self, pkgs, force=False):
        d = self.writable()
        ok, bad = [], []
        for pkg in pkgs:
            spec = hwdb.PACKAGES[pkg]
            if spec.get("local"):
                if not self.has(pkg):
                    log("  - %-26s provided by the ISO build (build/build-acpica-win64.sh)" % pkg)
                continue
            try:
                if spec.get("multi"):
                    tag, assets = gh_release_assets(spec["repo"], spec["asset"], spec.get("tag"))
                    cur = self.manifest.get(pkg)
                    if not force and cur and cur.get("tag") == tag and self.has(pkg):
                        log("  = %-26s %s (up to date)" % (pkg, tag))
                        ok.append(pkg)
                        continue
                    if not assets:
                        raise RuntimeError("release %s of %s has no matching assets" % (tag, spec["repo"]))
                    sub = os.path.join(d, "pkg", pkg)
                    shutil.rmtree(sub, ignore_errors=True)
                    os.makedirs(sub)
                    for name, url in assets:
                        log("  + %-26s %s  <- %s" % (pkg, tag, url))
                        http(url, os.path.join(sub, name), quiet=True)
                    self.save_entry(pkg, d, {"tag": tag, "file": os.path.join("pkg", pkg), "url": spec["repo"],
                                             "assets": [n for n, _ in assets],
                                             "fetched": datetime.datetime.now().isoformat(timespec="seconds")})
                    ok.append(pkg)
                    continue
                if "repo" in spec:
                    tag, name, url = gh_latest(spec["repo"], spec["asset"])
                    cur = self.manifest.get(pkg)
                    if not force and cur and cur.get("tag") == tag and self.has(pkg):
                        log("  = %-26s %s (up to date)" % (pkg, tag))
                        ok.append(pkg)
                        continue
                else:
                    tag, name, url = datetime.date.today().isoformat(), spec["file"], spec["url"]
                    if not force and self.has(pkg):
                        log("  = %-26s cached" % pkg)
                        ok.append(pkg)
                        continue
                sub = os.path.join(d, "pkg")
                os.makedirs(sub, exist_ok=True)
                http(url, os.path.join(sub, name), quiet=True)
                if "repo" not in spec:              # direct file: version = content hash (changes only when it does)
                    import hashlib
                    with open(os.path.join(sub, name), "rb") as fh:
                        tag = "sha-" + hashlib.sha256(fh.read()).hexdigest()[:10]
                log("  + %-26s %s  <- %s" % (pkg, tag, url))
                self.save_entry(pkg, d, {"tag": tag, "file": os.path.join("pkg", name), "url": url,
                                         "fetched": datetime.datetime.now().isoformat(timespec="seconds")})
                ok.append(pkg)
            except Exception as e:
                log("  ! %-26s FAILED: %s" % (pkg, e))
                bad.append(pkg)
        return ok, bad


# ===========================================================================
# Hardware detection & analysis
# ===========================================================================
def run_detect(out=None):
    if not IS_WIN:
        die("live detection needs Windows/WinPE. Use --hw <file.json> on other systems.")
    ps1 = os.path.join(ENGINE_DIR, "detect.ps1")
    out = out or os.path.join(tempfile.gettempdir(), "hackpe-hw.json")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Out", out],
                       capture_output=True, text=True)
    if not os.path.isfile(out):
        die("detect.ps1 failed:\n" + r.stdout + r.stderr)
    return load_hw(out), out


def load_hw(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        hw = json.load(f)
    for k in ("Pci", "Hda", "Input", "Nics", "Disks"):   # PS 5.1 collapses 1-item arrays
        v = hw.get(k)
        hw[k] = [] if v is None else (v if isinstance(v, list) else [v])
    return hw


LAPTOP_CHASSIS = {8, 9, 10, 11, 12, 14, 18, 21, 30, 31, 32}


def acpi_path(loc):
    """'PCIROOT(0)#PCI(0100)#PCI(0000)' -> 'PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)'"""
    if not loc or not loc.upper().startswith("PCIROOT("):
        return None
    parts = loc.upper().split("#")
    m = re.match(r"PCIROOT\((\w+)\)", parts[0])
    out = ["PciRoot(0x%X)" % int(m.group(1), 16)]
    for p in parts[1:]:
        m = re.match(r"PCI\(([0-9A-F]{2})([0-9A-F]{2})\)", p)
        if not m:
            return None
        out.append("Pci(0x%X,0x%X)" % (int(m.group(1), 16), int(m.group(2), 16)))
    return "/".join(out)


def analyze(hw, force_platform=None, force_laptop=None):
    """Turn raw detection into facts."""
    f = {"warnings": [], "info": []}
    cpu = hw.get("Cpu", {})
    plat, notes = hwdb.classify_cpu(cpu)
    f["warnings"] += notes
    if force_platform:
        plat = force_platform
    f["platform"] = plat
    f["cpu"] = cpu
    sysi = hw.get("System", {})
    chassis = set(int(c) for c in (sysi.get("ChassisTypes") or []) if str(c).isdigit())
    laptop = sysi.get("PCSystemType") == 2 or bool(chassis & LAPTOP_CHASSIS) or bool(sysi.get("HasBattery"))
    f["laptop"] = laptop if force_laptop is None else force_laptop
    f["board"] = "%s %s" % (sysi.get("BoardManufacturer") or "", sysi.get("BoardProduct") or "")
    f["vendor"] = "%s %s" % (sysi.get("Manufacturer") or "", sysi.get("Model") or "")
    f["firmware"] = sysi.get("Firmware")

    gpus, nics, wifi, hda, nvme, lpc = [], [], [], [], [], None
    for d in hw.get("Pci", []):
        cls = (d.get("Class") or "").upper()
        ven, dev = d.get("Ven"), d.get("Dev")
        if ven is None or dev is None:
            continue
        if cls.startswith("03") or (not cls and ven in (0x10DE, 0x1002) and dev > 0x0100):
            g = hwdb.classify_gpu(d)
            g["location"] = acpi_path(d.get("LocationPath"))
            if g["kind"] == "dgpu" and ven == 0x1002 and cls.startswith("0380"):
                continue  # secondary function
            gpus.append(g)
        elif cls.startswith("0200"):
            kext, desc = hwdb.classify_ethernet(ven, dev)
            nics.append({"kext": kext, "name": desc})
        elif cls.startswith("0280"):
            kexts, desc = hwdb.classify_wifi(ven, dev)
            wifi.append({"kexts": kexts, "name": desc})
        elif cls.startswith("0403") and ven in (0x8086, 0x1022):   # onboard HDA (not GPU HDMI audio)
            hda.append(d)
        elif cls.startswith("0108"):
            nvme.append(d)
        elif cls.startswith("0601") and ven == 0x8086:
            lpc = dev
    f.update(gpus=gpus, nics=nics, wifi=wifi, hda=hda, nvme_ctrl=nvme, lpc=lpc)
    f["codecs"] = ["%s%04X" % (hwdb.CODEC_VENDOR.get(c["Ven"], "%04X:" % c["Ven"]), c["Dev"]) for c in hw.get("Hda", [])
                   if c.get("Ven") not in (0x8086, 0x1002, 0x10DE)]
    f["i2c"] = any(i.get("I2C") for i in hw.get("Input", []))
    f["ps2"] = any(re.search(r"PNP0303|PNP0F13|SYN|ELAN|ALPS", i.get("Ids", "")) for i in hw.get("Input", []))
    f["bad_nvme"] = [d["Model"] for d in hw.get("Disks", [])
                     if any(re.search(p, d.get("Model") or "", re.I) for p in hwdb.BAD_NVME)]
    mac = None
    for n in hw.get("Nics", []):
        if (n.get("PnpId") or "").upper().startswith("PCI\\") and n.get("Mac"):
            mac = n["Mac"]
            break
    f["mac"] = mac
    return f


def compat_matrix(f):
    """Newest Darwin reachable natively + per-version verdicts."""
    p = hwdb.PLATFORMS[f["platform"]]
    igpu = any(g["kind"] == "igpu" for g in f["gpus"])
    gfx = [hwdb.gpu_max_darwin(g["status"]) for g in f["gpus"] if g["kind"] == "dgpu"]
    if igpu and p["igpu_max"]:
        gfx.append(p["igpu_max"])
    gfx_max = max(gfx) if gfx else 0
    out = {}
    for key, name, ver, darwin, _ in hwdb.MACOS:
        if p["status"] == "unsupported" or p["cpu_max"] < darwin:
            out[key] = "no (CPU)"
        elif gfx_max < darwin:
            out[key] = "no (graphics)"
        elif p["status"] in ("experimental", "manual"):
            out[key] = "experimental"
        else:
            out[key] = "yes"
    return out, min(p["cpu_max"], gfx_max)


BIOS_SETTINGS = """Firmware (BIOS) settings - change these before booting OpenCore:
  DISABLE: Fast Boot, Secure Boot, CSM/Legacy, Intel SGX, Intel Platform Trust/fTPM (if issues),
           CFG Lock / MSR 0xE2 write protection (if the option exists), Serial/COM port,
           VT-d / IOMMU (or keep the DisableIoMapper quirk enabled - it is in this EFI)
  ENABLE:  VT-x/SVM, Above 4G Decoding, Re-Size BAR off (or ResizeAppleGpuBars=-1, already set),
           Hyper-Threading, EHCI/XHCI Hand-off, SATA mode AHCI, OS type 'Windows 10/11 UEFI'
  iGPU:    DVMT Pre-Allocated 64MB (if available); if iGPU is used for display set it as primary"""


def build_plan(hw, target_key, opts=None):
    opts = opts or {}
    tgt = hwdb.macos(target_key)
    td = tgt["darwin"]
    f = analyze(hw, opts.get("platform"), opts.get("laptop"))
    plat = f["platform"]
    P = hwdb.PLATFORMS[plat]
    W, I, B = list(f["warnings"]), list(f["info"]), []

    compat, native_max = compat_matrix(f)
    if P["status"] == "unsupported":
        B.append("%s is not supported by macOS (no CPU/iGPU support)." % P["name"])
    if P["cpu_max"] and P["cpu_max"] < td:
        B.append("%s CPUs are not supported natively beyond %s." % (P["name"], hwdb.DARWIN_NAME.get(P["cpu_max"], "?")))
    if P["status"] in ("manual", "experimental"):
        W.append("%s: this profile is a starting point only - expect manual tuning." % P["name"])
    if f["firmware"] == "BIOS":
        W.append("Firmware booted in legacy BIOS mode. OpenCore needs UEFI (or a manual LegacyBoot setup).")

    # ---- graphics decision ---------------------------------------------------
    dgpus = [g for g in f["gpus"] if g["kind"] == "dgpu"]
    good_dgpus = [g for g in dgpus if hwdb.gpu_max_darwin(g["status"]) >= td]
    igpu_present = any(g["kind"] == "igpu" for g in f["gpus"])
    igpu_ok = igpu_present and P["igpu_max"] >= td
    if opts.get("display") == "igpu":
        good_dgpus = []
    if good_dgpus:
        display = "dgpu"
    elif igpu_ok:
        display = "igpu"
    else:
        display = "none"
        why = ", ".join("%s (%s)" % (g["name"], ("iGPU not supported by macOS on %s" % P["name"] if P["igpu_max"] == 0
                                                  else "iGPU drivers removed after %s" % hwdb.DARWIN_NAME.get(P["igpu_max"], "?"))
                                                 if g["kind"] == "igpu" else (g["notes"] or g["status"]))
                        for g in f["gpus"]) or "no GPU detected"
        B.append("No graphics device usable on %s: %s" % (tgt["name"], why))
    for g in dgpus:
        if g not in good_dgpus and display != "none":
            W.append("%s is not usable on %s and will be disabled (-wegnoegpu). Connect the monitor to the %s."
                     % (g["name"], tgt["name"], "motherboard (iGPU)" if display == "igpu" else "supported GPU"))
        if g["notes"] and g in good_dgpus:
            I.append("%s: %s" % (g["name"], g["notes"]))
    if any(g["kind"] == "apu" for g in f["gpus"]):
        W.append("AMD APU graphics needs NootedRed (not configured automatically).")

    smbios = opts.get("smbios") or hwdb.pick_smbios(plat, f["laptop"], td, display == "igpu",
                                                     f["cpu"].get("Name", ""))
    if hwdb.SMBIOS_MAX.get(smbios, 99) < td:
        W.append("SMBIOS %s is not officially supported by %s." % (smbios, tgt["name"]))

    # ---- kexts --------------------------------------------------------------
    k = ["Lilu.kext", "VirtualSMC.kext", "WhateverGreen.kext"]
    if f["hda"]:
        k.append("AppleALC.kext")
    if P["vendor"] == "intel":
        k += ["SMCProcessor.kext"] + ([] if f["laptop"] else ["SMCSuperIO.kext"])
    if f["laptop"]:
        k += ["SMCBatteryManager.kext", "ECEnabler.kext"]
        if f["i2c"]:
            k += ["VoodooI2C.kext", "VoodooI2CHID.kext"]
        k += ["VoodooPS2Controller.kext", "BrightnessKeys.kext"]
    k.append("RestrictEvents.kext")
    if f["nvme_ctrl"]:
        k.append("NVMeFix.kext")
    for n in f["nics"]:
        if n["kext"] and n["kext"] not in k:
            k.append(n["kext"])
    for wf in f["wifi"]:
        for x in wf["kexts"]:
            if x not in k:
                k.append(x)
    if plat in ("alderlake", "raptorlake"):
        k.append("CpuTopologyRebuild.kext")
    if plat in ("sandybridge", "ivybridge") and td >= 22:
        k.append("CryptexFixup.kext")
    kblock = []
    if P["vendor"] == "amd" and smbios in ("MacPro7,1", "iMacPro1,1", "MacPro6,1"):
        # AppleIntelMCEReporter panics on AMD with these SMBIOS models
        kblock.append({"Arch": "Any", "Comment": "AppleIntelMCEReporter panics on AMD", "Enabled": True,
                       "Identifier": "com.apple.driver.AppleIntelMCEReporter", "MaxKernel": "",
                       "MinKernel": "19.0.0", "Strategy": "Disable"})
    if any(g["status"] == "nootrx" for g in good_dgpus):
        k.append("NootRX.kext")
    k.append("USBToolBox.kext")

    # ---- SSDTs (prebuilt Dortania; SSDTTime output overrides) --------------
    s = []
    if P["vendor"] == "intel":
        if plat not in ("sandybridge", "ivybridge"):
            s.append("SSDT-PLUG-DRTNIA")
        if f["laptop"]:
            s += ["SSDT-EC-USBX-LAPTOP", "SSDT-PNLF"]
            if f["i2c"]:
                s.append("SSDT-XOSI")
        else:
            s.append("SSDT-EC-USBX-DESKTOP" if plat not in ("sandybridge", "ivybridge", "haswell", "broadwell")
                     else "SSDT-EC-DESKTOP")
        if plat in ("coffeelake", "cometlake", "rocketlake", "alderlake", "raptorlake", "icelake"):
            s.append("SSDT-AWAC")
        if plat == "coffeelake":
            if f["lpc"] and 0xA300 <= f["lpc"] <= 0xA32F:
                s.append("SSDT-PMC")
            elif not f["lpc"]:
                W.append("Could not identify the chipset: if this is a 300-series board (not Z370) add SSDT-PMC.")
        if plat == "hedt":
            W.append("HEDT needs SSDT-RTC0-RANGE / SSDT-UNC tailored to the board: run SSDTTime.")
    else:
        s.append("SSDT-EC-DESKTOP")
        if re.search(r"B550|A520", f["board"], re.I):
            s.append("SSDT-CPUR")
        W.append("AMD: prebuilt SSDTs assume generic ACPI paths. Strongly recommended: Tools > SSDTTime "
                 "(FakeEC + USBX) and rebuild - the EFI builder will use its output automatically.")
    if f["laptop"]:
        W.append("Laptop: prebuilt SSDTs rarely match laptop ACPI. Run SSDTTime (EC, PLUG, PNLF, XOSI/GPI0) for best results.")

    # ---- device properties / boot-args --------------------------------------
    dp = {}
    if igpu_present and P["vendor"] == "intel":
        props, notes = hwdb.igpu_properties(plat, f["laptop"], headless=(display != "igpu"))
        igpu_did = next((g.get("did") for g in f["gpus"] if g["kind"] == "igpu"), None)
        if props and f["laptop"] and plat == "cometlake" and igpu_did in (0x9BC4, 0x9BE6, 0x9BF6):
            props["device-id"] = bytes.fromhex("9B3E0000")   # CML-H UHD 630 -> CFL-H id
            notes.append("Comet Lake UHD 630 spoofed to 0x3E9B.")
        if props:
            dp["PciRoot(0x0)/Pci(0x2,0x0)"] = props
        I += notes
    boot_args = ["-v", "keepsyms=1", "debug=0x100"]
    if f["hda"]:
        boot_args.append("alcid=1")
    if any(g.get("did") in (0x731F, 0x7340, 0x7341, 0x73BF, 0x73AF, 0x73A5, 0x73FF, 0x73EF, 0x73E3) for g in good_dgpus):
        boot_args.append("agdpmod=pikera")
    if [g for g in dgpus if g not in good_dgpus] and display != "none":
        boot_args.append("-wegnoegpu")
    if td >= 25:
        boot_args.append("-lilubetaall")
    spoof = {0x73EF: "FF730000", 0x73A5: "BF730000", 0x6FDF: "DF670000", 0x699F: "FF670000"}
    for g in good_dgpus:
        if g.get("did") in spoof:
            if g.get("location"):
                dp[g["location"]] = {"device-id": bytes.fromhex(spoof[g["did"]])}
                I.append("%s: device-id spoof added at %s" % (g["name"], g["location"]))
            else:
                W.append("%s needs a device-id spoof (%s) but its PCI path is unknown - add it manually."
                         % (g["name"], spoof[g["did"]]))

    # ---- macOS version specific notes ---------------------------------------
    if td >= 25:
        if f["hda"]:
            W.append("Tahoe removed AppleHDA: onboard analog audio will not work with AppleALC alone. "
                     "HDMI/DP audio still works. Options after install: restore AppleHDA with a root patcher, "
                     "VoodooHDA, or a USB audio device.")
        I.append("Tahoe (26) is the last macOS release for x86 Macs - the long-term target for hackintosh builds.")
    if td >= 23 and any("Broadcom" in w["name"] for w in f["wifi"]):
        W.append("Broadcom Wi-Fi on Sonoma+ needs an OpenCore Legacy Patcher root patch after install.")
    if any("Intel Wi-Fi" in w["name"] for w in f["wifi"]):
        I.append("Intel Wi-Fi: itlwm needs the HeliPort app after install. Check the itlwm release notes for %s support."
                 % tgt["name"])
    if any(not w["kexts"] for w in f["wifi"]):
        W += [w["name"] for w in f["wifi"] if not w["kexts"]]
    if not [n for n in f["nics"] if n["kext"] or "native" in n["name"]]:
        W.append("No supported Ethernet detected: the macOS installer downloads from the internet "
                 "(recovery-based install). Use a supported USB/PCIe NIC or a full offline installer.")
    for m in f["bad_nvme"]:
        W.append("NVMe '%s' is known to be incompatible/unstable in macOS - install to another drive." % m)
    if plat in ("alderlake", "raptorlake"):
        I.append("Alder/Raptor Lake: CPUID spoofed as Comet Lake + CpuTopologyRebuild for P/E cores. "
                 "If boot fails, disable E-cores in BIOS as a test.")
    if P["vendor"] == "amd":
        I.append("AMD: kernel patches from AMD_Vanilla, core count set to %s." % f["cpu"].get("Cores"))
    if f["laptop"] and f["i2c"]:
        I.append("I2C touchpad detected: VoodooI2C may need GPIO pinning (SSDT-GPI0) - see VoodooI2C docs.")

    comps = components(f, P, tgt, display, good_dgpus, dgpus)
    uefi_apfs_legacy = td <= 19
    sbm = "Disabled" if any(g["status"] in ("webdriver",) for g in good_dgpus) or td <= 17 else "Default"

    plan = {
        "hackpe": VERSION, "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "target": tgt, "platform": plat, "platform_name": P["name"], "laptop": f["laptop"],
        "cpu": f["cpu"].get("Name"), "cores": f["cpu"].get("Cores") or 4, "board": f["board"].strip(),
        "system": f["vendor"].strip(), "display": display, "gpus": f["gpus"], "smbios": smbios,
        "kexts": k, "ssdts": s, "kernel_block": kblock, "components": comps,
        "smbios_friendly": hwdb.SMBIOS_FRIENDLY.get(smbios, smbios), "booter_quirks": hwdb.booter_quirks(plat, f["board"]),
        "kernel_quirks": hwdb.kernel_quirks(plat, f["laptop"], f["vendor"]),
        "device_properties": dp, "boot_args": boot_args, "apfs_legacy": uefi_apfs_legacy,
        "secure_boot_model": sbm, "mac": f["mac"], "amd": P["vendor"] == "amd",
        "cpuid_spoof": plat in ("alderlake", "raptorlake"),
        "flex_ratio": plat in ("sandybridge", "ivybridge", "haswell", "broadwell"),
        "compat": compat, "native_max": hwdb.DARWIN_NAME.get(native_max, "none"),
        "nics": f["nics"], "wifi": f["wifi"], "codecs": f["codecs"],
        "warnings": W, "info": I, "blockers": B,
    }
    if opts.get("xhci_port_limit"):
        plan["kernel_quirks"]["XhciPortLimit"] = True
    return plan


def components(f, P, tgt, display, good_dgpus, dgpus):
    """Plain-language verdict per component: status ok | warn | bad."""
    td, name = tgt["darwin"], tgt["name"]
    C = []

    def add(kind, title, status, note):
        C.append({"kind": kind, "name": title, "status": status, "note": note})

    cpu = f["cpu"].get("Name") or "Unknown CPU"
    if P["status"] == "unsupported" or not P["cpu_max"]:
        add("Processor", cpu, "bad", "%s processors are not supported by macOS." % P["name"])
    elif P["cpu_max"] < td:
        add("Processor", cpu, "bad", "Supported only up to macOS %s." % hwdb.DARWIN_NAME.get(P["cpu_max"], "?"))
    elif P["status"] in ("experimental", "manual"):
        add("Processor", cpu, "warn", "%s: works, but usually needs manual fine-tuning." % P["name"])
    elif P["vendor"] == "amd":
        add("Processor", cpu, "ok", "AMD Ryzen works with special kernel patches (added automatically).")
    else:
        add("Processor", cpu, "ok", "%s - supported." % P["name"])

    for g in f["gpus"]:
        if g["kind"] == "igpu":
            ok = P["igpu_max"] >= td
            if ok and display == "igpu":
                add("Graphics", "Intel integrated graphics", "ok", "Will drive your display. Connect the monitor to the motherboard.")
            elif ok:
                add("Graphics", "Intel integrated graphics", "ok", "Used in the background (video encoding); the graphics card drives the display.")
            elif display == "none":
                add("Graphics", "Intel integrated graphics", "bad", "Not supported on %s by macOS %s." % (P["name"], name))
            else:
                add("Graphics", "Intel integrated graphics", "warn", "Not supported - not needed because your graphics card is.")
        elif g["kind"] == "apu":
            add("Graphics", g["name"], "warn", "AMD APU graphics needs NootedRed (advanced, not automatic).")
        elif g in good_dgpus:
            add("Graphics", g["name"], "ok", "Supported. Connect the monitor to this card." + (" " + g["notes"] if g["notes"] else ""))
        else:
            add("Graphics", g["name"], "warn" if display != "none" else "bad",
                (g["notes"] or "No macOS driver.") + (" It will be switched off in macOS." if display != "none" else
                                                       " A supported AMD card (e.g. RX 580 / RX 6600) is needed."))
    if not f["gpus"]:
        add("Graphics", "No graphics detected", "bad", "Could not identify a graphics device.")

    if f["nics"]:
        for n in f["nics"]:
            good = bool(n["kext"]) or "native" in n["name"]
            add("Ethernet", n["name"], "ok" if good else "warn",
                "Supported - used to download macOS during installation." if good else "May not work in macOS.")
    else:
        add("Ethernet", "None found", "warn", "The installer downloads macOS from Apple: you need a supported network adapter.")
    for w in f["wifi"]:
        if not w["kexts"]:
            add("Wi-Fi", "Wi-Fi card", "warn", "No macOS driver - use Ethernet or replace the card. Not needed for installing.")
        elif "Intel" in w["name"]:
            add("Wi-Fi", "Intel Wi-Fi", "warn", "Works with the HeliPort app after installation (AirDrop/Handoff limited).")
        elif td >= 23:
            add("Wi-Fi", "Broadcom Wi-Fi", "warn", "Needs an extra patch (OpenCore Legacy Patcher) on macOS %s." % name)
        else:
            add("Wi-Fi", "Broadcom Wi-Fi", "ok", "Supported.")
    if f["hda"]:
        if td >= 25:
            add("Audio", ", ".join(f["codecs"]) or "Onboard audio", "warn",
                "Tahoe removed the onboard-audio driver: speakers/headphone jack need a post-install patch. HDMI/USB audio work.")
        else:
            add("Audio", ", ".join(f["codecs"]) or "Onboard audio", "ok", "Supported through AppleALC (layout may need adjusting).")
    for m in f["bad_nvme"]:
        add("Storage", m, "warn", "This SSD is known to have problems in macOS - install macOS on a different drive.")
    if f["laptop"]:
        add("Input", "Keyboard / touchpad", "warn" if f["i2c"] else "ok",
            "I2C touchpad: usually works but may need tuning." if f["i2c"] else "PS/2 keyboard and touchpad supported.")
    fw = f.get("firmware")
    add("Firmware", "UEFI" if fw == "UEFI" else (fw or "Unknown"), "ok" if fw == "UEFI" else "warn",
        "Good - OpenCore needs UEFI." if fw == "UEFI" else "Switch the BIOS to UEFI mode (disable CSM/Legacy).")
    return C


def plan_report(p):
    L = []
    L.append("=" * 72)
    L.append(" HackPE plan: macOS %s %s  ->  %s" % (p["target"]["name"], p["target"]["version"], p["smbios"]))
    L.append("=" * 72)
    L.append(" CPU       : %s  [%s, %s cores]" % (p["cpu"], p["platform_name"], p["cores"]))
    L.append(" System    : %s | board %s | %s" % (p["system"], p["board"], "LAPTOP" if p["laptop"] else "desktop"))
    for g in p["gpus"]:
        L.append(" GPU       : %s  [%s]" % (g["name"], g["status"]))
    L.append(" Display   : %s" % {"igpu": "integrated GPU", "dgpu": "discrete GPU", "none": "NONE"}[p["display"]])
    for n in p["nics"]:
        L.append(" Ethernet  : %s%s" % (n["name"], "  -> " + n["kext"] if n["kext"] else ""))
    for w in p["wifi"]:
        L.append(" Wi-Fi     : %s" % w["name"])
    if p["codecs"]:
        L.append(" Audio     : %s" % ", ".join(p["codecs"]))
    L.append("")
    L.append(" macOS compatibility (native, no root patches):")
    L.append("   " + "  ".join("%s:%s" % (hwdb.MACOS_BY_KEY[k][1].replace(" ", ""), "OK" if v == "yes" else
                                          ("exp" if v == "experimental" else "--")) for k, v in p["compat"].items()))
    L.append("   newest native: %s" % p["native_max"])
    L.append("")
    L.append(" Kexts     : " + ", ".join(x.replace(".kext", "") for x in p["kexts"]))
    L.append(" SSDTs     : " + ", ".join(p["ssdts"]))
    L.append(" boot-args : " + " ".join(p["boot_args"]))
    for title, items in (("BLOCKERS", p["blockers"]), ("WARNINGS", p["warnings"]), ("NOTES", p["info"])):
        if items:
            L.append("")
            L.append(" " + title + ":")
            for it in items:
                L.append("  * " + it)
    L.append("")
    return "\n".join(L)


# ===========================================================================
# EFI builder
# ===========================================================================
def _find_bundle(root, bundle):
    """Shallowest <bundle> dir not nested inside another .kext."""
    best = None
    for dirpath, dirnames, _ in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if ".kext" in rel and rel != ".":
            dirnames[:] = []
            continue
        if bundle in dirnames:
            cand = os.path.join(dirpath, bundle)
            if best is None or cand.count(os.sep) < best.count(os.sep):
                best = cand
    if best is None:  # fall back: bundle nested in an unusual layout
        hits = glob.glob(os.path.join(root, "**", bundle), recursive=True)
        best = min(hits, key=len) if hits else None
    return best


def _kext_exec(kext_dir, bundle_rel):
    info = os.path.join(kext_dir, "Contents", "Info.plist")
    try:
        exe = plistlib.load(open(info, "rb")).get("CFBundleExecutable")
    except Exception:
        exe = None
    if exe and os.path.isfile(os.path.join(kext_dir, "Contents", "MacOS", exe)):
        return "Contents/MacOS/" + exe
    return ""


def ssdt_kinds(name):
    """What an SSDT does, from its file name - used to avoid generated + prebuilt duplicates."""
    n = name.upper()
    k = set()
    for kind, rx in (("EC", r"(^|-)EC(-|\.|$)"), ("USBX", r"USBX"), ("PLUG", r"PLUG"), ("RTC", r"AWAC|RTC"),
                     ("PMC", r"PMC"), ("PNLF", r"PNLF"), ("XOSI", r"XOSI"), ("CPUR", r"CPUR"),
                     ("RHUB", r"RHUB|USB-RESET"), ("UNC", r"UNC"), ("HPET", r"HPET")):
        if re.search(rx, n):
            k.add(kind)
    return k


SCHEMA_ARRAYS = [("ACPI", "Add"), ("ACPI", "Delete"), ("ACPI", "Patch"), ("Booter", "MmioWhitelist"),
                 ("Booter", "Patch"), ("Kernel", "Add"), ("Kernel", "Block"), ("Kernel", "Force"),
                 ("Kernel", "Patch"), ("Misc", "Entries"), ("Misc", "Tools"), ("UEFI", "Drivers"),
                 ("UEFI", "ReservedMemory")]


def conform_entry(entry, template):
    """Give an array entry exactly the key set of this OpenCore release's Sample.plist entry:
    keys we did not set inherit the sample value, keys OpenCore no longer knows are dropped."""
    out = {}
    for k, v in template.items():
        if k in entry:
            out[k] = entry[k]
        elif k == "Enabled":
            out[k] = True
        elif isinstance(v, bool):
            out[k] = False
        elif isinstance(v, str):
            out[k] = ""
        elif isinstance(v, bytes):
            out[k] = b""
        elif isinstance(v, int):
            out[k] = 0
        else:
            out[k] = v
    return out


def _kext_entry(bundle_path, exe, comment="", mink="", maxk=""):
    return {"Arch": "Any", "BundlePath": bundle_path, "Comment": comment, "Enabled": True,
            "ExecutablePath": exe, "MaxKernel": maxk, "MinKernel": mink, "PlistPath": "Contents/Info.plist"}


def tool_exe(oc_root, name):
    d = os.path.join(oc_root, "Utilities", name)
    if IS_WIN:
        return os.path.join(d, name + ".exe")
    if sys.platform == "darwin":
        return os.path.join(d, name)
    return os.path.join(d, name + ".linux")


def gen_serial(oc_root, model):
    exe = tool_exe(oc_root, "macserial")
    try:
        if not IS_WIN:
            os.chmod(exe, 0o755)
        out = subprocess.run([exe, "-m", model, "-g", "-n", "1"], capture_output=True, text=True, timeout=30).stdout
        m = re.search(r"^\s*(\S+)\s*\|\s*(\S+)", out, re.M)
        if m:
            return m.group(1), m.group(2)
    except Exception as e:
        log("warn: macserial failed: %s" % e)
    return None, None


def build_efi(plan, out_dir, cache, ssdt_dir=None, usb_map=None, validate=True):
    if not cache.has("OpenCorePkg"):
        die("OpenCorePkg is not in the cache. Run 'fetch' (needs internet) first.")
    oc = cache.extracted("OpenCorePkg")
    log("OpenCore %s from cache" % cache.manifest["OpenCorePkg"].get("tag"))
    efi = os.path.join(out_dir, "EFI")
    if os.path.exists(efi):
        shutil.rmtree(efi)
    shutil.copytree(os.path.join(oc, "X64", "EFI"), efi)
    ocd = os.path.join(efi, "OC")
    W = []

    # -- prune drivers/tools to the essentials --------------------------------
    drivers = ["OpenRuntime.efi", "ResetNvramEntry.efi"]
    hfs = cache.path("HfsPlus")
    if hfs:
        shutil.copy2(hfs, os.path.join(ocd, "Drivers", "HfsPlus.efi"))
        drivers.insert(0, "HfsPlus.efi")
    else:
        drivers.insert(0, "OpenHfsPlus.efi")
    for fn in os.listdir(os.path.join(ocd, "Drivers")):
        if fn not in drivers:
            os.remove(os.path.join(ocd, "Drivers", fn))
    for fn in os.listdir(os.path.join(ocd, "Tools")):
        if fn != "OpenShell.efi":
            os.remove(os.path.join(ocd, "Tools", fn))

    cfg = plistlib.load(open(os.path.join(oc, "Docs", "Sample.plist"), "rb"))
    for key in [k for k in cfg if k.startswith("#")]:
        del cfg[key]
    # Remember one sample entry per array section: OpenCore adds keys between releases
    # (e.g. UEFI/Drivers/HideVerbose in 1.0.8) - entries are conformed to these templates.
    templates = {}
    for sec, sub in SCHEMA_ARRAYS:
        arr = cfg.get(sec, {}).get(sub)
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            templates[(sec, sub)] = dict(arr[0])

    # -- ACPI -----------------------------------------------------------------
    acpi_add, acpi_patch, covered = [], [], set()
    if ssdt_dir and os.path.isdir(ssdt_dir) and glob.glob(os.path.join(ssdt_dir, "*.aml")):
        log("Using generated/custom SSDTs from %s" % ssdt_dir)
        for aml in sorted(glob.glob(os.path.join(ssdt_dir, "*.aml"))):
            n = os.path.basename(aml)
            if n.upper().startswith(("DSDT", "DMAR")):
                continue
            shutil.copy2(aml, os.path.join(ocd, "ACPI", n))
            acpi_add.append({"Comment": n, "Enabled": True, "Path": n})
            covered |= ssdt_kinds(n)
        pf = os.path.join(ssdt_dir, "patches_OC.plist")
        if os.path.isfile(pf):
            extra = plistlib.load(open(pf, "rb"))
            acpi_patch = extra.get("ACPI", {}).get("Patch", [])
            names = {a["Path"] for a in acpi_add}
            for a in extra.get("ACPI", {}).get("Add", []):
                if a.get("Path") in names:
                    acpi_add = [x for x in acpi_add if x["Path"] != a["Path"]] + [a]
            log("  merged %d ACPI patches from patches_OC.plist" % len(acpi_patch))
    for name in plan["ssdts"]:                 # prebuilt Dortania SSDTs for anything not generated
        kinds = ssdt_kinds(name)
        if covered and kinds & covered:
            if kinds <= covered:
                continue
            # Partly generated (e.g. SSDTTime made SSDT-USBX but found no LPC bridge for SSDT-EC):
            # use the prebuilt variant without the generated part, never drop the missing device.
            sub = name
            if "USBX" in covered:
                sub = sub.replace("-USBX", "")
            if ssdt_kinds(sub) & covered or sub == name:
                W.append("%s only partly generated (%s missing) - add it manually"
                         % (name, ", ".join(sorted(kinds - covered))))
                continue
            log("  %s: %s was generated, using prebuilt %s for the rest" % (name, "/".join(sorted(kinds & covered)), sub))
            name = sub
        src = cache.path("ssdt:" + name)
        if not src:
            W.append("SSDT %s missing from cache - skipped" % name)
            continue
        shutil.copy2(src, os.path.join(ocd, "ACPI", name + ".aml"))
        acpi_add.append({"Comment": name + " (prebuilt)", "Enabled": True, "Path": name + ".aml"})
    cfg["ACPI"]["Add"], cfg["ACPI"]["Delete"], cfg["ACPI"]["Patch"] = acpi_add, [], acpi_patch

    # -- Booter -------------------------------------------------------------------
    cfg["Booter"]["MmioWhitelist"], cfg["Booter"]["Patch"] = [], []
    cfg["Booter"]["Quirks"].update(plan["booter_quirks"])

    # -- DeviceProperties --------------------------------------------------------
    cfg["DeviceProperties"]["Add"] = plan["device_properties"]
    cfg["DeviceProperties"]["Delete"] = {}

    # -- Kexts ---------------------------------------------------------------------
    kadd, seen = [], set()
    order = ["Lilu.kext", "VirtualSMC.kext"] + [x for x in plan["kexts"] if x not in ("Lilu.kext", "VirtualSMC.kext")]
    if "VoodooI2C.kext" in order and "VoodooPS2Controller.kext" in order:   # VoodooInput from I2C wins
        order.remove("VoodooPS2Controller.kext")
        order.insert(order.index("VoodooI2CHID.kext") + 1 if "VoodooI2CHID.kext" in order else len(order),
                     "VoodooPS2Controller.kext")
    extra_kexts = []
    if usb_map and os.path.isdir(usb_map):
        extra_kexts.append(usb_map)
    for bundle in order:
        pkg = hwdb.KEXT_SOURCE.get(bundle)
        root = cache.extracted(pkg) if pkg else None
        src = _find_bundle(root, bundle) if root else None
        if not src:
            W.append("%s not available in cache (package %s) - skipped" % (bundle, pkg))
            continue
        dst = os.path.join(ocd, "Kexts", bundle)
        if os.path.exists(dst):
            continue
        shutil.copytree(src, dst, symlinks=False)
        minv, maxv = hwdb.KEXT_LIMITS.get(bundle, ("", ""))
        kadd.append(_kext_entry(bundle, _kext_exec(dst, bundle), hwdb.KEXT_COMMENT.get(bundle, ""), minv, maxv))
        seen.add(bundle)
        plug = os.path.join(dst, "Contents", "PlugIns")
        if os.path.isdir(plug):
            for pk in sorted(os.listdir(plug)):
                if not pk.endswith(".kext") or pk in seen:
                    continue
                exe = _kext_exec(os.path.join(plug, pk), pk)
                if not exe:
                    continue
                kadd.append(_kext_entry(bundle + "/Contents/PlugIns/" + pk, exe, "plugin of " + bundle))
                seen.add(pk)
    for km in extra_kexts:
        n = os.path.basename(km.rstrip("/\\"))
        shutil.copytree(km, os.path.join(ocd, "Kexts", n))
        kadd.append(_kext_entry(n, _kext_exec(os.path.join(ocd, "Kexts", n), n), "USB port map"))
        log("Added USB map %s" % n)
    if "USBToolBox.kext" in seen and not extra_kexts:
        W.append("USBToolBox.kext added without a UTBMap.kext: map USB ports (Tools > USBToolBox) and rebuild, "
                 "or keep total USB ports per controller <= 15.")
    cfg["Kernel"]["Add"] = kadd
    cfg["Kernel"]["Block"] = plan.get("kernel_block", [])
    cfg["Kernel"]["Force"], cfg["Kernel"]["Patch"] = [], []
    cfg["Kernel"]["Quirks"].update(plan["kernel_quirks"])
    if plan["cpuid_spoof"]:
        cfg["Kernel"]["Emulate"]["Cpuid1Data"] = bytes.fromhex("55060A00000000000000000000000000")
        cfg["Kernel"]["Emulate"]["Cpuid1Mask"] = bytes.fromhex("FFFFFFFF000000000000000000000000")
    if plan["amd"]:
        src = cache.path("AMD_Vanilla")
        if not src:
            W.append("AMD_Vanilla patches.plist missing - AMD systems WILL NOT boot without it")
        else:
            patches = plistlib.load(open(src, "rb"))["Kernel"]["Patch"]
            cores = int(plan["cores"]) & 0xFF
            for pt in patches:
                if "cpuid_cores_per_package" in pt.get("Comment", ""):
                    r = bytearray(pt["Replace"])
                    r[1] = cores
                    pt["Replace"] = bytes(r)
            cfg["Kernel"]["Patch"] = patches
            log("Applied %d AMD_Vanilla patches (cores=%d)" % (len(patches), cores))

    # -- Misc ----------------------------------------------------------------------
    m = cfg["Misc"]
    m["Boot"].update(Timeout=5, PickerMode="Builtin", ShowPicker=True, HideAuxiliary=True, PickerAttributes=17)
    m["Debug"].update(AppleDebug=True, ApplePanic=True, DisableWatchDog=True, Target=3)
    m["Security"].update(AllowSetDefault=True, ScanPolicy=0, Vault="Optional",
                         SecureBootModel=plan["secure_boot_model"], BlacklistAppleUpdate=True)
    m["Entries"] = []
    m["Tools"] = [{"Arguments": "", "Auxiliary": True, "Comment": "UEFI Shell", "Enabled": True,
                   "Flavour": "OpenShell:UEFIShell:Shell", "FullNvramAccess": False, "Name": "UEFI Shell",
                   "Path": "OpenShell.efi", "RealPath": False, "TextMode": False}]

    # -- NVRAM ----------------------------------------------------------------------
    g = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
    nv = cfg["NVRAM"]["Add"][g]
    nv.pop("#INFO (prev-lang:kbd)", None)
    nv["boot-args"] = " ".join(plan["boot_args"])
    nv["csr-active-config"] = bytes.fromhex("00000000")
    nv["prev-lang:kbd"] = b"en-US:0"
    cfg["NVRAM"]["Delete"][g] = sorted(set(cfg["NVRAM"]["Delete"].get(g, []) + ["boot-args", "csr-active-config", "prev-lang:kbd"]))
    cfg["NVRAM"]["WriteFlash"] = True

    # -- PlatformInfo ---------------------------------------------------------------
    serial, mlb = gen_serial(oc, plan["smbios"])
    if not serial:
        W.append("macserial failed: SystemSerialNumber/MLB are placeholders - generate them with GenSMBIOS/macserial.")
    gi = cfg["PlatformInfo"]["Generic"]
    gi["SystemProductName"] = plan["smbios"]
    gi["SystemSerialNumber"] = serial or "W00000000001"
    gi["MLB"] = mlb or "M0000000000000001"
    gi["SystemUUID"] = str(uuid.uuid4()).upper()
    mac = (plan.get("mac") or "").replace(":", "").replace("-", "")
    gi["ROM"] = bytes.fromhex(mac) if re.fullmatch(r"[0-9A-Fa-f]{12}", mac) else os.urandom(6)
    gi["SpoofVendor"] = True
    cfg["PlatformInfo"]["UpdateSMBIOSMode"] = "Custom" if re.search(r"dell|sony|vaio", plan["system"], re.I) else "Create"

    # -- UEFI -----------------------------------------------------------------------
    cfg["UEFI"]["Drivers"] = [{"Arguments": "", "Comment": d, "Enabled": True, "LoadEarly": False, "Path": d}
                              for d in drivers]
    if plan["apfs_legacy"]:
        cfg["UEFI"]["APFS"].update(MinDate=-1, MinVersion=-1)
    if plan["flex_ratio"]:
        cfg["UEFI"]["Quirks"]["IgnoreInvalidFlexRatio"] = True

    for (sec, sub), tpl in templates.items():
        cfg[sec][sub] = [conform_entry(e, tpl) for e in cfg[sec][sub]]

    cfgp = os.path.join(ocd, "config.plist")
    with open(cfgp, "wb") as fh:
        plistlib.dump(cfg, fh, sort_keys=True)
    log("Wrote %s" % cfgp)

    # -- serial info & notes ----------------------------------------------------------
    notes = [plan_report(plan), "Generated SMBIOS: %s  serial %s  MLB %s  UUID %s" %
             (plan["smbios"], gi["SystemSerialNumber"], gi["MLB"], gi["SystemUUID"]),
             "Check the serial at https://checkcoverage.apple.com - it should be INVALID (not purchased) "
             "before signing in to iCloud/iMessage.", "", BIOS_SETTINGS, "",
             "Next steps:", "  1. Copy EFI to the USB (make-usb does this) and boot it (F8/F11/F12 boot menu).",
             "  2. In OpenCore pick 'macOS Base System', erase the target disk in Disk Utility as APFS/GUID.",
             "  3. After install, copy this EFI to the internal disk ESP (Tools > Install EFI to disk).",
             "  4. Map USB ports with USBToolBox and rebuild, then remove -v debug boot-args when stable."]
    if W:
        notes += ["", "Builder warnings:"] + ["  * " + w for w in W]
    open(os.path.join(out_dir, "BUILD-NOTES.txt"), "w", encoding="utf-8").write("\n".join(notes) + "\n")
    json.dump(plan, open(os.path.join(out_dir, "plan.json"), "w"), indent=1, default=lambda o: o.hex())

    ok = True
    if validate:
        ok = ocvalidate(oc, cfgp)
    for w in W:
        log("warn: " + w)
    log("EFI ready: %s" % efi)
    return efi, ok, W


def ocvalidate(oc_root, cfgp):
    exe = tool_exe(oc_root, "ocvalidate")
    if not os.path.isfile(exe):
        log("ocvalidate not available for this OS - skipped")
        return True
    try:
        if not IS_WIN:
            os.chmod(exe, 0o755)
        r = subprocess.run([exe, cfgp], capture_output=True, text=True, timeout=60)
    except Exception as e:
        log("warn: ocvalidate could not run (%s) - validate manually" % e)
        return True
    out = (r.stdout + r.stderr).strip()
    log("ocvalidate: " + (out.splitlines()[-1] if out else "no output"))
    if r.returncode != 0:
        log(out)
        return False
    return True


# ===========================================================================
# macOS recovery
# ===========================================================================
def recovery_catalog(cache):
    """Parse upstream recovery_urls.txt (preferred) with built-in fallback."""
    cat = {}
    for key, name, ver, darwin, (b, m, o) in hwdb.MACOS:
        cat[key] = {"name": name, "version": ver, "board": b, "mlb": m, "os": o, "src": "builtin"}
    oc = cache.extracted("OpenCorePkg") if cache.has("OpenCorePkg") else None
    txt = os.path.join(oc, "Utilities", "macrecovery", "recovery_urls.txt") if oc else None
    if txt and os.path.isfile(txt):
        header = None
        for line in open(txt, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            if not line.startswith("./") and not line.startswith("python"):
                header = line.rstrip(":").strip().lower().replace(" ", "")
                continue
            if "<" in line or "-diag" in line:
                continue
            mm = re.search(r"-b\s+(\S+)\s+-m\s+(\S+)(?:\s+-os\s+(\S+))?", line)
            if not mm or not header:
                continue
            for key in cat:
                if key == header and cat[key]["src"] != "upstream":
                    cat[key].update(board=mm.group(1), mlb=mm.group(2), os=mm.group(3), src="upstream")
    return cat


def macrecovery_script(cache):
    if not cache.has("OpenCorePkg"):
        die("OpenCorePkg not cached - macrecovery.py unavailable. Run fetch first.")
    return os.path.join(cache.extracted("OpenCorePkg"), "Utilities", "macrecovery", "macrecovery.py")


def download_recovery(cache, target, out):
    cat = recovery_catalog(cache)
    if target not in cat:
        die("unknown target %s" % target)
    e = cat[target]
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, macrecovery_script(cache), "-b", e["board"], "-m", e["mlb"]]
    if e["os"]:
        cmd += ["-os", e["os"]]
    cmd += ["download", "-o", out]
    log("Downloading macOS %s recovery from Apple (board %s, %s)..." % (e["name"], e["board"], e["src"]))
    env = dict(os.environ)
    r = subprocess.run(cmd, env=env)
    files = glob.glob(os.path.join(out, "*.dmg"))
    if r.returncode != 0 or not files:
        die("macrecovery failed (exit %s). Check the network connection." % r.returncode)
    log("Recovery saved: %s" % ", ".join(os.path.basename(x) for x in files))


# ===========================================================================
# Disks (Windows / WinPE)
# ===========================================================================
def ps(cmd):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout


def list_disks():
    if not IS_WIN:
        die("disk operations require Windows/WinPE")
    out = ps("Get-Disk | Select-Object Number,FriendlyName,BusType,Size,PartitionStyle,IsBoot,IsSystem | "
             "ForEach-Object { $_.BusType = \"$($_.BusType)\"; $_ } | ConvertTo-Json -Depth 3")
    data = json.loads(out or "[]")
    data = data if isinstance(data, list) else [data]
    letters = {}
    try:
        pj = json.loads(ps("Get-Partition | Where-Object DriveLetter | Select-Object DiskNumber,"
                           "@{n='L';e={[string]$_.DriveLetter}} | ConvertTo-Json") or "[]")
        for x in (pj if isinstance(pj, list) else [pj]):
            letters.setdefault(x["DiskNumber"], []).append(x["L"])
    except Exception:
        pass
    for d in data:
        d["SizeGB"] = round((d.get("Size") or 0) / 1e9, 1)
        d["Letters"] = letters.get(d["Number"], [])
        # WinPE runs from RAM, so its own boot stick is not flagged IsBoot - detect it by content
        d["IsHackPE"] = any(os.path.isfile(l + ":\\sources\\boot.wim") and os.path.isdir(l + ":\\HackPE")
                            for l in d["Letters"])
    return data


def free_letter(prefer="O"):
    used = {l for l in string.ascii_uppercase if os.path.exists(l + ":\\")}
    for l in [prefer] + list("PQRSTUVWKLMN"):
        if l not in used:
            return l
    die("no free drive letter")


def diskpart(script):
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(script)
    try:
        r = subprocess.run(["diskpart", "/s", path], capture_output=True, text=True)
        log(r.stdout.strip()[-1500:])
        if r.returncode != 0:
            die("diskpart failed (%s)" % r.returncode)
    finally:
        os.remove(path)


def check_target_disk(num, allow_any=False):
    disks = {d["Number"]: d for d in list_disks()}
    if num not in disks:
        die("disk %s not found" % num)
    d = disks[num]
    if d.get("IsBoot") or d.get("IsSystem"):
        die("disk %s is the running system/boot disk - refusing" % num)
    if d.get("IsHackPE"):
        die("disk %s is the HackPE boot stick itself - use a second USB stick for the macOS installer" % num)
    if not allow_any and d.get("BusType") != "USB":
        die("disk %s is %s, not USB. Use --any-bus if you really mean it." % (num, d.get("BusType")))
    return d


OFFLINE_MIN_GB = 30


def prep_usb(num, allow_any=False, offline=False, oc_size_mb=4096):
    """GPT + FAT32 'OPENCORE' (EFI + recovery). With offline=True also an exFAT 'MACOS-INST'
    partition for the full installer. Returns (opencore_root, installer_root_or_None)."""
    d = check_target_disk(num, allow_any)
    letter = free_letter("O")
    if offline:
        if d["SizeGB"] < OFFLINE_MIN_GB:
            die("the full offline installer needs a USB stick of at least 32 GB (this one is %.1f GB)" % d["SizeGB"])
        letter2 = next(l for l in "PQRSTUVWKLMN" if l != letter and not os.path.exists(l + ":\\"))
        log("Erasing disk %s (%s, %.1f GB) -> GPT: FAT32 'OPENCORE' (%s:) + exFAT 'MACOS-INST' (%s:)"
            % (num, d["FriendlyName"], d["SizeGB"], letter, letter2))
        diskpart("select disk %d\nclean\nconvert gpt\ncreate partition primary size=%d\n"
                 "format fs=fat32 quick label=OPENCORE\nassign letter=%s\ncreate partition primary\n"
                 "format fs=exfat quick label=MACOS-INST\nassign letter=%s\nexit\n" % (num, oc_size_mb, letter, letter2))
    else:
        letter2 = None
        size = "" if d["SizeGB"] <= 32 else " size=16000"
        log("Erasing disk %s (%s, %.1f GB) -> GPT + FAT32 'OPENCORE' as %s:" % (num, d["FriendlyName"], d["SizeGB"], letter))
        diskpart("select disk %d\nclean\nconvert gpt\ncreate partition primary%s\n"
                 "format fs=fat32 quick label=OPENCORE\nassign letter=%s\nexit\n" % (num, size, letter))
    time.sleep(2)
    for l in filter(None, (letter, letter2)):
        if not os.path.exists(l + ":\\"):
            die("volume %s: did not appear after formatting" % l)
    return letter + ":\\", (letter2 + ":\\" if letter2 else None)


def mount_esp(num):
    letter = free_letter("S")
    ps("$p = Get-Partition -DiskNumber %d | Where-Object { $_.GptType -eq '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' } "
       "| Select-Object -First 1; if (-not $p) { throw 'no EFI System Partition on disk %d' }; "
       "$p | Set-Partition -NewDriveLetter %s" % (num, num, letter))
    return letter


def unmount_letter(letter):
    try:
        ps("Get-Volume -DriveLetter %s | Get-Partition | Remove-PartitionAccessPath -AccessPath '%s:\\'" % (letter, letter))
    except Exception as e:
        log("warn: could not remove %s: (%s)" % (letter, e))


def copy_efi(efi_dir, dest_root):
    dst = os.path.join(dest_root, "EFI")
    if os.path.exists(dst):
        bak = dst + ".bak-" + time.strftime("%Y%m%d%H%M%S")
        log("Existing EFI moved to %s" % bak)
        os.rename(dst, bak)
    shutil.copytree(efi_dir, dst)
    log("EFI copied to %s" % dst)


def confirm(msg, yes):
    if yes:
        return
    a = input("%s\nType YES to continue: " % msg)
    if a.strip() != "YES":
        die("aborted by user", 2)


# ===========================================================================
# High-level flows
# ===========================================================================
def get_hw(args):
    if getattr(args, "hw", None):
        return load_hw(args.hw)
    hw, _ = run_detect()
    return hw


def plan_opts(args):
    o = {}
    for k in ("platform", "smbios", "display"):
        if getattr(args, k, None):
            o[k] = getattr(args, k)
    if getattr(args, "laptop", None) is not None:
        o["laptop"] = args.laptop
    if getattr(args, "xhci_port_limit", False):
        o["xhci_port_limit"] = True
    return o


def default_ssdt_dir():
    d = os.path.join(ROOT, "tools", "SSDTTime", "Results")
    return d if os.path.isdir(d) and glob.glob(os.path.join(d, "*.aml")) else None


def default_usb_map():
    for base in [os.path.join(ROOT, "usbmap")] + [os.path.join(d[0] + ":\\", "HackPE", "usbmap") for d in "CDEFGH"] if IS_WIN \
            else [os.path.join(ROOT, "usbmap")]:
        p = os.path.join(base, "UTBMap.kext")
        if os.path.isdir(p):
            return p
    return None


def cmd_build(args, cache):
    if getattr(args, "disk", None) is None:
        stage("build", "Building OpenCore EFI")
    hw = get_hw(args)
    plan = build_plan(hw, args.target, plan_opts(args))
    log(plan_report(plan))
    if plan["blockers"] and not args.force:
        die("blockers found (see above). Use --force to build anyway.", 3)
    out = args.out or os.path.join(tempfile.gettempdir(), "HackPE-EFI-" + args.target)
    os.makedirs(out, exist_ok=True)
    ssdt = args.ssdt_dir or default_ssdt_dir()
    efi, ok, _ = build_efi(plan, out, cache, ssdt, args.usb_map or default_usb_map())
    if getattr(args, "disk", None) is None:
        stage("done", out)
    return efi, ok


def cmd_make_usb(args, cache):
    stage("check", "Checking the USB stick")
    d = check_target_disk(args.disk, args.any_bus)
    if getattr(args, "offline", False):
        if hwdb.macos(args.target)["darwin"] < 20:
            die("full offline installers exist for Big Sur and newer only")
        if d["SizeGB"] < OFFLINE_MIN_GB:
            die("the full offline installer needs a USB stick of at least 32 GB (this one is %.1f GB)" % d["SizeGB"])
    stage("build", "Building OpenCore EFI for this PC")
    efi, ok = cmd_build(args, cache)
    confirm("ALL DATA on disk %s will be erased." % args.disk, args.yes)
    tgt = hwdb.macos(args.target)
    offline = getattr(args, "offline", False)
    if offline and tgt["darwin"] < 20:
        die("full offline installers exist for Big Sur and newer only")
    with_pe = getattr(args, "with_hackpe", False)
    media = tools.hackpe_media() if with_pe else None
    if with_pe and not media:
        log("warn: HackPE boot media not found - the HackPE boot entry is skipped (only possible when running from HackPE)")
        with_pe = False
    stage("format", "Erasing and formatting the USB stick")
    root, inst = prep_usb(args.disk, args.any_bus, offline, 8192 if with_pe else 4096)
    stage("copy", "Copying OpenCore to the USB stick")
    copy_efi(efi, root)
    shutil.copy2(os.path.join(os.path.dirname(efi), "BUILD-NOTES.txt"), root)
    if not args.no_recovery:
        stage("recovery", "Downloading macOS from Apple")
        download_recovery(cache, args.target, os.path.join(root, "com.apple.recovery.boot"))
    if with_pe:
        stage("hackpe", "Adding HackPE to the OpenCore boot menu")
        try:
            tools.add_hackpe(root, media)
            ocvalidate(cache.extracted("OpenCorePkg"), os.path.join(root, "EFI", "OC", "config.plist"))
        except Exception as e:
            log("warn: HackPE boot entry not added: %s" % e)
    if offline:
        stage("offline", "Downloading the full offline installer (12-18 GB)")
        tools.offline_installer(cache, tgt["version"], tgt["darwin"], os.path.join(inst, "macOS-" + args.target))
        log("Offline install: in Recovery > Terminal run   sh /Volumes/MACOS-INST/install-offline.sh")
    log("")
    log("USB installer ready on %s  ->  boot it on this PC and pick 'macOS Base System' in OpenCore." % root)
    stage("done", root)


def cmd_install_esp(args):
    efi = os.path.join(args.efi, "EFI") if os.path.isdir(os.path.join(args.efi, "EFI")) else args.efi
    if not os.path.isfile(os.path.join(efi, "OC", "OpenCore.efi")):
        die("%s does not look like an OpenCore EFI folder" % efi)
    confirm("The EFI folder on the ESP of disk %s will be replaced (old one kept as EFI.bak-*). "
            "Windows' EFI\\Microsoft is preserved only if you merge manually." % args.disk, args.yes)
    stage("mount", "Opening the EFI partition of disk %s" % args.disk)
    try:
        letter = mount_esp(args.disk)
    except Exception as e:
        die("could not open the EFI partition of disk %s: %s" % (args.disk, e))
    stage("copy", "Installing OpenCore")
    try:
        dst = letter + ":\\EFI"
        os.makedirs(dst, exist_ok=True)
        for sub in ("OC", "BOOT"):
            tgt = os.path.join(dst, sub)
            if os.path.exists(tgt):
                os.rename(tgt, tgt + ".bak-" + time.strftime("%Y%m%d%H%M%S"))
            shutil.copytree(os.path.join(efi, sub), tgt)
        if os.path.isfile(os.path.join(dst, "OC", "Tools", tools.HACKPE_TOOL)) or tools.hackpe_media():
            try:                                     # HackPE entry only if the whole WinPE fits on this ESP
                if os.path.isdir(os.path.join(dst, "Microsoft")):
                    raise RuntimeError("Windows boot loader present on this ESP")
                tools.add_hackpe(letter + ":\\")
            except Exception as e:
                tools.remove_hackpe_tool_entry(os.path.join(dst, "OC"))
                log("HackPE boot entry not installed on this disk (%s) - boot HackPE from the USB stick instead." % e)
        log("OpenCore installed to ESP of disk %s (EFI\\Microsoft untouched)." % args.disk)
    finally:
        unmount_letter(letter)
    stage("done", "disk %s" % args.disk)


def _reach(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        urllib.request.urlopen(req, context=ssl_ctx(), timeout=6)
        return True
    except urllib.error.HTTPError:
        return True        # got an answer -> reachable
    except Exception:
        return False


def preflight(cache):
    admin = True
    if IS_WIN:
        try:
            import ctypes
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            admin = False
    missing = [k for k in hwdb.PACKAGES if not cache.has(k)]
    oc = cache.manifest.get("OpenCorePkg", {}).get("tag") if cache.has("OpenCorePkg") else None
    return {"winpe": IS_WIN and os.path.exists(r"X:\Windows\System32\wpeutil.exe"), "admin": admin,
            "python": pyplatform.python_version(), "opencore": oc, "packages": len(hwdb.PACKAGES) - len(missing),
            "packages_total": len(hwdb.PACKAGES), "missing": missing,
            "net_apple": _reach("http://osrecovery.apple.com/"), "net_github": _reach("https://api.github.com/")}


# ===========================================================================
# Interactive wizard (text UI, works everywhere)
# ===========================================================================
def ask(prompt, choices=None, default=None):
    while True:
        a = input("%s%s: " % (prompt, " [%s]" % default if default else "")).strip() or (default or "")
        if not choices or a in choices:
            return a
        log("  choose one of: " + ", ".join(choices))


def wizard(cache):
    tools.maximize_console()
    log("\n  HackPE %s - Hackintosh installer wizard\n" % VERSION)
    if not cache.has("OpenCorePkg"):
        log("The download cache is empty. Connecting to the internet to fetch OpenCore & kexts...")
        cache.fetch(list(hwdb.PACKAGES))
    log("Detecting hardware...")
    hw, hwp = run_detect() if IS_WIN else (load_hw(ask("Path to hardware json")), None)
    keys = [m[0] for m in hwdb.MACOS]
    for i, (k, n, v, d, _) in enumerate(hwdb.MACOS, 1):
        log("  %d) macOS %s %s" % (i, n, v))
    sel = int(ask("Target macOS", [str(i) for i in range(1, len(keys) + 1)], str(len(keys))))
    target = keys[sel - 1]
    plan = build_plan(hw, target)
    log(plan_report(plan))
    if plan["blockers"] and ask("Blockers found. Continue anyway? (y/n)", ["y", "n"], "n") != "y":
        return
    out = os.path.join(tempfile.gettempdir(), "HackPE-EFI-" + target)
    os.makedirs(out, exist_ok=True)
    efi, ok, _ = build_efi(plan, out, cache, default_ssdt_dir(), default_usb_map())
    if ask("Create a USB installer now? (y/n)", ["y", "n"], "y") != "y":
        log("EFI is in %s" % out)
        return
    disks = [d for d in list_disks() if d.get("BusType") == "USB" and not d.get("IsBoot") and not d.get("IsHackPE")]
    if not disks:
        die("no USB disks found. Insert a USB stick (>= 4 GB) and run the wizard again.")
    for d in disks:
        log("  disk %s: %s  %.1f GB" % (d["Number"], d["FriendlyName"], d["SizeGB"]))
    num = int(ask("Disk number to ERASE", [str(d["Number"]) for d in disks]))
    confirm("ALL DATA on disk %s will be erased." % num, False)
    root, _ = prep_usb(num)
    copy_efi(efi, root)
    shutil.copy2(os.path.join(out, "BUILD-NOTES.txt"), root)
    download_recovery(cache, target, os.path.join(root, "com.apple.recovery.boot"))
    log("\nDone! Read %sBUILD-NOTES.txt, set the BIOS options, reboot and boot from the USB." % root)


# ===========================================================================
# CLI
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(prog="hackpe", description="HackPE hackintosh toolkit engine")
    ap.add_argument("--cache", help="cache dir(s), ';' separated (default: $HACKPE_CACHE or ./cache)")
    sp = ap.add_subparsers(dest="cmd")

    def plan_args(p, need_target=True):
        p.add_argument("--target", required=need_target, choices=[m[0] for m in hwdb.MACOS])
        p.add_argument("--hw", help="hardware json from 'detect' (default: detect now)")
        p.add_argument("--platform", choices=list(hwdb.PLATFORMS), help="override CPU platform")
        p.add_argument("--smbios", help="override SMBIOS model, e.g. iMac20,1")
        p.add_argument("--display", choices=["igpu", "dgpu"], help="force display device")
        g = p.add_mutually_exclusive_group()
        g.add_argument("--laptop", dest="laptop", action="store_true", default=None)
        g.add_argument("--desktop", dest="laptop", action="store_false")
        p.add_argument("--xhci-port-limit", action="store_true", help="enable XhciPortLimit (temporary)")

    p = sp.add_parser("detect"); p.add_argument("--out"); p.add_argument("--summary", action="store_true")
    p = sp.add_parser("plan"); plan_args(p); p.add_argument("--out"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("fetch"); p.add_argument("--only"); p.add_argument("--force", action="store_true")
    p.add_argument("--all", action="store_true")
    p = sp.add_parser("build-efi"); plan_args(p); p.add_argument("--out"); p.add_argument("--force", action="store_true")
    p.add_argument("--ssdt-dir"); p.add_argument("--usb-map")
    p = sp.add_parser("versions"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("recovery"); p.add_argument("--target", required=True); p.add_argument("--out", required=True)
    p = sp.add_parser("disks"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("make-usb"); plan_args(p); p.add_argument("--disk", type=int, required=True)
    p.add_argument("--yes", action="store_true"); p.add_argument("--force", action="store_true")
    p.add_argument("--any-bus", action="store_true"); p.add_argument("--no-recovery", action="store_true")
    p.add_argument("--offline", action="store_true", help="add exFAT partition with the full installer (Big Sur+, 32 GB stick)")
    p.add_argument("--with-hackpe", action="store_true", help="also make HackPE bootable from this stick's OpenCore menu")
    p.add_argument("--out"); p.add_argument("--ssdt-dir"); p.add_argument("--usb-map")
    p = sp.add_parser("install-esp"); p.add_argument("--efi", required=True); p.add_argument("--disk", type=int, required=True)
    p.add_argument("--yes", action="store_true")
    p = sp.add_parser("validate"); p.add_argument("config")
    sp.add_parser("wizard")
    p = sp.add_parser("tools"); p.add_argument("--run", metavar="KEY", help="run one tool (menu key) and exit")
    p = sp.add_parser("import"); p.add_argument("--pkg", required=True); p.add_argument("--file", required=True)
    p.add_argument("--tag", default="local")
    p = sp.add_parser("acpi-dump"); p.add_argument("--out")
    p = sp.add_parser("ssdt-auto"); p.add_argument("--hw"); p.add_argument("--tables")
    p.add_argument("--target", default="tahoe"); p.add_argument("--laptop", dest="laptop", action="store_true", default=None)
    p.add_argument("--desktop", dest="laptop", action="store_false")
    sp.add_parser("ssdttime")
    p = sp.add_parser("app"); p.add_argument("name", choices=list(tools.APPS)); p.add_argument("args", nargs="*")
    p = sp.add_parser("load-drivers"); p.add_argument("--folder"); p.add_argument("--auto", action="store_true")
    sp.add_parser("wifi")
    p = sp.add_parser("backfill-dlls"); p.add_argument("--image", required=True); p.add_argument("scan", nargs="+")
    sp.add_parser("usbtoolbox")
    p = sp.add_parser("offline-installer"); p.add_argument("--target", required=True, choices=[m[0] for m in hwdb.MACOS])
    p.add_argument("--out"); p.add_argument("--list", action="store_true")
    sp.add_parser("gibmacos")
    sp.add_parser("opcore-simplify")
    p = sp.add_parser("hardware-sniffer"); p.add_argument("--out")
    p = sp.add_parser("run"); p.add_argument("program"); p.add_argument("args", nargs="*")
    p = sp.add_parser("status"); p.add_argument("--json", action="store_true")
    sp.add_parser("maximize")
    p = sp.add_parser("console"); g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--hide", action="store_true"); g.add_argument("--show", action="store_true")
    p = sp.add_parser("workarea"); p.add_argument("--bottom", type=int, default=0); p.add_argument("--top", type=int, default=0)
    p = sp.add_parser("windows"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("focus"); p.add_argument("--hwnd", type=int, required=True)
    p = sp.add_parser("custom-tools"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("serial"); p.add_argument("--model", required=True)
    p = sp.add_parser("mount-esp"); p.add_argument("--disk", type=int, required=True)
    p = sp.add_parser("add-hackpe"); p.add_argument("--target", required=True, help="root of an OpenCore FAT32 volume, e.g. O:\\")
    sp.add_parser("tops")
    sp.add_parser("bash")
    p = sp.add_parser("preflight"); p.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    cache = Cache(a.cache.split(";") if a.cache else None)

    if a.cmd == "detect":
        hw, path = run_detect(a.out)
        if a.summary:
            f = analyze(hw)
            log("CPU      : %s -> %s" % (hw["Cpu"].get("Name"), hwdb.PLATFORMS[f["platform"]]["name"]))
            log("System   : %s | %s | %s | firmware %s" % (f["vendor"], f["board"], "laptop" if f["laptop"] else "desktop", f["firmware"]))
            for g in f["gpus"]:
                log("GPU      : %s [%s] %s" % (g["name"], g["status"], g["notes"]))
            for n in f["nics"]:
                log("Ethernet : %s" % n["name"])
            for w in f["wifi"]:
                log("Wi-Fi    : %s" % w["name"])
            for c in f["codecs"]:
                log("Audio    : %s" % c)
            for d in hw.get("Disks", []):
                log("Disk %-4s: %s (%s, %s GB)" % (d.get("Number"), d.get("Model"), d.get("BusType"), d.get("SizeGB")))
            comp, mx = compat_matrix(f)
            log("Newest native macOS: %s" % hwdb.DARWIN_NAME.get(mx, "none"))
        log("hardware json: %s" % path)
    elif a.cmd == "plan":
        plan = build_plan(get_hw(a), a.target, plan_opts(a))
        if a.out:
            json.dump(plan, open(a.out, "w"), indent=1, default=lambda o: o.hex())
        log(json.dumps(plan, indent=1, default=lambda o: o.hex()) if a.json else plan_report(plan))
    elif a.cmd == "fetch":
        pk = a.only.split(",") if a.only else list(hwdb.PACKAGES)
        log("Fetching %d packages into %s" % (len(pk), cache.writable()))
        ok, bad = cache.fetch(pk, a.force)
        log("done: %d ok, %d failed %s" % (len(ok), len(bad), bad if bad else ""))
        sys.exit(1 if "OpenCorePkg" in bad else 0)
    elif a.cmd == "build-efi":
        _, ok = cmd_build(a, cache)
        sys.exit(0 if ok else 4)
    elif a.cmd == "versions":
        cat = recovery_catalog(cache)
        if a.json:
            log(json.dumps(cat))
        else:
            for k, e in cat.items():
                log("%-11s macOS %-12s %-6s board %s %s(%s)" % (k, e["name"], e["version"], e["board"],
                                                                "-os latest " if e["os"] else "", e["src"]))
    elif a.cmd == "recovery":
        download_recovery(cache, a.target, a.out)
    elif a.cmd == "disks":
        d = list_disks()
        log(json.dumps(d) if a.json else "\n".join("%3s  %-6s %7.1f GB  %s%s" % (x["Number"], x["BusType"], x["SizeGB"],
                                                     x["FriendlyName"], "  (BOOT)" if x.get("IsBoot") else
                                                     "  (HackPE stick)" if x.get("IsHackPE") else "") for x in d))
    elif a.cmd == "make-usb":
        cmd_make_usb(a, cache)
    elif a.cmd == "install-esp":
        cmd_install_esp(a)
    elif a.cmd == "validate":
        sys.exit(0 if ocvalidate(cache.extracted("OpenCorePkg"), a.config) else 4)
    elif a.cmd == "preflight":
        r = preflight(cache)
        log(json.dumps(r) if a.json else "\n".join("%-10s %s" % (k, v) for k, v in r.items()))
    elif a.cmd == "add-hackpe":
        stage("hackpe", "Adding HackPE to the OpenCore boot menu")
        tools.add_hackpe(a.target)
        ok = ocvalidate(cache.extracted("OpenCorePkg"), os.path.join(a.target, "EFI", "OC", "config.plist"))
        stage("done", a.target)
        sys.exit(0 if ok else 4)
    elif a.cmd == "console":
        tools.console_window(a.show)
    elif a.cmd == "workarea":
        tools.set_work_area(a.bottom, a.top)
    elif a.cmd == "windows":
        w = tools.list_windows()
        log(json.dumps([{"hwnd": h, "title": t} for h, t in w]) if a.json else "\n".join("%d  %s" % x for x in w))
    elif a.cmd == "focus":
        tools.focus_window(a.hwnd)
    elif a.cmd == "custom-tools":
        ct = tools.custom_tools()
        log(json.dumps(ct) if a.json else "\n".join(ct))
    elif a.cmd == "serial":
        sn, mlb = gen_serial(cache.extracted("OpenCorePkg"), a.model)
        log("Model  %s\nSerial %s\nMLB    %s\nUUID   %s\nROM    %s" % (a.model, sn, mlb, str(uuid.uuid4()).upper(), os.urandom(6).hex().upper()))
        log("Check the serial at https://checkcoverage.apple.com - it should NOT be valid.")
    elif a.cmd == "mount-esp":
        l = mount_esp(a.disk)
        log("EFI partition of disk %d mounted as %s:  (it stays mounted until reboot)" % (a.disk, l))
        stage("done", l + ":\\")
    elif a.cmd == "maximize":
        tools.maximize_console()
    elif a.cmd == "tops":
        sys.exit(tools.run_tops(cache))
    elif a.cmd == "bash":
        sys.exit(tools.run_bash())
    elif a.cmd == "status" and a.json:
        log(json.dumps({k: cache.manifest[k].get("tag") for k in hwdb.PACKAGES if cache.has(k)}, indent=1, sort_keys=True))
    elif a.cmd == "status":
        log("HackPE %s  python %s  %s" % (VERSION, pyplatform.python_version(), "WinPE" if os.path.exists(
            r"X:\Windows\System32\wpeutil.exe") else pyplatform.platform()))
        log("cache dirs: %s" % "; ".join(cache.dirs))
        for k in hwdb.PACKAGES:
            e = cache.manifest.get(k)
            log("  %-28s %s" % (k, (e.get("tag") + "  " + e.get("fetched", "")) if e and cache.has(k) else "-- missing --"))
    elif a.cmd == "import":
        cache.import_file(a.pkg, a.file, a.tag)
    elif a.cmd == "tools":
        if a.run:
            tools.run_tool(a.run, cache, sys.modules[__name__])
        else:
            tools.tools_menu(cache, sys.modules[__name__])
    elif a.cmd == "acpi-dump":
        stage("dump", "Dumping ACPI tables")
        out = tools.acpi_dump(cache, a.out)
        stage("done", out)
    elif a.cmd == "ssdt-auto":
        hw = load_hw(a.hw) if a.hw else run_detect()[0]
        plan = build_plan(hw, a.target, {"laptop": a.laptop} if a.laptop is not None else {})
        stage("ssdt", "Generating SSDTs")
        res, amls = tools.ssdt_auto(cache, plan, a.tables)
        if not amls:
            die("no SSDTs were generated")
        stage("done", res)
    elif a.cmd == "app":
        sys.exit(tools.launch_app(cache, a.name, a.args))
    elif a.cmd == "load-drivers":
        if a.auto:
            tools.auto_load_drivers()
        elif a.folder:
            tools.load_drivers(a.folder)
    elif a.cmd == "wifi":
        tools.wifi_connect()
    elif a.cmd == "backfill-dlls":
        tools.backfill_dlls(a.image, a.scan)
    elif a.cmd == "ssdttime":
        sys.exit(tools.run_ssdttime(cache))
    elif a.cmd == "offline-installer":
        t = hwdb.macos(a.target)
        if not a.list and not a.out:
            die("--out is required (an exFAT/NTFS location: files are larger than 4 GB)")
        stage("offline", "Downloading the full offline installer")
        tools.offline_installer(cache, t["version"], t["darwin"], a.out or tempfile.gettempdir(), a.list)
        stage("done", a.out or "")
    elif a.cmd == "opcore-simplify":
        sys.exit(tools.run_opcore_simplify(cache))
    elif a.cmd == "hardware-sniffer":
        sys.exit(tools.run_hardware_sniffer(cache, a.out)[0])
    elif a.cmd == "gibmacos":
        sys.exit(tools.run_gibmacos(cache))
    elif a.cmd == "usbtoolbox":
        tools.run_usbtoolbox(cache)
    elif a.cmd == "run":
        sys.exit(tools.run_program(a.program, a.args))
    elif a.cmd == "wizard" or a.cmd is None:
        wizard(cache)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("interrupted", 130)
