@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 936 >nul 2>&1

set "OUT=C:\XP_Remaining_Issues_Report.txt"
set "TMP=C:\XPRemainingTmp"
set "PS=%SystemRoot%\system32\WindowsPowerShell\v1.0\powershell.exe"

if exist "%TMP%" rd /s /q "%TMP%" >nul 2>&1
md "%TMP%" >nul 2>&1

echo Windows XP Remaining Issues Diagnostic > "%OUT%"
echo Generated: %DATE% %TIME% >> "%OUT%"
echo Computer: %COMPUTERNAME% User: %USERNAME% >> "%OUT%"
echo.>> "%OUT%"
echo IMPORTANT: Run this with the PenPower device physically plugged in.>> "%OUT%"

call :section "1. CORE HEALTH AFTER ACL/RPC REPAIR"
sc query RpcSs >> "%OUT%" 2>&1
sc query winmgmt >> "%OUT%" 2>&1
sc query PlugPlay >> "%OUT%" 2>&1
tasklist >> "%OUT%" 2>&1
wmic os get Caption,Version,BuildNumber /format:list >> "%OUT%" 2>&1

call :section "2. LIVE PENPOWER / USB / HID ENUMERATION"
echo --- Current live PnP objects matching VID 04B4 / PenPower --- >> "%OUT%"
wmic path Win32_PnPEntity where "PNPDeviceID like '%%04B4%%' or Name like '%%PenPower%%'" get Name,PNPDeviceID,ConfigManagerErrorCode,Status,Service /format:list > "%TMP%\pen_live.txt" 2>&1
type "%TMP%\pen_live.txt" >> "%OUT%"

echo.>> "%OUT%"
echo --- Historical registry instances --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB\Vid_04b4&Pid_fef3" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\HID\Vid_04b4&Pid_fef3" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- HID class / filter configuration --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Control\Class\{745A17A0-74D3-11D0-B6FE-00A0C90F57DA}" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- HID/USB services --- >> "%OUT%"
for %%S in (HidServ hidusb usbhub usbehci PlugPlay) do (
  echo [%%S] >> "%OUT%"
  sc query %%S >> "%OUT%" 2>&1
  sc qc %%S >> "%OUT%" 2>&1
)

echo.>> "%OUT%"
echo --- HID/USB files --- >> "%OUT%"
for %%F in (hidserv.dll hidclass.sys hidparse.sys hidusb.sys usbhub.sys usbport.sys usbehci.sys usbccgp.sys) do (
  dir /s /b "%SystemRoot%\%%F" >> "%OUT%" 2>&1
)

call :section "3. USB CONTROLLER PATH - XP EHCI VS XHCI"
echo --- EHCI controllers (XP USB 2.0 path) --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\PCI" /s | findstr /i /c:"DEV_8C26" /c:"DEV_8C2D" /c:"usbehci" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- xHCI controller (USB 3.0 path) --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\PCI" /s | findstr /i /c:"DEV_8C31" /c:"xHCI" /c:"eXtensible" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Any installed xHCI service candidates --- >> "%OUT%"
sc query type= driver state= all | findstr /i "xhci usb3 iusb" >> "%OUT%" 2>&1

call :section "4. SETUPAPI HISTORY FOR PENPOWER"
if exist "%SystemRoot%\setupapi.log" (
  findstr /i /c:"VID_04B4" /c:"PID_FEF3" /c:"PenPower" "%SystemRoot%\setupapi.log" >> "%OUT%" 2>&1
) else (
  echo %SystemRoot%\setupapi.log not found. >> "%OUT%"
)

call :section "5. PENPOWER SOFTWARE / DRIVER INSTALLATION"
echo --- Installed program registry matches --- >> "%OUT%"
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" /s | findstr /i "PenPower Tablet PPJr HandWriter Tablet Driver WinTab" >> "%OUT%" 2>&1
reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" /s | findstr /i "PenPower Tablet PPJr HandWriter Tablet Driver WinTab" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Common PenPower files/processes --- >> "%OUT%"
tasklist | findstr /i "PPJr PenPower Tablet" >> "%OUT%" 2>&1
dir /s /b "C:\Program Files\*PenPower*" >> "%OUT%" 2>&1
dir /s /b "C:\Program Files\*PPJr*" >> "%OUT%" 2>&1
dir /s /b "%SystemRoot%\system32\Wintab32.dll" >> "%OUT%" 2>&1

call :section "6. POWERSHELL ENGINE FILES / DOTNET"
if exist "%PS%" (
  dir /a "%PS%" >> "%OUT%" 2>&1
  cacls "%PS%" >> "%OUT%" 2>&1
) else (
  echo POWERSHELL.EXE MISSING: %PS% >> "%OUT%"
)

reg query "HKLM\SOFTWARE\Microsoft\PowerShell\1" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v2.0.50727" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v3.0" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v3.5" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Client" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" /s >> "%OUT%" 2>&1

call :section "7. POWERSHELL NOPROFILE TEST"
if exist "%PS%" (
  "%PS%" -NoLogo -NoProfile -NonInteractive -Command "Write-Output 'PS_NOPROFILE_OK'; $PSVersionTable; [Environment]::Version" > "%TMP%\ps_noprofile.txt" 2>&1
  set "PSNP=!ERRORLEVEL!"
  echo ExitCode=!PSNP! >> "%OUT%"
  type "%TMP%\ps_noprofile.txt" >> "%OUT%"
) else (
  set "PSNP=999"
)

call :section "8. POWERSHELL NORMAL-PROFILE TEST"
if exist "%PS%" (
  "%PS%" -NoLogo -NonInteractive -Command "Write-Output 'PS_NORMAL_OK'; $PSVersionTable" > "%TMP%\ps_normal.txt" 2>&1
  set "PSNORMAL=!ERRORLEVEL!"
  echo ExitCode=!PSNORMAL! >> "%OUT%"
  type "%TMP%\ps_normal.txt" >> "%OUT%"
) else (
  set "PSNORMAL=999"
)

call :section "9. POWERSHELL PROFILE FILES"
echo --- User Shell Folder Personal --- >> "%OUT%"
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" /v Personal >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders" /v Personal >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- All *profile.ps1 under current user --- >> "%OUT%"
dir /s /b "%USERPROFILE%\*profile.ps1" > "%TMP%\profiles.txt" 2>nul
type "%TMP%\profiles.txt" >> "%OUT%"

for /f "usebackq delims=" %%F in ("%TMP%\profiles.txt") do (
  echo.>> "%OUT%"
  echo ===== PROFILE: %%F ===== >> "%OUT%"
  type "%%F" >> "%OUT%" 2>&1
)

echo.>> "%OUT%"
echo --- System-wide PowerShell profiles --- >> "%OUT%"
dir /s /b "%SystemRoot%\system32\WindowsPowerShell\v1.0\*profile.ps1" >> "%OUT%" 2>&1

call :section "10. POWERSHELL EVENT LOG EVIDENCE"
if exist "%SystemRoot%\system32\eventquery.vbs" (
  echo --- PowerShell events --- >> "%OUT%"
  cscript //nologo "%SystemRoot%\system32\eventquery.vbs" /L "Windows PowerShell" /FO LIST /V >> "%OUT%" 2>&1
  echo.>> "%OUT%"
  echo --- Application Error events --- >> "%OUT%"
  cscript //nologo "%SystemRoot%\system32\eventquery.vbs" /L APPLICATION /FI "Source eq Application Error" /FO LIST /V >> "%OUT%" 2>&1
  echo.>> "%OUT%"
  echo --- .NET Runtime events --- >> "%OUT%"
  cscript //nologo "%SystemRoot%\system32\eventquery.vbs" /L APPLICATION /FI "Source eq .NET Runtime" /FO LIST /V >> "%OUT%" 2>&1
)

call :section "11. POWERSHELL SHORTCUTS"
echo Set sh = CreateObject("WScript.Shell") > "%TMP%\readlnk.vbs"
echo p = WScript.Arguments(0) >> "%TMP%\readlnk.vbs"
echo Set lnk = sh.CreateShortcut(p) >> "%TMP%\readlnk.vbs"
echo WScript.Echo "LINK=" ^& p >> "%TMP%\readlnk.vbs"
echo WScript.Echo "TARGET=" ^& lnk.TargetPath >> "%TMP%\readlnk.vbs"
echo WScript.Echo "ARGS=" ^& lnk.Arguments >> "%TMP%\readlnk.vbs"
echo WScript.Echo "WORKDIR=" ^& lnk.WorkingDirectory >> "%TMP%\readlnk.vbs"

dir /s /b "%ALLUSERSPROFILE%\*.lnk" 2>nul | findstr /i "PowerShell" > "%TMP%\pslinks.txt"
dir /s /b "%USERPROFILE%\*.lnk" 2>nul | findstr /i "PowerShell" >> "%TMP%\pslinks.txt"
for /f "usebackq delims=" %%L in ("%TMP%\pslinks.txt") do (
  cscript //nologo "%TMP%\readlnk.vbs" "%%L" >> "%OUT%" 2>&1
)

call :section "12. AUTO SUMMARY"

findstr /i "04B4" "%TMP%\pen_live.txt" >nul 2>&1
if errorlevel 1 (
  echo [PEN] CURRENTLY NOT ENUMERATED by Windows PnP. >> "%OUT%"
  echo       Historical VID_04B4/PID_FEF3 entries exist, but Windows does not see it live now. >> "%OUT%"
  echo       First test a rear/native USB 2.0 port, not a USB 3.0/xHCI port. >> "%OUT%"
  echo       If no USB 2.0 port sees it either, check cable/device power and SetupAPI history above. >> "%OUT%"
) else (
  echo [PEN] CURRENTLY ENUMERATED by Windows. >> "%OUT%"
  findstr /i "ConfigManagerErrorCode=0" "%TMP%\pen_live.txt" >nul 2>&1
  if not errorlevel 1 (
    echo       PnP reports Code 0. Hardware/HID layer is alive; PenPower application/WinTab/driver layer is the likely problem. >> "%OUT%"
  ) else (
    echo       Windows sees the device but reports a PnP error. Review ConfigManagerErrorCode above. >> "%OUT%"
  )
)

echo.>> "%OUT%"
findstr /c:"PS_NOPROFILE_OK" "%TMP%\ps_noprofile.txt" >nul 2>&1
if not errorlevel 1 (
  echo [POWERSHELL] Engine works with -NoProfile. >> "%OUT%"
  findstr /c:"PS_NORMAL_OK" "%TMP%\ps_normal.txt" >nul 2>&1
  if errorlevel 1 (
    echo              Normal startup fails: a PowerShell profile is the primary suspect. >> "%OUT%"
  ) else (
    echo              Normal command-mode startup also works. The flashing window is most likely the shortcut/launch arguments, not the engine. >> "%OUT%"
  )
  echo              A clean launcher can be used: powershell.exe -NoProfile -NoExit >> "%OUT%"
) else (
  echo [POWERSHELL] Engine itself fails even with -NoProfile. >> "%OUT%"
  echo              Review .NET installation and Application/.NET events above before reinstalling WMF/PowerShell. >> "%OUT%"
)

echo.>> "%OUT%"
echo === RECOMMENDED NEXT ACTIONS === >> "%OUT%"
echo PenPower: >> "%OUT%"
echo 1. If NOT live-enumerated: move the device to a known USB 2.0 port and rerun this script. >> "%OUT%"
echo 2. If live-enumerated with Code 0 but PenPower says hardware not found: software layer is broken. Back up/remove old PenPower/Tablet/WinTab components and reinstall the matching PenPower software. >> "%OUT%"
echo 3. Do not assume HidServ=Disabled is itself the cause; standard USB HID enumeration uses hidusb/hidclass independently. >> "%OUT%"
echo.>> "%OUT%"
echo PowerShell: >> "%OUT%"
echo 1. If -NoProfile works: launch with -NoProfile -NoExit and repair/remove the bad profile or bad shortcut arguments. >> "%OUT%"
echo 2. If -NoProfile fails: repair .NET/Windows Management Framework based on the captured error/event, not by random DLL replacement. >> "%OUT%"

echo.>> "%OUT%"
echo END OF REPORT >> "%OUT%"

echo.
echo DONE
echo Report saved to:
echo %OUT%
echo.
pause
exit /b

:section
echo.>> "%OUT%"
echo =============================================================================>> "%OUT%"
echo %~1>> "%OUT%"
echo =============================================================================>> "%OUT%"
exit /b
