#!/usr/bin/env python3
"""
Generate RELEASE_NOTES.md for a HackPE release.

  release_notes.py --current versions.json [--previous prev.json] --version v1.2.0
                   [--prev-tag v1.1.0] [--repo owner/name] [--sha256 file.iso.sha256] [--out RELEASE_NOTES.md]

Prints 'changed=true|false' (component versions differ from the previous release) for GitHub Actions.
"""
import argparse, json, os, subprocess

LABELS = {
    "HackPE": "HackPE", "Python": "Python (embeddable)", "ACPICA": "ACPICA iasl/acpidump (64-bit build)",
    "ADK": "Windows ADK", "WinPE": "WinPE base", "OpenCorePkg": "OpenCore",
    "ExplorerPP": "Explorer++", "NotepadPP": "Notepad++", "OCAT": "OCAuxiliaryTools",
    "USBToolBoxTool": "USBToolBox (Windows tool)", "USBToolBoxKext": "USBToolBox.kext",
    "HardwareSniffer": "Hardware-Sniffer", "OpCoreSimplify": "OpCore-Simplify", "FerramentasTops": "Ferramentas TOPS",
    "PortableGit": "Portable Git", "jq": "jq", "AMD_Vanilla": "AMD_Vanilla patches", "HfsPlus": "HfsPlus.efi",
}
ORDER = ["HackPE", "WinPE", "ADK", "Python", "ACPICA", "OpenCorePkg"]


def label(k):
    if k.startswith("ssdt:"):
        return "Dortania " + k[5:]
    return LABELS.get(k, k)


def load(p):
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def git_log(prev_tag):
    rng = ["%s..HEAD" % prev_tag] if prev_tag else ["-n", "25"]
    try:
        out = subprocess.run(["git", "log", "--no-merges", "--pretty=format:- %s (%h)"] + rng,
                             capture_output=True, text=True, check=True).stdout.strip()
        return out
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", required=True)
    ap.add_argument("--previous")
    ap.add_argument("--version", required=True)
    ap.add_argument("--prev-tag")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--sha256")
    ap.add_argument("--out", default="RELEASE_NOTES.md")
    a = ap.parse_args()

    cur, prev = load(a.current), load(a.previous) if a.previous else {}
    ignore = {"HackPE"}
    keys = [k for k in ORDER if k in cur] + sorted((k for k in cur if k not in ORDER), key=lambda k: (k.startswith("ssdt:"), label(k).lower()))
    changes = []
    for k in keys:
        if k in ignore:
            continue
        old, new = prev.get(k), cur.get(k)
        if not prev:
            continue
        if old is None:
            changes.append((k, None, new))
        elif str(old) != str(new):
            changes.append((k, old, new))
    removed = [k for k in prev if k not in cur and k not in ignore]
    # date-stamped direct downloads (raw files) change tag every day - only report real version changes
    real = [c for c in changes if not (c[1] and c[2] and len(str(c[2])) == 10 and str(c[2])[4] == "-" and
                                       len(str(c[1])) == 10 and str(c[1])[4] == "-")]
    changed = bool(real or removed) or not prev

    L = ["## HackPE %s" % a.version, ""]
    if prev:
        L.append("### What's new since %s" % (a.prev_tag or "the previous release"))
        L.append("")
        if real or removed:
            L += ["| Component | Previous | Now |", "|---|---|---|"]
            for k, old, new in real:
                L.append("| %s | %s | **%s** %s |" % (label(k), old or "—", new, "🆕" if old is None else "⬆️"))
            for k in removed:
                L.append("| %s | %s | removed |" % (label(k), prev[k]))
        else:
            L.append("No component version changes.")
        L.append("")
    log = git_log(a.prev_tag)
    if log:
        L += ["### Changes in HackPE", "", log, ""]
    L += ["### Bundled versions", "", "<details><summary>All %d components</summary>" % len(keys), "",
          "| Component | Version |", "|---|---|"]
    for k in keys:
        L.append("| %s | %s |" % (label(k), cur[k]))
    L += ["", "</details>", ""]
    if a.sha256 and os.path.isfile(a.sha256):
        L += ["### Verify", "", "```", open(a.sha256, encoding="utf-8").read().strip(), "```", ""]
    L += ["### Use", "",
          "1. Write the ISO to a USB stick (Rufus / Ventoy) or boot it in a VM.",
          "2. Boot the target PC from it (UEFI, Secure Boot off) - the HackPE Assistant starts automatically.",
          "3. Follow the Assistant; use a **second** USB stick for the macOS installer.", ""]
    if a.repo and a.prev_tag:
        L.append("**Full diff**: https://github.com/%s/compare/%s...%s" % (a.repo, a.prev_tag, a.version))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("changed=%s" % ("true" if changed else "false"))


if __name__ == "__main__":
    main()
