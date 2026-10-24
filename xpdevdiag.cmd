@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 936 >nul 2>&1
set "OUT=C:\XP_DeviceArrival_Diagnostic.txt"
set "TMP=C:\XPDeviceDiag"
if exist "%TMP%" rd /s /q "%TMP%" >nul 2>&1
md "%TMP%" >nul 2>&1

echo Windows XP Device Arrival Diagnostic > "%OUT%"
echo Generated: %DATE% %TIME% >> "%OUT%"
echo READ-ONLY diagnostic. >> "%OUT%"

call :sec "1. CORE SERVICES"
for %%S in (RpcSs PlugPlay ShellHWDetection MountMgr disk PartMgr ftdisk USBSTOR usbhub usbehci hidusb HidServ Cdrom redbook dmserver) do (
 echo --- %%S --- >> "%OUT%"
 sc query %%S >> "%OUT%" 2>&1
 sc qc %%S >> "%OUT%" 2>&1
)

call :sec "2. AUTOPLAY / AUTORUN POLICY"
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoDriveTypeAutoRun >> "%OUT%" 2>&1
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoDriveAutoRun >> "%OUT%" 2>&1
reg query "HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoDriveTypeAutoRun >> "%OUT%" 2>&1
reg query "HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoDriveAutoRun >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Services\Cdrom" /v AutoRun >> "%OUT%" 2>&1

call :sec "3. STORAGE / CDROM / USB CLASS FILTERS"
for %%G in (
 "{4D36E967-E325-11CE-BFC1-08002BE10318}"
 "{4D36E965-E325-11CE-BFC1-08002BE10318}"
 "{71A27CDD-812A-11D0-BEC7-08002BE2092F}"
 "{36FC9E60-C465-11CF-8056-444553540000}"
 "{745A17A0-74D3-11D0-B6FE-00A0C90F57DA}"
) do (
 echo --- CLASS %%~G --- >> "%OUT%"
 reg query "HKLM\SYSTEM\CurrentControlSet\Control\Class\%%~G" /v UpperFilters >> "%OUT%" 2>&1
 reg query "HKLM\SYSTEM\CurrentControlSet\Control\Class\%%~G" /v LowerFilters >> "%OUT%" 2>&1
)

call :sec "4. DRIVER FILES / ACLS"
for %%F in (mountmgr.sys disk.sys partmgr.sys ftdisk.sys usbstor.sys usbhub.sys usbport.sys usbehci.sys hidclass.sys hidparse.sys hidusb.sys cdrom.sys redbook.sys) do (
 echo --- %%F --- >> "%OUT%"
 dir /a "%SystemRoot%\system32\drivers\%%F" >> "%OUT%" 2>&1
 cacls "%SystemRoot%\system32\drivers\%%F" >> "%OUT%" 2>&1
)
dir /a "%SystemRoot%\system32\shsvcs.dll" >> "%OUT%" 2>&1
cacls "%SystemRoot%\system32\shsvcs.dll" >> "%OUT%" 2>&1

call :sec "5. CURRENT DRIVE / VOLUME STATE"
fsutil fsinfo drives >> "%OUT%" 2>&1
mountvol >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\MountedDevices" >> "%OUT%" 2>&1

call :sec "6. CURRENT DEVICE ENUMERATION"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB" /s | findstr /i "HKEY DeviceDesc LocationInformation HardwareID Service ConfigFlags ActiveService FailReasonID ClassGUID" >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USBSTOR" /s | findstr /i "HKEY DeviceDesc FriendlyName HardwareID Service ConfigFlags ActiveService ClassGUID" >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\STORAGE" /s | findstr /i "HKEY DeviceDesc FriendlyName HardwareID Service ConfigFlags ActiveService ClassGUID" >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\IDE" /s | findstr /i "HKEY CdRom DeviceDesc FriendlyName HardwareID Service ConfigFlags ActiveService" >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\SCSI" /s | findstr /i "HKEY CdRom DeviceDesc FriendlyName HardwareID Service ConfigFlags ActiveService" >> "%OUT%" 2>&1

call :sec "7. DEVICE ARRIVAL A/B TEST"
echo.
echo ===============================================================
echo UNPLUG the USB flash drive and PenPower device.
echo REMOVE the disc from the optical drive.
echo Press any key when done.
echo ===============================================================
pause >nul
call :snap "%TMP%\before.txt"

echo.
echo ===============================================================
echo Plug USB flash drive into a known USB 2.0 port.
echo Plug in PenPower.
echo Insert a readable CD/DVD.
echo Wait 10 seconds, then press any key.
echo ===============================================================
pause >nul
ping 127.0.0.1 -n 4 >nul
call :snap "%TMP%\after.txt"

echo --- BEFORE/AFTER DIFFERENCES --- >> "%OUT%"
fc /l "%TMP%\before.txt" "%TMP%\after.txt" >> "%OUT%" 2>&1
echo --- AFTER SNAPSHOT --- >> "%OUT%"
type "%TMP%\after.txt" >> "%OUT%"

call :sec "8. PENPOWER AFTER INSERTION"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB\Vid_04b4&Pid_fef3" /s >> "%OUT%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\HID\Vid_04b4&Pid_fef3" /s >> "%OUT%" 2>&1

call :sec "9. SETUPAPI HISTORY"
if exist "%SystemRoot%\setupapi.log" (
 findstr /i /c:"VID_04B4" /c:"PID_FEF3" /c:"USBSTOR" /c:"CDROM" "%SystemRoot%\setupapi.log" >> "%OUT%" 2>&1
) else (
 echo setupapi.log not found. >> "%OUT%"
)

call :sec "10. WMI ACCESS STATUS"
tasklist >> "%OUT%" 2>&1
wmic logicaldisk get DeviceID,DriveType,FileSystem,VolumeName /format:list >> "%OUT%" 2>&1
wmic diskdrive get Model,PNPDeviceID,Status /format:list >> "%OUT%" 2>&1
wmic cdrom get Name,Drive,MediaLoaded,PNPDeviceID,Status /format:list >> "%OUT%" 2>&1

call :sec "11. SUMMARY GUIDE"
echo If drive/mount state changes but no popup: AutoPlay policy/handler problem. >> "%OUT%"
echo If USB appears but no USBSTOR/volume: storage driver/class/filter problem. >> "%OUT%"
echo If USB enum does not change at all: controller/port/device/cable path problem. >> "%OUT%"
echo If CD-ROM is enumerated but media state never changes: CD media-change/filter problem. >> "%OUT%"
echo WMI 0x80070005 is separate; registry/mountvol evidence above is authoritative for this test. >> "%OUT%"
echo END OF REPORT >> "%OUT%"

echo.
echo DONE
echo Report: %OUT%
pause
exit /b

:snap
set "S=%~1"
echo SNAPSHOT %DATE% %TIME% > "%S%"
echo ==== DRIVES ==== >> "%S%"
fsutil fsinfo drives >> "%S%" 2>&1
echo ==== MOUNTVOL ==== >> "%S%"
mountvol >> "%S%" 2>&1
echo ==== MOUNTEDDEVICES ==== >> "%S%"
reg query "HKLM\SYSTEM\MountedDevices" >> "%S%" 2>&1
echo ==== USB ==== >> "%S%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB" /s | findstr /i "HKEY DeviceDesc LocationInformation HardwareID Service ConfigFlags ActiveService FailReasonID ClassGUID" >> "%S%" 2>&1
echo ==== USBSTOR ==== >> "%S%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USBSTOR" /s | findstr /i "HKEY DeviceDesc FriendlyName HardwareID Service ConfigFlags ActiveService ClassGUID" >> "%S%" 2>&1
echo ==== STORAGE ==== >> "%S%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\STORAGE" /s | findstr /i "HKEY DeviceDesc FriendlyName HardwareID Service ConfigFlags ActiveService ClassGUID" >> "%S%" 2>&1
echo ==== PENPOWER ==== >> "%S%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\USB\Vid_04b4&Pid_fef3" /s >> "%S%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\HID\Vid_04b4&Pid_fef3" /s >> "%S%" 2>&1
echo ==== CDROM ==== >> "%S%"
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\IDE" /s | findstr /i "HKEY CdRom DeviceDesc FriendlyName HardwareID Service ConfigFlags ActiveService" >> "%S%" 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\SCSI" /s | findstr /i "HKEY CdRom DeviceDesc FriendlyName HardwareID Service ConfigFlags ActiveService" >> "%S%" 2>&1
exit /b

:sec
echo.>> "%OUT%"
echo =============================================================================>> "%OUT%"
echo %~1>> "%OUT%"
echo =============================================================================>> "%OUT%"
exit /b
