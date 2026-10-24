@echo off
setlocal EnableExtensions
chcp 936 >nul 2>&1

set "OUT=C:\XP_Doctor_Report.txt"
set "TMP=C:\XPDoctorTmp"

if exist "%TMP%" rd /s /q "%TMP%" >nul 2>&1
md "%TMP%" >nul 2>&1

echo Windows XP FINAL Diagnostic Report > "%OUT%"
echo Generated: %DATE% %TIME% >> "%OUT%"
echo Computer: %COMPUTERNAME% User: %USERNAME% >> "%OUT%"
echo This script is diagnostic-only except that it attempts to START RpcSs once. >> "%OUT%"
echo It does NOT change registry values, service configuration, ACLs, drivers, or files. >> "%OUT%"

call :section "A. OS / BOOT / USER / CONTROL SETS"
ver >> "%OUT%" 2>&1
echo.>> "%OUT%"
net user "%USERNAME%" >> "%OUT%" 2>&1
echo.>> "%OUT%"
reg query "HKLM\SYSTEM\Select" >> "%OUT%" 2>&1
echo.>> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Control\SafeBoot\Option" /s >> "%OUT%" 2>&1
echo.>> "%OUT%"
fsutil dirty query C: >> "%OUT%" 2>&1

call :section "B. CRITICAL SERVICE STATE / CONFIG / DEPENDENCIES"
for %%S in (RpcSs DcomLaunch Eventlog PlugPlay SamSs EventSystem SENS winmgmt AudioSrv CryptSvc Schedule Netman ShellHWDetection ProtectedStorage Spooler Themes LanmanWorkstation NWCWorkstation Dhcp W32Time) do (
  echo.>> "%OUT%"
  echo --- %%S --- >> "%OUT%"
  sc query %%S >> "%OUT%" 2>&1
  sc qc %%S >> "%OUT%" 2>&1
)
echo.>> "%OUT%"
echo --- RpcSs dependents --- >> "%OUT%"
sc enumdepend RpcSs >> "%OUT%" 2>&1

call :section "C. SERVICE SECURITY DESCRIPTORS"
for %%S in (RpcSs DcomLaunch Eventlog PlugPlay SamSs Themes LanmanWorkstation NWCWorkstation AudioSrv winmgmt EventSystem SENS) do (
  echo.>> "%OUT%"
  echo --- SDDL %%S --- >> "%OUT%"
  sc sdshow %%S >> "%OUT%" 2>&1
)
sc sdshow RpcSs > "%TMP%\rpcss_sddl.txt" 2>&1

call :section "D. RPCSS CURRENT VS LAST-KNOWN-GOOD / OTHER CONTROL SETS"
for %%C in (001 002 003 004) do (
  echo.>> "%OUT%"
  echo ===== ControlSet%%C RpcSs ===== >> "%OUT%"
  reg query "HKLM\SYSTEM\ControlSet%%C\Services\RpcSs" /v Start >> "%OUT%" 2>&1
  reg query "HKLM\SYSTEM\ControlSet%%C\Services\RpcSs" /v Type >> "%OUT%" 2>&1
  reg query "HKLM\SYSTEM\ControlSet%%C\Services\RpcSs" /v ObjectName >> "%OUT%" 2>&1
  reg query "HKLM\SYSTEM\ControlSet%%C\Services\RpcSs" /v ImagePath >> "%OUT%" 2>&1
  reg query "HKLM\SYSTEM\ControlSet%%C\Services\RpcSs\Parameters" /v ServiceDll >> "%OUT%" 2>&1
  reg query "HKLM\SYSTEM\ControlSet%%C\Services\RpcSs\Security" /v Security >> "%OUT%" 2>&1
)
reg query "HKLM\SYSTEM\ControlSet001\Services\RpcSs\Security" /v Security 2>nul | findstr /i "REG_BINARY" > "%TMP%\rpcsec001.txt"
reg query "HKLM\SYSTEM\ControlSet002\Services\RpcSs\Security" /v Security 2>nul | findstr /i "REG_BINARY" > "%TMP%\rpcsec002.txt"
echo.>> "%OUT%"
echo --- Raw Security value comparison: ControlSet001 vs ControlSet002 --- >> "%OUT%"
fc /l "%TMP%\rpcsec001.txt" "%TMP%\rpcsec002.txt" >> "%OUT%" 2>&1

call :section "E. SVCHOST GROUPS / SERVICE GROUP ORDER"
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Svchost" /v rpcss >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Svchost" /v DcomLaunch >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Svchost" /v netsvcs >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Control\ServiceGroupOrder" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Control\GroupOrderList" /s >> "%OUT%" 2>&1

call :section "F. ACTIVE RPC START TEST / ENDPOINT TEST"
sc start RpcSs > "%TMP%\rpcstart.txt" 2>&1
type "%TMP%\rpcstart.txt" >> "%OUT%"
ping 127.0.0.1 -n 3 >nul
echo.>> "%OUT%"
sc query RpcSs >> "%OUT%" 2>&1
echo.>> "%OUT%"
netstat -ano | find ":135" >> "%OUT%" 2>&1

call :section "G. BASIC API / WMI RETEST"
echo --- tasklist --- >> "%OUT%"
tasklist >> "%OUT%" 2>&1
echo.>> "%OUT%"
echo --- systeminfo --- >> "%OUT%"
systeminfo >> "%OUT%" 2>&1
echo.>> "%OUT%"
echo --- WMIC OS --- >> "%OUT%"
wmic os get Caption,Version,BuildNumber,LastBootUpTime /format:list >> "%OUT%" 2>&1
echo.>> "%OUT%"
echo --- WMIC service --- >> "%OUT%"
wmic service get Name,State,StartMode /format:list >> "%OUT%" 2>&1
echo.>> "%OUT%"
echo --- WMIC sound --- >> "%OUT%"
wmic sounddev get Name,Status,ConfigManagerErrorCode,PNPDeviceID /format:list >> "%OUT%" 2>&1

call :section "H. CORE RPC / COM / SECURITY FILES + ACLS"
for %%F in (
 "%SystemRoot%\system32\svchost.exe"
 "%SystemRoot%\system32\rpcss.dll"
 "%SystemRoot%\system32\rpcrt4.dll"
 "%SystemRoot%\system32\ole32.dll"
 "%SystemRoot%\system32\oleaut32.dll"
 "%SystemRoot%\system32\services.exe"
 "%SystemRoot%\system32\advapi32.dll"
 "%SystemRoot%\system32\secur32.dll"
 "%SystemRoot%\system32\lsasrv.dll"
 "%SystemRoot%\system32\ntdll.dll"
 "%SystemRoot%\system32\kernel32.dll"
) do (
  echo.>> "%OUT%"
  echo --- %%~F --- >> "%OUT%"
  dir /a /q "%%~F" >> "%OUT%" 2>&1
  cacls "%%~F" >> "%OUT%" 2>&1
)

echo Set fso = CreateObject("Scripting.FileSystemObject") > "%TMP%\filever.vbs"
echo p = WScript.Arguments(0) >> "%TMP%\filever.vbs"
echo If fso.FileExists(p) Then >> "%TMP%\filever.vbs"
echo   WScript.Echo p ^& " VERSION=" ^& fso.GetFileVersion(p) ^& " SIZE=" ^& fso.GetFile(p).Size >> "%TMP%\filever.vbs"
echo Else >> "%TMP%\filever.vbs"
echo   WScript.Echo p ^& " MISSING" >> "%TMP%\filever.vbs"
echo End If >> "%TMP%\filever.vbs"

echo.>> "%OUT%"
echo --- Core file versions --- >> "%OUT%"
for %%F in (
 "%SystemRoot%\system32\svchost.exe"
 "%SystemRoot%\system32\rpcss.dll"
 "%SystemRoot%\system32\rpcrt4.dll"
 "%SystemRoot%\system32\ole32.dll"
 "%SystemRoot%\system32\services.exe"
 "%SystemRoot%\system32\advapi32.dll"
 "%SystemRoot%\system32\secur32.dll"
 "%SystemRoot%\system32\lsasrv.dll"
) do cscript //nologo "%TMP%\filever.vbs" "%%~F" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- ACL: system directories --- >> "%OUT%"
cacls "%SystemRoot%" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32\config" >> "%OUT%" 2>&1

call :section "I. BACKUP COPIES OF RPC / COM CORE FILES"
for %%F in (rpcss.dll rpcrt4.dll ole32.dll svchost.exe services.exe advapi32.dll secur32.dll lsasrv.dll) do (
  echo.>> "%OUT%"
  echo --- Copies of %%F under %SystemRoot% --- >> "%OUT%"
  dir /s /b "%SystemRoot%\%%F" >> "%OUT%" 2>&1
)

call :section "J. SECURITY POLICY / SETUP SECURITY TEMPLATES"
if exist "C:\XP_Diagnostic_CurrentSecurity.inf" del /f /q "C:\XP_Diagnostic_CurrentSecurity.inf" >nul 2>&1
secedit /export /cfg "C:\XP_Diagnostic_CurrentSecurity.inf" /areas SERVICES USER_RIGHTS >> "%OUT%" 2>&1
echo.>> "%OUT%"
echo --- Current exported security: RPC / service rights --- >> "%OUT%"
if exist "C:\XP_Diagnostic_CurrentSecurity.inf" (
  findstr /i /c:"RpcSs" /c:"SeServiceLogonRight" /c:"SeImpersonatePrivilege" /c:"SeAssignPrimaryTokenPrivilege" /c:"SeIncreaseQuotaPrivilege" "C:\XP_Diagnostic_CurrentSecurity.inf" >> "%OUT%" 2>&1
) else (
  echo Security export was not created. >> "%OUT%"
)

echo.>> "%OUT%"
echo --- RpcSs entries in setup/security templates --- >> "%OUT%"
for %%F in (
 "%SystemRoot%\security\templates\setup security.inf"
 "%SystemRoot%\security\templates\securedc.inf"
 "%SystemRoot%\security\templates\securews.inf"
 "%SystemRoot%\repair\secsetup.inf"
 "%SystemRoot%\inf\defltwk.inf"
) do (
  if exist "%%~F" (
    echo FILE: %%~F >> "%OUT%"
    findstr /i /c:"RpcSs" "%%~F" >> "%OUT%" 2>&1
  )
)
if exist "%SystemRoot%\security\templates" (
  for /r "%SystemRoot%\security\templates" %%F in (*.inf) do (
    findstr /i /m /c:"RpcSs" "%%F" >nul 2>&1
    if not errorlevel 1 (
      echo FILE: %%F >> "%OUT%"
      findstr /i /c:"RpcSs" "%%F" >> "%OUT%" 2>&1
    )
  )
)

call :section "K. NETWORK PROVIDERS / LOGIN-TIME NETWORK PATH"
reg query "HKLM\SYSTEM\CurrentControlSet\Control\NetworkProvider\Order" /s >> "%OUT%" 2>&1
for %%S in (NWCWorkstation LanmanWorkstation WebClient RDPNP) do (
  echo.>> "%OUT%"
  echo --- %%S --- >> "%OUT%"
  sc query %%S >> "%OUT%" 2>&1
  sc qc %%S >> "%OUT%" 2>&1
  reg query "HKLM\SYSTEM\CurrentControlSet\Services\%%S\NetworkProvider" /s >> "%OUT%" 2>&1
)
echo.>> "%OUT%"
net use >> "%OUT%" 2>&1
echo.>> "%OUT%"
for %%F in (nwprovau.dll nwwks.dll ntlanman.dll drprov.dll webclnt.dll davclnt.dll mpr.dll) do (
  dir /a "%SystemRoot%\system32\%%F" >> "%OUT%" 2>&1
)

call :section "L. EXPLORER / WINLOGON / TASKBAR / CLIPBOARD-RELATED STATE"
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v Shell >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v Userinit >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\Notify" /s >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StuckRects2" /s >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Streams\Desktop" /s >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Core shell files --- >> "%OUT%"
for %%F in (explorer.exe shell32.dll browseui.dll shdocvw.dll shlwapi.dll ole32.dll oleaut32.dll comctl32.dll user32.dll) do (
  dir /a "%SystemRoot%\%%F" >> "%OUT%" 2>&1
  dir /a "%SystemRoot%\system32\%%F" >> "%OUT%" 2>&1
)

call :section "M. THIRD-PARTY SHELL EXTENSIONS / CLSID SERVERS"
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellIconOverlayIdentifiers" /s >> "%OUT%" 2>&1
reg query "HKCR\*\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1
reg query "HKCR\Directory\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1
reg query "HKCR\Folder\shellex\ContextMenuHandlers" /s >> "%OUT%" 2>&1
for %%G in (
 "{CC00F81D-5262-450A-B1FA-D6BEE3406263}"
 "{A8C746CE-3270-4706-9F78-C76E9BB68237}"
 "{7C0F6D57-E799-4C8A-A319-8E2B4D724CF0}"
 "{5E19C0CE-C02C-46c2-98C3-A2E12EDE0E17}"
 "{367F6AE2-6809-4bed-B09B-228893FB33DD}"
 "{2E7A2C6C-B938-40a4-BA1C-C7EC982DC202}"
 "{6C18531F-CA85-45F7-8278-FF33CF0A5964}"
 "{731B2DBD-0F02-456D-A15C-BC2A5DAAF5A4}"
 "{36A21736-36C2-4C11-8ACB-D4136F2B57BD}"
 "{F9D0EFE7-1939-4156-B6E9-0006A5FDDC4E}"
) do (
  echo.>> "%OUT%"
  echo --- CLSID %%~G --- >> "%OUT%"
  reg query "HKCR\CLSID\%%~G\InprocServer32" /s >> "%OUT%" 2>&1
)

call :section "N. AUDIO STACK / REALTEK"
for %%S in (AudioSrv HDAudBus IntcAzAudAddService wdmaud sysaudio kmixer) do (
  echo.>> "%OUT%"
  echo --- %%S --- >> "%OUT%"
  sc query %%S >> "%OUT%" 2>&1
  sc qc %%S >> "%OUT%" 2>&1
  reg query "HKLM\SYSTEM\CurrentControlSet\Services\%%S" /s >> "%OUT%" 2>&1
)
for %%F in (audiosrv.dll RtkHDAud.sys portcls.sys wdmaud.sys sysaudio.sys kmixer.sys drmk.sys stream.sys ks.sys) do (
  echo.>> "%OUT%"
  echo --- %%F --- >> "%OUT%"
  dir /s /b "%SystemRoot%\%%F" >> "%OUT%" 2>&1
)
echo.>> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\HDAUDIO" /s | findstr /i "HKEY DeviceDesc HardwareID Service ConfigFlags ActiveService Driver Mfg" >> "%OUT%" 2>&1

call :section "O. USB / HID / PENPOWER / PNP"
for %%S in (PlugPlay HidServ hidusb usbhub usbehci usbccgp USBSTOR) do (
  echo.>> "%OUT%"
  echo --- %%S --- >> "%OUT%"
  sc query %%S >> "%OUT%" 2>&1
  sc qc %%S >> "%OUT%" 2>&1
  reg query "HKLM\SYSTEM\CurrentControlSet\Services\%%S" /s >> "%OUT%" 2>&1
)
for %%F in (hidclass.sys hidparse.sys hidusb.sys hid.dll usbhub.sys usbport.sys usbehci.sys usbccgp.sys) do (
  echo.>> "%OUT%"
  dir /s /b "%SystemRoot%\%%F" >> "%OUT%" 2>&1
)
echo.>> "%OUT%"
echo --- USB filtered enum --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB" /s | findstr /i "HKEY DeviceDesc LocationInformation HardwareID Service ConfigFlags ActiveService FailReasonID ClassGUID" >> "%OUT%" 2>&1
echo.>> "%OUT%"
echo --- HID filtered enum --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\HID" /s | findstr /i "HKEY DeviceDesc HardwareID Service ConfigFlags ClassGUID" >> "%OUT%" 2>&1
echo.>> "%OUT%"
echo --- PenPower known VID/PID --- >> "%OUT%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB\Vid_04b4&Pid_fef3" /s >> "%OUT%" 2>&1

call :section "P. EVENT LOG / SCM FAILURE EVIDENCE"
sc query Eventlog >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\config\SysEvent.Evt" >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\config\AppEvent.Evt" >> "%OUT%" 2>&1
if exist "%SystemRoot%\system32\eventquery.vbs" (
  echo.>> "%OUT%"
  echo --- Service Control Manager errors/warnings (eventquery) --- >> "%OUT%"
  cscript //nologo "%SystemRoot%\system32\eventquery.vbs" /L SYSTEM /FI "Source eq Service Control Manager" /FO LIST /V >> "%OUT%" 2>&1
)
if exist "%SystemRoot%\system32\wevtutil.exe" (
  echo.>> "%OUT%"
  echo --- wevtutil System events --- >> "%OUT%"
  "%SystemRoot%\system32\wevtutil.exe" qe System /c:100 /rd:true /f:text >> "%OUT%" 2>&1
)

call :section "Q. WMI REPOSITORY / MANAGEMENT FILES"
sc query winmgmt >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\winmgmt" /s >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\wbem\Repository" >> "%OUT%" 2>&1
for %%F in (wmiprvse.exe fastprox.dll wbemcore.dll wmisvc.dll wmipcima.dll) do (
  dir /a "%SystemRoot%\system32\wbem\%%F" >> "%OUT%" 2>&1
)

call :section "R. AUTO-DIAGNOSIS SUMMARY"

sc query RpcSs | find "RUNNING" >nul 2>&1
if errorlevel 1 (
  echo [CRITICAL] RpcSs is NOT running. >> "%OUT%"
) else (
  echo [OK] RpcSs is running. >> "%OUT%"
)

findstr /i /c:"FAILED 5" "%TMP%\rpcstart.txt" >nul 2>&1
if not errorlevel 1 (
  echo [CRITICAL] Manual RpcSs start returns ERROR 5 / ACCESS DENIED. >> "%OUT%"
  echo            Because Administrators normally have SERVICE_ALL_ACCESS, this strongly points to a damaged RpcSs service DACL or an access denial on the service startup path. >> "%OUT%"
)

findstr /i /c:"CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA" "%TMP%\rpcss_sddl.txt" >nul 2>&1
if errorlevel 1 (
  echo [HIGH] RpcSs SDDL does NOT contain the normal full-control ACE for Built-in Administrators (BA). >> "%OUT%"
  echo        Service-object ACL corruption is very likely. >> "%OUT%"
) else (
  echo [OK] RpcSs SDDL contains a full-control BA ACE. >> "%OUT%"
)

findstr /i ";;;SY" "%TMP%\rpcss_sddl.txt" >nul 2>&1
if errorlevel 1 (
  echo [HIGH] RpcSs SDDL has no LocalSystem (SY) ACE. Auto-start can fail. >> "%OUT%"
) else (
  echo [INFO] RpcSs SDDL contains a LocalSystem (SY) ACE; inspect its rights above. >> "%OUT%"
)

fc /l "%TMP%\rpcsec001.txt" "%TMP%\rpcsec002.txt" >nul 2>&1
if errorlevel 1 (
  echo [HIGH] RpcSs Security REG_BINARY differs between ControlSet001 and ControlSet002. >> "%OUT%"
  echo        Since your LastKnownGood was previously ControlSet002, restoring ONLY RpcSs\Security from ControlSet002 is a strong repair candidate after backup. >> "%OUT%"
) else (
  echo [INFO] RpcSs Security REG_BINARY is identical in ControlSet001 and ControlSet002. >> "%OUT%"
)

netstat -ano | find ":135" >nul 2>&1
if errorlevel 1 (
  echo [CRITICAL] Nothing is listening on RPC Endpoint Mapper port 135. This is consistent with RpcSs being down. >> "%OUT%"
) else (
  echo [INFO] Something is listening on TCP/UDP 135; review endpoint section. >> "%OUT%"
)

sc query AudioSrv | find "RUNNING" >nul 2>&1
if errorlevel 1 (
  echo [HIGH] Windows Audio is not running. Your Realtek device/driver records exist, so audio loss is currently best explained by the broken RpcSs dependency chain rather than a missing Realtek driver. >> "%OUT%"
)

reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB\Vid_04b4&Pid_fef3" >nul 2>&1
if errorlevel 1 (
  echo [HIGH] PenPower USB VID/PID record is missing. >> "%OUT%"
) else (
  echo [INFO] PenPower USB VID_04B4 PID_FEF3 is still enumerated in registry. Fix RPC/PlugPlay stack before reinstalling the pen driver. >> "%OUT%"
)

wmic os get Caption > "%TMP%\wmitest.txt" 2>&1
findstr /i "Windows" "%TMP%\wmitest.txt" >nul 2>&1
if errorlevel 1 (
  echo [HIGH] WMI is still nonfunctional, consistent with winmgmt failing because RpcSs is down. >> "%OUT%"
) else (
  echo [OK] WMI query succeeded. >> "%OUT%"
)

echo.>> "%OUT%"
echo === REPAIR DECISION TREE (DO NOT RUN BLINDLY) === >> "%OUT%"
echo 1. FIRST repair RpcSs. Do NOT reinstall audio/HID drivers before RPC is healthy. >> "%OUT%"
echo 2. If RpcSs SDDL lacks BA full-control or SY start rights: back up current SDDL/registry and restore the service security descriptor from a known-good XP baseline or setup security template. >> "%OUT%"
echo 3. If ControlSet001 RpcSs\Security differs from ControlSet002 and ControlSet002 is your LastKnownGood: safest targeted repair is to back up RpcSs, then restore ONLY the RpcSs\Security subkey from ControlSet002 and reboot. >> "%OUT%"
echo 4. If SDDL is normal but start still returns Error 5: inspect the file ACLs above for svchost.exe/rpcss.dll/rpcrt4.dll/ole32.dll and the setup-security template; then repair file/registry ACLs, not the binaries first. >> "%OUT%"
echo 5. If ACLs are normal: compare rpcss.dll/rpcrt4.dll/ole32.dll with the backup copies listed above. Use matching XP/update-level copies only. >> "%OUT%"
echo 6. After RpcSs is RUNNING, start/recheck EventSystem, SENS, winmgmt, AudioSrv, CryptSvc, Schedule, Netman and ShellHWDetection. >> "%OUT%"
echo 7. Only after RPC is fixed, re-test: desktop delay, taskbar buttons, Explorer copy/paste, PowerShell, sound, PenPower. Many may recover automatically. >> "%OUT%"
echo 8. If Explorer problems remain after RPC repair, temporarily disable third-party shell extensions shown above (360, SoftMgr, AutoCAD, kwansvc) and reset Taskband as a SECONDARY step. >> "%OUT%"
echo 9. NWCWorkstation is a legacy NetWare provider. It is not the primary failure while RpcSs is broken; remove/disable it only later if no NetWare is used. >> "%OUT%"
echo 10. Avoid broad secedit /configure or random DLL downloads until the targeted RpcSs ACL comparison is reviewed. >> "%OUT%"

echo.>> "%OUT%"
echo END OF FINAL REPORT >> "%OUT%"

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
