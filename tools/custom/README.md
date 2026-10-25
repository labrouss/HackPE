# Your own tools

Anything you put here (`.exe`, `.py`, `.cmd`, `.bat`, `.ps1`, or a folder like `GenSMBIOS\GenSMBIOS.py`)
is baked into the ISO and listed under **Tools > Your tools** (T1, T2, ...).

You can also add tools later without rebuilding: copy them to `\HackPE\tools` on the HackPE USB stick.

Notes for WinPE: only 64-bit Windows programs run (no WOW64), there is no Explorer/.NET GUI shell,
and Python scripts run with the bundled Python (standard library only, no tkinter).
