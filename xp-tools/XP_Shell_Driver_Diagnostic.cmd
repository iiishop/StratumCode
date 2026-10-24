@echo off
setlocal
chcp 936 >nul 2>&1

set "OUT=%USERPROFILE%\Desktop\XP_Shell_Driver_Diagnostic.txt"

echo Windows XP Shell / Clipboard / Driver Diagnostic > "%OUT%"
echo Generated: %DATE% %TIME% >> "%OUT%"
echo Computer: %COMPUTERNAME% User: %USERNAME% >> "%OUT%"

call :section "1. OS / BOOT / BASIC SYSTEM"
ver >> "%OUT%" 2>&1
systeminfo >> "%OUT%" 2>&1

call :section "2. WINLOGON / USERINIT / SHELL"
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v Shell >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v Userinit >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v GinaDLL >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows NT\CurrentVersion\Winlogon" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Winlogon Notify --- >> "%OUT%"
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\Notify" /s >> "%OUT%" 2>&1

call :section "3. RUN / RUNONCE / STARTUP"
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce" >> "%OUT%" 2>&1
echo.>> "%OUT%"
wmic startup get Caption,Command,Location,User /format:list >> "%OUT%" 2>&1

call :section "4. NETWORK / MAPPED DRIVES"
net use >> "%OUT%" 2>&1
ipconfig /all >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Control\NetworkProvider\Order" >> "%OUT%" 2>&1

call :section "5. AUTOMATIC SERVICES"
wmic service where "StartMode='Auto'" get Name,DisplayName,State,StartMode,StartName,PathName /format:list >> "%OUT%" 2>&1

call :section "6. PNP DEVICES WITH ERRORS"
wmic path Win32_PnPEntity where "ConfigManagerErrorCode<>0" get Name,PNPDeviceID,ConfigManagerErrorCode,Manufacturer,Service /format:list >> "%OUT%" 2>&1

call :section "7. SOUND DEVICES"
wmic sounddev get Name,Manufacturer,PNPDeviceID,Status,ConfigManagerErrorCode /format:list >> "%OUT%" 2>&1

call :section "8. USB / HID / INPUT DEVICES"
wmic path Win32_PnPEntity where "PNPDeviceID like 'USB%%'" get Name,PNPDeviceID,ConfigManagerErrorCode,Manufacturer,Service /format:list >> "%OUT%" 2>&1
echo.>> "%OUT%"
wmic path Win32_PnPEntity where "Name like '%%HID%%'" get Name,PNPDeviceID,ConfigManagerErrorCode,Manufacturer,Service /format:list >> "%OUT%" 2>&1
echo.>> "%OUT%"
wmic path Win32_PnPEntity where "Name like '%%Tablet%%' or Name like '%%Pen%%' or Name like '%%Wacom%%' or Name like '%%Hanvon%%' or Name like '%%Huion%%' or Name like '%%Ugee%%'" get Name,PNPDeviceID,ConfigManagerErrorCode,Manufacturer,Service /format:list >> "%OUT%" 2>&1

call :section "9. EXPLORER / SHELL REGISTRY"
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StuckRects2" /s >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Streams\Desktop" /s >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Browser Helper Objects --- >> "%OUT%"
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Browser Helper Objects" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- ShellExecuteHooks --- >> "%OUT%"
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellExecuteHooks" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Shell Icon Overlays --- >> "%OUT%"
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellIconOverlayIdentifiers" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Context Menu Handlers --- >> "%OUT%"
reg query "HKCR\*\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1
reg query "HKCR\Directory\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1
reg query "HKCR\Folder\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1

call :section "10. CORE SHELL / OLE FILES"
for %%F in (
 explorer.exe
 shell32.dll
 ole32.dll
 oleaut32.dll
 comctl32.dll
 shlwapi.dll
 browseui.dll
 shdocvw.dll
 user32.dll
 gdi32.dll
 advapi32.dll
 rpcrt4.dll
 comdlg32.dll
 urlmon.dll
 wininet.dll
 msvcrt.dll
) do (
 echo --- %%F --- >> "%OUT%"
 dir /a "%SystemRoot%\%%F" >> "%OUT%" 2>&1
 dir /a "%SystemRoot%\system32\%%F" >> "%OUT%" 2>&1
 dir /a "%SystemRoot%\system32\dllcache\%%F" >> "%OUT%" 2>&1
)

call :section "11. COMPARE SYSTEM32 VS DLLCACHE FOR CORE FILES"
for %%F in (
 shell32.dll
 ole32.dll
 oleaut32.dll
 comctl32.dll
 shlwapi.dll
 browseui.dll
 shdocvw.dll
 user32.dll
 gdi32.dll
 advapi32.dll
 rpcrt4.dll
 comdlg32.dll
 urlmon.dll
 wininet.dll
 msvcrt.dll
) do (
 if exist "%SystemRoot%\system32\%%F" if exist "%SystemRoot%\system32\dllcache\%%F" (
   echo [%%F] >> "%OUT%"
   fc /b "%SystemRoot%\system32\%%F" "%SystemRoot%\system32\dllcache\%%F" >> "%OUT%" 2>&1
 )
)

call :section "12. STORAGE / USB / HID / AUDIO CORE SERVICES"
for %%S in (atapi intelide pciide pciidex usbehci usbohci usbuhci usbhub hidusb hidclass portcls wdmaud sysaudio kmixer) do (
 echo --- %%S --- >> "%OUT%"
 reg query "HKLM\SYSTEM\CurrentControlSet\Services\%%S" /s >> "%OUT%" 2>&1
)

call :section "13. CURRENT PROCESSES"
tasklist /v >> "%OUT%" 2>&1

call :section "14. RECENT SYSTEM LOG ERRORS / WARNINGS"
if exist "%SystemRoot%\system32\eventquery.vbs" (
 cscript //nologo "%SystemRoot%\system32\eventquery.vbs" /L SYSTEM /FI "Type eq ERROR" /FO LIST >> "%OUT%" 2>&1
 cscript //nologo "%SystemRoot%\system32\eventquery.vbs" /L SYSTEM /FI "Type eq WARNING" /FO LIST >> "%OUT%" 2>&1
) else (
 echo eventquery.vbs not found. >> "%OUT%"
)

call :section "15. RECENT APPLICATION LOG ERRORS / WARNINGS"
if exist "%SystemRoot%\system32\eventquery.vbs" (
 cscript //nologo "%SystemRoot%\system32\eventquery.vbs" /L APPLICATION /FI "Type eq ERROR" /FO LIST >> "%OUT%" 2>&1
 cscript //nologo "%SystemRoot%\system32\eventquery.vbs" /L APPLICATION /FI "Type eq WARNING" /FO LIST >> "%OUT%" 2>&1
) else (
 echo eventquery.vbs not found. >> "%OUT%"
)

call :section "16. OPTIONAL SHELL SELF-TEST PATHS"
echo Test these manually after report generation: >> "%OUT%"
echo 1. Open two Explorer folders and test Ctrl+C / Ctrl+V. >> "%OUT%"
echo 2. Open Notepad and test text copy/paste. >> "%OUT%"
echo 3. Check whether running windows appear as taskbar buttons. >> "%OUT%"
echo 4. Record whether explorer.exe exits/restarts during the test. >> "%OUT%"

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
echo ==============================================================================>> "%OUT%"
echo %~1>> "%OUT%"
echo ==============================================================================>> "%OUT%"
exit /b
