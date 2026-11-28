"""Comprobación y descarga de actualizaciones desde GitHub Releases.
Módulo sin interfaz gráfica: la ventana lo usa en hilos y las pruebas lo ejercitan sin red."""
from collections import namedtuple
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# release.yml lo cambia por la etiqueta v* antes de compilar; «-dev» = build local, que no se autoactualiza
APP_VERSION = "0.0.0-dev"
REPO = "rodriguezrobinj/Light-Novel-Creator-EPUB"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
RELEASES_URL = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL = 24 * 3600  # una comprobación automática al día como máximo
# Solo se descargan instaladores y sumas desde dominios oficiales de GitHub por HTTPS
TRUSTED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")
LOG = logging.getLogger("updater")

# Notas que GitHub escribe solo en inglés («What's Changed», «* fix: … by @x in URL»): la app habla español
NOTE_HEADINGS = {"what's changed": "Novedades", "new contributors": "Personas que colaboran por primera vez"}
NOTE_TYPES = re.compile(r"^(feat|fix|docs|merge|perf|refactor|chore|test|ci|build|style)(\([^)]*\))?!?:\s*", re.I)


def spanish_notes(markdown):
    """Las notas de una versión en español: títulos traducidos, cada cambio sin «fix:» ni «by @x in URL» (con el
    enlace al pull request detrás) y «Full Changelog» como «Todos los cambios»."""
    out = []
    for line in markdown.splitlines():
        heading = re.match(r"^(#+)\s*(.+?)\s*$", line)
        if heading and heading[2].lower() in NOTE_HEADINGS:
            out.append(f"{heading[1]} {NOTE_HEADINGS[heading[2].lower()]}")
            continue
        first = re.match(r"^([*-])\s+@(\S+) made their first contribution in (\S+)\s*$", line)
        if first:
            out.append(f"{first[1]} @{first[2]} ([primer cambio]({first[3]}))")
            continue
        item = re.match(r"^([*-])\s+(.*?)(?: by @\S+)?(?: in (https://\S+/pull/(\d+)))?\s*$", line)
        if item and item[2]:
            text = NOTE_TYPES.sub("", item[2])
            text = text[:1].upper() + text[1:]
            out.append(f"{item[1]} {text}" + (f" ([#{item[4]}]({item[3]}))" if item[3] else ""))
            continue
        full = re.match(r"^\*\*Full Changelog\*\*:\s*(\S+)\s*$", line)
        if full:
            out.append(f"[Todos los cambios]({full[1]})")
            continue
        out.append(line)
    return "\n".join(out)


Release = namedtuple(
    "Release",
    "version tag title notes html_url setup_url setup_name portable_url checksums_url prerelease",
)


def parse_version(text):
    """Clave ordenable para versiones tipo v0.5.0 o 0.5.0-beta.1; None si no parece una versión."""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})(?:-([0-9A-Za-z.-]+))?", text.strip())
    if not m:
        return None
    nums = [int(n) for n in m.group(1).split(".")]
    while len(nums) < 4:
        nums.append(0)
    pre = m.group(2)
    if not pre:
        return (tuple(nums), 1, ())  # versión final > pre-release con los mismos números
    parts = tuple((0, int(p)) if p.isdigit() else (1, p.lower()) for p in pre.split("."))
    return (tuple(nums), 0, parts)


def is_newer(candidate, current=APP_VERSION):
    """True si candidate es una versión posterior a current."""
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


def install_mode(frozen=None, executable=None):
    """Modo de ejecución: 'portable' (carpeta datos junto al .exe), 'installed' (.exe normal) o 'source' (Python)."""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else bool(frozen)
    if not is_frozen:
        return "source"
    exe = Path(sys.executable if executable is None else executable)
    return "portable" if (exe.parent / "datos").is_dir() else "installed"


def trusted_url(url):
    """Solo HTTPS hacia github.com o subdominios oficiales de githubusercontent.com."""
    if not isinstance(url, str) or not url:
        return False
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host in TRUSTED_HOSTS or host.endswith(".githubusercontent.com"))


def should_auto_check(settings, now=None):
    """True si toca comprobar actualizaciones al abrir según las preferencias y la última consulta."""
    if not settings.get("check_updates", True) or APP_VERSION.endswith("-dev"):
        return False
    now = time.time() if now is None else now
    last = settings.get("last_update_check", 0)
    try:
        last = float(last)
    except (TypeError, ValueError):
        last = 0.0
    return (now - last) >= CHECK_INTERVAL


def pick_release(items, current=APP_VERSION):
    """Elige la versión más reciente de la lista de releases de GitHub que supere a current, o None."""
    if not isinstance(items, list):
        return None
    best_key, best = None, None
    for item in items:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = str(item.get("tag_name") or "").strip()
        key = parse_version(tag)
        if not key or not is_newer(tag, current):
            continue
        if best_key is not None and key <= best_key:
            continue
        assets = item.get("assets") or []
        setup_url = setup_name = portable_url = checksums_url = ""
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            if not trusted_url(url):
                continue
            low = name.lower()
            if low.endswith("-setup.exe") and not setup_url:
                setup_url, setup_name = url, name
            elif low.endswith("-portable.zip") and not portable_url:
                portable_url = url
            elif low == "sha256sums.txt" and not checksums_url:
                checksums_url = url
        html_url = str(item.get("html_url") or "")
        if not trusted_url(html_url):
            html_url = RELEASES_URL
        notes = str(item.get("body") or "").strip()[:4000]
        version = tag.lstrip("vV")
        title = str(item.get("name") or tag).strip()
        best_key = key
        best = Release(version, tag, title, notes, html_url, setup_url, setup_name, portable_url, checksums_url,
                       bool(item.get("prerelease")))
    return best


def check_latest(current=APP_VERSION, timeout=12, api_url=RELEASES_API):
    """Consulta GitHub Releases y devuelve el Release más nuevo o None si ya está al día."""
    request = urllib.request.Request(
        api_url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"LightNovelCreator/{current}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError) as e:
        LOG.info("No se pudieron consultar actualizaciones: %s", e)
        raise RuntimeError("No se pudo comprobar si hay actualizaciones: revisa la conexión a internet.") from e
    except ValueError as e:
        LOG.warning("Respuesta inesperada de GitHub Releases: %s", e)
        raise RuntimeError("GitHub devolvió una respuesta inesperada al comprobar actualizaciones.") from e
    release = pick_release(data, current)
    LOG.info("Actualizaciones: instalada=%s, nueva=%s", current, release.tag if release else "ninguna")
    return release


def parse_checksums(text):
    """Lee un SHA256SUMS.txt («<sha256>  <archivo>») y devuelve {nombre_minúsculas: sha256}."""
    sums = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            sums[Path(m.group(2)).name.lower()] = m.group(1).lower()
    return sums


def _fetch_checksum(release, filename, timeout=15):
    """Descarga SHA256SUMS.txt del release y devuelve la suma esperada para filename, o '' si no viene."""
    if not release.checksums_url or not trusted_url(release.checksums_url):
        return ""
    request = urllib.request.Request(
        release.checksums_url,
        headers={"User-Agent": f"LightNovelCreator/{APP_VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not trusted_url(response.geturl()):
                raise RuntimeError("La descarga de SHA256SUMS.txt redirigió a un dominio no confiable.")
            text = response.read(65536).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as e:
        raise RuntimeError("No se pudo descargar el archivo de verificación SHA-256.") from e
    return parse_checksums(text).get(Path(filename).name.lower(), "")


def download_setup(release, dest_dir=None, on_progress=None, cancel=None, timeout=60):
    """Descarga el instalador .exe de release, comprueba su SHA-256 si existe y devuelve su Path."""
    if not release.setup_url or not trusted_url(release.setup_url):
        raise RuntimeError("Esta versión no incluye un instalador descargable desde GitHub.")
    filename = Path(release.setup_name or f"LightNovelCreator_{release.version}_x64-setup.exe").name
    if not filename.lower().endswith(".exe"):
        raise RuntimeError("El archivo de actualización no es un instalador válido.")
    expected_sha = _fetch_checksum(release, filename, timeout=min(timeout, 20))
    if not expected_sha:  # release.yml siempre publica las sumas: sin ellas no se ejecuta nada en silencio
        raise RuntimeError("Esta versión no trae la suma SHA-256 del instalador: no se instalará automáticamente.")
    folder = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir()) / "LightNovelCreator-update"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    part = folder / f"{filename}.part"
    digest = hashlib.sha256()
    request = urllib.request.Request(
        release.setup_url,
        headers={"User-Agent": f"LightNovelCreator/{APP_VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not trusted_url(response.geturl()):
                raise RuntimeError("La descarga del instalador redirigió a un dominio no confiable.")
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(part, "wb") as out:
                while True:
                    if cancel and cancel():
                        raise InterruptedError("Descarga cancelada.")
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total)
    except InterruptedError:
        part.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as e:
        part.unlink(missing_ok=True)
        raise RuntimeError("No se pudo descargar el instalador: revisa la conexión a internet.") from e
    actual_sha = digest.hexdigest().lower()
    if actual_sha != expected_sha:
        part.unlink(missing_ok=True)
        LOG.error("SHA-256 no coincide para %s: esperado=%s obtenido=%s", filename, expected_sha, actual_sha)
        raise RuntimeError("El instalador descargado no coincide con la suma SHA-256 oficial: no se ejecutará.")
    os.replace(part, target)
    LOG.info("Instalador descargado en %s (SHA-256 verificado)", target)
    return target


def launch_installer(setup_path):
    """Lanza Inno Setup en modo silencioso para actualizar y reabrir la aplicación al terminar."""
    path = Path(setup_path)
    if not path.is_file():
        raise RuntimeError("No se encontró el instalador descargado.")
    LOG.info("Lanzando instalador: %s", path)
    # /RELAUNCH=1 activa la entrada [Run] de setup.iss que reabre la app; sin /NORESTART… habría dos ventanas
    subprocess.Popen([str(path), "/SILENT", "/CLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/RELAUNCH=1"])
