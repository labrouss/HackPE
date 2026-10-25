#!/usr/bin/env python3
"""
HackPE regression tests (run in CI on Linux, or locally).

  python tests/run_tests.py [--cache DIR] [--offline]

1. downloads OpenCore + the core packages through the engine's own fetch code
2. plans every fixture against every macOS version and checks expectations
3. builds real EFIs and validates each config.plist with OpenCore's ocvalidate
"""
import argparse, json, os, plistlib, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))
import hackpe  # noqa: E402
import hwdb    # noqa: E402

CORE = ["OpenCorePkg", "Lilu", "VirtualSMC", "WhateverGreen", "AppleALC", "RestrictEvents", "NVMeFix",
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

    print("\n%d failure(s)" % len(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
