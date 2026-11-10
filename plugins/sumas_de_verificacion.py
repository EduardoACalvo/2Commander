"""Complemento de ejemplo: sumas de verificación de los archivos marcados."""
import hashlib
import os

NOMBRE = "Sumas de verificación"
DESCRIPCION = "Calcula MD5, SHA-1 y SHA-256 de los archivos marcados y las copia al portapapeles."
NAMES = {"es": NOMBRE, "en": "Checksums"}
DESCRIPTIONS = {"es": DESCRIPCION,
                "en": "Computes MD5, SHA-1 and SHA-256 of the selected files and copies them "
                      "to the clipboard."}

TEXTS = {
    "es": {"command": "Sumas de verificación (MD5 / SHA)...",
           "title": "Sumas de verificación",
           "none": "Marque uno o más archivos del disco."},
    "en": {"command": "Checksums (MD5 / SHA)...",
           "title": "Checksums",
           "none": "Select one or more files on disk."},
}


def _hashes(path):
    hs = [hashlib.md5(), hashlib.sha1(), hashlib.sha256()]
    with open(path, "rb") as f:
        while True:
            buf = f.read(1 << 20)
            if not buf:
                break
            for h in hs:
                h.update(buf)
    return [h.hexdigest() for h in hs]


def register(api):
    t = TEXTS.get(api.language(), TEXTS["en"])

    def run():
        paths = [p for p in api.selected_paths() if os.path.isfile(p)]
        if not paths:
            api.message(t["none"])
            return
        lines = []
        for p in paths:
            md5, sha1, sha256 = _hashes(p)
            lines += [os.path.basename(p), f"  MD5     {md5}", f"  SHA-1   {sha1}",
                      f"  SHA-256 {sha256}", ""]
        text = "\n".join(lines)
        api.copy_to_clipboard(text)
        api.show_text(t["title"], text)

    api.add_command(t["command"], run, "Ctrl+Shift+H")
