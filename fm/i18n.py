"""Traducción de la interfaz.

El texto fuente del programa está en español. Cada idioma extra es un archivo
fm/locales/<código>.json que asocia el texto en español con su traducción:

    {"_name": "English", "Copiar": "Copy", "Copiar {n} elemento(s) a:": "Copy {n} item(s) to:"}

Uso en el código:

    from .i18n import _, _c
    _("Copiar")                                    # texto fijo
    _c("menú", "Ver")                              # mismo texto, otra traducción según contexto
    _("Copiar {n} elemento(s) a:").format(n=3)     # con datos: siempre .format() después de _()

_() se llama en tiempo de ejecución (al armar menús o mostrar diálogos), nunca al
importar un módulo, así el idioma elegido ya está activo. Si falta una traducción se
muestra el texto en español.
"""
import ctypes
import json
import locale
import os
import sys

SOURCE_LANG = "es"
SOURCE_NAME = "Español"
LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

_lang = SOURCE_LANG
_table = {}
# separadores de números por idioma: (miles, decimales)
_SEPARATORS = {"es": (".", ","), "en": (",", ".")}
_DATE_FORMATS = {"es": "%d/%m/%Y %H:%M", "en": "%Y-%m-%d %H:%M"}


def _(text):
    """Traduce un texto en español al idioma activo."""
    return _table.get(text, text) if _table else text


def _c(context, text):
    """Como _(), para un texto que se traduce distinto según dónde aparece.

    La clave en el archivo de idioma es "contexto|texto"; sin traducción se muestra texto.
    Ejemplo: _c("menú", "Ver") -> "Show" (menú) mientras _("Ver") -> "View" (acción F3).
    """
    return _table.get(context + "|" + text, text) if _table else text


def available():
    """[(código, nombre)] de los idiomas disponibles, con el español primero."""
    out = [(SOURCE_LANG, SOURCE_NAME)]
    if os.path.isdir(LOCALE_DIR):
        for fn in sorted(os.listdir(LOCALE_DIR)):
            if fn.endswith(".json"):
                code = fn[:-5]
                try:
                    with open(os.path.join(LOCALE_DIR, fn), encoding="utf-8") as f:
                        name = json.load(f).get("_name", code)
                except (OSError, ValueError):
                    continue
                out.append((code, name))
    return out


def system_language():
    """Código de dos letras del idioma de Windows (o del sistema)."""
    code = ""
    if sys.platform == "win32":
        try:
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            code = locale.windows_locale.get(lcid, "")
        except (AttributeError, OSError):
            code = ""
    if not code:
        try:
            code = locale.getlocale()[0] or ""
        except ValueError:
            code = ""
    return code.split("_")[0].lower()


def resolve(pref):
    """'auto' o un código -> un código disponible. Sin coincidencia: inglés si existe."""
    codes = [c for c, _n in available()]
    if pref and pref != "auto" and pref in codes:
        return pref
    sys_code = system_language()
    if sys_code in codes:
        return sys_code
    return "en" if "en" in codes else SOURCE_LANG


def set_language(pref):
    """Activa un idioma ('auto', 'es', 'en', ...). Devuelve el código activado."""
    global _lang, _table
    code = resolve(pref)
    table = {}
    if code != SOURCE_LANG:
        try:
            with open(os.path.join(LOCALE_DIR, code + ".json"), encoding="utf-8") as f:
                table = {k: v for k, v in json.load(f).items()
                         if not k.startswith("_") and isinstance(v, str) and v}
        except (OSError, ValueError):
            code, table = SOURCE_LANG, {}
    _lang, _table = code, table
    return code


def language():
    return _lang


def separators():
    """(miles, decimales) para mostrar números en el idioma activo."""
    return _SEPARATORS.get(_lang, _SEPARATORS["en"])


def date_format():
    """Formato de fecha y hora de las listas para el idioma activo."""
    return _DATE_FORMATS.get(_lang, _DATE_FORMATS["en"])
