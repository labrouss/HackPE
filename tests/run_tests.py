#!/usr/bin/env python3
"""
HackPE regression tests (run in CI on Linux, or locally).

  python tests/run_tests.py [--cache DIR] [--offline]

1. downloads OpenCore + the core packages through the engine's own fetch code
2. plans every fixture against every macOS version and checks expectations
3. builds real EFIs and validates each config.plist with OpenCore's ocvalidate
"""
import argparse, glob, json, os, plistlib, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))
import hackpe  # noqa: E402
import hwdb    # noqa: E402

CORE = ["SSDTTime", "OpenCorePkg", "Lilu", "VirtualSMC", "WhateverGreen", "AppleALC", "RestrictEvents", "NVMeFix",
        "IntelMausi", "VoodooPS2", "AMD_Vanilla", "HfsPlus"] + [k for k in hwdb.PACKAGES if k.startswith("ssdt:")]

# fixture -> {target: (expected smbios, expect blockers?)}
EXPECT = {
    "cfl_rx580":  {"tahoe": ("iMac20,1", False), "sonoma": ("iMac19,1", False), "highsierra": ("iMac18,3", False)},
    "ryzen_6650": {"tahoe": ("MacPro7,1", False), "catalina": ("MacPro7,1", False)},
    "cml_laptop": {"tahoe": ("MacBookPro16,1", False), "sequoia": ("MacBookPro15,1", False)},
    "adl_rtx":    {"tahoe": ("MacPro7,1", True)},
    "hsw_igpu":   {"tahoe": ("MacPro7,1", True), "monterey": ("iMac17,1", False)},
}

fails = []


def check(cond, msg):
    print(("  ok    " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "hackpe-test-cache"))
    ap.add_argument("--offline", action="store_true", help="use the cache as-is")
    ap.add_argument("--tables", help="folder of real ACPI tables (.aml) to test SSDT auto-generation")
    ap.add_argument("--iasl", help="native iasl binary for SSDTTime (Linux/macOS)")
    a = ap.parse_args()
    cache = hackpe.Cache([a.cache])
    os.environ["HACKPE_OUTPUT"] = tempfile.mkdtemp(prefix="hackpe-output-")   # isolated output folder

    print("== fetch")
    if not a.offline:
        ok, bad = cache.fetch(CORE)
        check("OpenCorePkg" in ok, "OpenCorePkg downloaded")
        check(not bad, "all core packages downloaded %s" % (bad or ""))
        cache = hackpe.Cache([a.cache])

    print("== recovery catalog")
    cat = hackpe.recovery_catalog(cache)
    check(cat["tahoe"]["src"] == "upstream", "Tahoe entry parsed from upstream recovery_urls.txt")
    check(cat["tahoe"]["os"] == "latest", "Tahoe uses -os latest")

    print("== tools")
    import tools as _t
    sh = os.path.join(tempfile.mkdtemp(), "install-offline.sh")
    open(sh, "w", newline="\n").write(_t.OFFLINE_SCRIPT)
    check(subprocess.run(["sh", "-n", sh]).returncode == 0, "install-offline.sh is valid shell")
    check("startosinstall" in _t.OFFLINE_SCRIPT and "installer -pkg" in _t.OFFLINE_SCRIPT, "offline helper installs + starts installer")
    for pkg in ("ExplorerPP", "NotepadPP", "OCAT", "OpCoreSimplify", "HardwareSniffer", "gibMacOS", "SSDTTime", "USBToolBoxTool",
                "FerramentasTops", "PortableGit", "jq"):
        check(pkg in hwdb.PACKAGES, "package %s defined" % pkg)

    print("== plans")
    fixtures = sorted(f[:-5] for f in os.listdir(os.path.join(HERE, "fixtures")) if f.endswith(".json"))
    for fx in fixtures:
        hw = hackpe.load_hw(os.path.join(HERE, "fixtures", fx + ".json"))
        for key, *_ in hwdb.MACOS:
            plan = hackpe.build_plan(hw, key)
            json.dumps(plan, default=lambda o: o.hex())          # must serialise for the GUI
            check(bool(plan["components"]), "%s/%s has component verdicts" % (fx, key))
            exp = EXPECT.get(fx, {}).get(key)
            if exp:
                check(plan["smbios"] == exp[0], "%s/%s smbios %s == %s" % (fx, key, plan["smbios"], exp[0]))
                check(bool(plan["blockers"]) == exp[1], "%s/%s blockers=%s" % (fx, key, bool(plan["blockers"])))

    print("== EFI builds + ocvalidate")
    out_root = tempfile.mkdtemp(prefix="hackpe-efi-")
    oc = cache.extracted("OpenCorePkg")
    ocv = hackpe.tool_exe(oc, "ocvalidate")
    os.chmod(ocv, 0o755)
    for fx in fixtures:
        hw = hackpe.load_hw(os.path.join(HERE, "fixtures", fx + ".json"))
        for key in ("highsierra", "catalina", "monterey", "sonoma", "sequoia", "tahoe"):
            plan = hackpe.build_plan(hw, key)
            out = os.path.join(out_root, "%s-%s" % (fx, key))
            os.makedirs(out)
            efi, ok, _ = hackpe.build_efi(plan, out, cache, validate=False)
            cfg = os.path.join(efi, "OC", "config.plist")
            r = subprocess.run([ocv, cfg], capture_output=True, text=True)
            good = r.returncode == 0 and "No issues found" in r.stdout
            check(good, "%s/%s ocvalidate" % (fx, key))
            if not good:
                print("        " + (r.stdout + r.stderr).strip().replace("\n", "\n        "))
            p = plistlib.load(open(cfg, "rb"))
            check(p["Kernel"]["Add"][0]["BundlePath"] == "Lilu.kext", "%s/%s Lilu loads first" % (fx, key))
            check(p["PlatformInfo"]["Generic"]["SystemSerialNumber"] != "W00000000001", "%s/%s serial generated" % (fx, key))
            if plan["amd"]:
                cores = [x["Replace"][1] for x in p["Kernel"]["Patch"] if "cpuid_cores_per_package" in x["Comment"]]
                check(cores and all(c == plan["cores"] for c in cores), "%s/%s AMD core count patched" % (fx, key))

    print("== HackPE as OpenCore boot entry")
    import tools as _t2
    media = tempfile.mkdtemp(prefix="pe-media-")
    for rel, data in ((("sources", "boot.wim"), b"wim"), (("boot", "boot.sdi"), b"sdi"),
                      (("EFI", "Microsoft", "Boot", "BCD"), b"bcd"), (("EFI", "Boot", "bootx64.efi"), b"MZ"),
                      (("HackPE", "cache", "manifest.json"), b"{}")):
        os.makedirs(os.path.join(media, *rel[:-1]), exist_ok=True)
        open(os.path.join(media, *rel), "wb").write(data)
    stick = tempfile.mkdtemp(prefix="oc-stick-")
    plan = hackpe.build_plan(hackpe.load_hw(os.path.join(HERE, "fixtures", "cfl_rx580.json")), "tahoe")
    efi, _, _ = hackpe.build_efi(plan, stick, cache, validate=False)
    _t2.add_hackpe(stick, media, log_fn=lambda m: None)
    cfgp = os.path.join(efi, "OC", "config.plist")
    r = subprocess.run([ocv, cfgp], capture_output=True, text=True)
    check(r.returncode == 0 and "No issues found" in r.stdout, "config with HackPE tool entry passes ocvalidate")
    ent = [x for x in plistlib.load(open(cfgp, "rb"))["Misc"]["Tools"] if x["Path"] == "HackPE.efi"]
    check(len(ent) == 1 and ent[0]["RealPath"] and not ent[0]["Auxiliary"], "HackPE entry is visible and uses RealPath")
    check(all(os.path.exists(os.path.join(stick, *p)) for p in (("sources", "boot.wim"), ("boot", "boot.sdi"),
          ("EFI", "Microsoft", "Boot", "BCD"), ("EFI", "OC", "Tools", "HackPE.efi"))), "WinPE files copied next to OpenCore")
    check(open(os.path.join(stick, "EFI", "BOOT", "BOOTx64.efi"), "rb").read(2) != b"MZ" or
          os.path.getsize(os.path.join(stick, "EFI", "BOOT", "BOOTx64.efi")) > 2, "OpenCore BOOTx64.efi not overwritten")
    shutil.rmtree(media, ignore_errors=True)

    iasl = a.iasl or shutil.which("iasl")
    table_sets = []
    if iasl:
        # Deterministic: a synthetic DSDT shaped like a real desktop (PCI0 -> LPCB, RTC, Processor objects)
        synth = tempfile.mkdtemp(prefix="hackpe-synth-")
        r = subprocess.run([iasl, "-p", os.path.join(synth, "DSDT"), os.path.join(HERE, "acpi", "DSDT-desktop.dsl")],
                           capture_output=True, text=True)
        check(os.path.isfile(os.path.join(synth, "DSDT.aml")), "synthetic DSDT compiles")
        table_sets.append(("synthetic desktop DSDT", synth, True))
    if a.tables:
        # Smoke test on the CI runner's own tables. They are a VM's (no LPC bridge, thousands of
        # ACPI0007 CPUs) so SSDTTime cannot build SSDT-EC there - the EFI must still get an EC.
        table_sets.append(("this machine's ACPI tables " + a.tables, a.tables, False))
    if table_sets:
        import tools as _tl
        st = _tl.prepare_ssdttime(cache)
        if iasl:
            shutil.copy2(iasl, os.path.join(st, "Scripts", "iasl"))
    for label, tables, strict in table_sets:
        print("== SSDT auto-generation on %s" % label)
        for fx in fixtures:
            plan = hackpe.build_plan(hackpe.load_hw(os.path.join(HERE, "fixtures", fx + ".json")), "tahoe")
            res, amls = _tl.ssdt_auto(cache, plan, tables, log_fn=lambda m: None)
            if strict:
                check("SSDT-EC.aml" in amls, "%s: SSDT-EC generated (%s)" % (fx, ", ".join(amls)))
                if plan["laptop"]:
                    check("SSDT-PNLF.aml" in amls, "%s: SSDT-PNLF generated" % fx)
            out = tempfile.mkdtemp(prefix="hackpe-hyb-")
            efi, _, _ = hackpe.build_efi(plan, out, cache, ssdt_dir=res, validate=False)
            cfgp = os.path.join(efi, "OC", "config.plist")
            adds = [x["Path"] for x in plistlib.load(open(cfgp, "rb"))["ACPI"]["Add"]]
            kinds = [hackpe.ssdt_kinds(x) for x in adds]
            check(sum("EC" in k for k in kinds) == 1, "%s: exactly one EC SSDT in the EFI (%s)" % (fx, ", ".join(adds)))
            check(sum("USBX" in k for k in kinds) <= 1, "%s: no duplicate USBX" % fx)
            r = subprocess.run([ocv, cfgp], capture_output=True, text=True)
            check(r.returncode == 0 and "No issues found" in r.stdout, "%s: EFI with generated SSDTs passes ocvalidate" % fx)
            if r.returncode != 0:
                print("        " + r.stdout.strip().replace("\n", "\n        "))

    print("== USB disk image (starter OpenCore + layout)")
    st_out = tempfile.mkdtemp(prefix="hackpe-starter-")
    sefi, sok = hackpe.build_starter_efi(cache, st_out)
    check(sok, "starter EFI passes ocvalidate")
    scfg = plistlib.load(open(os.path.join(sefi, "OC", "config.plist"), "rb"))
    check(scfg["Misc"]["Security"]["ScanPolicy"] == 0 and not scfg["Kernel"]["Add"] and not scfg["ACPI"]["Add"],
          "starter EFI: ScanPolicy 0, no kexts/SSDTs (boots on any PC)")
    check(not scfg["PlatformInfo"]["UpdateSMBIOS"], "starter EFI does not touch SMBIOS")
    if all(shutil.which(t) for t in ("sgdisk", "mkfs.fat", "mcopy", "mdir")):
        media = tempfile.mkdtemp(prefix="hackpe-media-")
        for rel, data in ((("EFI", "Boot", "bootx64.efi"), b"MZ" + b"\0" * 4096), (("EFI", "Microsoft", "Boot", "BCD"), b"bcd"),
                          (("boot", "boot.sdi"), b"sdi"), (("sources", "boot.wim"), os.urandom(1 << 20)),
                          (("HackPE", "cache", "manifest.json"), b"{}")):
            os.makedirs(os.path.join(media, *rel[:-1]), exist_ok=True)
            open(os.path.join(media, *rel), "wb").write(data)
        img = os.path.join(tempfile.mkdtemp(prefix="hackpe-img-"), "HackPE-usb.img")
        r = subprocess.run([sys.executable, os.path.join(HERE, "..", "build", "make_disk_image.py"), "--efi", st_out,
                            "--media", media, "--out", img, "--macos-mib", "64", "--hackpe-free-mib", "32"],
                           capture_output=True, text=True)
        check(r.returncode == 0 and os.path.isfile(img), "make_disk_image builds an image")
        if r.returncode != 0:
            print("        " + (r.stdout + r.stderr).strip().replace("\n", "\n        "))
        else:
            gpt = subprocess.run(["sgdisk", "-p", img], capture_output=True, text=True).stdout
            parts = re.findall(r"^\s+(\d+)\s+(\d+)\s+\d+\s+\S+ \S+\s+(\w+)\s+(\S+)", gpt, re.M)
            check([(p[2], p[3]) for p in parts] == [("EF00", "EFI"), ("0700", "HACKPE"), ("0700", "MACOS")],
                  "GPT layout EFI(ESP) | HACKPE | MACOS (%s)" % parts)
            env = dict(os.environ, MTOOLS_SKIP_CHECK="1")

            def ls(num, path):
                off = int(dict((p[0], p[1]) for p in parts)[str(num)]) * 512
                return subprocess.run(["mdir", "-b", "-i", "%s@@%d" % (img, off), path], capture_output=True, text=True, env=env).stdout
            check("OpenCore.efi" in ls(1, "::/EFI/OC") and hackpe.STARTER_MARK in ls(1, "::/EFI/OC"), "P1 holds the starter OpenCore")
            mb = ls(2, "::/EFI/Microsoft/Boot")
            check("bootmgfw.efi" in mb and ".contentDetails" in mb, "P2: bootmgfw.efi + .contentDetails for the OpenCore picker")
            check("boot.wim" in ls(2, "::/sources") and "HackPE" in ls(2, "::/"), "P2 holds WinPE + toolkit")
            check("README.txt" in ls(3, "::/"), "P3 MACOS is empty except README")
    else:
        print("  (skipped image layout test: install gdisk dosfstools mtools)")

    print("== tools run from writable copies (cache may be on a read-only CD)")
    import stat
    import tools as _tw
    ro = tempfile.mkdtemp(prefix="hackpe-ro-")
    exe = os.path.join(ro, "Windows.exe")
    open(exe, "wb").write(b"MZ" + os.urandom(64))
    os.chmod(ro, stat.S_IREAD | stat.S_IEXEC)
    was = _tw.IS_WIN
    try:
        _tw.IS_WIN = True
        cp = _tw.local_exe(exe, "USBToolBox")
        check(cp != exe and os.path.isfile(cp) and os.access(os.path.dirname(cp), os.W_OK),
              "local_exe copies a cached program to a writable folder (%s)" % cp)
        check(_tw.local_exe(exe, "USBToolBox") == cp, "local_exe reuses the copy")
    finally:
        _tw.IS_WIN = was
        os.chmod(ro, stat.S_IRWXU)
    src = open(os.path.join(HERE, "..", "engine", "tools.py")).read()
    for fn in ("run_hardware_sniffer", "acpica_tool"):
        body = src.split("def %s(" % fn, 1)[1].split("\ndef ", 1)[0]
        check("local_exe(" in body, "%s runs its program via local_exe" % fn)
    body = src.split("def run_usbtoolbox(", 1)[1].split("\ndef ", 1)[0]
    check('pc_dir("usb", sub="USBToolBox")' in body and "cwd=tooldir" in body,
          "USBToolBox runs from a copy in output\\<PC>\\usb (usb.json persists, never on the CD)")
    launch = open(os.path.join(HERE, "..", "Launch.cmd"), newline="").read()
    check("where powershell" not in launch.lower(), "Launch.cmd does not depend on where.exe (missing in WinPE)")

    print("== output folder (one folder per PC)")
    import tools as _to
    root = _to.output_root()
    check(root == os.environ["HACKPE_OUTPUT"] and os.path.isfile(os.path.join(root, "README.txt")), "output root + README.txt")
    ids = {}
    for fx in fixtures:
        hwx = hackpe.load_hw(os.path.join(HERE, "fixtures", fx + ".json"))
        ids[fx] = _to.machine(hwx)["id"]
    check(len(set(ids.values())) == len(ids), "every test PC gets its own folder %s" % sorted(ids.values()))
    check(ids["cfl_rx580"].startswith("ASUSTeK_PRIME-Z390-A_"), "generic 'To Be Filled/System' -> motherboard name (%s)" % ids["cfl_rx580"])
    check(ids["hsw_igpu"].startswith("Dell-Inc_OptiPlex-9020_"), "OEM PC -> vendor + model (%s)" % ids["hsw_igpu"])
    env = dict(os.environ)
    eng = [sys.executable, os.path.join(HERE, "..", "engine", "hackpe.py"), "--cache", a.cache]
    fa, fb = (os.path.join(HERE, "fixtures", f + ".json") for f in ("cml_laptop", "adl_rtx"))
    ha, hb = hackpe.load_hw(fa), hackpe.load_hw(fb)
    pa, pb = (os.path.join(root, ids[f]) for f in ("cml_laptop", "adl_rtx"))
    for _ in range(2):
        subprocess.run(eng + ["build-efi", "--target", "tahoe", "--hw", fa], capture_output=True, env=env)
    check(os.path.isfile(os.path.join(pa, "efi", "tahoe", "EFI", "OC", "config.plist")), "build-efi saves to output\\<PC>\\efi\\<target>")
    check(os.path.isdir(os.path.join(pa, "efi", "tahoe.previous", "EFI")), "the previous build is kept as <target>.previous")
    check(os.path.isfile(os.path.join(pa, "hardware", "hw.json")) and os.path.isfile(os.path.join(pa, "hardware", "report.txt")),
          "hardware report saved (hw.json + report.txt)")
    check(len(glob.glob(os.path.join(pa, "logs", "*-build-efi*.log"))) >= 2, "every build-efi run is logged")
    check(os.path.isfile(os.path.join(pa, "machine.json")) and ids["cml_laptop"] in open(os.path.join(root, "machines.txt")).read(),
          "machine.json + machines.txt index")
    # SSDTs / USB map of PC A must never end up in PC B's EFI
    fake = os.path.join(pa, "ssdt", "20990101-000000-auto")
    os.makedirs(fake, exist_ok=True)
    shutil.copy2(cache.path("ssdt:SSDT-EC-LAPTOP"), os.path.join(fake, "SSDT-EC.aml"))
    os.makedirs(os.path.join(pa, "usb", "UTBMap.kext", "Contents"), exist_ok=True)
    check(hackpe.default_ssdt_dir(ha) == fake, "newest SSDT run of the PC is used")
    check(hackpe.default_ssdt_dir(hb) is None and hackpe.default_usb_map(hb) is None,
          "another PC's SSDTs / USB map are never used")
    check(hackpe.default_usb_map(ha) == os.path.join(pa, "usb", "UTBMap.kext"), "the PC's own UTBMap.kext is used")
    shutil.rmtree(fake); shutil.rmtree(os.path.join(pa, "usb"))
    po = json.loads(subprocess.run(eng + ["paths", "--json", "--target", "sonoma", "--hw", fb], capture_output=True, text=True, env=env).stdout)
    check(po["efi"] == os.path.join(pb, "efi", "sonoma") and po["pc"] == pb, "paths --json reports the PC's folders")

    print("== embeddable Python stays isolated (._pth kept) + script-folder hook")
    bld = open(os.path.join(HERE, "..", "Build-HackPE.ps1"), encoding="ascii").read()
    check("'python*._pth' | Remove-Item" not in bld and "'import site'" in bld,
          "build keeps the ._pth file (no registry/PYTHONPATH leaks from an installed Python)")
    check("trying the previous release" in bld, "build falls back to the previous Python if the newest fails its smoke test")
    hook = re.search(r"\$ScriptDirHook = '([^']+)'", bld).group(1)
    base = tempfile.mkdtemp(prefix="hackpe-pyhook-")
    henv = dict(os.environ, PYTHONUSERBASE=os.path.join(base, "ub"), PYTHONNOUSERSITE="")
    henv.pop("PYTHONNOUSERSITE")
    usite = subprocess.run([sys.executable, "-c", "import site; print(site.getusersitepackages())"],
                           capture_output=True, text=True, env=henv).stdout.strip()
    os.makedirs(usite, exist_ok=True)
    open(os.path.join(usite, "hackpe_scriptdir.pth"), "w").write(hook + "\n")
    sd = os.path.join(base, "tool"); os.makedirs(os.path.join(sd, "Scripts"))
    open(os.path.join(sd, "Scripts", "__init__.py"), "w").close()
    open(os.path.join(sd, "Scripts", "utils.py"), "w").write("OK = 1\n")
    open(os.path.join(sd, "Tool.py"), "w").write("from Scripts import utils\nprint('hook-ok', utils.OK)\n")
    if sys.version_info >= (3, 11):     # -P = do not add the script folder (what ._pth does)
        r = subprocess.run([sys.executable, "-P", os.path.join(sd, "Tool.py")], capture_output=True, text=True, env=henv, cwd=base)
        check("hook-ok 1" in r.stdout, "script-folder hook: 'from Scripts import ...' works like SSDTTime needs (%s)" % (r.stderr.strip()[-120:] or "ok"))
    eng = open(os.path.join(HERE, "..", "engine", "hackpe.py")).read()
    check('reconfigure(encoding="utf-8"' in eng, "engine forces UTF-8 output (PYTHONIOENCODING is ignored in isolated mode)")

    print("== HackPE Bar wiring")
    import inspect
    import tools as tools  # noqa: F811
    bar = open(os.path.join(HERE, "..", "gui", "HackPE-Bar.ps1"), encoding="ascii").read()
    keys = set(re.findall(r"Start-Tool '([a-z0-9]+)'", bar))
    handled = set(re.findall(r'c == "([a-z0-9]+)"', inspect.getsource(tools.tool_action)))
    check(keys and keys <= handled, "every bar tool key is handled by tool_action (missing: %s)" % sorted(keys - handled))
    subs = set(re.findall(r'sp\.add_parser\("([a-z-]+)"', open(os.path.join(HERE, "..", "engine", "hackpe.py")).read()))
    used = set(re.findall(r"Invoke-Engine @\('([a-z-]+)'", bar)) | set(re.findall(r"Start-EngineConsole @\('([a-z-]+)'", bar))
    used |= {"tools", "app"}
    check(used <= subs, "every engine command the bar calls exists (missing: %s)" % sorted(used - subs))
    for k in ("ocat", "explorer", "notepad"):
        check(("Start-App '%s'" % k) in bar and k in tools.APPS, "bar launches app %s" % k)
    launch = open(os.path.join(HERE, "..", "Launch.cmd"), newline="").read()
    check("HackPE-Bar.ps1" in launch and "\r\n" in launch and ":menu" in launch, "Launch.cmd starts the bar with text-menu fallback (CRLF)")

    print("\n%d failure(s)" % len(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
