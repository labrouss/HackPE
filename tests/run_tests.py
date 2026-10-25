#!/usr/bin/env python3
"""
HackPE regression tests (run in CI on Linux, or locally).

  python tests/run_tests.py [--cache DIR] [--offline]

1. downloads OpenCore + the core packages through the engine's own fetch code
2. plans every fixture against every macOS version and checks expectations
3. builds real EFIs and validates each config.plist with OpenCore's ocvalidate
"""
import argparse, json, os, plistlib, re, subprocess, sys, tempfile

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
    import shutil
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
