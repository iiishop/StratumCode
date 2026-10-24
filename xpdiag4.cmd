@echo off
setlocal
chcp 936 >nul 2>&1

set "OUT=C:\XP_Diagnostic_Stage4.txt"
set "SEC=C:\XP_Stage4_SecurityExport.inf"

echo Windows XP Stage-4 RPC Permission Diagnostic > "%OUT%"
echo Generated: %DATE% %TIME% >> "%OUT%"
echo Computer: %COMPUTERNAME% User: %USERNAME% >> "%OUT%"

call :section "1. CURRENT USER / LOCAL GROUPS"
whoami >> "%OUT%" 2>&1
whoami /user >> "%OUT%" 2>&1
whoami /groups >> "%OUT%" 2>&1
whoami /priv >> "%OUT%" 2>&1
echo.>> "%OUT%"
net user "%USERNAME%" >> "%OUT%" 2>&1
echo.>> "%OUT%"
net localgroup >> "%OUT%" 2>&1

call :section "2. SERVICE SECURITY DESCRIPTORS"
for %%S in (RpcSs DcomLaunch Eventlog PlugPlay Themes LanmanWorkstation NWCWorkstation AudioSrv winmgmt EventSystem SENS) do (
  echo.>> "%OUT%"
  echo --- %%S --- >> "%OUT%"
  sc sdshow %%S >> "%OUT%" 2>&1
)

call :section "3. RPC SERVICE CONFIG / REGISTRY SNAPSHOT"
sc qc RpcSs >> "%OUT%" 2>&1
sc query RpcSs >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\RpcSs" /s >> "%OUT%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Svchost" /v rpcss >> "%OUT%" 2>&1

call :section "4. FILE ACLS"
for %%F in (
 "%SystemRoot%\system32\svchost.exe"
 "%SystemRoot%\system32\rpcss.dll"
 "%SystemRoot%\system32\rpcrt4.dll"
 "%SystemRoot%\system32\ole32.dll"
 "%SystemRoot%\system32\services.exe"
) do (
  echo.>> "%OUT%"
  echo --- %%~F --- >> "%OUT%"
  cacls "%%~F" >> "%OUT%" 2>&1
)

call :section "5. DIRECTORY ACLS"
cacls "%SystemRoot%\system32" >> "%OUT%" 2>&1
cacls "%SystemRoot%" >> "%OUT%" 2>&1

call :section "6. REGISTRY ACL EXPORT / SECURITY TEMPLATE"
if exist "%SEC%" del /f /q "%SEC%" >nul 2>&1
secedit /export /cfg "%SEC%" /areas USER_RIGHTS SERVICES >> "%OUT%" 2>&1
echo.>> "%OUT%"
if exist "%SEC%" (
  echo --- Exported security template snippets --- >> "%OUT%"
  findstr /i /c:"RpcSs" /c:"SeServiceLogonRight" /c:"SeImpersonatePrivilege" /c:"SeAssignPrimaryTokenPrivilege" /c:"SeIncreaseQuotaPrivilege" "%SEC%" >> "%OUT%" 2>&1
) else (
  echo Security export file was not created. >> "%OUT%"
)

call :section "7. SERVICE CONTROL MANAGER / RPC START RETEST"
echo --- QUERYLOCK --- >> "%OUT%"
sc querylock >> "%OUT%" 2>&1
echo.>> "%OUT%"
echo --- START RpcSs --- >> "%OUT%"
sc start RpcSs >> "%OUT%" 2>&1
echo.>> "%OUT%"
sc query RpcSs >> "%OUT%" 2>&1

call :section "8. NETWORKSERVICE ACCOUNT / PROFILE PATHS"
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\S-1-5-20" /s >> "%OUT%" 2>&1
dir /a "%SystemRoot%\ServiceProfiles" >> "%OUT%" 2>&1
dir /a "%SystemDrive%\Documents and Settings\NetworkService" >> "%OUT%" 2>&1
dir /a "%SystemDrive%\Documents and Settings\NetworkService\Local Settings" >> "%OUT%" 2>&1

call :section "9. SERVICE LOGON / SECURITY-RELATED FILES"
dir /a "%SystemRoot%\system32\secur32.dll" >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\lsasrv.dll" >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\advapi32.dll" >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\scecli.dll" >> "%OUT%" 2>&1
dir /a "%SystemRoot%\system32\scesrv.dll" >> "%OUT%" 2>&1

call :section "10. SERVICES THAT CURRENTLY RUN AS NETWORKSERVICE"
sc query DcomLaunch >> "%OUT%" 2>&1
sc qc DcomLaunch >> "%OUT%" 2>&1
sc query Dhcp >> "%OUT%" 2>&1
sc qc Dhcp >> "%OUT%" 2>&1
sc query W32Time >> "%OUT%" 2>&1
sc qc W32Time >> "%OUT%" 2>&1

call :section "11. FINAL MARKERS"
echo If RpcSs sdshow lacks an ACE for BA with RP or broad service rights, its service ACL is suspect. >> "%OUT%"
echo BA = Built-in Administrators, SY = LocalSystem, AU = Authenticated Users. >> "%OUT%"
echo RP in a service SDDL means SERVICE_START. >> "%OUT%"
echo END OF STAGE-4 REPORT >> "%OUT%"

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
