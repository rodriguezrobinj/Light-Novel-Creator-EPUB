"""Ilustraciones más grandes y limpias con Real-ESRGAN (realesrgan-ncnn-vulkan, BSD-3): se descarga una vez y
corre en la tarjeta gráfica por Vulkan. Sin interfaz: la ventana llama a upscale_many en el proceso de trabajo."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import pymupdf

import convert

ESR_VERSION = "20220424"
ESR_URL = ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
           f"realesrgan-ncnn-vulkan-{ESR_VERSION}-windows.zip")
ESR_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
ESR_SIZE = "45 MB"
EXE = "realesrgan-ncnn-vulkan.exe"
# animevideov3: 12-16 s por ilustración en una Intel HD 620 y limpio; x4plus-anime tardaba 2 min y deformaba el texto
# que se transparenta del dorso. Solo se descomprime lo que se usa (10 MB de 140)
MODEL = "realesr-animevideov3"
KEEP = (EXE, "vcomp140.dll") + tuple(f"models/{MODEL}-x{s}.{ext}" for s in (2, 3, 4) for ext in ("bin", "param"))
DEFAULT_TARGET = 2000  # sin «Reducir»: lo de una tablet


class UpscaleError(convert.BookError):
    """Real-ESRGAN no pudo y el porqué, en español: no es un fallo de la app."""


def folder(base):
    return Path(base) / f"realesrgan-{ESR_VERSION}"


def installed(base):
    return all((folder(base) / name).is_file() for name in KEEP)


def download(base, progress=None):
    """Descarga Real-ESRGAN, lo compara con el oficial (se ejecuta lo que traiga) y pone en su sitio, de una vez, solo
    lo que se usa. Proceso de trabajo. Devuelve su carpeta."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    part, digest = base / "realesrgan.zip.part", hashlib.sha256()
    try:
        with urllib.request.urlopen(ESR_URL, timeout=60) as reply, open(part, "wb") as f:
            total, got = int(reply.headers.get("Content-Length") or 0), 0
            while chunk := reply.read(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got, total)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise UpscaleError("No se pudo descargar Real-ESRGAN: comprueba la conexión a internet.") from e
    tmp = None
    try:
        if digest.hexdigest() != ESR_SHA256:
            raise UpscaleError("El Real-ESRGAN descargado no coincide con el oficial: no se usa.")
        tmp = Path(tempfile.mkdtemp(prefix="lnepub-realesrgan-", dir=base))
        with zipfile.ZipFile(part) as z:
            for name in KEEP:
                z.extract(name, tmp / "nuevo")
        shutil.rmtree(folder(base), ignore_errors=True)  # uno a medias de antes
        os.replace(tmp / "nuevo", folder(base))
    except (OSError, KeyError, zipfile.BadZipFile) as e:
        raise UpscaleError("No se pudo descomprimir Real-ESRGAN: comprueba que queda sitio en el disco.") from e
    finally:
        part.unlink(missing_ok=True)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return str(folder(base))


def scale_for(width, height, target):
    """Cuánto agrandar para que el lado mayor llegue a target: 0 si ya llega; si no, 2, 3 o 4 (el modelo no da más)."""
    side = max(width, height)
    if side >= target:
        return 0
    return next((s for s in (2, 3) if side * s >= target), 4)


def upscale(base, data, target):
    """La imagen agrandada con Real-ESRGAN y reducida a target de lado mayor (JPEG 92, o PNG con transparencia; la
    gris sigue gris), o None si ya tiene buen tamaño."""
    pix = pymupdf.Pixmap(data)
    scale = scale_for(pix.width, pix.height, target)
    if not scale:
        return None
    gray = pix.n - pix.alpha == 1
    with tempfile.TemporaryDirectory(prefix="lnepub-realesrgan-") as tmp:
        src, dst = Path(tmp) / "entrada.png", Path(tmp) / "salida.png"
        (pymupdf.Pixmap(pymupdf.csRGB, pix) if gray and not pix.alpha else pix).save(src)
        try:
            run = subprocess.run([str(folder(base) / EXE), "-i", str(src), "-o", str(dst), "-n", MODEL,
                                  "-s", str(scale), "-m", str(folder(base) / "models")],
                                 cwd=folder(base), capture_output=True, timeout=600,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            raise UpscaleError("Real-ESRGAN tardó más de 10 minutos con una ilustración: se deja como estaba.") from e
        except OSError as e:
            raise UpscaleError("No se pudo ejecutar Real-ESRGAN: vuelve a descargarlo borrando su carpeta.") from e
        if run.returncode or not dst.is_file():
            said = run.stderr.decode("utf-8", "replace")
            if "vkCreateInstance" in said or "invalid gpu" in said or "vkCreateDevice" in said:
                raise UpscaleError("Real-ESRGAN necesita una tarjeta gráfica con Vulkan, y en este equipo no hay o su "
                                   "controlador no lo admite. Actualizar el controlador de la tarjeta gráfica suele "
                                   "bastar.")
            last = [line for line in said.splitlines() if line.strip() and not line.rstrip().endswith("%")]
            raise UpscaleError("Real-ESRGAN no pudo mejorar la ilustración." + (f"\n{last[-1]}" if last else ""))
        big = pymupdf.Pixmap(str(dst))
    if gray and big.n - big.alpha != 1:
        big = pymupdf.Pixmap(pymupdf.csGRAY, big)
    ratio = target / max(big.width, big.height)
    if ratio < 1:
        big = pymupdf.Pixmap(big, round(big.width * ratio), round(big.height * ratio), None)
    return big.tobytes("png") if big.alpha else convert._jpeg(big, 92)


def upscale_many(base, items, target, progress=None):
    """[(bloque, imagen nueva)] de las ilustraciones [(bloque, imagen)] que no llegaban a target. Proceso de
    trabajo; progress(hechas, total)."""
    out = []
    for n, (k, data) in enumerate(items):
        new = upscale(base, data, target)
        if new:
            out.append((k, new))
        if progress:
            progress(n + 1, len(items))
    return out
