#!/usr/bin/env python3
"""
Assemble the flash-ready HackPE USB image (GPT, no root needed: sgdisk + mkfs.fat + mtools).

  P1  EFI     FAT32  ESP   ~300 MB  generic "starter" OpenCore (picker only)
  P2  HACKPE  FAT32  data  WinPE media (bootmgr/BCD/boot.wim) + \\HackPE (cache, apps)
  P3  MACOS   FAT32  data  empty - HackPE downloads macOS recovery here (prepare-stick)

OpenCore (ScanPolicy 0) lists the Windows Boot Manager on P2 as "HackPE Toolkit"; the firmware boot menu
can also start P2 directly (\\EFI\\BOOT\\BOOTX64.EFI). The rest of a bigger stick stays free: HackPE turns
it into an exFAT 'MACOS-INST' partition for the full offline installer when asked.

  python3 build/make_disk_image.py --efi STARTER_DIR --media ISO_CONTENT_DIR --out HackPE-usb.img [--xz]
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

MIB = 1024 * 1024
FAT32_MAX_FILE = 4 * 1024 ** 3 - 1


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit("command failed: %s\n%s%s" % (" ".join(cmd), r.stdout, r.stderr))
    return r.stdout


def tree_size(path):
    total, biggest = 0, ("", 0)
    for dp, _, fs in os.walk(path):
        for f in fs:
            n = os.path.getsize(os.path.join(dp, f))
            total += n
            if n > biggest[1]:
                biggest = (os.path.join(dp, f), n)
    return total, biggest


def find_ci(root, *parts):
    """Case-insensitive path lookup (ISO extractors differ in case)."""
    cur = root
    for p in parts:
        if not os.path.isdir(cur):
            return None
        hit = next((e for e in os.listdir(cur) if e.lower() == p.lower()), None)
        if hit is None:
            return None
        cur = os.path.join(cur, hit)
    return cur


def make_fat(path, size_mib, label, sources, extra=None):
    """FAT32 file system image of size_mib with the contents of the source dirs (+ extra {rel: bytes})."""
    with open(path, "wb") as f:
        f.truncate(size_mib * MIB)
    args = ["mkfs.fat", "-F", "32", "-n", label]
    if size_mib < 512:                       # small ESP: 512-byte clusters keep FAT32 legal (>= 65525 clusters)
        args += ["-S", "512", "-s", "1"]
    run(args + [path])
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    for src in sources:
        for entry in sorted(os.listdir(src)):
            run(["mcopy", "-s", "-Q", "-o", "-i", path, os.path.join(src, entry), "::/"], env=env)
    for rel, data in (extra or {}).items():
        parts = rel.split("/")
        for i in range(1, len(parts)):
            d = "::/" + "/".join(parts[:i])
            subprocess.run(["mmd", "-i", path, d], capture_output=True, env=env)   # exists -> ignored
        with tempfile.NamedTemporaryFile(delete=False) as t:
            t.write(data)
        try:
            run(["mcopy", "-o", "-i", path, t.name, "::/" + rel], env=env)
        finally:
            os.remove(t.name)


def sparse_copy(src, dst_f, offset):
    """Copy a file into the image at offset, skipping zero blocks (keeps the image sparse)."""
    blk = 4 * MIB
    zero = bytes(blk)
    with open(src, "rb") as s:
        pos = 0
        while True:
            b = s.read(blk)
            if not b:
                break
            if b != zero[:len(b)]:
                dst_f.seek(offset + pos)
                dst_f.write(b)
            pos += len(b)


def partition_starts(img):
    out = run(["sgdisk", "-p", img])
    return {int(m.group(1)): int(m.group(2)) for m in re.finditer(r"^\s+(\d+)\s+(\d+)\s+\d+", out, re.M)}


def build(a):
    for t in ("sgdisk", "mkfs.fat", "mcopy", "mmd"):
        if not shutil.which(t):
            sys.exit("%s not found - apt-get install gdisk dosfstools mtools" % t)
    efi_src = a.efi if os.path.isdir(os.path.join(a.efi, "EFI")) else os.path.dirname(a.efi.rstrip("/"))
    if not os.path.isfile(os.path.join(efi_src, "EFI", "OC", "OpenCore.efi")):
        sys.exit("%s has no EFI/OC/OpenCore.efi" % efi_src)
    media = a.media
    need = [("sources", "boot.wim"), ("boot", "boot.sdi"), ("EFI", "Microsoft", "Boot", "BCD"), ("EFI", "Boot", "bootx64.efi")]
    missing = ["/".join(p) for p in need if not find_ci(media, *p)]
    if missing:
        sys.exit("%s is not WinPE media, missing: %s" % (media, ", ".join(missing)))
    if not a.allow_no_hackpe and not find_ci(media, "HackPE"):
        sys.exit("%s has no \\HackPE folder (toolkit cache/apps)" % media)

    size, biggest = tree_size(media)
    if biggest[1] > FAT32_MAX_FILE:
        sys.exit("%s is %.1f GB - too big for FAT32" % biggest)
    hackpe_mib = int(size * 1.03 / MIB) + a.hackpe_free_mib
    esp_mib, macos_mib = a.esp_mib, a.macos_mib

    # P2 extras: OpenCore finds Windows Boot Manager at \EFI\Microsoft\Boot\bootmgfw.efi and names it
    # from .contentDetails; the ISO only has \EFI\Boot\bootx64.efi (the same binary).
    extra = {"EFI/Microsoft/Boot/.contentDetails": b"HackPE Toolkit"}
    if not find_ci(media, "EFI", "Microsoft", "Boot", "bootmgfw.efi"):
        extra["EFI/Microsoft/Boot/bootmgfw.efi"] = open(find_ci(media, "EFI", "Boot", "bootx64.efi"), "rb").read()
    readme = (b"HackPE MACOS partition\r\n\r\nBoot HackPE and choose USB > 'Prepare this stick for macOS'.\r\n"
              b"HackPE builds OpenCore for your PC and downloads macOS recovery from Apple into this partition.\r\n")

    work = tempfile.mkdtemp(prefix="hackpe-img-", dir=a.tmp)
    try:
        p1, p2, p3 = (os.path.join(work, n) for n in ("p1.fat", "p2.fat", "p3.fat"))
        print("P1 EFI     %5d MiB  <- %s" % (esp_mib, efi_src))
        make_fat(p1, esp_mib, "EFI", [efi_src])
        print("P2 HACKPE  %5d MiB  <- %s (%.0f MiB of files)" % (hackpe_mib, media, size / MIB))
        make_fat(p2, hackpe_mib, "HACKPE", [media], extra)
        print("P3 MACOS   %5d MiB  (empty - filled by HackPE: prepare-stick)" % macos_mib)
        make_fat(p3, macos_mib, "MACOS", [], {"README.txt": readme})

        total_mib = 1 + esp_mib + hackpe_mib + macos_mib + 1
        if os.path.exists(a.out):
            os.remove(a.out)
        with open(a.out, "wb") as f:
            f.truncate(total_mib * MIB)
        run(["sgdisk", "-o",
             "-n", "1:2048:+%dM" % esp_mib, "-t", "1:ef00", "-c", "1:EFI",
             "-n", "2:0:+%dM" % hackpe_mib, "-t", "2:0700", "-c", "2:HACKPE",
             "-n", "3:0:+%dM" % macos_mib, "-t", "3:0700", "-c", "3:MACOS", a.out])
        starts = partition_starts(a.out)
        with open(a.out, "r+b") as f:
            for num, fs in ((1, p1), (2, p2), (3, p3)):
                sparse_copy(fs, f, starts[num] * 512)
        run(["sgdisk", "-v", a.out])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("Image: %s (%d MiB)" % (a.out, total_mib))
    print(run(["sgdisk", "-p", a.out]).split("Number", 1)[-1].rstrip())

    outputs = [a.out]
    if a.xz:
        xz = a.out + ".xz"
        if os.path.exists(xz):
            os.remove(xz)
        subprocess.run(["xz", "-T0", "-6", "-k", "-f", a.out], check=True)
        print("Compressed: %s (%.0f MiB)" % (xz, os.path.getsize(xz) / MIB))
        outputs = [xz]
        if not a.keep_raw:
            os.remove(a.out)
    for o in outputs:
        h = hashlib.sha256()
        with open(o, "rb") as fh:
            for b in iter(lambda: fh.read(MIB), b""):
                h.update(b)
        with open(o + ".sha256", "w") as fh:
            fh.write("%s  %s\n" % (h.hexdigest(), os.path.basename(o)))
    return outputs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--efi", required=True, help="folder containing EFI/ (starter OpenCore)")
    ap.add_argument("--media", required=True, help="WinPE media folder (extracted HackPE ISO)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--esp-mib", type=int, default=300)
    ap.add_argument("--macos-mib", type=int, default=3072, help="room for macOS recovery (1-2 GB, growing)")
    ap.add_argument("--hackpe-free-mib", type=int, default=512, help="free space on HACKPE for cache updates/logs")
    ap.add_argument("--xz", action="store_true", help="compress to .img.xz (Rufus / balenaEtcher read it directly)")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--tmp", default=None)
    ap.add_argument("--allow-no-hackpe", action="store_true", help=argparse.SUPPRESS)
    build(ap.parse_args())


if __name__ == "__main__":
    main()
