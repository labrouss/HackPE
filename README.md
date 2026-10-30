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
| every Monday | rebuild with the newest everything; publishes a pre-release **only if a component changed** |
| pull request | tests only |

```bash
git init -b main && git add . && git commit -m "HackPE"
git remote add origin https://github.com/<you>/hackpe.git
git push -u origin main
git tag v1.0.0 && git push origin v1.0.0        # -> release with the ISO
```

**Always the latest:** every build resolves the newest OpenCore, kexts, apps and tools (no cache between runs). It also uses the newest stable embeddable Python, the latest ACPICA release (built from source) and the current Windows ADK. If the newest Python fails the build's smoke test, the build falls back to the previous release automatically. The bundled Python runs isolated, so a Python installed on the build runner or on your own PC can't interfere with it.

**Changelog:** each release gets generated notes:
- a table of component upgrades (⬆️) and additions (🆕) compared with the previous release's `versions.json`
- the HackPE commits since that release
- the full list of bundled versions and the ISO's SHA-256

Re-running a release updates its notes.

Extra NIC/storage drivers: commit them into `drivers/` and they're injected into WinPE automatically.
A Windows build takes about 25–40 minutes, mostly ADK download and DISM.

> **Licensing:** WinPE is covered by the Windows ADK license terms, and Microsoft limits WinPE to deployment and recovery use, so publishing the ISO publicly may not be allowed. The safe option is a **private** repository, so artifacts and releases are visible only to you. Note that private-repo Windows minutes count double, and artifact storage on the free plan is 500 MB. Release assets don't count toward that, so prefer tagged releases. Or keep the repo public without releases and let people build their own ISO.

## USB image (flash and boot, no USB-making step)

A second workflow, `.github/workflows/build-image.yml`, runs after every successful ISO build (or by hand: *Actions → Build HackPE USB image → Run workflow*, for any release). It turns the ISO into a ready-to-flash stick image, so you get both:

| Download | Use it for |
|---|---|
| `HackPE-<version>.iso` | VMs, Ventoy, Rufus "ISO mode", burning |
| `HackPE-usb-<version>.img.xz` | Flash with **Rufus** (DD mode), **balenaEtcher** or `xzcat … \| sudo dd of=/dev/sdX bs=4M` |

The image is about 6 GB once written, so it fits any 8 GB+ stick:

| # | Partition | Contents |
|---|---|---|
| 1 | **EFI**, FAT32, 300 MB | Generic "starter" OpenCore: no kexts or SSDTs, so it boots on any PC and only shows the picker |
| 2 | **HACKPE**, FAT32 | The WinPE toolkit (the same as the ISO). The OpenCore picker shows it as **HackPE Toolkit** and starts it after 10 s |
| 3 | **MACOS**, FAT32, 3 GB | Empty. HackPE downloads macOS recovery from Apple into it |

**How users use it:**
1. Flash the image to a stick.
2. Boot it. OpenCore starts HackPE.
3. In the Assistant, choose **This HackPE stick (recommended)**. You can also use Bar › USB › *Prepare this HackPE stick*, or run `hackpe.py prepare-stick --target tahoe`. Nothing on the stick is erased:
   - OpenCore built for this PC replaces the starter on partition 1 (the starter is kept as `EFI\OC.bak-*`);
   - macOS recovery is downloaded to MACOS;
   - with *offline installer* on a 32 GB+ stick, the free space becomes an exFAT `MACOS-INST` partition holding the full installer.
4. Reboot. The OpenCore picker now shows **macOS Base System** next to **HackPE Toolkit**: install macOS, then boot HackPE again for the post-install EFI step.

**No macOS in the image:** the image never contains Apple software. Redistributing macOS isn't permitted, and the 2 GiB release-file limit wouldn't allow it anyway. The workflow also boots the image in QEMU (UEFI, as a USB stick) and saves screenshots of the OpenCore picker and the HackPE desktop as the `boot-test-<version>` artifact. If the compressed image ever passes 2 GiB, it's split into `.part0/.part1` files, and the release notes explain how to join them.

The WinPE licensing note above applies to the image just as it does to the ISO.

## Config Compare (web page)

A browser tool in `web/` (React + Vite) that compares two OpenCore `config.plist` files. It's published to GitHub Pages by `.github/workflows/pages.yml`. The files never leave the browser.

- **Plan:** every difference, each with an explanation, sorted by severity: **boot-critical**, **hardware-specific**, **this PC only** (serials, MLB, UUID, ROM, which are never copied), **cosmetic/debug** and **inactive** (disabled leftovers). Tick the steps you want, then *Download matched config* to get the target with those changes applied, or *Copy plan as checklist* for Markdown. The page also lists the `.aml`, `.kext` and `.efi` files the target EFI needs.
- **OpenCore-aware matching:**
  - Kexts, SSDTs, ACPI and kernel patches, drivers and tools are matched by name, not by position.
  - Values are decoded: boot-args token by token, SIP flags, ScanPolicy, CPUID spoof generation, framebuffer and device IDs, audio layout.
  - A kext load order that differs from the reference is flagged.
- **Health check** of both files. It finds plugins that load before their parent kext, Lilu not first, kexts loaded twice, conflicting kexts (AppleALC + VoodooHDA, itlwm + AirportItlwm, UTBMap + UTBDefault…), OpenCanopy missing for the External picker, debug boot-args, AppleALC on Tahoe, and disabled Sample.plist leftovers.
- **Light and dark theme:** the switch is in the top bar. It starts from your system setting and remembers your choice.
- **Browse configs:** both files as one tree on the same layout as the plan (reference | key | target), with search, an "only differences" filter and decoded values. *Show in the config browser* on any plan step jumps to that key.

```bash
cd web && npm install
npm run dev            # local dev server
npm test               # engine tests (Node's built-in runner, no extra packages)
npm run build:single   # dist-single/index.html: one self-contained file that works offline
```

One-time setup for Pages: **Settings > Pages > Source: GitHub Actions**. The site is then at `https://<user>.github.io/<repo>/`, with the single-file version alongside it as `hackpe-config-compare.html`.

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

Every page has a *Need help with this step?* link.

## HackPE Bar (taskbar-style menu)

When HackPE boots, a **taskbar docked at the bottom of the screen** replaces the old numbered text menu, and the Assistant opens above it:

```
[ HackPE ] | ACPI  USB  EFI  macOS  Apps  Your tools  System  Windows      ...status   Net: LAN 192.168.1.20   14:05   Power
```

| Menu | What's in it |
|---|---|
| **HackPE** | Assistant, Expert mode, text-mode wizard, hardware report, bundled versions, pre-flight check, update downloads, classic text menu |
| **ACPI** | Dump ACPI tables, auto-generate SSDTs for this PC, SSDTTime (all options), view generated SSDTs/patches |
| **USB** | USBToolBox port mapping, make a USB installer, add HackPE to a stick's OpenCore menu |
| **EFI** | Rebuild EFI with SSDTs + USB map, OpCore-Simplify, Hackintosh EFI Builder (window and console), Hardware-Sniffer, Ferramentas TOPS, OCAuxiliaryTools, ocvalidate, macserial, open an EFI partition |
| **macOS** | Full offline installer (gibMacOS), gibMacOS interactive, USB installer, post-install EFI to the internal disk |
| **Apps** | Explorer++, Notepad++, OCAuxiliaryTools, Hackintosh EFI Builder, Command prompt, PowerShell, Git Bash, DiskPart, *Run a program…* (file picker) |
| **Your tools** | Everything in `\HackPE\tools` on the stick (refreshed every time the menu opens) |
| **System** | Network status, Wi-Fi, load drivers from a folder (folder picker) or from `\HackPE\drivers` |
| **Windows** | Switch between open windows (WinPE has no taskbar of its own) |
| **Power** | Restart, Shut down |

- Every tool opens **maximized in its own window**. In WinPE the bar reserves its strip of the screen, so maximized windows stop above it instead of covering it.
- The network label shows the current IP; click it for the network status.
- **Keyboard:** click the bar (or Alt+Tab to *HackPE Bar*), then **F1…F9** open the menus in order (F1 HackPE … F9 Windows). Arrow keys move through a menu, Enter runs the item, and Esc closes it.
- **Fallback:** *HackPE › Classic text menu* (or Alt+F4 on the bar) returns to the numbered text menu, where **`B`** brings the bar back. If PowerShell/WinForms can't start, the text menu appears on its own.

## Text menu and Tools (`T`)

The classic text menu has option **`T  Tools`**. A single tool can also run by its key (`hackpe.py tools --run 2` = auto-generate SSDTs), which is what the bar does. Every tool also has a CLI (`hackpe.py <command>`):

| Key | Tool | CLI |
|---|---|---|
| 1 | **Dump ACPI tables** with 64-bit `acpidump.exe`, falling back to the Windows firmware-table API. Saved to `output\<PC>\acpi\<time>`. | `acpi-dump` |
| 2 | **Auto-generate SSDTs**: dumps the tables, then drives SSDTTime with the right generators for the detected platform (EC, USBX, PLUG, RTCAWAC, PMC on 300-series, PNLF and XOSI on laptops). | `ssdt-auto` |
| 3 | SSDTTime, interactive with all options | `ssdttime` |
| 5 | **USBToolBox**, which produces `UTBMap.kext` + `usb.json`, saved in `output\<PC>\usb` (discovery continues after a reboot) | `usbtoolbox` |
| 6 | Rebuild the EFI. Generated SSDTs and the USB map are picked up automatically. Generated SSDTs replace the matching prebuilt ones; prebuilt ones fill any gaps (e.g. SSDT-CPUR on AMD). | `build-efi` |
| 7 / 8 / 9 | ocvalidate · macserial · mount the EFI partition of any disk | `validate` |
| X / E / O | **Explorer++** (file explorer) · **Notepad++** · **OCAuxiliaryTools** (config editor) | `app explorer\|notepad\|ocat` |
| I / J | **[Hackintosh EFI Builder](https://github.com/yusufklncc/Hackintosh-for-All-Computers)** (MIT): an alternative EFI generator built from 179 device profiles. It can also download recovery and write the USB stick. **I** opens the window version; **J** runs the console version and passes it this PC's USB map and newest ACPI dump. Both save to `output\<PC>\efi-builder`. | `app efibuilder`, `tools --run j` |
| W / L | **Wi-Fi connect** · **load drivers from a folder** | `wifi`, `load-drivers` |
| R / T1… | **Run any program** (`.exe .py .cmd .bat .ps1`) · your own tools from `\HackPE\tools` on the stick or `tools/custom` in the repo | `run` |

`acpidump.exe` and `iasl.exe` are **64-bit builds** made from the official ACPICA source by `build/build-acpica-win64.sh` (MinGW-w64, done in CI). Intel only ships 32-bit Windows binaries, and x64 WinPE has no WOW64 layer to run them. When HackPE is built, any Windows DLLs the bundled apps need that WinPE lacks (DirectX, dwmapi, uxtheme…) are found by scanning the PE imports and copied from the build host.

## Output folder: every result in one place

Every tool saves its results in **one folder per PC**. It goes on the stick when HackPE finds a writable `\HackPE` folder (the USB image, or a stick made by HackPE), so results survive a reboot. Otherwise it goes to `X:\HackPE-work\output` in RAM, and HackPE reminds you to copy it.

```
\HackPE\output\
  README.txt                what goes where
  machines.txt              index: folder | PC | CPU | last used
  ASUSTeK_PRIME-Z390-A_b1bcb5\          <- vendor/model (motherboard for DIY PCs) + ID from the system UUID
    machine.json            vendor, model, board, CPU, first/last seen
    hardware\               hw.json, report.txt, Hardware-Sniffer-<time>\
    acpi\<time>\            ACPI table dumps
    ssdt\<time>-auto\       SSDTs + patches_OC.plist (auto-generation or SSDTTime)
    usb\                    UTBMap.kext, usb.json, USBToolBox\ (its working copy), history\<time>\
    efi\tahoe\              EFI\, BUILD-NOTES.txt, plan.json   (+ tahoe.previous = the build before)
    opcore-simplify\<time>\ EFIs made with OpCore-Simplify
    efi-builder\            Hackintosh EFI Builder: EFI\, machine.json (its working folder)
    logs\<time>-<command>.log   every USB creation, EFI build, download, SSDT run
  downloads\                tools downloaded by Ferramentas TOPS
```

- **One stick for many PCs.** An EFI build uses the newest SSDTs and the USB map of the PC it runs on. Results from another PC are never mixed in.
- **Open it:** Bar › HackPE (or Apps) › *Output folder of this PC*, Tools menu **V**, `hackpe.py open-output`, or `hackpe.py paths` to print the locations.
- **Choose another place:** set `HACKPE_OUTPUT` to any folder before starting HackPE.

## Full offline installer (no internet during the install)

Tick *Also put the FULL offline installer on the stick* in the Assistant, or run `make-usb --offline`. This needs a 32 GB+ stick and Big Sur or newer. The stick gets two partitions:

| Partition | Content |
|---|---|
| `OPENCORE` (FAT32, 4 GB) | EFI + macOS Recovery (to boot the installer environment) |
| `MACOS-INST` (exFAT) | Apple's full `InstallAssistant.pkg` (12–18 GB, via **gibMacOS**) + `install-offline.sh` |

In Recovery, erase the disk as *Macintosh HD*, then open **Utilities > Terminal** and run `sh /Volumes/MACOS-INST/install-offline.sh`. The script unpacks the installer onto the disk and starts `startosinstall`, so no network is needed. Windows can't build Apple's native full-installer USB (that needs macOS tools); this route gets the same result.

## Boot HackPE from the OpenCore menu

When the installer stick is created from HackPE, *Add HackPE to this stick's OpenCore boot menu* (on by default) copies the toolkit (about 2 GB) onto the stick's OPENCORE partition and adds a **"HackPE Toolkit"** entry to the OpenCore picker. One stick then installs macOS **and** boots the toolkit, for example to install the EFI to the internal disk, regenerate SSDTs or map USB.

How it works: Windows Boot Manager is registered as an OpenCore **Tools** entry (`EFI\OC\Tools\HackPE.efi`, `RealPath=true`). It reads `\EFI\Microsoft\Boot\BCD` from the same partition, and the BCD boots `\sources\boot.wim` from that partition as a RAM disk. OpenCore's own `EFI\BOOT\BOOTx64.efi` is untouched.

- Existing OpenCore stick: Tools `A` or `hackpe.py add-hackpe --target O:\`.
- Internal disk: when OpenCore is installed to the EFI partition, HackPE is added only if the whole toolkit fits there and no Windows boot loader lives on it. Otherwise the entry is left out (EFI partitions are usually 100–300 MB).

## Drivers: touchpad, Ethernet, Wi-Fi

| Source | What | How |
|---|---|---|
| **Inbox drivers of the build host** (default) | HID-over-I2C/SPI, Intel/AMD Serial-IO I2C and GPIO controllers (needed by most laptop touchpads), Realtek/Intel/Killer Ethernet, USB Ethernet, Android/iPhone USB tethering (RNDIS/NCM), Wi-Fi adapters | `-HarvestInboxDrivers $true` |
| **This PC's own drivers** | Build on the target laptop to include its exact touchpad, LAN and Wi-Fi drivers | `-HarvestHostDrivers` |
| `drivers/` in the repo | Your `.inf` driver folders | committed → CI |
| `drivers/sources.txt` | URLs of `.zip`/`.cab` driver packs (one per line) | downloaded at build |
| **`\HackPE\drivers` on the stick** | Drop drivers there; they're **loaded at every boot** with no rebuild needed | automatic |

Wi-Fi: plain WinPE has **no WLAN service**, so Wi-Fi drivers alone are not enough. Build on Windows 11 with
`-WindowsIso D:\Win11.iso` (or `-WinReWim C:\path\winre.wim`). HackPE then uses **WinRE** as its base, which includes the Wi-Fi stack, and `W  Wi-Fi connect` works. Without it, use Ethernet, a USB Ethernet adapter, or phone USB tethering.

All tools start **maximized**: the console is enlarged and maximized, GUI apps get `SW_MAXIMIZE`, and their windows are maximized by process ID as a fallback (for apps that ignore the start-up flag). The Assistant and Expert mode open full-screen.

Other tools: drop any portable 64-bit tool (e.g. a downloaded hackintosh utility pack) into `\\HackPE\\tools` on the stick or `tools/custom` in the repo. It shows up as T1, T2… in the Tools menu.

No mouse or touchpad yet? Everything is keyboard-driven: Tab/Enter in the GUI, F1…F9 + arrows on the HackPE Bar, and the text menus. **Expert mode** (the tabbed GUI with every option) and a text-mode menu/wizard are always available.

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
Launch.cmd                 WinPE auto-start: HackPE Bar, text menu fallback
gui/HackPE-Bar.ps1         taskbar-style launcher docked at the bottom of the screen
gui/HackPE-Assistant.ps1   guided step-by-step assistant (default)
gui/HackPE-Expert.ps1      expert GUI (5 tabs, every option)
engine/hackpe.py      engine: detect/plan/fetch/build-efi/recovery/make-usb/prepare-stick/install-esp/starter-efi/wizard
engine/hwdb.py        hardware knowledge base (CPU/GPU/NIC/Wi-Fi, SMBIOS rules, quirks, packages)
engine/detect.ps1     driver-independent hardware probe
engine/tools.py       Tools menu: ACPI dump, SSDT auto-gen, SSDTTime, USBToolBox, apps, drivers, Wi-Fi
build/                build-acpica-win64.sh (64-bit iasl/acpidump for WinPE), make_disk_image.py (USB image),
                      boot_test.sh (QEMU/OVMF boot screenshots), release_notes.py
tools/custom/         your own tools, baked into the ISO
tests/                regression tests + hardware fixtures (run in CI)
drivers/              optional extra WinPE drivers (injected by CI)
.github/workflows/    build-iso.yml (test + ISO + release), build-image.yml (USB image -> same release),
                      pages.yml (Config Compare web page -> GitHub Pages)
web/                  Config Compare: React page that compares two config.plist files (engine in web/src/engine)
data/                 cacert.pem (downloaded at build)
```

To support new hardware, edit `engine/hwdb.py`. All the rules live in tables there.
