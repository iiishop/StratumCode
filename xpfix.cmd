@echo off
setlocal EnableExtensions
chcp 936 >nul 2>&1

set "OUT=C:\XP_Fix_Report.txt"
set "BK=C:\XPRepairBackup"
set "DB=%BK%\xpfix.sdb"
set "LOG1=%BK%\secedit_filestore_regkeys.log"
set "LOG2=%BK%\secedit_services.log"

if not exist "%BK%" md "%BK%"

echo Windows XP RPC / ACL Repair > "%OUT%"
echo Started: %DATE% %TIME% >> "%OUT%"
echo.>> "%OUT%"

echo ================================================================>> "%OUT%"
echo 0. SELECT LOCAL XP SECURITY TEMPLATE>> "%OUT%"
echo ================================================================>> "%OUT%"

set "TPL="
if exist "%SystemRoot%\repair\secsetup.inf" set "TPL=%SystemRoot%\repair\secsetup.inf"
if not defined TPL if exist "%SystemRoot%\security\templates\setup security.inf" set "TPL=%SystemRoot%\security\templates\setup security.inf"

if not defined TPL (
    echo ERROR: No local XP setup security template was found.>> "%OUT%"
    echo Checked:>> "%OUT%"
    echo   %SystemRoot%\repair\secsetup.inf>> "%OUT%"
    echo   %SystemRoot%\security\templates\setup security.inf>> "%OUT%"
    echo Repair aborted without making changes.>> "%OUT%"
    goto END
)

echo Using template: "%TPL%">> "%OUT%"
copy /y "%TPL%" "%BK%\security_template_used.inf" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo ================================================================>> "%OUT%"
echo 1. BACKUP CURRENT STATE>> "%OUT%"
echo ================================================================>> "%OUT%"

reg export "HKLM\SYSTEM\CurrentControlSet\Services\RpcSs" "%BK%\RpcSs.reg" /y >> "%OUT%" 2>&1
reg export "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Svchost" "%BK%\Svchost.reg" /y >> "%OUT%" 2>&1
reg save HKLM\SYSTEM "%BK%\SYSTEM.hiv" /y >> "%OUT%" 2>&1
reg save HKLM\SOFTWARE "%BK%\SOFTWARE.hiv" /y >> "%OUT%" 2>&1

sc sdshow RpcSs > "%BK%\RpcSs_before_sddl.txt" 2>&1
sc qc RpcSs > "%BK%\RpcSs_before_config.txt" 2>&1

cacls "%SystemRoot%" > "%BK%\acl_windows_before.txt" 2>&1
cacls "%SystemRoot%\system32" > "%BK%\acl_system32_before.txt" 2>&1
cacls "%SystemRoot%\system32\svchost.exe" > "%BK%\acl_svchost_before.txt" 2>&1
cacls "%SystemRoot%\system32\rpcss.dll" > "%BK%\acl_rpcss_before.txt" 2>&1

secedit /export /cfg "%BK%\security_before.inf" >> "%OUT%" 2>&1

echo Backups written under %BK%>> "%OUT%"

echo.>> "%OUT%"
echo ================================================================>> "%OUT%"
echo 2. BASELINE BEFORE REPAIR>> "%OUT%"
echo ================================================================>> "%OUT%"
sc query RpcSs >> "%OUT%" 2>&1
sc start RpcSs >> "%OUT%" 2>&1
sc query RpcSs >> "%OUT%" 2>&1
echo.>> "%OUT%"
cacls "%SystemRoot%" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32\svchost.exe" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32\rpcss.dll" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo ================================================================>> "%OUT%"
echo 3. PHASE 1 - RESTORE XP FILE AND REGISTRY SECURITY>> "%OUT%"
echo ================================================================>> "%OUT%"
echo Command: secedit FILESTORE REGKEYS using local XP setup template>> "%OUT%"

if exist "%DB%" del /f /q "%DB%" >nul 2>&1
secedit /configure /db "%DB%" /cfg "%TPL%" /overwrite /areas FILESTORE REGKEYS /log "%LOG1%" /quiet >> "%OUT%" 2>&1

echo Phase 1 complete.>> "%OUT%"
echo.>> "%OUT%"
echo ACLs after Phase 1:>> "%OUT%"
cacls "%SystemRoot%" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32\svchost.exe" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32\rpcss.dll" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo Trying RpcSs after Phase 1...>> "%OUT%"
sc start RpcSs >> "%OUT%" 2>&1
ping 127.0.0.1 -n 3 >nul
sc query RpcSs >> "%OUT%" 2>&1

sc query RpcSs | find "RUNNING" >nul 2>&1
if not errorlevel 1 goto RPC_OK

echo.>> "%OUT%"
echo ================================================================>> "%OUT%"
echo 4. PHASE 2 - RESTORE CORE SERVICE SECURITY>> "%OUT%"
echo ================================================================>> "%OUT%"
echo RpcSs is still not running. Applying SERVICES area only.>> "%OUT%"

secedit /configure /db "%DB%" /cfg "%TPL%" /overwrite /areas SERVICES /log "%LOG2%" /quiet >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo RpcSs SDDL after service-security repair:>> "%OUT%"
sc sdshow RpcSs >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo Trying RpcSs after Phase 2...>> "%OUT%"
sc start RpcSs >> "%OUT%" 2>&1
ping 127.0.0.1 -n 3 >nul
sc query RpcSs >> "%OUT%" 2>&1

sc query RpcSs | find "RUNNING" >nul 2>&1
if not errorlevel 1 goto RPC_OK

echo.>> "%OUT%"
echo ================================================================>> "%OUT%"
echo RESULT: RPC STILL FAILED>> "%OUT%"
echo ================================================================>> "%OUT%"
echo RpcSs still did not reach RUNNING after FILESTORE, REGKEYS and SERVICES repair.>> "%OUT%"
echo No further automatic changes were made.>> "%OUT%"
echo Review these logs:>> "%OUT%"
echo   %LOG1%>> "%OUT%"
echo   %LOG2%>> "%OUT%"
echo   %BK%\RpcSs_before_sddl.txt>> "%OUT%"
goto END

:RPC_OK
echo.>> "%OUT%"
echo ================================================================>> "%OUT%"
echo RESULT: RPC RESTORED - START DEPENDENT SERVICES>> "%OUT%"
echo ================================================================>> "%OUT%"
echo RpcSs is RUNNING.>> "%OUT%"

for %%S in (EventSystem SENS winmgmt AudioSrv CryptSvc Schedule Netman ShellHWDetection ProtectedStorage Spooler SamSs) do (
    echo.>> "%OUT%"
    echo --- %%S --- >> "%OUT%"
    sc start %%S >> "%OUT%" 2>&1
    sc query %%S >> "%OUT%" 2>&1
)

echo.>> "%OUT%"
echo ================================================================>> "%OUT%"
echo 5. FUNCTIONAL VERIFICATION>> "%OUT%"
echo ================================================================>> "%OUT%"

echo --- RPC endpoint 135 --- >> "%OUT%"
netstat -ano | find ":135" >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- tasklist --- >> "%OUT%"
tasklist >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- systeminfo --- >> "%OUT%"
systeminfo >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- WMI OS --- >> "%OUT%"
wmic os get Caption,Version,BuildNumber,LastBootUpTime /format:list >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- WMI Sound --- >> "%OUT%"
wmic sounddev get Name,Status,ConfigManagerErrorCode,PNPDeviceID /format:list >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- PnP errors --- >> "%OUT%"
wmic path Win32_PnPEntity where "ConfigManagerErrorCode<>0" get Name,PNPDeviceID,ConfigManagerErrorCode,Service /format:list >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- Important service snapshot --- >> "%OUT%"
for %%S in (RpcSs EventSystem SENS winmgmt AudioSrv CryptSvc Schedule Netman ShellHWDetection SamSs) do sc query %%S >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo ================================================================>> "%OUT%"
echo 6. WHAT TO TEST AFTER REBOOT>> "%OUT%"
echo ================================================================>> "%OUT%"
echo 1. Reboot Windows XP once.>> "%OUT%"
echo 2. Measure login-to-desktop time again.>> "%OUT%"
echo 3. Check whether open windows appear on the taskbar.>> "%OUT%"
echo 4. Test Explorer file copy/paste between two local folders.>> "%OUT%"
echo 5. Test PowerShell and tasklist.>> "%OUT%"
echo 6. Test sound playback.>> "%OUT%"
echo 7. Test the PenPower tablet.>> "%OUT%"
echo.>> "%OUT%"
echo Do NOT reinstall Realtek or PenPower drivers before this reboot/test.>> "%OUT%"

:END
echo.>> "%OUT%"
echo Finished: %DATE% %TIME%>> "%OUT%"
echo END OF REPAIR REPORT>> "%OUT%"

echo.
echo DONE
echo Report:
echo %OUT%
echo Backup:
echo %BK%
echo.
pause
exit /b
