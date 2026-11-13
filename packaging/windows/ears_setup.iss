; ==============================================================================
; EARS -- Inno Setup Installer Script (Builds dist\Ears_Setup.exe)
; ==============================================================================
[Setup]
AppId={{8F9E7D6C-5B4A-4E3F-9A2B-1C0D9E8F7A6B}
AppName=Ears Audio Captioner
AppVersion=0.1.0
AppPublisher=Ears Project
DefaultDirName={localappdata}\Programs\Ears
DefaultGroupName=Ears Audio Captioner
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Ears_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Ears Audio Captioner

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Start Ears automatically when I log into Windows"; GroupDescription: "Startup:"

[Files]
Source: "..\..\dist\Ears_Windows_Portable\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Ears Audio Captioner"; Filename: "wscript.exe"; Parameters: """{app}\Ears.vbs"""; WorkingDir: "{app}"
Name: "{group}\Uninstall Ears"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Ears Audio Captioner"; Filename: "wscript.exe"; Parameters: """{app}\Ears.vbs"""; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "EarsAudioCaptioner"; ValueData: "wscript.exe ""{app}\Ears.vbs"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "wscript.exe"; Parameters: """{app}\Ears.vbs"""; Flags: nowait

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\windows\uninstall_windows.ps1"" -Purge"; Flags: runhidden
