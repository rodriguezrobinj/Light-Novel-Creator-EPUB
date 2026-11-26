; Instalador de Light Novel Creator EPUB (Inno Setup 6)
; Compilación local:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.5.0 /DAppNumericVersion=0.5.0.0 installer\setup.iss

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "0.0.0.0"
#endif
#ifndef OutputName
  #define OutputName "LightNovelCreator_" + AppVersion + "_x64-setup"
#endif

#define AppName "Light Novel Creator"
#define AppPublisher "Light Novel Creator EPUB"
#define AppURL "https://github.com/rodriguezrobinj/Light-Novel-Creator-EPUB"
#define AppExeName "PDF a EPUB.exe"
#define DistDir "..\dist\Light Novel Creator"

[Setup]
; Identificador fijo para que nuevas versiones actualicen sobre la anterior sin duplicar entradas
AppId={{F3F4BAAF-7ADA-40DD-8A83-0BB31ADFEDEA}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppNumericVersion}
VersionInfoDescription=Instalador de {#AppName}
VersionInfoProductName={#AppName}
; ProductVersion solo admite A.B.C.D: «0.5.0-beta.1» o «0.0.0-dev» van en el texto
VersionInfoProductVersion={#AppNumericVersion}
VersionInfoProductTextVersion={#AppVersion}

; Solo Windows 10 y 11 de 64 bits
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; Instalación por usuario por defecto (sin UAC), con opción de instalar para todos los usuarios (también vía WinGet)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

; Apariencia, licencia e idioma automático
WizardStyle=modern
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
LicenseFile=..\LICENSE
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage

; Salida en dist\installer\ (ignorado en .gitignore) y compresión sólida LZMA2
OutputDir=..\dist\installer
OutputBaseFilename={#OutputName}
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes

; Actualizaciones limpias aunque el usuario tenga la app abierta
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=yes
ChangesAssociations=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
spanish.OpenWithTask=Registrar en el menú «Abrir con…» para archivos PDF y EPUB
english.OpenWithTask=Register in the "Open with…" menu for PDF and EPUB files
spanish.DocumentType=Libro para Light Novel Creator
english.DocumentType=Book for Light Novel Creator

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "openwith"; Description: "{cm:OpenWithTask}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Limpia _internal en actualizaciones para no dejar DLLs antiguas de PySide6, Chromium o PyMuPDF
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Sin carpeta datos\: así la versión instalada guarda ajustes y cachés en %APPDATA%\PDF a EPUB
Source: "{#DistDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DistDir}\*"; DestDir: "{app}"; Excludes: "{#AppExeName},datos\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "Convertir y editar novelas ligeras en EPUB"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon; Comment: "Convertir y editar novelas ligeras en EPUB"

[Registry]
; Aparece en «Abrir con…» sin reemplazar el lector predeterminado de PDF o EPUB del usuario
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\DefaultIcon"; ValueType: string; ValueData: """{app}\{#AppExeName}"",0"; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\shell\open"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExeName}"" ""%1"""; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\SupportedTypes"; ValueType: string; ValueName: ".epub"; ValueData: ""; Tasks: openwith

Root: HKA; Subkey: "Software\Classes\LightNovelCreator.Document"; ValueType: string; ValueData: "{cm:DocumentType}"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\LightNovelCreator.Document\DefaultIcon"; ValueType: string; ValueData: """{app}\{#AppExeName}"",0"; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\LightNovelCreator.Document\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExeName}"" ""%1"""; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "LightNovelCreator.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.epub\OpenWithProgids"; ValueType: string; ValueName: "LightNovelCreator.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: openwith

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
; La actualización desde la app (/SILENT /RELAUNCH=1) la reabre: la app ya se cerró sola y Restart Manager no la reabriría
Filename: "{app}\{#AppExeName}"; Flags: nowait; Check: ShouldRelaunch

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;
