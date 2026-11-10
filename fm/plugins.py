"""Complementos: archivos .py que agregan comandos y visores.

Se cargan de dos carpetas: «plugins» junto al programa (los que vienen de ejemplo) y
«plugins» dentro de la carpeta de configuración del usuario (los propios). Cada archivo
define una función register(api); ver PluginAPI para lo que puede hacer.

Ejemplo mínimo:

    NOMBRE = "Saludo"
    DESCRIPCION = "Muestra cuántos archivos hay marcados."

    def register(api):
        api.add_command("Contar marcados", lambda: api.show_text(
            "Saludo", f"{len(api.selected_paths())} archivo(s)"))

Nombre y descripción: NAME / DESCRIPTION (o NOMBRE / DESCRIPCION; si están los dos, gana
NAME). Para mostrarlos en varios idiomas se agregan NAMES = {"es": "Saludo", "en": "Greeting"}
y DESCRIPTIONS = {...}: se usa el del idioma activo y, si falta, NAME / NOMBRE.
api.language() devuelve el código del idioma activo ("es", "en", ...) para que el
complemento elija sus propios textos dentro de register().
"""
import importlib.util
import os
import sys
import traceback

from . import i18n
from .i18n import _

APP_PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "plugins")


def user_plugin_dir():
    from .app import config_path
    return os.path.join(os.path.dirname(config_path()), "plugins")


class Plugin:
    def __init__(self, path):
        self.path = path
        self.id = os.path.splitext(os.path.basename(path))[0]
        self.name = self.id
        self.description = ""
        self.commands = []   # [(etiqueta, función, atajo)]
        self.viewers = []    # [(extensiones, función)]
        self.error = ""
        self.enabled = True


class PluginAPI:
    """Lo que un complemento puede usar. Las rutas son siempre del disco."""

    def __init__(self, app, plugin):
        self._app = app
        self._plugin = plugin

    # --------------------------------------------------- registrar
    def add_command(self, label, callback, shortcut=None):
        """Agrega un comando al menú Comandos > Complementos (atajo opcional, p. ej. 'Ctrl+Shift+H')."""
        self._plugin.commands.append((label, callback, shortcut))

    def add_viewer(self, extensions, callback):
        """callback(nombre, datos_bytes) -> texto para mostrar en el visor (F3), o None."""
        exts = {e.lower().lstrip(".") for e in extensions}
        self._plugin.viewers.append((exts, callback))

    # --------------------------------------------------- consultar
    def language(self):
        """Código del idioma activo de la interfaz ("es", "en", ...)."""
        return i18n.language()

    def current_dir(self):
        loc = self._app.panel.loc
        return loc.path if loc.is_disk else None

    def other_dir(self):
        loc = self._app.other.loc
        return loc.path if loc.is_disk else None

    def selected_paths(self):
        """Rutas de lo marcado (o del archivo bajo el cursor) en el panel activo."""
        p = self._app.panel
        if not p.loc.is_disk:
            return []
        return [e.full for e in p.selected_entries()]

    def cursor_path(self):
        p = self._app.panel
        e = p.cursor_entry()
        return e.full if e and not e.is_up and p.loc.is_disk else None

    # --------------------------------------------------- actuar
    def refresh(self):
        self._app.refresh()

    def go_to(self, folder):
        from .vfs import Location
        self._app.panel.navigate(Location.disk(folder))

    def show_text(self, title, text):
        from .dialogs import Viewer
        Viewer(self._app.root, title, text.encode("utf-8"), "txt")

    def message(self, text, title=None):
        from .dialogs import info
        info(self._app.root, title or _("Complemento"), text)

    def ask(self, prompt, initial="", title=None):
        from .dialogs import ask_string
        return ask_string(self._app.root, title or _("Complemento"), prompt, initial)

    def confirm(self, text, title=None):
        from .dialogs import confirm
        return confirm(self._app.root, title or _("Complemento"), text)

    def copy_to_clipboard(self, text):
        self._app.root.clipboard_clear()
        self._app.root.clipboard_append(text)

    def status(self, text):
        self._app.set_status(text)


def _localized(texts, default):
    """Texto de un dict {idioma: texto} para el idioma activo, o default."""
    if isinstance(texts, dict):
        value = texts.get(i18n.language())
        if isinstance(value, str) and value:
            return value
    return default


class PluginManager:
    def __init__(self, app):
        self.app = app
        self.plugins = []

    def dirs(self):
        return [APP_PLUGIN_DIR, user_plugin_dir()]

    def load_all(self):
        self.plugins = []
        disabled = set(self.app.cfg.get("plugins_disabled", []))
        seen = set()
        for folder in self.dirs():
            if not os.path.isdir(folder):
                continue
            for fn in sorted(os.listdir(folder)):
                if not fn.endswith(".py") or fn.startswith("_"):
                    continue
                pl = Plugin(os.path.join(folder, fn))
                if pl.id in seen:      # uno del usuario con el mismo nombre reemplaza al de ejemplo
                    self.plugins = [p for p in self.plugins if p.id != pl.id]
                seen.add(pl.id)
                pl.enabled = pl.id not in disabled
                if pl.enabled:
                    self._load(pl)
                self.plugins.append(pl)

    def _load(self, pl):
        try:
            spec = importlib.util.spec_from_file_location(f"twocommander_plugin_{pl.id}", pl.path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            pl.name = getattr(mod, "NAME", None) or getattr(mod, "NOMBRE", pl.id)
            pl.description = getattr(mod, "DESCRIPTION", None) or getattr(mod, "DESCRIPCION", "")
            if not hasattr(mod, "register"):
                raise RuntimeError(_("no tiene la función register(api)"))
            mod.register(PluginAPI(self.app, pl))
            pl.name = _localized(getattr(mod, "NAMES", None), pl.name)
            pl.description = _localized(getattr(mod, "DESCRIPTIONS", None), pl.description)
        except Exception as ex:
            pl.error = f"{type(ex).__name__}: {ex}"
            pl.commands, pl.viewers = [], []
            print(traceback.format_exc(), file=sys.stderr)

    def set_enabled(self, plugin_id, on):
        disabled = set(self.app.cfg.get("plugins_disabled", []))
        (disabled.discard if on else disabled.add)(plugin_id)
        self.app.cfg["plugins_disabled"] = sorted(disabled)

    def commands(self):
        return [(pl, label, fn, sc) for pl in self.plugins if pl.enabled and not pl.error
                for label, fn, sc in pl.commands]

    def view_text(self, ext, name, data):
        """Texto de un complemento visor para esta extensión, o None."""
        ext = (ext or "").lower()
        for pl in self.plugins:
            if not pl.enabled or pl.error:
                continue
            for exts, fn in pl.viewers:
                if ext in exts or "*" in exts:
                    try:
                        out = fn(name, data)
                    except Exception as ex:
                        out = _("El complemento «{name}» falló:\n{error}").format(
                            name=pl.name, error=ex)
                    if out is not None:
                        return out
        return None
