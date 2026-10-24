@echo off
setlocal
chcp 936 >nul 2>&1

set "OUT=C:\XP_Diagnostic_Stage3.txt"

echo Windows XP Stage-3 RPC Active Test > "%OUT%"
echo Generated: %DATE% %TIME% >> "%OUT%"
echo Computer: %COMPUTERNAME% User: %USERNAME% >> "%OUT%"

call :section "1. SVCHOST GROUPS"
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Svchost" /v rpcss >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Svchost" /v DcomLaunch >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Svchost" /v netsvcs >> "%OUT%" 2>&1

call :section "2. RPC SERVICE CONFIG"
sc query RpcSs >> "%OUT%" 2>&1
sc qc RpcSs >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\RpcSs" /s >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\rpcss.dll" >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\rpcrt4.dll" >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\ole32.dll" >> "%OUT%" 2>&1

call :section "3. RPC START TEST"
echo Running: sc start RpcSs >> "%OUT%"
sc start RpcSs >> "%OUT%" 2>&1
echo ExitCode=%ERRORLEVEL% >> "%OUT%"
ping 127.0.0.1 -n 4 >nul
sc query RpcSs >> "%OUT%" 2>&1

call :section "4. RPC ENDPOINT TEST"
netstat -ano >> "%OUT%" 2>&1

call :section "5. DEPENDENT SERVICE START TESTS"
for %%S in (EventSystem SENS winmgmt AudioSrv CryptSvc Schedule Netman ShellHWDetection) do (
  echo.>> "%OUT%"
  echo --- START %%S --- >> "%OUT%"
  sc start %%S >> "%OUT%" 2>&1
  echo ExitCode=%ERRORLEVEL% >> "%OUT%"
  sc query %%S >> "%OUT%" 2>&1
)

call :section "6. BASIC FUNCTION RETEST"
echo --- TASKLIST --- >> "%OUT%"
tasklist >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- SYSTEMINFO --- >> "%OUT%"
systeminfo >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- WMIC OS --- >> "%OUT%"
wmic os get Caption,Version,BuildNumber,LastBootUpTime /format:list >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- WMIC SOUNDDEV --- >> "%OUT%"
wmic sounddev get Name,Status,ConfigManagerErrorCode,PNPDeviceID /format:list >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo --- WMIC BAD PNP --- >> "%OUT%"
wmic path Win32_PnPEntity where "ConfigManagerErrorCode<>0" get Name,PNPDeviceID,ConfigManagerErrorCode,Service /format:list >> "%OUT%" 2>&1

call :section "7. RPC / DCOM RELATED REGISTRY"
reg query "HKLM\SOFTWARE\Microsoft\Ole" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Rpc" /s >> "%OUT%" 2>&1

call :section "8. SERVICE CONTROL MANAGER SNAPSHOT"
sc query RpcSs >> "%OUT%" 2>&1
sc query DcomLaunch >> "%OUT%" 2>&1
sc query EventSystem >> "%OUT%" 2>&1
sc query SENS >> "%OUT%" 2>&1
sc query winmgmt >> "%OUT%" 2>&1
sc query AudioSrv >> "%OUT%" 2>&1
sc query PlugPlay >> "%OUT%" 2>&1

echo.>> "%OUT%"
echo END OF STAGE-3 REPORT >> "%OUT%"

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
