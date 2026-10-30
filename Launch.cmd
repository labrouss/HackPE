@echo off
rem ==== HackPE launcher (called from startnet.cmd in WinPE, or run on Windows as admin) ====
setlocal enabledelayedexpansion
set "HACKPE_ROOT=%~dp0"
set "HACKPE_ROOT=%HACKPE_ROOT:~0,-1%"
set "PY=%HACKPE_ROOT%\python\python.exe"
set "ENGINE=%HACKPE_ROOT%\engine\hackpe.py"
set PYTHONIOENCODING=utf-8

rem --- cache search order: boot media (persists on USB) -> RAM work dir -> bundled
set "HACKPE_CACHE="
for %%D in (C D E F G H I J K L M N O P Q R S T U V W Y Z) do (
    if exist "%%D:\HackPE\cache\manifest.json" set "HACKPE_CACHE=!HACKPE_CACHE!%%D:\HackPE\cache;"
)
if exist X:\ (set "WORK=X:\HackPE-work") else (set "WORK=%TEMP%\HackPE-work")
set "HACKPE_CACHE=!HACKPE_CACHE!%WORK%\cache;%HACKPE_ROOT%\cache"
endlocal & set "HACKPE_CACHE=%HACKPE_CACHE%" & set "HACKPE_ROOT=%HACKPE_ROOT%" & set "PY=%PY%" & set "ENGINE=%ENGINE%" & set PYTHONIOENCODING=utf-8

title HackPE
color 0F
"%PY%" "%ENGINE%" maximize
rem --- drivers dropped into <stick>\HackPE\drivers are loaded automatically (touchpad, LAN, Wi-Fi...)
"%PY%" "%ENGINE%" load-drivers --auto
echo.
echo   HackPE - Hackintosh toolkit  (OpenCore / macOS up to 26 Tahoe)
echo   cache: %HACKPE_CACHE%
echo.

rem --- HackPE Bar: taskbar-style launcher docked at the bottom of the screen. It runs in this
rem     console (hidden while the bar is up) - closing it brings back the classic text menu below.
rem     (no "where.exe" check: WinPE does not ship it - test the file itself)
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
if exist X:\ (set "BARLOG=X:\HackPE-work\bar.log") else (set "BARLOG=%TEMP%\HackPE-work\bar.log")
set "BARSTART=-StartAssistant"
:bar
if not exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
    echo   PowerShell is not part of this WinPE image - using the text menu.
    goto menu
)
echo   Starting the HackPE Bar (and the Assistant)...  log: %BARLOG%
"%PS%" -NoProfile -ExecutionPolicy Bypass -STA -File "%HACKPE_ROOT%\gui\HackPE-Bar.ps1" %BARSTART%
set "BARRC=%errorlevel%"
"%PY%" "%ENGINE%" console --show
"%PY%" "%ENGINE%" workarea --bottom 0
"%PY%" "%ENGINE%" maximize
if not "%BARRC%"=="0" (
    echo.
    echo   *** The HackPE Bar stopped with exit code %BARRC%. Details:
    if exist "%BARLOG%" type "%BARLOG%"
    echo.
    if defined BARSTART (
        echo   Starting the HackPE Assistant instead...
        start "HackPE Assistant" "%PS%" -NoProfile -ExecutionPolicy Bypass -STA -File "%HACKPE_ROOT%\gui\HackPE-Assistant.ps1"
    )
)
set "BARSTART="

:menu
echo.
echo   ---------------------------------------------
echo    B  HackPE Bar (taskbar-style menu)
echo    1  HackPE Assistant (guided, recommended)
echo    E  Expert mode (all options)
echo    2  Text-mode wizard (detect, build EFI, make USB)
echo    T  Tools: ACPI dump, SSDT auto-generation, SSDTTime, USBToolBox,
echo          OCAuxiliaryTools, Hackintosh EFI Builder, drivers, Wi-Fi, run any program
echo    X  File explorer (Explorer++)       N  Text editor (Notepad++)
echo    O  Output folder of this PC (hardware, ACPI, SSDTs, USB map, EFIs, logs)
echo    3  Hardware report
echo    4  Update OpenCore / kexts (needs internet)
echo    5  Command prompt
echo    6  Network: show IP configuration
echo    R  Reboot          S  Shut down
echo   ---------------------------------------------
set "c="
set /p "c=  Choice: "
if /i "%c%"=="B" goto bar
if "%c%"=="1" start "HackPE Assistant" "%PS%" -NoProfile -ExecutionPolicy Bypass -STA -File "%HACKPE_ROOT%\gui\HackPE-Assistant.ps1"
if /i "%c%"=="E" start "HackPE Expert" "%PS%" -NoProfile -ExecutionPolicy Bypass -STA -File "%HACKPE_ROOT%\gui\HackPE-Expert.ps1"
if "%c%"=="2" "%PY%" "%ENGINE%" wizard
if /i "%c%"=="T" "%PY%" "%ENGINE%" tools
if /i "%c%"=="X" "%PY%" "%ENGINE%" app explorer
if /i "%c%"=="N" "%PY%" "%ENGINE%" app notepad
if /i "%c%"=="O" "%PY%" "%ENGINE%" open-output
if "%c%"=="3" "%PY%" "%ENGINE%" detect --summary
if "%c%"=="4" "%PY%" "%ENGINE%" fetch
if "%c%"=="5" start "HackPE shell" /max cmd.exe /k cd /d "%HACKPE_ROOT%"
if "%c%"=="6" ipconfig /all
if /i "%c%"=="R" wpeutil reboot
if /i "%c%"=="S" wpeutil shutdown
goto menu
