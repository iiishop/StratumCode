@echo off
setlocal
chcp 936 >nul 2>&1

set "OUT=C:\XP_Diagnostic_Stage2.txt"

echo Windows XP Stage-2 Diagnostic > "%OUT%"
echo Generated: %DATE% %TIME% >> "%OUT%"
echo Computer: %COMPUTERNAME% User: %USERNAME% >> "%OUT%"

call :section "1. RPC / DCOM / WMI CORE SERVICES"

for %%S in (RpcSs DcomLaunch winmgmt EventSystem SENS PlugPlay AudioSrv LanmanWorkstation WebClient Nla) do (
  echo.>> "%OUT%"
  echo --- SERVICE %%S --- >> "%OUT%"
  sc query %%S >> "%OUT%" 2>&1
  sc qc %%S >> "%OUT%" 2>&1
)

call :section "2. RPC / DCOM REGISTRY"

reg query "HKLM\SYSTEM\CurrentControlSet\Services\RpcSs" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\DcomLaunch" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\winmgmt" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Ole" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Rpc" /s >> "%OUT%" 2>&1

call :section "3. WMI BASIC HEALTH TESTS"

echo --- WMIC OS --- >> "%OUT%"
wmic os get Caption,Version,BuildNumber,LastBootUpTime /format:list >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- WMIC COMPUTER SYSTEM --- >> "%OUT%"
wmic computersystem get Manufacturer,Model,TotalPhysicalMemory /format:list >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- WMIC SERVICES SAMPLE --- >> "%OUT%"
wmic service get Name,State,StartMode /format:list >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- WMI repository path --- >> "%OUT%"
dir /a "%SystemRoot%\system32\wbem\Repository" >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\wbem" >> "%OUT%" 2>&1

call :section "4. NETWORK PROVIDERS"

reg query "HKLM\SYSTEM\CurrentControlSet\Control\NetworkProvider\Order" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\NWCWorkstation" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\WebClient" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\RDPNP" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- NET USE --- >> "%OUT%"
net use >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Provider-related DLLs --- >> "%OUT%"
for %%F in (
  nwprovau.dll
  ntlanman.dll
  drprov.dll
  webclnt.dll
  mpr.dll
  mup.sys
) do (
  echo [%%F] >> "%OUT%"
  dir /a "%SystemRoot%\system32\%%F" >> "%OUT%" 2>&1
  dir /a "%SystemRoot%\system32\drivers\%%F" >> "%OUT%" 2>&1
)

call :section "5. HID / USB CORE FILES"

for %%F in (
  hidclass.sys
  hidparse.sys
  hidusb.sys
  hid.dll
  usbhub.sys
  usbport.sys
  usbehci.sys
  usbuhci.sys
  usbohci.sys
  usbccgp.sys
) do (
  echo.>> "%OUT%"
  echo --- %%F --- >> "%OUT%"
  dir /a "%SystemRoot%\system32\drivers\%%F" >> "%OUT%" 2>&1
  dir /a "%SystemRoot%\system32\%%F" >> "%OUT%" 2>&1
)

call :section "6. HID / USB SERVICE KEYS"

for %%S in (hidclass hidusb usbhub usbehci usbuhci usbohci usbccgp) do (
  echo.>> "%OUT%"
  echo --- SERVICE KEY %%S --- >> "%OUT%"
  reg query "HKLM\SYSTEM\CurrentControlSet\Services\%%S" /s >> "%OUT%" 2>&1
)

call :section "7. AUDIO CORE FILES"

for %%F in (
  portcls.sys
  wdmaud.sys
  sysaudio.sys
  kmixer.sys
  drmk.sys
  stream.sys
  ks.sys
  ksuser.dll
  mmdrv.dll
  winmm.dll
) do (
  echo.>> "%OUT%"
  echo --- %%F --- >> "%OUT%"
  dir /a "%SystemRoot%\system32\drivers\%%F" >> "%OUT%" 2>&1
  dir /a "%SystemRoot%\system32\%%F" >> "%OUT%" 2>&1
)

call :section "8. AUDIO SERVICE KEYS"

for %%S in (AudioSrv portcls wdmaud sysaudio kmixer drmk stream ks) do (
  echo.>> "%OUT%"
  echo --- SERVICE KEY %%S --- >> "%OUT%"
  reg query "HKLM\SYSTEM\CurrentControlSet\Services\%%S" /s >> "%OUT%" 2>&1
)

call :section "9. PNP / DEVICE ENUMERATIONS FROM REGISTRY"

echo --- PCI ENUM --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\PCI" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- USB ENUM --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB" /s >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- HID ENUM --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\HID" /s >> "%OUT%" 2>&1

call :section "10. REALTEK AUDIO / CLASS KEYS"

reg query "HKLM\SYSTEM\CurrentControlSet\Enum\HDAUDIO" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Control\Class\{4D36E96C-E325-11CE-BFC1-08002BE10318}" /s >> "%OUT%" 2>&1

call :section "11. EXPLORER / OLE / CLIPBOARD RELATED FILES"

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
  rpcrt4.dll
  mpr.dll
) do (
  echo.>> "%OUT%"
  echo --- %%F --- >> "%OUT%"
  dir /a "%SystemRoot%\%%F" >> "%OUT%" 2>&1
  dir /a "%SystemRoot%\system32\%%F" >> "%OUT%" 2>&1
)

call :section "12. EXPLORER / SHELL EXTENSIONS"

reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellExecuteHooks" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellIconOverlayIdentifiers" /s >> "%OUT%" 2>&1
reg query "HKCR\*\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1
reg query "HKCR\Directory\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1
reg query "HKCR\Folder\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1

call :section "13. EVENT LOG SERVICE + FILES"

sc query Eventlog >> "%OUT%" 2>&1
sc qc Eventlog >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\Eventlog" /s >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\config\*.evt" >> "%OUT%" 2>&1

call :section "14. RPC ENDPOINT / BASIC COMMAND TESTS"

echo --- SC ENUM --- >> "%OUT%"
sc query type= service state= all >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- TASKLIST --- >> "%OUT%"
tasklist >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- NET START --- >> "%OUT%"
net start >> "%OUT%" 2>&1

call :section "15. KEY FILE VERSION / SIZE SNAPSHOT"

for %%F in (
  "%SystemRoot%\system32\rpcss.dll"
  "%SystemRoot%\system32\rpcrt4.dll"
  "%SystemRoot%\system32\wbem\wmiprvse.exe"
  "%SystemRoot%\system32\wbem\fastprox.dll"
  "%SystemRoot%\system32\wbem\wbemcore.dll"
  "%SystemRoot%\system32\services.exe"
  "%SystemRoot%\system32\svchost.exe"
) do (
  echo.>> "%OUT%"
  echo %%~F >> "%OUT%"
  dir /a "%%~F" >> "%OUT%" 2>&1
)

call :section "16. QUICK MANUAL TEST CHECKLIST"

echo Please test manually: >> "%OUT%"
echo 1. Does services.msc open and list services? >> "%OUT%"
echo 2. Does eventvwr.msc open System/Application logs? >> "%OUT%"
echo 3. Can Notepad copy/paste text between two Notepad windows? >> "%OUT%"
echo 4. Can Explorer copy/paste a TXT file between two local folders? >> "%OUT%"
echo 5. Does Device Manager show yellow question/exclamation marks? >> "%OUT%"
echo 6. Does opening Sound and Audio Devices show an audio device? >> "%OUT%"

echo.>> "%OUT%"
echo END OF STAGE-2 REPORT >> "%OUT%"

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
