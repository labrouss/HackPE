# HackPE: all-in-one bootable WinPE 11 hackintosh toolkit

[![Build HackPE ISO](https://github.com/OWNER/REPO/actions/workflows/build-iso.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/build-iso.yml)

HackPE is a WinPE 11 USB or ISO that you boot **on the PC you want to turn into a hackintosh**. It looks at the real hardware, works out which macOS versions the PC can run (up to **macOS 26 Tahoe**), builds an OpenCore EFI for that hardware, and creates the macOS USB installer. You don't need a Mac or an installed Windows on the target PC.

```
 Boot HackPE ─► 1 Detect ─► 2 Compatibility ─► 3 Build EFI ─► 4 USB installer ─► reboot ─► install macOS ─► 5 EFI to disk
```

## What it automates

| Step | What HackPE does |
|---|---|
| **Detect** | Reads CPU family/model, PCI vendor/device/class IDs, GPU PCI paths, board, chassis, NICs, Wi-Fi, NVMe, I2C/PS2 input and the MAC address. Works **without vendor drivers**, so it's reliable inside WinPE. |
| **Compatibility** | Builds a per-version matrix from High Sierra to Tahoe, finds the newest native macOS, and flags blockers such as an unsupported GPU or CPU. |
| **Plan** | Picks the SMBIOS model (Tahoe only accepts T2-era models: iMac20,x, MacPro7,1, MacBookPro16,x), kexts, SSDTs, Booter/Kernel quirks, iGPU framebuffer, GPU spoofs and boot-args. |
| **Build EFI** | Uses the latest OpenCore and kexts, prunes drivers and tools, and writes `config.plist` starting from `Sample.plist`. It generates the serial/MLB/UUID with `macserial`, uses the real NIC MAC as ROM, applies AMD_Vanilla patches with your core count, adds the CPUID spoof plus CpuTopologyRebuild on Alder/Raptor Lake, and runs **ocvalidate**. |
| **USB installer** | Wipes a second stick (GPT + FAT32 `OPENCORE`), copies the EFI, and downloads the macOS recovery from Apple using upstream `macrecovery.py` and `recovery_urls.txt`. |
| **Post-install** | Installs OpenCore to the internal disk's ESP without touching `EFI\Microsoft`. Also includes USBToolBox (USB mapping), SSDTTime (custom SSDTs, picked up by the next build automatically), a Notepad config editor and a driver loader. |

## Build it (on any Windows 10/11 x64 PC)

1. Install the **Windows ADK** (Deployment Tools) and the **Windows PE add-on for the ADK**.
2. Open an elevated PowerShell in this folder and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Build-HackPE.ps1                                   # -> C:\HackPE-Build\HackPE.iso
.\Build-HackPE.ps1 -UsbDrive E:                      # also write a bootable USB stick
.\Build-HackPE.ps1 -DriversPath D:\Drivers\NIC       # inject extra NIC/storage drivers
.\Build-HackPE.ps1 -GitHubToken ghp_xxx              # avoids GitHub API rate limits (60/h anonymous)
.\Build-HackPE.ps1 -PortableOnly                     # no ADK: run the GUI on a normal Windows PC
```

The script downloads Python (embeddable), a CA bundle, OpenCore, about 25 kext packages, the Dortania SSDTs, the AMD_Vanilla patches, HfsPlus, USBToolBox and SSDTTime into an **offline cache**. It then builds the WinPE image (WMI, NetFX, PowerShell, StorageWMI, DISM and SecureBoot cmdlets) and creates the ISO or USB.

You can also write `HackPE.iso` to a stick with Rufus or Ventoy.

## Build it with GitHub Actions (no local ADK needed)

Push this folder to a GitHub repository and `.github/workflows/build-iso.yml` does the rest:

| Job | Runner | What it does |
|---|---|---|
| **test** | ubuntu | pyflakes, PowerShell syntax check, then `tests/run_tests.py`: downloads the real OpenCore + kexts, plans 5 hardware fixtures against every macOS version, builds EFIs and validates every `config.plist` with `ocvalidate` |
| **iso** | windows-2025 | installs Windows ADK + WinPE add-on silently, runs `Build-HackPE.ps1`, uploads `HackPE-<version>.iso` + `.sha256` |

| Trigger | Result |
|---|---|
| push to `main` | test + ISO as a workflow **artifact** (kept 7 days) |
| push a tag `v1.0.0` | test + ISO + **GitHub Release** `v1.0.0` |
| *Actions → Build HackPE ISO → Run workflow* | manual build, optional release / Python version |
| 1st of every month | rebuild with the newest OpenCore and kexts, published as a pre-release |
| pull request | tests only |

```bash
git init -b main && git add . && git commit -m "HackPE"
git remote add origin https://github.com/<you>/hackpe.git
git push -u origin main
git tag v1.0.0 && git push origin v1.0.0        # -> release with the ISO
```

Extra NIC/storage drivers: commit them into `drivers/` and they're injected into WinPE automatically.
A Windows build takes about 25–40 minutes, mostly ADK download and DISM.

> **Licensing:** WinPE is covered by the Windows ADK license terms, and Microsoft limits WinPE to deployment and recovery use, so publishing the ISO publicly may not be allowed. The safe option is a **private** repository, so artifacts and releases are visible only to you. Note that private-repo Windows minutes count double, and artifact storage on the free plan is 500 MB. Release assets don't count toward that, so prefer tagged releases. Or keep the repo public without releases and let people build their own ISO.

## Use it: the HackPE Assistant

Boot the target PC from the HackPE stick (UEFI mode, Secure Boot off). The **Assistant** starts and walks you through each step in plain language:

| Step | What the user sees |
|---|---|
| **Welcome** | Live checks with green/yellow/red badges: toolkit files, internet (with *Load network driver* and *Download toolkit files* buttons), second USB stick, time and backup. Choose *Create installer* or *macOS is already installed*. |
| **Your PC** | A banner such as "Good news! This PC can run up to macOS 26 Tahoe", then one row per component (CPU, each GPU, Ethernet, Wi-Fi, audio, SSD, touchpad, firmware) with a short explanation. A desktop/laptop override is available. |
| **Choose macOS** | Every version from High Sierra to Tahoe, tagged RECOMMENDED, SUPPORTED, EXPERIMENTAL or NO: GRAPHICS / NO: CPU. A "What to expect" panel shows the Mac model the PC will appear as, and what won't work. |
| **USB stick** | USB sticks appear automatically as they're plugged in. The HackPE stick, system disks and sticks that are too small are greyed out with the reason. An erase confirmation checkbox plus a final dialog protect against mistakes. |
| **Create** | Progress steps, a real progress bar for the Apple download, an elapsed timer and Cancel. Errors come with a plain explanation (no network, stick write-protected, …), a *Try again* button and an optional log. |
| **Next steps** | Personalised instructions using your PC brand's BIOS and boot-menu keys (ASUS F8, MSI F11, Dell F12, …), a tick-off BIOS checklist that matches your graphics setup, and the Disk Utility/install steps. The same instructions are saved to the stick as `NEXT-STEPS.txt`. |

**After installing macOS**, boot HackPE again and choose *macOS is already installed*. Pick the macOS drive, and the Assistant copies OpenCore from the installer stick (or builds a fresh one) to that drive's EFI partition, leaving Windows' boot loader alone.

Every page has a *Need help with this step?* link. **Expert mode** (the tabbed GUI with every option) and a text-mode menu/wizard are always available.

Command line (same engine, also works on normal Windows):

```bat
hackpe.py detect --summary
hackpe.py plan --target tahoe
hackpe.py build-efi --target sequoia --out C:\efi [--platform cometlake --smbios iMac20,1 --display igpu]
hackpe.py make-usb --target tahoe --disk 2
hackpe.py recovery --target sonoma --out E:\com.apple.recovery.boot
hackpe.py install-esp --efi C:\efi --disk 0
hackpe.py fetch | status | versions | wizard
```

`detect --out pc.json` on one machine and `plan --hw pc.json` on another lets you plan for a different PC.

## Platform support (native, no root patches)

| Platform | Newest native | Notes |
|---|---|---|
| Intel 8th–10th gen (Coffee/Comet Lake), Ice Lake laptops | **Tahoe** | Best targets. iGPU or AMD dGPU. |
| Intel 6th/7th gen (Skylake/Kaby Lake) | **Tahoe** | Skylake iGPU is spoofed as Kaby Lake automatically. |
| Intel 4th/5th gen (Haswell/Broadwell) | Monterey (iGPU) / **Tahoe** (supported AMD dGPU) | iGPU drivers were removed in Ventura. |
| Intel 12th–14th gen (Alder/Raptor Lake), 11th gen desktop | **Tahoe** with a supported AMD dGPU | CPUID spoof + CpuTopologyRebuild. The iGPU is not supported. |
| AMD Ryzen / Threadripper (Zen 1–4) | **Tahoe** with a supported AMD dGPU | AMD_Vanilla patches. Use SSDTTime for SSDTs. |
| Intel 2nd/3rd gen | High Sierra / Big Sur | Legacy. Newer versions need OCLP. |
| Tiger Lake, Core Ultra, Zen 5, Intel Arc, NVIDIA RTX/GTX 16+, RX 7000, RX 6400/6500 | — | Unsupported or experimental. |

Supported dGPUs: Polaris (RX 460–590), Vega 56/64, Radeon VII, RX 5500/5600/5700, RX 6600/6800/6900 (6650 XT and 6950 XT get an automatic device-id spoof). The RX 6700 series needs NootRX (experimental). NVIDIA Kepler works up to Big Sur.

## macOS 26 Tahoe notes

* Tahoe is the **last macOS for x86**, so it's the long-term hackintosh target.
* **AppleHDA was removed.** AppleALC alone no longer gives onboard analog audio. HDMI/DP audio still works. After install, restore AppleHDA with a root patcher, use VoodooHDA, or use USB audio.
* Only T2-era SMBIOS models install Tahoe, and HackPE selects one automatically.
* Broadcom Wi-Fi needs an OCLP root patch on Sonoma and later. Check the itlwm releases for Intel Wi-Fi on Tahoe.

## Limits (read before blaming the tool)

* The generated EFI is a solid **Dortania-style starting point**, not a guaranteed first boot. Laptops and AMD systems almost always need SSDTTime SSDTs. Some boards need quirk tweaks (`BUILD-NOTES.txt`, Dortania troubleshooting).
* The recovery-based install downloads macOS from Apple during installation, so the installer PC needs a **supported network adapter**.
* CFG Lock state can't be read from Windows, so `AppleXcpmCfgLock` is enabled as a safe default.
* HackPE doesn't apply OCLP root patches.
* macOS is licensed by Apple for Apple-branded hardware. Use this for learning and testing, at your own risk.

## Layout

```
Build-HackPE.ps1      host builder (ADK/WinPE, payload, cache, ISO/USB)
Launch.cmd                 WinPE auto-start + text menu fallback
gui/HackPE-Assistant.ps1   guided step-by-step assistant (default)
gui/HackPE-Expert.ps1      expert GUI (5 tabs, every option)
engine/hackpe.py      engine: detect/plan/fetch/build-efi/recovery/make-usb/install-esp/wizard
engine/hwdb.py        hardware knowledge base (CPU/GPU/NIC/Wi-Fi, SMBIOS rules, quirks, packages)
engine/detect.ps1     driver-independent hardware probe
tests/                regression tests + hardware fixtures (run in CI)
drivers/              optional extra WinPE drivers (injected by CI)
.github/workflows/    GitHub Actions: test + ISO build + release
data/                 cacert.pem (downloaded at build)
```

To support new hardware, edit `engine/hwdb.py`. All the rules live in tables there.
