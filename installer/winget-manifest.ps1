<#
.SYNOPSIS
Genera los manifiestos de WinGet (schema 1.10.0) para publicar Light Novel Creator en microsoft/winget-pkgs.

.EXAMPLE
.\installer\winget-manifest.ps1 -Version 0.5.0 -InstallerPath dist\installer\LightNovelCreator_0.5.0_x64-setup.exe
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [string]$OutputDir = "dist\winget",
    [string]$PackageId = "rodriguezrobinj.LightNovelCreator",
    [string]$Repo = "rodriguezrobinj/Light-Novel-Creator-EPUB"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $InstallerPath)) {
    throw "No se encontró el instalador: $InstallerPath"
}

$ver = $Version.TrimStart("v", "V")
$exe = Get-Item $InstallerPath
$sha = (Get-FileHash $exe.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
$url = "https://github.com/$Repo/releases/download/v$ver/$($exe.Name)"
$date = (Get-Date).ToString("yyyy-MM-dd")
$target = Join-Path $OutputDir $ver
New-Item -ItemType Directory -Force $target | Out-Null

$versionYaml = @"
# yaml-language-server: `$schema=https://aka.ms/winget-manifest.version.1.10.0.schema.json
PackageIdentifier: $PackageId
PackageVersion: $ver
DefaultLocale: es-ES
ManifestType: version
ManifestVersion: 1.10.0
"@

$installerYaml = @"
# yaml-language-server: `$schema=https://aka.ms/winget-manifest.installer.1.10.0.schema.json
PackageIdentifier: $PackageId
PackageVersion: $ver
Platform:
  - Windows.Desktop
MinimumOSVersion: 10.0.0.0
InstallerType: inno
UpgradeBehavior: install
FileExtensions:
  - pdf
  - epub
ReleaseDate: $date
AppsAndFeaturesEntries:
  - DisplayName: Light Novel Creator
    Publisher: Light Novel Creator EPUB
    DisplayVersion: $ver
    ProductCode: '{F3F4BAAF-7ADA-40DD-8A83-0BB31ADFEDEA}_is1'
    InstallerType: inno
Installers:
  - Architecture: x64
    Scope: user
    InstallerUrl: $url
    InstallerSha256: $sha
    InstallerSwitches:
      Custom: /CURRENTUSER
  - Architecture: x64
    Scope: machine
    InstallerUrl: $url
    InstallerSha256: $sha
    InstallerSwitches:
      Custom: /ALLUSERS
ManifestType: installer
ManifestVersion: 1.10.0
"@

$localeEsYaml = @"
# yaml-language-server: `$schema=https://aka.ms/winget-manifest.defaultLocale.1.10.0.schema.json
PackageIdentifier: $PackageId
PackageVersion: $ver
PackageLocale: es-ES
Publisher: Light Novel Creator EPUB
PublisherUrl: https://github.com/$Repo
PublisherSupportUrl: https://github.com/$Repo/issues
Author: rodriguezrobinj
PackageName: Light Novel Creator
PackageUrl: https://github.com/$Repo
License: AGPL-3.0-only
LicenseUrl: https://github.com/$Repo/blob/master/LICENSE
ShortDescription: Convierte PDF de novelas ligeras en libros EPUB y edita EPUB sin pérdida.
Description: >-
  Aplicación de escritorio para Windows diseñada para convertir archivos PDF de novelas ligeras en libros EPUB
  con texto fluido y editar archivos EPUB existentes sin pérdida, con OCR, vista previa en Chromium, corrector
  ortográfico, informe de calidad y traducción integrada.
Tags:
  - epub
  - pdf
  - ebook
  - light-novel
  - novela-ligera
  - converter
ReleaseNotesUrl: https://github.com/$Repo/releases/tag/v$ver
ManifestType: defaultLocale
ManifestVersion: 1.10.0
"@

$localeEnYaml = @"
# yaml-language-server: `$schema=https://aka.ms/winget-manifest.locale.1.10.0.schema.json
PackageIdentifier: $PackageId
PackageVersion: $ver
PackageLocale: en-US
Publisher: Light Novel Creator EPUB
PublisherUrl: https://github.com/$Repo
PublisherSupportUrl: https://github.com/$Repo/issues
PackageName: Light Novel Creator
PackageUrl: https://github.com/$Repo
License: AGPL-3.0-only
LicenseUrl: https://github.com/$Repo/blob/master/LICENSE
ShortDescription: Convert light novel PDFs to reflowable EPUB and edit existing EPUBs losslessly.
Tags:
  - epub
  - pdf
  - ebook
  - light-novel
  - converter
ReleaseNotesUrl: https://github.com/$Repo/releases/tag/v$ver
ManifestType: locale
ManifestVersion: 1.10.0
"@

$utf8 = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText((Join-Path $target "$PackageId.yaml"), $versionYaml, $utf8)
[System.IO.File]::WriteAllText((Join-Path $target "$PackageId.installer.yaml"), $installerYaml, $utf8)
[System.IO.File]::WriteAllText((Join-Path $target "$PackageId.locale.es-ES.yaml"), $localeEsYaml, $utf8)
[System.IO.File]::WriteAllText((Join-Path $target "$PackageId.locale.en-US.yaml"), $localeEnYaml, $utf8)

Write-Output "Manifiestos de WinGet generados en: $target"
Write-Output "SHA-256: $sha"
Write-Output "Para validar localmente: winget validate --manifest `"$target`""
