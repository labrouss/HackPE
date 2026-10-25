# Extra WinPE drivers (optional)

Drop Windows x64 driver folders here (`.inf` + `.sys` + `.cat`), e.g. a network card that WinPE
does not recognise (Realtek 2.5GbE, Killer, some Intel I226 revisions) or a storage controller.

The GitHub Actions build injects everything in this folder into WinPE automatically
(`Build-HackPE.ps1 -DriversPath drivers`). Locally: `.\Build-HackPE.ps1 -DriversPath .\drivers`.
