"""
HackPE tools: ACPI dump, automatic SSDT generation (SSDTTime driven), interactive
SSDTTime, USBToolBox, macserial, custom user tools and the text-mode Tools menu.

Used by:  hackpe.py tools | acpi-dump | ssdt-auto | ssdttime | usbtoolbox | run
"""
import datetime, glob, hashlib, json, os, platform, re, shutil, string, subprocess, sys, tempfile, zipfile

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE_DIR)
IS_WIN = os.name == "nt"
IS_PE = IS_WIN and os.path.exists(r"X:\Windows\System32\wpeutil.exe")


def log(m=""):
    print(m, flush=True)


def work_dir():
    d = r"X:\HackPE-work" if IS_PE else os.path.join(tempfile.gettempdir(), "HackPE-work")
    os.makedirs(d, exist_ok=True)
    return d


def media_dirs():
    """Writable \\HackPE folders on boot media / USB sticks (persist across reboots)."""
    out = []
    if not IS_WIN:
        return out
    for l in string.ascii_uppercase:
        if l == "X":
            continue
        d = l + ":\\HackPE"
        if os.path.isdir(d):
            try:
                p = os.path.join(d, ".w")
                open(p, "w").close()
                os.remove(p)
                out.append(d)
            except OSError:
                pass
    return out


# ===========================================================================
# Output folder: every tool writes its results here, one sub-folder per PC
#   <stick>\HackPE\output\<PC>\{hardware,acpi,ssdt,usb,efi,opcore-simplify,logs}
# On the stick when one is writable (survives reboot), otherwise X:\HackPE-work\output (RAM).
# ===========================================================================
OUTPUT_KINDS = {
    "hardware": "hardware detection (hw.json, report.txt) and Hardware-Sniffer reports",
    "acpi": "ACPI table dumps (one folder per dump)",
    "ssdt": "generated SSDTs + patches_OC.plist (one folder per run; build-efi uses the newest)",
    "usb": "USBToolBox: UTBMap.kext + usb.json (build-efi uses UTBMap.kext), history\\ keeps older maps",
    "efi": "OpenCore EFI per macOS version (EFI\\, BUILD-NOTES.txt, plan.json); the previous build is kept as <target>.previous",
    "opcore-simplify": "EFIs made with OpCore-Simplify",
    "logs": "a log of every long job (USB creation, EFI builds, downloads)",
}
_OUT = {}
_GENERIC = re.compile(r"to be filled|system product name|system manufacturer|default string|not applicable|"
                      r"o\.e\.m|^\s*(none|x|0|1234567890|invalid|system|default|unknown)\s*$", re.I)


def output_root():
    if "root" in _OUT:
        return _OUT["root"]
    d = os.environ.get("HACKPE_OUTPUT")
    cands = [d] if d else [os.path.join(m, "output") for m in media_dirs()]
    cands.append(os.path.join(work_dir(), "output"))
    for c in cands:
        try:
            os.makedirs(c, exist_ok=True)
            probe = os.path.join(c, ".w")
            open(probe, "w").close()
            os.remove(probe)
            break
        except OSError:
            continue
    _OUT["root"] = c
    _write_readme(c)
    return c


def output_is_volatile():
    """True when results only live in RAM (no writable stick) - they are lost at reboot."""
    return IS_PE and os.path.normcase(output_root()).startswith(os.path.normcase(work_dir()))


def _clean(x):
    x = re.sub(r"[^\w.+-]+", "-", (x or "").strip()).strip("-.")
    return x[:40]


def _probe_machine():
    if not IS_WIN:
        return {"Manufacturer": "", "Model": platform.node(), "UUID": platform.node()}
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                            "$p = Get-CimInstance Win32_ComputerSystemProduct; $b = Get-CimInstance Win32_BaseBoard; "
                            "$c = Get-CimInstance Win32_Processor | Select-Object -First 1; "
                            "[pscustomobject]@{Manufacturer=$p.Vendor; Model=$p.Name; UUID=$p.UUID; "
                            "BoardManufacturer=$b.Manufacturer; BoardProduct=$b.Product; Cpu=$c.Name} | ConvertTo-Json"],
                           capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}


def machine(hw=None):
    """Identity of this PC -> folder name like LENOVO_Legion-5_3fa9c1 (stable across boots)."""
    if "machine" in _OUT and hw is None:
        return _OUT["machine"]
    info = {}
    explicit = hw is not None                  # a --hw file may describe another PC: never mix in this PC's ids
    if hw is None:
        hwj = os.path.join(work_dir(), "hw.json")
        if os.path.isfile(hwj):
            try:
                with open(hwj, encoding="utf-8-sig") as f:
                    hw = json.load(f)
            except Exception:
                hw = None
    if hw:
        sy = hw.get("System") or {}
        info = {"Manufacturer": sy.get("Manufacturer"), "Model": sy.get("Model"), "UUID": sy.get("SystemUUID"),
                "BoardManufacturer": sy.get("BoardManufacturer"), "BoardProduct": sy.get("BoardProduct"),
                "Cpu": (hw.get("Cpu") or {}).get("Name")}
    if not info.get("UUID") and not explicit:
        cache_f = os.path.join(work_dir(), "machine.json")
        try:
            with open(cache_f) as f:
                probed = json.load(f)
        except Exception:
            probed = _probe_machine()
            try:
                with open(cache_f, "w") as f:
                    json.dump(probed, f)
            except OSError:
                pass
        info = {k: info.get(k) or probed.get(k) for k in set(info) | set(probed)}
    vendor, model = info.get("Manufacturer") or "", info.get("Model") or ""
    if not model or _GENERIC.search(model):              # generic DIY boards: use the motherboard name
        vendor, model = info.get("BoardManufacturer") or vendor, info.get("BoardProduct") or model
    if _GENERIC.search(vendor or ""):
        vendor = ""
    uid = (info.get("UUID") or "").upper()
    if not uid or _GENERIC.search(uid) or set(uid) <= set("F0-"):
        uid = "|".join(str(info.get(k) or "") for k in ("Manufacturer", "Model", "BoardProduct", "Cpu"))
    short = hashlib.sha1(uid.encode()).hexdigest()[:6]
    name = "_".join(x for x in (_clean(vendor), _clean(model)) if x) or "PC"
    m = {"id": "%s_%s" % (name, short), "vendor": vendor, "model": model, "cpu": info.get("Cpu") or "",
         "board": " ".join(x for x in (info.get("BoardManufacturer"), info.get("BoardProduct")) if x)}
    if hw is None or "machine" not in _OUT:
        _OUT["machine"] = m
    return m


def pc_dir(kind=None, sub=None, stamped=False, hw=None):
    r"""Folder for this PC's results: output\<PC>\<kind>\[<sub>|<timestamp>]  (created)."""
    m = machine(hw)
    d = os.path.join(output_root(), m["id"])
    if kind:
        d = os.path.join(d, kind)
    if sub:
        d = os.path.join(d, sub)
    elif stamped:
        d = os.path.join(d, stamp())
    os.makedirs(d, exist_ok=True)
    _touch_machine(m)
    return d


def latest_run(kind, pattern="*", hw=None):
    r"""Newest timestamped sub-folder of output\<PC>\<kind> containing pattern, or None."""
    base = os.path.join(output_root(), machine(hw)["id"], kind)
    runs = sorted((d for d in glob.glob(os.path.join(base, "2*")) if os.path.isdir(d) and glob.glob(os.path.join(d, pattern))),
                  reverse=True)
    return runs[0] if runs else None


def _touch_machine(m):
    if _OUT.get("touched") == m["id"]:
        return
    _OUT["touched"] = m["id"]
    root = output_root()
    d = os.path.join(root, m["id"])
    try:
        mf = os.path.join(d, "machine.json")
        info = {}
        if os.path.isfile(mf):
            with open(mf) as f:
                info = json.load(f)
        info.update({k: v for k, v in m.items() if v}, last_used=datetime.datetime.now().isoformat(timespec="seconds"))
        info.setdefault("first_seen", info["last_used"])
        with open(mf, "w") as f:
            json.dump(info, f, indent=1)
        rows = []
        for p in sorted(glob.glob(os.path.join(root, "*", "machine.json"))):
            try:
                with open(p) as f:
                    j = json.load(f)
                rows.append("%-40s %-28s %-42s %s" % (os.path.basename(os.path.dirname(p)), (j.get("vendor", "") + " " + j.get("model", "")).strip()[:28],
                                                      j.get("cpu", "")[:42], j.get("last_used", "")))
            except Exception:
                pass
        with open(os.path.join(root, "machines.txt"), "w") as f:
            f.write("PCs with HackPE results on this stick (newest result per folder is used automatically)\n\n")
            f.write("%-40s %-28s %-42s %s\n" % ("FOLDER", "PC", "CPU", "LAST USED"))
            f.write("\n".join(rows) + "\n")
    except OSError:
        pass


def _write_readme(root):
    p = os.path.join(root, "README.txt")
    if os.path.isfile(p):
        return
    lines = ["HackPE output folder", "=" * 20, "",
             "Every HackPE tool saves its results here, in one folder per PC (see machines.txt):", "",
             "  <PC>\\machine.json        which PC this is (vendor, model, CPU)"]
    for k, v in OUTPUT_KINDS.items():
        lines.append("  <PC>\\%-18s %s" % (k + "\\", v))
    lines += ["  downloads\\              tools downloaded by Ferramentas TOPS and similar", "",
              "HackPE picks the newest SSDTs and USB map of the PC it is running on when it builds an EFI.",
              "Delete any folder you no longer need - nothing else depends on it."]
    try:
        with open(p, "w") as f:
            f.write("\r\n".join(lines) + "\r\n")
    except OSError:
        pass


def keep_previous(path):
    """Before rebuilding <path>, keep the last build as <path>.previous (older ones are removed)."""
    if os.path.isdir(path) and os.listdir(path):
        prev = path + ".previous"
        shutil.rmtree(prev, ignore_errors=True)
        try:
            os.rename(path, prev)
        except OSError:
            shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return path


def copy_results(src, kind, hw=None, label=None):
    r"""Snapshot a tool's result folder into output\<PC>\<kind>\<timestamp>; returns it."""
    dst = pc_dir(kind, sub=(stamp() + ("-" + label if label else "")), hw=hw)
    for n in os.listdir(src):
        a = os.path.join(src, n)
        (shutil.copytree if os.path.isdir(a) else shutil.copy2)(a, os.path.join(dst, n))
    return dst


# ===========================================================================
# Window handling: every tool starts maximized
# ===========================================================================
SW_MAXIMIZE = 3


def maximize_console():
    """Grow the console buffer to the largest window the screen allows and maximize it."""
    if not IS_WIN:
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        h = k32.GetStdHandle(-11)
        k32.GetLargestConsoleWindowSize.restype = wintypes.DWORD
        big = k32.GetLargestConsoleWindowSize(h)
        cols, rows = big & 0xFFFF, (big >> 16) & 0xFFFF
        if cols and rows:
            k32.SetConsoleScreenBufferSize(h, wintypes._COORD(max(cols, 120), 9999))
        hwnd = k32.GetConsoleWindow()
        if hwnd:
            u32.ShowWindow(hwnd, SW_MAXIMIZE)
    except Exception:
        pass


def console_window(show):
    """Hide/show the console this process shares (used by the GUIs: no C# compiler needed)."""
    if not IS_WIN:
        return
    import ctypes
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 5 if show else 0)


def set_work_area(bottom_reserved=0, top_reserved=0):
    """Reserve screen space for the HackPE Bar so maximized windows do not cover it.
    WinPE only: on a full Windows desktop Explorer's taskbar owns the work area."""
    if not IS_WIN or not IS_PE:
        return
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    w, h = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    r = wintypes.RECT(0, top_reserved, w, h - bottom_reserved)
    u32.SystemParametersInfoW(0x002F, 0, ctypes.byref(r), 0x02)      # SPI_SETWORKAREA, SPIF_SENDCHANGE


def _startupinfo():
    if not IS_WIN:
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_MAXIMIZE
    return si


def maximize_windows_of(pid, timeout=15.0):
    """Maximize the top-level window(s) of a process once they appear (Qt and some apps ignore
    the STARTUPINFO show flag). Returns True when a window was maximized."""
    if not IS_WIN:
        return False
    import ctypes
    import time
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    end = time.time() + timeout
    while time.time() < end:
        hits = []

        def cb(hwnd, _):
            p = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid and u32.IsWindowVisible(hwnd) and not u32.GetWindow(hwnd, 4):   # GW_OWNER
                hits.append(hwnd)
            return True
        u32.EnumWindows(proto(cb), 0)
        if hits:
            for hwnd in hits:
                u32.ShowWindow(hwnd, SW_MAXIMIZE)
            return True
        time.sleep(0.25)
    return False


def list_windows(skip_titles=("HackPE Bar",)):
    """Visible, un-owned top-level windows with a title: [(hwnd, title)] - the bar's window switcher."""
    if not IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    out = []

    def cb(hwnd, _):
        if u32.IsWindowVisible(hwnd) and not u32.GetWindow(hwnd, 4):        # GW_OWNER
            n = u32.GetWindowTextLengthW(hwnd)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, b, n + 1)
                if b.value and b.value not in skip_titles and b.value != "Program Manager":
                    out.append((int(hwnd), b.value))
        return True
    u32.EnumWindows(proto(cb), 0)
    return out


def focus_window(hwnd):
    """Restore/maximize a window and bring it to the front."""
    if not IS_WIN:
        return
    import ctypes
    u32 = ctypes.windll.user32
    u32.ShowWindow(hwnd, SW_MAXIMIZE)
    u32.BringWindowToTop(hwnd)
    u32.SetForegroundWindow(hwnd)


def run_maximized(cmd, cwd=None, env=None, wait=True):
    """Start a program maximized. Console programs share (and use) our maximized console."""
    maximize_console()
    p = subprocess.Popen(cmd, cwd=cwd, env=env, startupinfo=_startupinfo())
    if IS_WIN:
        import threading
        threading.Thread(target=maximize_windows_of, args=(p.pid,), daemon=True).start()
    if wait:
        return p.wait()
    maximize_windows_of(p.pid, 8.0)       # detached GUI: wait briefly so its window gets maximized
    return 0


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


# ===========================================================================
# SSDTTime + ACPICA (iasl.exe / acpidump.exe)
# ===========================================================================
def pe_machine(path):
    """'x64', 'x86' or None for a Windows executable."""
    try:
        with open(path, "rb") as f:
            d = f.read(4096)
        off = int.from_bytes(d[0x3C:0x40], "little")
        m = int.from_bytes(d[off + 4:off + 6], "little")
        return {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}.get(m)
    except Exception:
        return None


def pe_imports(path):
    """Names of DLLs imported by a PE file (normal + delay-load imports)."""
    import struct
    with open(path, "rb") as f:
        d = f.read()
    try:
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe:pe + 4] != b"PE\0\0":
            return []
        nsec = struct.unpack_from("<H", d, pe + 6)[0]
        optsz = struct.unpack_from("<H", d, pe + 20)[0]
        opt = pe + 24
        magic = struct.unpack_from("<H", d, opt)[0]
        ddir = opt + (112 if magic == 0x20B else 96)
        secs = []
        for i in range(nsec):
            o = opt + optsz + i * 40
            vsize, va, rsize, raw = struct.unpack_from("<IIII", d, o + 8)
            secs.append((va, max(vsize, rsize), raw))

        def off(rva):
            for va, sz, raw in secs:
                if va <= rva < va + sz:
                    return rva - va + raw
            return None

        def cstr(o):
            return d[o:d.index(b"\0", o)].decode("ascii", "replace")
        names = []
        imp_rva = struct.unpack_from("<I", d, ddir + 8)[0]          # import directory
        o = off(imp_rva) if imp_rva else None
        while o is not None and o + 20 <= len(d):
            name_rva = struct.unpack_from("<I", d, o + 12)[0]
            if not name_rva:
                break
            no = off(name_rva)
            if no is not None:
                names.append(cstr(no))
            o += 20
        dl_rva = struct.unpack_from("<I", d, ddir + 13 * 8)[0]      # delay-load directory
        o = off(dl_rva) if dl_rva else None
        while o is not None and o + 32 <= len(d):
            name_rva = struct.unpack_from("<I", d, o + 4)[0]
            if not name_rva:
                break
            no = off(name_rva)
            if no is not None:
                names.append(cstr(no))
            o += 32
        return sorted(set(n.lower() for n in names))
    except Exception:
        return []


def backfill_dlls(image, scan_dirs, host_sys32=r"C:\Windows\System32"):
    """Copy DLLs that programs in scan_dirs need but the WinPE image lacks, from the build
    host (same Windows build), resolving dependencies of copied DLLs too."""
    img32 = os.path.join(image, "Windows", "System32")
    have = {f.lower() for f in os.listdir(img32)}
    bundled = set()
    todo = []
    for sd in scan_dirs:
        for dp, _, fs in os.walk(sd):
            for f in fs:
                if f.lower().endswith((".exe", ".dll")):
                    bundled.add(f.lower())
                    todo.append(os.path.join(dp, f))
    copied, missing, seen = [], set(), set()
    while todo:
        f = todo.pop()
        for dll in pe_imports(f):
            if dll in seen or dll.startswith(("api-ms-", "ext-ms-")) or dll in bundled or dll in have:
                continue
            seen.add(dll)
            src = os.path.join(host_sys32, dll)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(img32, dll))
                have.add(dll)
                copied.append(dll)
                todo.append(src)
                mui = os.path.join(host_sys32, "en-US", dll + ".mui")
                if os.path.isfile(mui):
                    os.makedirs(os.path.join(img32, "en-US"), exist_ok=True)
                    shutil.copy2(mui, os.path.join(img32, "en-US", dll + ".mui"))
            else:
                missing.add(dll)
    log("DLL backfill: copied %d from host: %s" % (len(copied), " ".join(sorted(copied)) or "-"))
    if missing:
        log("DLL backfill: not found on host (apps may fail): %s" % " ".join(sorted(missing)))
    return copied, sorted(missing)


def local_exe(path, pkg=None):
    """Run cached programs from a writable copy: the cache may sit on a read-only CD/ISO, and many tools
    (PyInstaller builds like USBToolBox) write their files next to the .exe, not into the working folder."""
    if not path or not IS_WIN:
        return path
    d = os.path.join(work_dir(), "bin", pkg or os.path.splitext(os.path.basename(path))[0])
    dst = os.path.join(d, os.path.basename(path))
    try:
        if not (os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(path)):
            os.makedirs(d, exist_ok=True)
            shutil.copy2(path, dst)
        return dst
    except OSError as e:
        log("warn: could not copy %s to %s (%s) - running it in place" % (path, d, e))
        return path


def acpica_tool(cache, name):
    """Best runnable iasl/acpidump: 64-bit build first; 32-bit only outside WinPE."""
    for pkg in ("ACPICA64_" + name, "ACPICA_" + name):
        p = cache.path(pkg)
        if not p:
            continue
        if IS_PE and pe_machine(p) == "x86":
            log("note: %s is 32-bit and cannot run in WinPE (no WOW64)" % os.path.basename(p))
            continue
        return local_exe(p, pkg)
    return None


def ssdttime_dir():
    return os.path.join(ROOT, "tools", "SSDTTime")


def prepare_ssdttime(cache):
    """Extract SSDTTime from the cache and drop cached iasl/acpidump into Scripts\\ (offline use)."""
    dst = ssdttime_dir()
    if not os.path.isfile(os.path.join(dst, "SSDTTime.py")):
        z = cache.path("SSDTTime")
        if not z:
            raise RuntimeError("SSDTTime is not in the download cache - run 'Update downloads' with internet")
        tmp = tempfile.mkdtemp(prefix="ssdttime-")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(tmp)
        top = next(os.path.join(tmp, d) for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d)))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(top, dst)
        shutil.rmtree(tmp, ignore_errors=True)
    scripts = os.path.join(dst, "Scripts")
    if IS_WIN:
        for name in ("iasl", "acpidump"):
            t = os.path.join(scripts, name + ".exe")
            if os.path.isfile(t) and IS_PE and pe_machine(t) == "x86":
                os.remove(t)                       # unusable here - replace with the 64-bit build
            p = acpica_tool(cache, name)
            if p and not os.path.isfile(t):
                shutil.copy2(p, t)
        if not os.path.isfile(os.path.join(scripts, "iasl.exe")):
            log("WARNING: no usable iasl.exe - SSDTTime will try to download one (32-bit builds fail in WinPE).")
    return dst


def acpidump_exe(cache):
    return acpica_tool(cache, "acpidump")


def _normalize_dump(folder):
    for f in os.listdir(folder):
        n = f.upper()
        if n.endswith(".DAT"):
            n = n[:-4] + ".aml"
        if n != f:
            os.replace(os.path.join(folder, f), os.path.join(folder, n))


def native_acpi_dump(out):
    """Fallback without acpidump.exe: Win32 GetSystemFirmwareTable('ACPI').
    Limitation: when several SSDTs exist Windows returns only the first one."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    provider = 0x41435049                       # 'ACPI'
    n = k32.EnumSystemFirmwareTables(provider, None, 0)
    buf = ctypes.create_string_buffer(n)
    k32.EnumSystemFirmwareTables(provider, buf, n)
    ids = [int.from_bytes(buf.raw[i:i + 4], "little") for i in range(0, n, 4)]
    seen = {}
    for tid in ids:
        sig = tid.to_bytes(4, "little").decode("ascii", "replace")
        seen[sig] = seen.get(sig, 0) + 1
        if seen[sig] > 1:
            continue
        size = k32.GetSystemFirmwareTable(provider, tid, None, 0)
        if not size:
            continue
        tb = ctypes.create_string_buffer(size)
        k32.GetSystemFirmwareTable(provider, tid, tb, size)
        with open(os.path.join(out, sig.upper() + ".aml"), "wb") as f:
            f.write(tb.raw[:size])
    dup = [s for s, c in seen.items() if c > 1]
    if dup:
        log("note: native dump got only the first of several %s tables - acpidump.exe dumps all" % ", ".join(dup))


def acpi_dump(cache, out=None):
    """Dump all ACPI tables of this PC. Returns folder."""
    if not IS_WIN:
        raise RuntimeError("ACPI dump needs Windows/WinPE (on Linux use /sys/firmware/acpi/tables)")
    out = out or pc_dir("acpi", stamped=True)
    os.makedirs(out, exist_ok=True)
    exe = acpidump_exe(cache)
    if exe:
        log("Dumping ACPI tables with acpidump.exe -> %s" % out)
        r = subprocess.run([exe, "-b"], cwd=out, capture_output=True, text=True)
        if r.returncode != 0:
            log(r.stdout + r.stderr)
        if not glob.glob(os.path.join(out, "dsdt*")) and not glob.glob(os.path.join(out, "DSDT*")):
            subprocess.run([exe, "-b", "-n", "DSDT"], cwd=out, capture_output=True)
        _normalize_dump(out)
    if not glob.glob(os.path.join(out, "*.aml")):
        log("Using the built-in Windows firmware-table API")
        native_acpi_dump(out)
    tables = sorted(os.listdir(out))
    if not any(t.upper().startswith("DSDT") for t in tables):
        raise RuntimeError("no DSDT could be dumped")
    log("Dumped %d tables: %s" % (len(tables), " ".join(t[:-4] for t in tables)))
    log("Saved to %s" % out)
    return out


# --- automatic SSDT generation ---------------------------------------------
PNLF_UID = {"sandybridge": 14, "ivybridge": 14, "haswell": 15, "broadwell": 15, "skylake": 16, "kabylake": 16}


def ssdt_selection(plan):
    """Which SSDTTime generators to run for this platform (Dortania recommendations)."""
    plat, laptop = plan["platform"], plan["laptop"]
    amd = plan.get("amd")
    steps = [("fake_ec", "Embedded controller (SSDT-EC)", {"laptop": laptop})]
    if amd or plat not in ("sandybridge", "ivybridge", "haswell", "broadwell"):
        steps.append(("ssdt_usbx", "USB power properties (SSDT-USBX)", {}))
    if not amd and plat not in ("sandybridge", "ivybridge"):
        steps.append(("plugin_type", "CPU power management (SSDT-PLUG)", {}))
    if plat in ("coffeelake", "cometlake", "icelake", "rocketlake", "alderlake", "raptorlake", "hedt") or amd:
        steps.append(("ssdt_awac", "RTC / AWAC clock fix (SSDT-RTCAWAC)", {}))
    if plat == "coffeelake" and "SSDT-PMC" in plan.get("ssdts", []):
        steps.append(("ssdt_pmc", "Native NVRAM on 300-series (SSDT-PMC)", {}))
    if laptop:
        steps.append(("ssdt_pnlf", "Backlight control (SSDT-PNLF)", {}))
        if "SSDT-XOSI" in plan.get("ssdts", []):
            steps.append(("ssdt_xosi", "I2C touchpad _OSI fix (SSDT-XOSI)", {}))
    return steps


class _AutoAnswer:
    """Replaces SSDTTime's input prompts with safe default answers."""

    def __init__(self, ssdt, uid):
        self.s, self.uid, self.calls = ssdt, uid, 0
        names = list(ssdt.osi_strings)
        self.win10 = str(names.index("Windows 10") + 1) if "Windows 10" in names else "1"

    def __call__(self, prompt="", **kw):
        self.calls += 1
        if self.calls > 60:
            raise RuntimeError("SSDTTime kept asking questions - aborting this step")
        p = prompt.lower()
        if "_uid" in p:
            return str(self.uid)
        if "gpu register" in p:
            return "n"
        if "latest windows version" in p and "default" not in p:
            return self.win10
        if "drag and drop" in p:                 # tables missing - leave the function
            return "m"
        if "are you sure" in p:
            return "y"
        return ""                                 # Enter = documented default


def ssdt_auto(cache, plan, tables=None, log_fn=log):
    """Dump (if needed) and generate the right SSDTs for this PC with SSDTTime.
    Output: tools/SSDTTime/Results (picked up automatically by build-efi)."""
    st = prepare_ssdttime(cache)
    if not tables:
        tables = acpi_dump(cache)
    results = os.path.join(st, "Results")
    if os.path.isdir(results) and os.listdir(results):
        bak, i = results + "-" + stamp(), 1
        while os.path.exists(bak):
            bak, i = "%s-%s-%d" % (results, stamp(), i), i + 1
        os.replace(results, bak)
        log_fn("Previous results moved to %s" % bak)
    sys.path.insert(0, st)
    cwd = os.getcwd()
    os.chdir(st)
    try:
        import importlib
        mod = importlib.import_module("SSDTTime")
        s = mod.SSDT()
        s.resize_window = False
        s.u.resize = lambda *a, **k: None
        s.u.head = lambda *a, **k: None
        s.u.grab = _AutoAnswer(s, 19 if plan["platform"] not in PNLF_UID else PNLF_UID[plan["platform"]])
        log_fn("Loading ACPI tables from %s ..." % tables)
        s.dsdt = s.load_dsdt(tables)
        if not s.dsdt or not s.d.acpi_tables:
            raise RuntimeError("SSDTTime could not load the ACPI tables (is iasl.exe available?)")
        done, failed = [], []
        for fn, desc, kw in ssdt_selection(plan):
            log_fn("")
            log_fn("==> %s" % desc)
            try:
                getattr(s, fn)(**kw)
                done.append(desc)
            except Exception as e:
                log_fn("    failed: %s" % e)
                failed.append(desc)
    finally:
        os.chdir(cwd)
        sys.path.remove(st)
    amls = sorted(os.path.basename(x) for x in glob.glob(os.path.join(results, "*.aml")))
    log_fn("")
    log_fn("Generated: %s" % (", ".join(amls) or "nothing"))
    if os.path.isfile(os.path.join(results, "patches_OC.plist")):
        log_fn("ACPI patches: patches_OC.plist (merged automatically by build-efi)")
    if failed:
        log_fn("Not generated: %s" % ", ".join(failed))
    if amls:
        saved = copy_results(results, "ssdt", label="auto")
        log_fn("Saved to %s  (build-efi uses the newest SSDTs of this PC)" % saved)
        return saved, amls
    return results, amls


def _prepare_zip_tool(cache, pkg, folder, marker):
    dst = os.path.join(ROOT, "tools", folder)
    if not os.path.isfile(os.path.join(dst, marker)):
        z = cache.path(pkg)
        if not z:
            raise RuntimeError("%s is not in the download cache - run 'Update downloads' with internet" % pkg)
        tmp = tempfile.mkdtemp(prefix=folder + "-")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(tmp)
        top = next(os.path.join(tmp, d) for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d)))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(top, dst)
        shutil.rmtree(tmp, ignore_errors=True)
    return dst


def run_ssdttime(cache):
    st = prepare_ssdttime(cache)
    cfg = os.path.join(st, "Scripts", "settings.json")
    if not os.path.isfile(cfg):                     # keep our maximized console size
        with open(cfg, "w") as f:
            f.write('{"resize_window": false}')
    res = os.path.join(st, "Results")
    before = {f: os.path.getmtime(f) for f in glob.glob(os.path.join(res, "*"))}
    rc = run_maximized([sys.executable, "SSDTTime.py"], cwd=st)
    after = {f: os.path.getmtime(f) for f in glob.glob(os.path.join(res, "*"))}
    if glob.glob(os.path.join(res, "*.aml")) and after != before:
        log("SSDTTime results saved to %s" % copy_results(res, "ssdt", label="ssdttime"))
    return rc


# ===========================================================================
# Full offline macOS installer (gibMacOS -> InstallAssistant.pkg, Big Sur and newer)
# ===========================================================================
OFFLINE_SCRIPT = r"""#!/bin/sh
# HackPE - offline macOS install from Recovery (no internet needed)
# In macOS Recovery: Disk Utility > erase the target disk (APFS, GUID, name "Macintosh HD"),
# then Utilities > Terminal and run:     sh /Volumes/MACOS-INST/install-offline.sh
HERE="$(cd "$(dirname "$0")" && pwd)"
PKG="$(find "$HERE" -maxdepth 4 -name InstallAssistant.pkg 2>/dev/null | head -n 1)"
if [ ! -f "$PKG" ]; then echo "InstallAssistant.pkg not found under $HERE"; exit 1; fi
echo "Installer package: $PKG"
echo ""
echo "Volumes:"; ls -1 /Volumes
echo ""
printf "Install macOS on which volume? [Macintosh HD]: "
read T
[ -z "$T" ] && T="Macintosh HD"
TGT="/Volumes/$T"
if [ ! -d "$TGT" ]; then echo "Volume $TGT not found - erase the disk in Disk Utility first."; exit 1; fi
echo ""
echo "1/2 Unpacking the installer onto $T (5-15 minutes, no progress bar)..."
installer -pkg "$PKG" -target "$TGT" || { echo "installer failed"; exit 1; }
APP="$(ls -d "$TGT"/Applications/Install\ macOS*.app 2>/dev/null | head -n 1)"
if [ -z "$APP" ]; then echo "Install app not found on $T"; exit 1; fi
echo "2/2 Starting $(basename "$APP") - the PC restarts several times;"
echo "    in OpenCore always pick 'macOS Installer', later '$T'."
"$APP/Contents/Resources/startosinstall" --volume "$TGT" --agreetolicense --nointeraction
"""


def offline_installer(cache, version, darwin, out, list_only=False):
    """Download Apple's full installer (InstallAssistant.pkg) for an x86-capable build."""
    if darwin < 20:
        raise RuntimeError("full offline installers (InstallAssistant.pkg) exist for Big Sur and newer only")
    gib = _prepare_zip_tool(cache, "gibMacOS", "gibMacOS", "gibMacOS.py")
    os.makedirs(out, exist_ok=True)
    base = [sys.executable, os.path.join(gib, "gibMacOS.py"), "-m", version, "-v", version, "--no-interactive"]
    if list_only:
        return subprocess.call(base + ["-j"], cwd=gib)
    log("Downloading the full macOS %s installer from Apple (12-18 GB) to %s" % (version, out))
    rc = subprocess.call(base + ["-D", "VMM-x86_64", "-o", out], cwd=gib)
    pkg = glob.glob(os.path.join(out, "**", "InstallAssistant.pkg"), recursive=True)
    if rc != 0 or not pkg:
        log("Retrying without the x86 device filter...")
        rc = subprocess.call(base + ["-o", out], cwd=gib)
        pkg = glob.glob(os.path.join(out, "**", "InstallAssistant.pkg"), recursive=True)
    if not pkg:
        raise RuntimeError("download failed - InstallAssistant.pkg not found")
    root = os.path.splitdrive(out)[0] + os.sep if IS_WIN else os.path.dirname(out)
    script = os.path.join(root, "install-offline.sh")
    with open(script, "w", newline="\n") as f:
        f.write(OFFLINE_SCRIPT)
    log("Offline installer ready: %s" % pkg[0])
    log("Recovery helper: %s" % script)
    return pkg[0]


# ===========================================================================
# Portable Git (bash/curl/unzip/git) + jq, and Ferramentas TOPS
# ===========================================================================
def git_root():
    cands = [os.path.join(ROOT, "apps", "PortableGit")]
    if IS_WIN:
        cands += [l + ":\\HackPE\\apps\\PortableGit" for l in string.ascii_uppercase if l != "X"]
    for c in cands:
        if os.path.isfile(os.path.join(c, "bin", "bash.exe")):
            return c
    return None


def bash_env():
    g = git_root()
    if not g:
        return None, None
    env = dict(os.environ)
    paths = [os.path.join(g, x) for x in ("usr\\bin", "mingw64\\bin", "bin", "cmd")]
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    home = os.path.join(work_dir(), "home")
    os.makedirs(os.path.join(home, "Desktop"), exist_ok=True)
    env.update(HOME=home, MSYSTEM="MINGW64", CHERE_INVOKING="1", TERM="xterm-256color")
    return os.path.join(g, "bin", "bash.exe"), env


def run_bash(script=None, args=None, cwd=None):
    bash, env = bash_env()
    if not bash:
        raise RuntimeError("Portable Git (bash) is not on this HackPE stick - rebuild with the default options, "
                           "or run the tool on normal Windows with Git for Windows installed")
    cmd = [bash, "--login", "-i"] if not script else [bash, script] + (args or [])
    log("Git Bash: git, curl, unzip, jq available. Type 'exit' to return.")
    return run_maximized(cmd, cwd=cwd or work_dir(), env=env)


def run_tops(cache):
    """Ferramentas TOPS (hnanoto) - Windows release assets, run with the right interpreter."""
    src = cache.path("FerramentasTops")
    if not src:
        raise RuntimeError("Ferramentas TOPS is not in the download cache - run 'Update downloads'")
    dst = os.path.join(ROOT, "tools", "FerramentasTops")
    if not os.path.isdir(dst):
        shutil.copytree(src if os.path.isdir(src) else os.path.dirname(src), dst)
        for z in glob.glob(os.path.join(dst, "*.zip")):
            with zipfile.ZipFile(z) as zf:
                zf.extractall(os.path.join(dst, os.path.splitext(os.path.basename(z))[0]))
    files = [os.path.join(dp, f) for dp, _, fs in os.walk(dst) for f in fs]
    pick = None
    for ext in (".exe", ".ps1", ".bat", ".cmd", ".sh", ".py"):
        c = [f for f in files if f.lower().endswith(ext) and "unins" not in f.lower()]
        if c:
            pick = sorted(c, key=lambda f: ("tops" not in f.lower() and "ferramentas" not in f.lower(), len(f)))[0]
            break
    if not pick:
        raise RuntimeError("no runnable file found in the Ferramentas TOPS release (%s)" % ", ".join(os.listdir(dst)))
    log("Ferramentas TOPS: %s" % pick)
    desk = os.path.join(work_dir(), "home", "Desktop")
    log("Tools it downloads are copied to %s when it closes." % os.path.join(output_root(), "downloads"))
    if pick.lower().endswith(".sh"):
        rc = run_bash(pick, cwd=os.path.dirname(pick))
    else:
        bash, env = bash_env()                   # give .exe/.ps1/.bat variants git/curl/jq too
        rc = run_maximized(run_cmd_for(pick), cwd=os.path.dirname(pick), env=env)
    if os.path.isdir(desk) and os.listdir(desk):
        dst = os.path.join(output_root(), "downloads", "TOPS-" + stamp())
        shutil.copytree(desk, dst)
        log("Downloads saved to %s" % dst)
    return rc


def run_cmd_for(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return [sys.executable, path]
    if ext == ".ps1":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    if ext in (".cmd", ".bat"):
        return ["cmd.exe", "/c", path]
    return [path]


# ===========================================================================
# OpCore-Simplify + Hardware-Sniffer (lzhoang2801)
# ===========================================================================
def run_hardware_sniffer(cache, out=None):
    """Hardware report + ACPI tables (the input OpCore-Simplify needs)."""
    exe = local_exe(cache.path("HardwareSniffer"), "HardwareSniffer")
    if not exe:
        raise RuntimeError("Hardware-Sniffer is not in the download cache - run 'Update downloads'")
    out = out or pc_dir("hardware", sub="Hardware-Sniffer-" + stamp())
    os.makedirs(out, exist_ok=True)
    log("Hardware-Sniffer: exporting report + ACPI tables to %s" % out)
    rc = run_maximized([exe, "-e", "-o", out], cwd=out)
    log("Saved to %s (use it with OpCore-Simplify on any PC)" % out)
    return rc, out


def prepare_opcore_simplify(cache):
    d = _prepare_zip_tool(cache, "OpCoreSimplify", "OpCore-Simplify", "OpCore-Simplify.py")
    scripts = os.path.join(d, "Scripts")
    for name in ("iasl", "acpidump"):                  # 64-bit ACPICA (32-bit cannot run in WinPE)
        p, t = acpica_tool(cache, name), os.path.join(scripts, name + ".exe")
        if p and IS_WIN and not os.path.isfile(t):
            shutil.copy2(p, t)
    hs = cache.path("HardwareSniffer")
    if hs and not os.path.isfile(os.path.join(scripts, "Hardware-Sniffer-CLI.exe")):
        shutil.copy2(hs, os.path.join(scripts, "Hardware-Sniffer-CLI.exe"))
    return d


def run_opcore_simplify(cache):
    d = prepare_opcore_simplify(cache)
    log("OpCore-Simplify needs internet (it downloads the latest kexts from GitHub).")
    log("Its EFI is copied to %s when it closes." % pc_dir("opcore-simplify"))
    rc = run_maximized([sys.executable, "OpCore-Simplify.py"], cwd=d)
    res = os.path.join(d, "Results")
    if os.path.isdir(res) and os.listdir(res):
        log("OpCore-Simplify results saved to %s" % copy_results(res, "opcore-simplify"))
    return rc


def run_gibmacos(cache):
    gib = _prepare_zip_tool(cache, "gibMacOS", "gibMacOS", "gibMacOS.py")
    return run_maximized([sys.executable, "gibMacOS.py"], cwd=gib)


# ===========================================================================
# HackPE as an OpenCore boot entry (chain-load Windows Boot Manager -> WinPE ramdisk)
# ===========================================================================
HACKPE_TOOL = "HackPE.efi"


def hackpe_media():
    """Root of the HackPE boot media (USB stick / ISO) we are running from, or None."""
    if not IS_WIN:
        return None
    for l in string.ascii_uppercase:
        r = l + ":\\"
        if l != "X" and all(os.path.exists(os.path.join(r, x)) for x in
                            ("sources\\boot.wim", "boot\\boot.sdi", "EFI\\Microsoft\\Boot\\BCD", "EFI\\Boot\\bootx64.efi")):
            return r
    return None


def _tree_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(path) for f in fs)


def hackpe_payload(media):
    """(source, relative destination) pairs that make WinPE bootable from another FAT32 volume."""
    items = [os.path.join("EFI", "Microsoft"), "boot", os.path.join("sources", "boot.wim"), "HackPE"]
    return [(os.path.join(media, a), a) for a in items if os.path.exists(os.path.join(media, a))]


def add_hackpe_tool_entry(cfg_path):
    """Add/refresh the 'HackPE' entry in Misc > Tools of an OpenCore config.plist."""
    import plistlib
    with open(cfg_path, "rb") as f:
        cfg = plistlib.load(f)
    tools_ = cfg.setdefault("Misc", {}).setdefault("Tools", [])
    tpl = dict(tools_[0]) if tools_ else {}
    entry = {"Arguments": "", "Auxiliary": False, "Comment": "HackPE toolkit (Windows PE) - chain-loads Windows Boot Manager",
             "Enabled": True, "Flavour": "HackPE:WindowsPE:Windows", "FullNvramAccess": False,
             "Name": "HackPE Toolkit", "Path": HACKPE_TOOL, "RealPath": True, "TextMode": False}
    if tpl:
        entry = {k: entry.get(k, v) for k, v in tpl.items()} | {k: v for k, v in entry.items() if k in tpl}
    cfg["Misc"]["Tools"] = [t for t in tools_ if t.get("Path") != HACKPE_TOOL] + [entry]
    with open(cfg_path, "wb") as f:
        plistlib.dump(cfg, f, sort_keys=True)


def add_hackpe(target_root, media=None, log_fn=log):
    """Make HackPE bootable from the OpenCore picker of the FAT32 volume at target_root
    (which must contain EFI\\OC). Returns True on success."""
    media = media or hackpe_media()
    if not media:
        raise RuntimeError("HackPE boot media not found (this works when running from the HackPE stick/ISO)")
    oc = os.path.join(target_root, "EFI", "OC")
    cfg = os.path.join(oc, "config.plist")
    if not os.path.isfile(cfg):
        raise RuntimeError("%s has no EFI\\OC\\config.plist" % target_root)
    if os.path.normcase(os.path.abspath(media)) == os.path.normcase(os.path.abspath(target_root)):
        raise RuntimeError("target is the HackPE media itself")
    payload = hackpe_payload(media)
    need = sum(_tree_size(src) for src, _ in payload) + 16 * 1024 * 1024
    free = shutil.disk_usage(target_root).free
    if free < need:
        raise RuntimeError("not enough space on %s: %.1f GB needed, %.1f GB free" % (target_root, need / 1e9, free / 1e9))
    if os.path.isdir(os.path.join(target_root, "EFI", "Microsoft")) and not os.path.isfile(os.path.join(target_root, "sources", "boot.wim")):
        raise RuntimeError("%s already holds a Windows boot loader (EFI\\Microsoft) - refusing to overwrite it" % target_root)
    log_fn("Copying HackPE (%.1f GB) to %s ..." % (need / 1e9, target_root))
    for src, rel in payload:
        dst = os.path.join(target_root, rel)
        if os.path.isdir(src):
            if os.path.basename(src).lower() == "hackpe":
                ignore = shutil.ignore_patterns("reports", "acpi", "ssdt", "Results*", "output", "usbmap")
            else:
                ignore = None
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    # Windows Boot Manager as an OpenCore tool; it reads \\EFI\\Microsoft\\Boot\\BCD of the same volume,
    # whose ramdisk entry points at [boot]\\sources\\boot.wim (= this volume)
    shutil.copy2(os.path.join(media, "EFI", "Boot", "bootx64.efi"), os.path.join(oc, "Tools", HACKPE_TOOL))
    add_hackpe_tool_entry(cfg)
    log_fn("OpenCore picker now has a 'HackPE Toolkit' entry on %s" % target_root)
    return True


def remove_hackpe_tool_entry(oc_dir):
    """Drop the HackPE picker entry (e.g. on an internal ESP that is too small for boot.wim)."""
    import plistlib
    cfg = os.path.join(oc_dir, "config.plist")
    with open(cfg, "rb") as f:
        c = plistlib.load(f)
    before = len(c.get("Misc", {}).get("Tools", []))
    c["Misc"]["Tools"] = [t for t in c["Misc"].get("Tools", []) if t.get("Path") != HACKPE_TOOL]
    if len(c["Misc"]["Tools"]) != before:
        with open(cfg, "wb") as f:
            plistlib.dump(c, f, sort_keys=True)
    try:
        os.remove(os.path.join(oc_dir, "Tools", HACKPE_TOOL))
    except OSError:
        pass


# ===========================================================================
# USBToolBox
# ===========================================================================
def run_usbtoolbox(cache):
    """USBToolBox runs from output\\<PC>\\usb\\USBToolBox: its usb.json (discovered ports) and the kext stay
    with this PC on the stick, so port discovery can continue after a reboot."""
    src = cache.path("USBToolBoxTool")
    if not src:
        raise RuntimeError("USBToolBox is not in the download cache - run 'Update downloads'")
    usb = pc_dir("usb")
    tooldir = pc_dir("usb", sub="USBToolBox")
    exe = os.path.join(tooldir, os.path.basename(src))
    if not (os.path.isfile(exe) and os.path.getsize(exe) == os.path.getsize(src)):
        shutil.copy2(src, exe)
    log("USBToolBox: D = discover ports (plug a USB2 + USB3 device into every port),")
    log("            S = select ports (<= 15 per controller), K = build UTBMap.kext.")
    log("Saved in %s" % usb)
    before = {k: os.path.getmtime(k) for k in glob.glob(os.path.join(tooldir, "**", "UTBMap.kext", "Contents", "Info.plist"), recursive=True)}
    run_maximized([exe], cwd=tooldir)
    if os.path.isfile(os.path.join(tooldir, "usb.json")):
        shutil.copy2(os.path.join(tooldir, "usb.json"), os.path.join(usb, "usb.json"))
    found = sorted(glob.glob(os.path.join(tooldir, "**", "UTBMap.kext"), recursive=True),
                   key=lambda k: os.path.getmtime(os.path.join(k, "Contents", "Info.plist")) if os.path.isfile(os.path.join(k, "Contents", "Info.plist")) else 0)
    new = [k for k in found if before.get(os.path.join(k, "Contents", "Info.plist")) != os.path.getmtime(os.path.join(k, "Contents", "Info.plist"))] \
        if found else []
    if not new:
        log("No new UTBMap.kext was built this time." + (" The previous map is kept." if os.path.isdir(os.path.join(usb, "UTBMap.kext")) else ""))
        return
    cur = os.path.join(usb, "UTBMap.kext")
    if os.path.isdir(cur):                                   # keep the old map
        hist = pc_dir("usb", sub=os.path.join("history", stamp()))
        shutil.copytree(cur, os.path.join(hist, "UTBMap.kext"))
        shutil.rmtree(cur, ignore_errors=True)
    shutil.copytree(new[-1], cur)
    log("UTBMap.kext saved to %s - it is included automatically in the next EFI build for this PC." % cur)


# ===========================================================================
# Bundled Windows apps: Explorer++ (file explorer), Notepad++ (editor), OCAT (config editor)
# ===========================================================================
APPS = {"explorer": ("ExplorerPP", "Explorer++.exe"), "notepad": ("NotepadPP", "notepad++.exe"),
        "ocat": ("OCAT", "OCAuxiliaryTools.exe")}


def app_path(cache, key):
    pkg, exe = APPS[key]
    base = os.path.join(ROOT, "apps", pkg)          # pre-extracted by the ISO build
    hits = glob.glob(os.path.join(base, "**", exe), recursive=True)
    if not hits and cache is not None and cache.has(pkg):
        try:
            os.makedirs(base, exist_ok=True)
            with zipfile.ZipFile(cache.path(pkg)) as z:
                z.extractall(base)
        except OSError:                              # read-only root (rare) -> work dir
            base = os.path.join(work_dir(), "apps", pkg)
            with zipfile.ZipFile(cache.path(pkg)) as z:
                z.extractall(base)
        hits = glob.glob(os.path.join(base, "**", exe), recursive=True)
    return hits[0] if hits else None


def launch_app(cache, key, args=None, wait=False):
    exe = app_path(cache, key)
    if not exe:
        if key == "notepad":
            exe = "notepad.exe"
        else:
            raise RuntimeError("%s is not available - run 'Update downloads' with internet" % APPS[key][1])
    env = dict(os.environ)
    if key == "explorer":
        # WinPE fix: without a Desktop folder Explorer++ shows no folder tree and cannot paste
        os.makedirs(os.path.join(os.environ.get("USERPROFILE", r"X:\Windows\System32\config\systemprofile"), "Desktop"), exist_ok=True)
    if key == "ocat":
        env["QT_OPENGL"] = "software"                # no GPU driver in WinPE -> bundled opengl32sw.dll
        env["QT_QPA_PLATFORM"] = "windows:darkmode=0"
    cmd = [exe] + (args or [])
    log("> %s" % " ".join(cmd))
    return run_maximized(cmd, cwd=os.path.dirname(exe) if os.path.isabs(exe) else None, env=env, wait=wait)


def edit_file(cache, path):
    return launch_app(cache, "notepad", [path])


# ===========================================================================
# Drivers (runtime): load every .inf from a folder, e.g. <stick>\HackPE\drivers
# ===========================================================================
def driver_dirs():
    out = []
    if IS_WIN:
        for l in string.ascii_uppercase:
            d = l + ":\\HackPE\\drivers"
            if l != "X" and os.path.isdir(d):
                out.append(d)
    return out


def load_drivers(folder, quiet=False):
    infs = [os.path.join(dp, f) for dp, _, fs in os.walk(folder) for f in fs if f.lower().endswith(".inf")]
    if not infs:
        if not quiet:
            log("No .inf files in %s" % folder)
        return 0
    ok = 0
    for inf in infs:
        r = subprocess.run(["drvload.exe", inf], capture_output=True, text=True)
        ok += r.returncode == 0
        if not quiet or r.returncode != 0:
            log("  %s %s" % ("ok  " if r.returncode == 0 else "FAIL", os.path.relpath(inf, folder)))
    log("Loaded %d of %d drivers from %s" % (ok, len(infs), folder))
    if IS_PE:
        subprocess.run(["wpeutil.exe", "InitializeNetwork"], capture_output=True)
    return ok


def auto_load_drivers():
    for d in driver_dirs():
        load_drivers(d, quiet=True)


# ===========================================================================
# Wi-Fi (needs a WinRE-based HackPE build: plain WinPE has no WLAN service)
# ===========================================================================
def wifi_available():
    r = subprocess.run(["sc.exe", "query", "wlansvc"], capture_output=True, text=True)
    return r.returncode == 0 and "SERVICE_NAME" in r.stdout


def wifi_connect():
    if not wifi_available():
        log("This HackPE build has no Wi-Fi stack (plain WinPE does not include the WLAN service).")
        log("Options: Ethernet, phone USB tethering, a USB Ethernet adapter - or rebuild HackPE")
        log("with  -WinReWim / -WindowsIso  (WinRE base includes Wi-Fi), see README.")
        return
    subprocess.run(["net", "start", "wlansvc"], capture_output=True)
    os.system("netsh wlan show interfaces")
    os.system("netsh wlan show networks mode=bssid")
    ssid = input("\nSSID to connect to (Enter to cancel): ").strip()
    if not ssid:
        return
    pw = input("Password (empty for an open network): ").strip()
    from xml.sax.saxutils import escape
    sec = ("<authEncryption><authentication>WPA2PSK</authentication><encryption>AES</encryption>"
           "<useOneX>false</useOneX></authEncryption><sharedKey><keyType>passPhrase</keyType>"
           "<protected>false</protected><keyMaterial>%s</keyMaterial></sharedKey>" % escape(pw)) if pw else \
          "<authEncryption><authentication>open</authentication><encryption>none</encryption><useOneX>false</useOneX></authEncryption>"
    xml = ('<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
           '<name>{n}</name><SSIDConfig><SSID><name>{n}</name></SSID></SSIDConfig><connectionType>ESS</connectionType>'
           '<connectionMode>manual</connectionMode><MSM><security>{s}</security></MSM></WLANProfile>').format(n=escape(ssid), s=sec)
    pf = os.path.join(work_dir(), "wifi-profile.xml")
    with open(pf, "w", encoding="utf-8") as f:
        f.write(xml)
    os.system('netsh wlan add profile filename="%s" user=all' % pf)
    os.remove(pf)
    os.system('netsh wlan connect name="%s"' % ssid.replace('"', ""))
    log("Waiting for an IP address...")
    import time
    time.sleep(8)
    os.system("ipconfig | findstr /i \"IPv4\"")


# ===========================================================================
# Custom tools: any .exe/.py/.cmd/.bat/.ps1 in <stick>\HackPE\tools or the toolkit tools folder
# ===========================================================================
TOOL_EXT = (".exe", ".py", ".cmd", ".bat", ".ps1", ".sh")


def custom_tools():
    dirs = [os.path.join(ROOT, "tools", "custom")] + [os.path.join(m, "tools") for m in media_dirs()]
    if IS_WIN:
        dirs += [l + ":\\HackPE\\tools" for l in string.ascii_uppercase if l != "X"]
    seen, out = set(), []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if os.path.isfile(p) and f.lower().endswith(TOOL_EXT) and f.lower() not in seen:
                seen.add(f.lower())
                out.append(p)
        for sub in sorted(os.listdir(d)):              # one level deep: tools\GenSMBIOS\GenSMBIOS.py
            sp = os.path.join(d, sub)
            if os.path.isdir(sp) and sub.lower() != "ssdttime":
                for f in sorted(os.listdir(sp)):
                    if f.lower().startswith(sub.lower()[:4]) and f.lower().endswith(TOOL_EXT) and f.lower() not in seen:
                        seen.add(f.lower())
                        out.append(os.path.join(sp, f))
    return out


def run_program(path, args=None):
    path = path.strip().strip('"')
    if not os.path.isfile(path):
        raise RuntimeError("not found: %s" % path)
    args = args or []
    if path.lower().endswith(".sh"):
        return run_bash(path, args, cwd=os.path.dirname(path))
    cmd = run_cmd_for(path) + args
    log("> %s" % " ".join(cmd))
    return run_maximized(cmd, cwd=os.path.dirname(path))


# ===========================================================================
# Text-mode Tools menu
# ===========================================================================
def _cls():
    os.system("cls" if IS_WIN else "clear")


def _pause(msg="Press Enter to return to the Tools menu..."):
    try:
        input("\n" + msg)
    except EOFError:
        pass


def tool_action(c, cache, engine, hwj=None, customs=None):
    """Run one Tools-menu action. Returns True (pause after), False (no pause) or None (unknown key).
    Shared by the text menu and the HackPE Bar (hackpe.py tools --run KEY)."""
    hwj = hwj or os.path.join(work_dir(), "hw.json")
    customs = custom_tools() if customs is None else customs
    if c == "1":
        acpi_dump(cache)
    elif c == "2":
        if not os.path.isfile(hwj):
            engine.run_detect(hwj)
        plan = engine.build_plan(engine.load_hw(hwj), "tahoe")
        print("Platform: %s, %s" % (plan["platform_name"], "laptop" if plan["laptop"] else "desktop"))
        print("Will generate: " + ", ".join(d for _, d, _ in ssdt_selection(plan)))
        ssdt_auto(cache, plan)
        print("\nNext: option 6 rebuilds the EFI with these SSDTs.")
    elif c == "3":
        run_ssdttime(cache)
    elif c == "4":
        r = latest_run("ssdt", "*.aml")
        runs = sorted(glob.glob(os.path.join(pc_dir("ssdt"), "2*")))
        if r:
            print("SSDT runs of this PC (newest is used by build-efi):")
            for x in runs:
                print("  %s%s" % (os.path.basename(x), "   <- newest" if x == r else ""))
            print("\n%s:" % r)
            for f in sorted(os.listdir(r)):
                print("  %s" % f)
            p = os.path.join(r, "patches_OC.plist")
            if os.path.isfile(p) and IS_WIN and input("\nOpen patches_OC.plist in the editor? (y/N) ").lower() == "y":
                edit_file(cache, p)
        else:
            print("Nothing generated yet - use option 2 or 3.")
    elif c == "5":
        run_usbtoolbox(cache)
    elif c == "m":
        vers = [(m[0], m[2], m[3]) for m in engine.hwdb.MACOS if m[3] >= 20]
        print("Versions: " + ", ".join("%s (%s)" % (k, v) for k, v, _ in vers))
        t = input("Target [tahoe]: ").strip().lower() or "tahoe"
        sel = [x for x in vers if x[0] == t]
        if not sel:
            raise RuntimeError("choose one of: " + ", ".join(k for k, _, _ in vers))
        print("Tip: download straight to an exFAT/NTFS stick or disk (FAT32 cannot hold files > 4 GB).")
        out = input("Destination folder (e.g. P:\\macOS): ").strip().strip('"')
        if not out:
            raise RuntimeError("no destination")
        offline_installer(cache, sel[0][1], sel[0][2], out)
    elif c == "g":
        run_gibmacos(cache)
    elif c == "s":
        run_opcore_simplify(cache)
    elif c == "f":
        run_tops(cache)
    elif c == "b":
        run_bash()
        return False
    elif c == "k":
        run_hardware_sniffer(cache)
    elif c == "v":
        d = pc_dir()
        print("Output folder of this PC: %s" % d)
        if output_is_volatile():
            print("NOTE: no writable stick found - this is in RAM and is lost at reboot. Copy it with Explorer++.")
        launch_app(cache, "explorer", [d])
        return False
    elif c == "u":
        keys = [m[0] for m in engine.hwdb.MACOS]
        print("Finishes a stick flashed from HackPE-usb.img: OpenCore for THIS PC + macOS recovery (nothing is erased).")
        print("Targets: " + ", ".join(keys))
        t = input("Target macOS [tahoe]: ").strip().lower() or "tahoe"
        if t not in keys:
            raise RuntimeError("unknown target %s" % t)
        args = ["prepare-stick", "--target", t]
        if input("Also the full offline installer on a new exFAT partition (32 GB+ stick)? (y/N) ").lower() == "y":
            args.append("--offline")
        hwj_ = os.path.join(work_dir(), "hw.json")
        if os.path.isfile(hwj_):
            args += ["--hw", hwj_]
        subprocess.call([sys.executable, os.path.join(ENGINE_DIR, "hackpe.py")] + args)
    elif c == "6":
        keys = [m[0] for m in engine.hwdb.MACOS]
        print("Targets: " + ", ".join(keys))
        t = input("Target macOS [tahoe]: ").strip().lower() or "tahoe"
        if t not in keys:
            raise RuntimeError("unknown target %s" % t)
        args = ["build-efi", "--target", t]
        if os.path.isfile(hwj):
            args += ["--hw", hwj]
        subprocess.call([sys.executable, os.path.join(ENGINE_DIR, "hackpe.py")] + args)
        print("\nEFI: %s\\EFI   (Expert mode > Install EFI to disk, or copy it to your stick)" % pc_dir("efi", sub=t))
    elif c == "7":
        p = input("Path to config.plist [latest build]: ").strip().strip('"')
        if not p:
            cands = sorted(glob.glob(os.path.join(pc_dir("efi"), "*", "EFI", "OC", "config.plist")), key=os.path.getmtime)
            p = cands[-1] if cands else ""
        engine.ocvalidate(cache.extracted("OpenCorePkg"), p)
    elif c == "8":
        model = input("SMBIOS model (e.g. iMac20,1): ").strip()
        s, m = engine.gen_serial(cache.extracted("OpenCorePkg"), model)
        print("\n  Model  %s\n  Serial %s\n  MLB    %s\n  UUID   %s" % (model, s, m, str(__import__("uuid").uuid4()).upper()))
    elif c == "9":
        n = int(input("Disk number (see DiskPart > list disk): ").strip())
        l = engine.mount_esp(n)
        print("EFI partition of disk %d mounted as %s:  - browse it from the command prompt." % (n, l))
        subprocess.call(["cmd.exe", "/k", "cd /d %s:\\ & dir /s /b EFI\\OC\\*.efi & echo. & echo Type exit when done" % l])
        engine.unmount_letter(l)
    elif c == "a":
        t = input("Drive letter of the OpenCore stick/partition (e.g. O): ").strip().rstrip(":\\")
        if not t:
            return False
        root = t[0].upper() + ":\\"
        add_hackpe(root)
        engine.ocvalidate(cache.extracted("OpenCorePkg"), os.path.join(root, "EFI", "OC", "config.plist"))
    elif c == "h":
        subprocess.call([sys.executable, os.path.join(ENGINE_DIR, "hackpe.py"), "detect", "--summary", "--out", hwj])
    elif c == "n":
        os.system("ipconfig /all")
        if IS_PE and input("\nRe-initialise networking (DHCP)? (y/N) ").lower() == "y":
            os.system("wpeutil InitializeNetwork")
    elif c == "w":
        wifi_connect()
    elif c == "l":
        print("Driver folders on sticks: %s" % (", ".join(driver_dirs()) or "none"))
        d = input("Folder with drivers (.inf, searched recursively): ").strip().strip('"')
        if d:
            load_drivers(d)
    elif c == "x":
        launch_app(cache, "explorer")
        return False
    elif c == "o":
        launch_app(cache, "ocat")
        print("OCAT started. If it does not appear, this PC/WinPE lacks a DLL it needs -")
        print("run OCAT on a normal Windows PC instead (same EFI folder works).")
    elif c == "d":
        run_maximized(["diskpart.exe"])
    elif c == "c":
        run_maximized(["cmd.exe", "/k", "cd /d %s" % work_dir()])
        return False
    elif c == "p":
        run_maximized(["powershell.exe", "-NoExit", "-NoProfile", "-Command", "Set-Location '%s'" % work_dir()])
        return False
    elif c == "e":
        launch_app(cache, "notepad")
        return False
    elif c == "r":
        p = input("Program path (.exe .py .cmd .bat .ps1): ").strip()
        a = input("Arguments (optional): ").strip()
        run_program(p, a.split() if a else None)
    elif re.fullmatch(r"t\d+", c) and 0 < int(c[1:]) <= len(customs):
        run_program(customs[int(c[1:]) - 1])
    else:
        return None
    return True


def run_tool(key, cache, engine):
    """Run a single tool in this (maximized) console - used by the HackPE Bar."""
    if IS_WIN:
        os.system("")
    maximize_console()
    try:
        r = tool_action(key.lower(), cache, engine)
        if r is None:
            print("Unknown tool key: %s" % key)
        elif not r:
            return
    except KeyboardInterrupt:
        print("\ninterrupted")
    except Exception as e:
        print("\nERROR: %s" % e)
    _volatile_note()
    _pause("Press Enter to close this window...")


def _volatile_note():
    try:
        if output_is_volatile():
            print("\nNOTE: results are in %s (RAM) - no writable HackPE stick was found." % output_root())
            print("      Copy that folder to a USB stick before rebooting (Bar > Apps > File explorer).")
    except Exception:
        pass


def tools_menu(cache, engine):
    """engine = the hackpe module (avoids a circular import)."""
    if IS_WIN:
        os.system("")                       # enable VT colours in conhost
    hwj = os.path.join(work_dir(), "hw.json")
    maximize_console()
    while True:
        _cls()
        customs = custom_tools()
        print("  ================================================================")
        print("   HackPE  -  Tools")
        print("  ================================================================")
        print("   ACPI")
        print("    1  Dump ACPI tables of this PC              (acpidump)")
        print("    2  Auto-generate SSDTs for this PC          (dump + SSDTTime)")
        print("    3  SSDTTime - interactive, all options")
        print("    4  Show generated SSDTs / ACPI patches")
        print("   USB / macOS")
        print("    5  USBToolBox - map USB ports -> UTBMap.kext")
        print("    U  Prepare THIS HackPE stick for macOS (flashed image: EFI + recovery)")
        print("    V  Open the output folder of this PC (all results: hardware, ACPI, SSDTs, USB map, EFIs, logs)")
        print("    M  Download full OFFLINE macOS installer (gibMacOS, Big Sur+)")
        print("    G  gibMacOS - interactive (all versions / catalogs)")
        print("   EFI")
        print("    6  Rebuild EFI with generated SSDTs + USB map")
        print("    S  OpCore-Simplify - alternative automatic EFI generator (online)")
        print("    F  Ferramentas TOPS - hackintosh tool collection (online)")
        print("    K  Hardware-Sniffer - hardware report + ACPI dump (for OpCore-Simplify)")
        print("    7  Validate a config.plist                  (ocvalidate)")
        print("    8  Generate a new SMBIOS serial             (macserial)")
        print("    9  Open EFI partition of a disk             (mount as S:)")
        print("    A  Add HackPE to the OpenCore boot menu of a USB stick")
        print("   Apps")
        print("    X  File explorer (Explorer++)       E  Text editor (Notepad++)")
        print("    O  OCAuxiliaryTools (config.plist editor / OpenCore updater)")
        print("   System")
        print("    H  Hardware report         N  Network status       W  Wi-Fi connect")
        print("    L  Load drivers from a folder (.inf, e.g. touchpad / LAN / Wi-Fi)")
        print("    D  DiskPart                C  Command prompt       P  PowerShell")
        print("    R  Run a program (.exe .py .cmd .bat .ps1 .sh)   B  Git Bash shell")
        if customs:
            print("   Your tools  (from \\HackPE\\tools on the stick)")
            for i, c in enumerate(customs, 1):
                print("   %2s  %s" % ("T%d" % i, os.path.basename(c)))
        print("   ----------------------------------------------------------------")
        print("    Q  Back to main menu")
        try:
            c = input("\n   Choice: ").strip().lower()
        except EOFError:
            return
        if c in ("q", ""):
            return
        _cls()
        try:
            if not tool_action(c, cache, engine, hwj, customs):
                continue
        except KeyboardInterrupt:
            print("\ninterrupted")
        except Exception as e:
            print("\nERROR: %s" % e)
        _pause()
