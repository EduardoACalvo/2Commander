"""Ventana principal: menús, barra de herramientas, dos paneles, línea de comandos y teclas F."""
if __name__ == "__main__" and not __package__:
    # Ejecutado directamente (python fm/app.py): arrancar el programa desde main.py,
    # que carga este archivo como parte del paquete "fm".
    import os as _os
    import runpy as _runpy
    import sys as _sys
    _app_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    _sys.path.insert(0, _app_dir)
    _runpy.run_path(_os.path.join(_app_dir, "main.py"), run_name="__main__")
    raise SystemExit

import ctypes
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import tkinter as tk
from tkinter import ttk

from . import connect, dialogs, i18n, icons, remote, vfs
from .panel import Panel
from .plugins import PluginManager
from .sync import SyncDialog
from .vfs import Location, fmt_size, fmt_human, fmt_date
from .i18n import _, _c

APP_NAME = "2 Commander"
VERSION = "1.0"

DEFAULTS = {
    "show_hidden": False, "recycle": True, "confirm_delete": True, "save_on_exit": True,
    "show_toolbar": True, "show_cmdline": True, "show_fkeys": True, "font_size": 9,
    "editor": "notepad.exe", "hotlist": [], "cmd_history": [], "geometry": "1180x720",
    "left": None, "right": None, "left_tabs": None, "right_tabs": None, "language": "auto",
}


def config_path(folder="2Commander"):
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, folder, "config.json")


# Carpeta usada antes del cambio de nombre; se lee solo si no existe la nueva.
LEGACY_CONFIG_FOLDER = "ComandanteDoble"


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _e=None):
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#ffffe1", relief="solid", bd=1,
                 font=("Segoe UI", 8)).pack()

    def hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class App:
    def __init__(self, root, screenshot=False, lang=None):
        self.root = root
        self.screenshot = screenshot
        self.cfg = dict(DEFAULTS)
        if not screenshot:
            path = config_path()
            if not os.path.exists(path):
                path = config_path(LEGACY_CONFIG_FOLDER)
            try:
                with open(path, encoding="utf-8") as f:
                    self.cfg.update(json.load(f))
            except (OSError, ValueError):
                pass
        # --lang vale solo para esta ejecución; si no, el idioma configurado ("auto" = el de Windows)
        i18n.set_language(lang or self.cfg.get("language", "auto"))
        self.clip = None          # ("copy"|"cut", loc, [Entry])
        self.quick_view = False
        self.active = None
        self.plugins = PluginManager(self)
        self.plugins.load_all()

        root.title(f"{APP_NAME} (x64) {VERSION}")
        self._icons = (icons.app_icon(32), icons.app_icon(16))
        root.iconphoto(True, *self._icons)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(1084, sw - 20), min(609, sh - 80)
        default = f"{w}x{h}+{(sw - w) // 2}+{max((sh - h) // 2 - 30, 0)}"
        root.geometry(default if screenshot or not self.cfg.get("geometry_saved")
                      else self.cfg["geometry"])
        root.minsize(640, 400)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self._style()
        self._menus()
        self._toolbar()
        self._panels()
        self._cmdline()
        self._fkeys()
        self.apply_layout()
        self.bind_plugin_keys()
        self.activate(self.left)
        self.left.tree.focus_set()
        root.after(2000, self._poll)

    # ================================================================== construcción
    def _style(self):
        st = ttk.Style(self.root)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        fs = self.cfg.get("font_size", 9)
        st.configure("Files.Treeview", font=("Segoe UI", fs), rowheight=int(fs * 1.9),
                     background="white", fieldbackground="white", borderwidth=1, indent=0)
        # sin el espacio del triángulo de árbol: el ícono queda pegado al borde izquierdo
        st.layout("Files.Treeview.Item", [
            ("Treeitem.padding", {"sticky": "nswe", "children": [
                ("Treeitem.image", {"side": "left", "sticky": ""}),
                ("Treeitem.text", {"sticky": "nswe"})]})])
        st.configure("Files.Treeview.Item", padding=(1, 0))
        st.configure("Files.Treeview.Heading", font=("Segoe UI", 9), padding=(2, 1))
        # el cursor se ve celeste en el panel activo y casi blanco en el otro
        st.map("Files.Treeview", background=[("selected", "#eef5fc")],
               foreground=[("selected", "black")])
        st.map("Active.Files.Treeview", background=[("selected", "#cce8ff")],
               foreground=[("selected", "black")])

    def _menus(self):
        r = self.root
        m = tk.Menu(r)
        A = self.act

        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label=_("Cambiar atributos..."), command=A(self.change_attrs))
        fm.add_command(label=_("Comprimir..."), accelerator="Alt+F5", command=A(self.pack))
        fm.add_command(label=_("Descomprimir..."), accelerator="Alt+F9", command=A(self.unpack))
        fm.add_separator()
        fm.add_command(label=_("Comparar por contenido..."), command=A(self.compare_content))
        fm.add_command(label=_("Nuevo archivo de texto..."), accelerator="Shift+F4",
                       command=A(self.new_file))
        fm.add_command(label=_("Propiedades..."), accelerator="Alt+Enter", command=A(self.properties))
        fm.add_separator()
        fm.add_command(label=_("Salir"), accelerator="Alt+F4", command=self.quit)
        m.add_cascade(label=_("Archivo"), menu=fm)

        mm = tk.Menu(m, tearoff=0)
        mm.add_command(label=_("Seleccionar grupo..."), accelerator="Num +",
                       command=A(lambda: self.mark_group(True)))
        mm.add_command(label=_("Deseleccionar grupo..."), accelerator="Num -",
                       command=A(lambda: self.mark_group(False)))
        mm.add_command(label=_("Seleccionar todo"), accelerator="Ctrl+A",
                       command=A(lambda: self.panel.mark_where(lambda e: True, True)))
        mm.add_command(label=_("Deseleccionar todo"), accelerator="Ctrl+Num -",
                       command=A(lambda: self.panel.mark_where(lambda e: True, False)))
        mm.add_command(label=_("Invertir selección"), accelerator="Num *",
                       command=A(lambda: self.panel.invert_marks()))
        mm.add_separator()
        mm.add_command(label=_("Copiar nombres al portapapeles"), command=A(lambda: self.copy_names(False)))
        mm.add_command(label=_("Copiar nombres con ruta completa"),
                       command=A(lambda: self.copy_names(True)))
        mm.add_separator()
        mm.add_command(label=_("Comparar directorios"), accelerator="Shift+F2",
                       command=A(self.compare_dirs))
        m.add_cascade(label=_("Seleccionar"), menu=mm)

        cm = tk.Menu(m, tearoff=0)
        cm.add_command(label=_("Buscar archivos..."), accelerator="Alt+F7", command=A(self.search))
        cm.add_command(label=_("Cambio de nombre múltiple..."), accelerator="Ctrl+M",
                       command=A(self.multi_rename))
        cm.add_command(label=_("Directorios favoritos"), accelerator="Ctrl+D", command=A(self.hotlist))
        cm.add_command(label=_("Historial de carpetas"), accelerator="Alt+↓",
                       command=A(self.history_menu))
        cm.add_command(label=_("Sincronizar carpetas..."), command=A(self.sync_dirs))
        cm.add_separator()
        cm.add_command(label=_("Destino = Origen"), command=A(self.target_eq_source))
        cm.add_command(label=_("Intercambiar paneles"), accelerator="Ctrl+U", command=A(self.swap))
        cm.add_separator()
        cm.add_command(label=_("Abrir consola (cmd)"), command=A(self.console))
        cm.add_command(label=_("Abrir en el Explorador"), command=A(self.explorer))
        cm.add_separator()
        self.plugin_menu = tk.Menu(cm, tearoff=0, postcommand=self._fill_plugin_menu)
        cm.add_cascade(label=_("Complementos"), menu=self.plugin_menu)
        m.add_cascade(label=_("Comandos"), menu=cm)

        nm = tk.Menu(m, tearoff=0)
        nm.add_command(label=_("Conectar a servidor FTP / SFTP..."), accelerator="Ctrl+F",
                       command=A(self.ftp_connect))
        nm.add_command(label=_("Conexión rápida por dirección..."), accelerator="Ctrl+N",
                       command=A(self.ftp_quick))
        nm.add_command(label=_("Desconectar del servidor"), accelerator="Ctrl+Shift+F",
                       command=A(self.ftp_disconnect))
        nm.add_separator()
        nm.add_command(label=_("Conectar unidad de red..."), command=A(lambda: self.net_dialog(True)))
        nm.add_command(label=_("Desconectar unidad de red..."),
                       command=A(lambda: self.net_dialog(False)))
        nm.add_separator()
        nm.add_command(label=_("Actualizar lista de unidades"), command=A(self.refresh_drives))
        m.add_cascade(label=_("Red"), menu=nm)

        sm = tk.Menu(m, tearoff=0)
        sm.add_command(label=_("Breve"), accelerator="Ctrl+F1",
                       command=A(lambda: self.panel.set_view_mode("brief")))
        sm.add_command(label=_("Completo"), accelerator="Ctrl+F2",
                       command=A(lambda: self.panel.set_view_mode("full")))
        sm.add_command(label=_("Vista rápida"), accelerator="Ctrl+Q", command=A(self.toggle_quick))
        sm.add_separator()
        sm.add_command(label=_("Todos los archivos"), command=A(lambda: self.set_filter("")))
        sm.add_command(label=_("Programas"), command=A(lambda: self.set_filter("*.exe;*.com;*.bat;*.cmd")))
        sm.add_command(label=_("Filtro personalizado..."), command=A(self.custom_filter))
        sm.add_separator()
        for lab, key, acc in ((_("Por nombre"), "name", "Ctrl+F3"), (_("Por extensión"), "ext", "Ctrl+F4"),
                              (_("Por fecha/hora"), "date", "Ctrl+F5"), (_("Por tamaño"), "size", "Ctrl+F6"),
                              (_("Sin ordenar"), "none", "Ctrl+F7")):
            sm.add_command(label=lab, accelerator=acc, command=A(lambda k=key: self.panel.set_sort(k)))
        sm.add_command(label=_("Invertir orden"),
                       command=A(lambda: self.panel.set_sort(self.panel.sort_key, not self.panel.sort_rev)))
        sm.add_separator()
        sm.add_command(label=_("Nueva pestaña"), accelerator="Ctrl+T", command=A(lambda: self.panel.new_tab()))
        sm.add_command(label=_("Cerrar pestaña"), accelerator="Ctrl+W",
                       command=A(lambda: self.panel.close_tab()))
        sm.add_command(label=_("Bloquear / desbloquear pestaña"),
                       command=A(lambda: self.panel.toggle_lock()))
        sm.add_separator()
        self.hidden_var = tk.BooleanVar(value=self.cfg["show_hidden"])
        sm.add_checkbutton(label=_("Mostrar archivos ocultos/sistema"), accelerator="Ctrl+H",
                           variable=self.hidden_var, command=A(self.toggle_hidden_menu))
        sm.add_command(label=_("Actualizar"), accelerator="Ctrl+R", command=A(self.refresh))
        m.add_cascade(label=_c("menú", "Ver"), menu=sm)

        om = tk.Menu(m, tearoff=0)
        om.add_command(label=_("Opciones..."), command=A(self.options))
        om.add_command(label=_("Complementos..."), command=A(self.plugin_dialog))
        om.add_command(label=_("Guardar posición"), command=A(self.save_config))
        m.add_cascade(label=_("Configuración"), menu=om)

        self.start_menu = tk.Menu(m, tearoff=0, postcommand=self._fill_start_menu)
        m.add_cascade(label=_("Inicio"), menu=self.start_menu)

        hm = tk.Menu(m, tearoff=0, name="help")
        hm.add_command(label=_("Atajos de teclado"), accelerator="F1", command=self.help_keys)
        hm.add_separator()
        hm.add_command(label=_("Acerca de {app}...").format(app=APP_NAME), command=self.about)
        m.add_cascade(label=_("Ayuda"), menu=hm)
        r.configure(menu=m)
        r.after(200, self._help_menu_right)

    def _help_menu_right(self):
        """En Windows, deja "Ayuda" pegado al borde derecho de la barra de menús."""
        if sys.platform != "win32":
            return
        try:
            from ctypes import wintypes

            class MENUITEMINFOW(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("fMask", wintypes.UINT),
                            ("fType", wintypes.UINT), ("fState", wintypes.UINT),
                            ("wID", wintypes.UINT), ("hSubMenu", ctypes.c_void_p),
                            ("hbmpChecked", ctypes.c_void_p), ("hbmpUnchecked", ctypes.c_void_p),
                            ("dwItemData", ctypes.c_size_t), ("dwTypeData", ctypes.c_void_p),
                            ("cch", wintypes.UINT), ("hbmpItem", ctypes.c_void_p)]
            u = ctypes.WinDLL("user32")
            u.GetMenu.restype = ctypes.c_void_p
            u.GetMenu.argtypes = [ctypes.c_void_p]
            u.GetMenuItemCount.argtypes = [ctypes.c_void_p]
            u.GetMenuItemInfoW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.BOOL,
                                           ctypes.POINTER(MENUITEMINFOW)]
            u.SetMenuItemInfoW.argtypes = u.GetMenuItemInfoW.argtypes
            u.DrawMenuBar.argtypes = [ctypes.c_void_p]
            hwnd = int(self.root.wm_frame(), 16)
            hm = u.GetMenu(hwnd)
            n = u.GetMenuItemCount(hm) if hm else 0
            if n <= 0:
                return
            info = MENUITEMINFOW()
            info.cbSize = ctypes.sizeof(info)
            info.fMask = 0x100  # MIIM_FTYPE
            if u.GetMenuItemInfoW(hm, n - 1, True, ctypes.byref(info)):
                info.fType |= 0x4000  # MFT_RIGHTJUSTIFY
                u.SetMenuItemInfoW(hm, n - 1, True, ctypes.byref(info))
                u.DrawMenuBar(hwnd)
        except (OSError, AttributeError, ValueError):
            pass

    @staticmethod
    def _start_default():
        return [[_("Bloc de notas"), "notepad.exe"], [_("Calculadora"), "calc.exe"],
                [_("Símbolo del sistema"), "cmd.exe"], ["Paint", "mspaint.exe"]]

    def _fill_start_menu(self):
        sm = self.start_menu
        sm.delete(0, "end")
        items = self.cfg.setdefault("start_menu", self._start_default())
        for label, cmd in items:
            sm.add_command(label=label, command=self.act(
                lambda cmd=cmd: self.launch(cmd, shell=cmd.lower().startswith("cmd"))))
        sm.add_separator()
        sm.add_command(label=_("Agregar programa al menú Inicio..."), command=self.act(self._add_start))
        if items:
            rm = tk.Menu(sm, tearoff=0)
            for i, (label, _cmd) in enumerate(items):
                rm.add_command(label=label, command=lambda i=i: items.pop(i))
            sm.add_cascade(label=_("Quitar del menú Inicio"), menu=rm)

    def _add_start(self):
        cmd = dialogs.ask_string(self.root, _("Menú Inicio"), _("Programa o comando:"), "notepad.exe")
        if not cmd:
            return
        label = dialogs.ask_string(self.root, _("Menú Inicio"), _("Nombre en el menú:"),
                                   os.path.splitext(os.path.basename(cmd.strip('"')))[0])
        if label:
            self.cfg.setdefault("start_menu", []).append([label, cmd])

    def _tool_button(self, parent, name, tip, cmd, bg):
        b = tk.Button(parent, image=icons.get("tb_" + name), relief="flat", bd=1, bg=bg,
                      activebackground="#cce8ff", takefocus=False, command=self.act(cmd),
                      width=24, height=24, highlightthickness=0)
        b._bg = bg
        b.bind("<Enter>", lambda e: e.widget.configure(bg="#e5f3ff"), add="+")
        b.bind("<Leave>", lambda e: e.widget.configure(bg=e.widget._bg), add="+")
        Tooltip(b, tip)
        return b

    def _toolbar(self):
        bg = "#ffffff"
        self.toolbar = tk.Frame(self.root, bd=0, bg=bg)
        self.view_buttons = {}
        spec = [
            ("refresh", _("Actualizar (Ctrl+R)"), self.refresh), None,
            ("brief", _("Vista breve (Ctrl+F1)"), lambda: self.set_view("brief")),
            ("full", _("Vista completa (Ctrl+F2)"), lambda: self.set_view("full")),
            ("thumbs", _("Vista rápida (Ctrl+Q)"), self.toggle_quick),
            ("tree", _("Historial de carpetas (Alt+↓)"), self.history_menu), None,
            ("invert", _("Invertir selección (Num *)"), lambda: self.panel.invert_marks()),
            ("hotlist", _("Directorios favoritos (Ctrl+D)"), self.hotlist), None,
            ("back", _("Atrás (Alt+←)"), lambda: self.panel.history_move(-1)),
            ("fwd", _("Adelante (Alt+→)"), lambda: self.panel.history_move(1)), None,
            ("pack", _("Comprimir (Alt+F5)"), self.pack),
            ("unpack", _("Descomprimir (Alt+F9)"), self.unpack), None,
            ("ftp", _("Conectar a servidor FTP / SFTP (Ctrl+F)"), self.ftp_connect),
            ("ftpurl", _("Conexión rápida por dirección (Ctrl+N)"), self.ftp_quick), None,
            ("search", _("Buscar archivos (Alt+F7)"), self.search),
            ("rename", _("Cambio de nombre múltiple (Ctrl+M)"), self.multi_rename),
            ("compare", _("Comparar directorios (Shift+F2)"), self.compare_dirs),
            ("sync", _("Sincronizar carpetas"), self.sync_dirs), None,
            ("notepad", _("Bloc de notas"), lambda: self.launch("notepad.exe")),
            ("console", _("Consola (cmd)"), self.console),
        ]
        for item in spec:
            if item is None:
                tk.Frame(self.toolbar, width=1, bg="#d0d0d0").pack(side="left", fill="y",
                                                                  padx=4, pady=4)
                continue
            name, tip, cmd = item
            b = self._tool_button(self.toolbar, name, tip, cmd, bg)
            b.pack(side="left", padx=1, pady=2)
            if name in ("brief", "full"):
                self.view_buttons[name] = b
        self.toolbar_line = tk.Frame(self.root, height=1, bg="#dadada")

    def set_view(self, mode):
        self.panel.set_view_mode(mode)
        self._update_view_buttons()

    def _update_view_buttons(self):
        mode = self.panel.view_mode if self.active else "full"
        for name, b in getattr(self, "view_buttons", {}).items():
            b._bg = "#cce8ff" if name == mode else "#ffffff"
            b.configure(bg=b._bg, relief="solid" if name == mode else "flat")

    def _middle_bar(self):
        """Columna de botones entre los dos paneles."""
        bar = tk.Frame(self.pw, bd=0)
        tk.Frame(bar, height=86, width=1).pack()
        # mismo margen a cada lado y separación pareja; un hueco extra separa los
        # botones de archivo (ver, buscar, copiar, mover) de los de crear
        for name, tip, cmd, gap in (("view", _("Ver (F3)"), self.view, 4),
                                    ("search", _("Buscar archivos (Alt+F7)"), self.search, 7),
                                    ("copy", _("Copiar (F5)"), self.copy, 7),
                                    ("move", _("Mover / cambiar nombre (F6)"), self.move, 7),
                                    ("pack", _("Comprimir (Alt+F5)"), self.pack, 33),
                                    ("newfolder", _("Nueva carpeta (F7)"), self.mkdir, 7)):
            self._tool_button(bar, name, tip, cmd, "#f0f0f0").pack(padx=4, pady=(gap, 0))
        return bar

    def _panels(self):
        # Los dos paneles siempre miden lo mismo (columnas "uniform") y no se pueden
        # redimensionar a mano; la columna del medio ocupa solo lo que miden sus botones.
        self.pw = tk.Frame(self.root, bd=0)
        self.pw.rowconfigure(0, weight=1)
        self.pw.columnconfigure(0, weight=1, uniform="panels")
        self.pw.columnconfigure(1, weight=0)
        self.pw.columnconfigure(2, weight=1, uniform="panels")
        home = os.path.expanduser("~")
        left_start = self.cfg.get("left") or os.path.join(home, "Downloads")
        right_start = self.cfg.get("right") or os.path.join(home, "Desktop")
        if not os.path.isdir(left_start):
            left_start = vfs.drive_of(home)
        if not os.path.isdir(right_start):
            right_start = home
        self.left = Panel(self.pw, self, "left", left_start)
        self.right = Panel(self.pw, self, "right", right_start)
        for p, key in ((self.left, "left_tabs"), (self.right, "right_tabs")):
            for extra in (self.cfg.get(key) or [])[1:]:
                if os.path.isdir(extra):
                    p.tabs.append(p._new_tab_state(Location.disk(extra)))
            p._render_tabs()
        self.middle = self._middle_bar()
        self.left.grid(row=0, column=0, sticky="nsew")
        self.middle.grid(row=0, column=1, sticky="ns")
        self.right.grid(row=0, column=2, sticky="nsew")

    def _cmdline(self):
        self.cmd_frame = tk.Frame(self.root)
        self.prompt = tk.Label(self.cmd_frame, font=("Segoe UI", 9), anchor="e")
        self.prompt.pack(side="left")
        self.cmd_var = tk.StringVar()
        self.cmd = ttk.Combobox(self.cmd_frame, textvariable=self.cmd_var, font=("Segoe UI", 9),
                                values=self.cfg.get("cmd_history", []))
        self.cmd.pack(side="left", fill="x", expand=True, padx=(2, 2))
        self.cmd.bind("<Return>", lambda e: self.run_cmdline())
        self.cmd.bind("<Escape>", lambda e: (self.cmd_var.set(""), self.panel.tree.focus_set()))
        self.cmd.bind("<Tab>", lambda e: (self.panel.tree.focus_set(), "break")[-1])
        self.msg = tk.Label(self.cmd_frame, font=("Segoe UI", 8), fg="#505050", anchor="e")
        self.msg.pack(side="right")

    def _fkeys(self):
        self.fk_frame = tk.Frame(self.root)
        tk.Frame(self.fk_frame, height=1, bg="#dadada").grid(row=0, column=0, columnspan=14,
                                                             sticky="ew")
        spec = [(_("F3 Ver"), self.view), (_("F4 Editar"), self.edit), (_("F5 Copiar"), self.copy),
                (_("F6 RenMov"), self.move), (_("F7 Nueva carpeta"), self.mkdir),
                (_("F8 Eliminar"), self.delete), (_("Alt+F4 Salir"), self.quit)]
        for i, (t, cmd) in enumerate(spec):
            self.fk_frame.columnconfigure(2 * i, weight=1, uniform="fk")
            b = tk.Button(self.fk_frame, text=t, font=("Segoe UI", 9), relief="flat", bd=0,
                          bg="#f0f0f0", activebackground="#cce8ff", takefocus=False,
                          highlightthickness=0, pady=2, command=self.act(cmd))
            b._bg = "#f0f0f0"
            b.bind("<Enter>", lambda e: e.widget.configure(bg="#e5f3ff"), add="+")
            b.bind("<Leave>", lambda e: e.widget.configure(bg=e.widget._bg), add="+")
            b.grid(row=1, column=2 * i, sticky="ew")
            if i < len(spec) - 1:
                tk.Frame(self.fk_frame, width=1, bg="#c8c8c8").grid(row=1, column=2 * i + 1,
                                                                    sticky="ns", pady=3)

    def apply_layout(self):
        for w in (self.toolbar, self.toolbar_line, self.pw, self.cmd_frame, self.fk_frame):
            w.pack_forget()
        if self.cfg["show_toolbar"]:
            self.toolbar.pack(fill="x")
            self.toolbar_line.pack(fill="x")
        if self.cfg["show_fkeys"]:
            self.fk_frame.pack(fill="x", side="bottom")
        if self.cfg["show_cmdline"]:
            self.cmd_frame.pack(fill="x", side="bottom", pady=(2, 2))
        self.pw.pack(fill="both", expand=True, padx=1, pady=(1, 0))

    # ================================================================== utilidades
    def act(self, fn):
        """Ejecuta una acción y devuelve el foco al panel activo."""
        def run(*_):
            try:
                fn()
            except Exception as ex:  # nunca cerrar la ventana por un error
                self.error(str(ex))
            if self.active is not None and self.root.focus_get() is None:
                self.panel.tree.focus_set()
        return run

    @property
    def panel(self):
        return self.active

    @property
    def other(self):
        return self.right if self.active is self.left else self.left

    def activate(self, panel):
        if self.active is panel:
            return
        self.active = panel
        panel.set_active(True)
        (self.right if panel is self.left else self.left).set_active(False)
        self._update_prompt()
        self._update_view_buttons()
        if self.quick_view:
            self.cursor_moved(panel)

    def switch_panel(self):
        o = self.other
        if self.quick_view:
            return
        self.activate(o)
        o.tree.focus_set()

    def _update_prompt(self):
        if self.active:
            loc = self.active.loc
            path = loc.local_dir()
            if len(path) > 1 and path[1] == ":":
                path = path[0].lower() + path[1:]
            self.prompt.configure(text=path + ">")

    def panel_changed(self, panel):
        if panel is self.active:
            self._update_prompt()

    def cursor_moved(self, panel):
        if self.quick_view and panel is self.active:
            self._show_quick()

    def error(self, msg):
        if self.screenshot:
            print(msg, file=sys.stderr)
            return
        dialogs.info(self.root, _("Error"), msg)

    def set_status(self, text):
        self.msg.configure(text=text)
        self.root.after(5000, lambda: self.msg.configure(text=""))

    def _poll(self):
        for p in (self.left, self.right):
            p.poll_changes()
        self.root.after(2000, self._poll)

    def refresh(self):
        self.left.reload()
        self.right.reload()

    def refresh_drives(self):
        self.left.refresh_drives()
        self.right.refresh_drives()

    # ================================================================== teclado
    def bind_panel_keys(self, panel, t):
        A = self.act
        keys = {
            "<Tab>": self.switch_panel, "<Shift-Tab>": self.switch_panel,
            "<Return>": self.enter, "<KP_Enter>": self.enter,
            "<Control-Return>": self.name_to_cmd,
            "<BackSpace>": self.backspace,
            "<Insert>": lambda: panel.toggle_mark(move=True),
            "<space>": self.space,
            "<F1>": self.help_keys, "<F2>": self.refresh, "<Control-r>": self.refresh,
            "<F3>": self.view, "<F4>": self.edit, "<Shift-F4>": self.new_file,
            "<F5>": self.copy, "<Shift-F5>": self.copy_same, "<F6>": self.move,
            "<Shift-F6>": lambda: panel.start_rename(),
            "<F7>": self.mkdir, "<F8>": self.delete, "<Delete>": self.delete,
            "<Shift-F8>": lambda: self.delete(permanent=True),
            "<Shift-Delete>": lambda: self.delete(permanent=True),
            "<Alt-F5>": self.pack, "<Alt-F9>": self.unpack, "<Alt-F7>": self.search,
            "<Shift-F2>": self.compare_dirs,
            "<Control-m>": self.multi_rename, "<Control-u>": self.swap,
            "<Control-t>": lambda: panel.new_tab(), "<Control-w>": lambda: panel.close_tab(),
            "<Control-Tab>": lambda: panel.next_tab(1),
            "<Control-Shift-Tab>": lambda: panel.next_tab(-1),
            "<Alt-F1>": lambda: self.drive_menu(self.left),
            "<Alt-F2>": lambda: self.drive_menu(self.right),
            "<Control-F1>": lambda: panel.set_view_mode("brief"),
            "<Control-F2>": lambda: panel.set_view_mode("full"),
            "<Control-F3>": lambda: panel.set_sort("name"),
            "<Control-F4>": lambda: panel.set_sort("ext"),
            "<Control-F5>": lambda: panel.set_sort("date"),
            "<Control-F6>": lambda: panel.set_sort("size"),
            "<Control-F7>": lambda: panel.set_sort("none"),
            "<Control-q>": self.toggle_quick,
            "<Alt-Left>": lambda: panel.history_move(-1),
            "<Alt-Right>": lambda: panel.history_move(1),
            "<Alt-Down>": self.history_menu,
            "<Control-d>": self.hotlist, "<Control-h>": self.toggle_hidden,
            "<Control-backslash>": lambda: panel.go_root(),
            "<Control-Prior>": lambda: panel.go_up(),
            "<Control-Left>": lambda: self.open_in(self.left),
            "<Control-Right>": lambda: self.open_in(self.right),
            "<KP_Add>": lambda: self.mark_group(True),
            "<KP_Subtract>": lambda: self.mark_group(False),
            "<KP_Multiply>": lambda: panel.invert_marks(),
            "<Control-a>": lambda: panel.mark_where(lambda e: True, True),
            "<Control-KP_Add>": lambda: panel.mark_where(lambda e: True, True),
            "<Control-KP_Subtract>": lambda: panel.mark_where(lambda e: True, False),
            "<Control-c>": lambda: self.clip_set("copy"),
            "<Control-x>": lambda: self.clip_set("cut"),
            "<Control-v>": self.clip_paste,
            "<Control-p>": self.path_to_cmd,
            "<Alt-Return>": self.properties,
            "<Escape>": lambda: self.cmd_var.set(""),
            "<Control-f>": self.ftp_connect, "<Control-n>": self.ftp_quick,
            "<Control-F>": self.ftp_disconnect,
            "<Home>": lambda: self._jump(0), "<End>": lambda: self._jump(-1),
        }
        for k, fn in keys.items():
            t.bind(k, lambda e, fn=fn: (A(fn)(), "break")[-1])
        t.bind("<Key>", self._typed)

    def _jump(self, idx):
        kids = self.panel.tree.get_children()
        if kids:
            iid = kids[idx]
            self.panel.tree.selection_set(iid)
            self.panel.tree.focus(iid)
            self.panel.tree.see(iid)

    def _typed(self, ev):
        """Las letras escritas sobre la lista van a la línea de comandos."""
        if ev.state & (0x4 | 0x20000):  # Ctrl o Alt
            return None
        if ev.char and ev.char.isprintable() and len(ev.char) == 1:
            self.cmd_var.set(self.cmd_var.get() + ev.char)
            self.cmd.icursor("end")
            return "break"
        return None

    def enter(self):
        if self.cmd_var.get().strip():
            self.run_cmdline()
        else:
            self.panel.open_entry(self.panel.cursor_entry())

    def backspace(self):
        s = self.cmd_var.get()
        if s:
            self.cmd_var.set(s[:-1])
        else:
            self.panel.go_up()

    def space(self):
        p = self.panel
        e = p.cursor_entry()
        if e and e.is_dir and not e.is_up and p.loc.is_disk:
            total, files, dirs = vfs.dir_size(e.full)
            e.size = total
            p.tree.set(p.cursor_iid(), "size", fmt_human(total))
        p.toggle_mark()

    def name_to_cmd(self):
        e = self.panel.cursor_entry()
        if e and not e.is_up:
            n = f'"{e.name}"' if " " in e.name else e.name
            s = self.cmd_var.get()
            self.cmd_var.set((s + " " if s and not s.endswith(" ") else s) + n + " ")

    def path_to_cmd(self):
        p = self.panel.loc.display()
        self.cmd_var.set(self.cmd_var.get() + (f'"{p}"' if " " in p else p))

    # ================================================================== línea de comandos
    def run_cmdline(self):
        cmd = self.cmd_var.get().strip()
        if not cmd:
            return
        hist = [c for c in self.cfg.get("cmd_history", []) if c != cmd]
        self.cfg["cmd_history"] = ([cmd] + hist)[:30]
        self.cmd["values"] = self.cfg["cmd_history"]
        self.cmd_var.set("")
        p = self.panel
        cwd = p.loc.path if not p.loc.archive else os.path.dirname(p.loc.archive)
        low = cmd.lower()
        if len(cmd) == 2 and cmd[1] == ":":
            for d in vfs.list_drives():
                if d[0].lower() == low[0]:
                    p.change_drive(d)
            p.tree.focus_set()
            return
        if low == "cd" or low.startswith(("cd ", "cd\\", "cd..")):
            arg = cmd[2:].strip().strip('"') or "~"
            target = os.path.expanduser(arg) if arg == "~" else os.path.normpath(os.path.join(cwd, arg))
            if os.path.isdir(target):
                p.navigate(Location.disk(target))
            else:
                self.error(_("La carpeta no existe:\n{path}").format(path=target))
            p.tree.focus_set()
            return
        if low in ("cls", "exit"):
            p.tree.focus_set()
            return
        self.launch(cmd, cwd=cwd, shell=True)
        p.tree.focus_set()

    def launch(self, cmd, cwd=None, shell=False):
        cwd = cwd or (self.panel.loc.path if not self.panel.loc.archive else None)
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if shell else 0
        try:
            subprocess.Popen(cmd, cwd=cwd, shell=shell, creationflags=flags)
        except OSError as ex:
            self.error(_("No se pudo ejecutar:\n{cmd}\n\n{error}").format(cmd=cmd, error=ex))

    def shell_open(self, path, cwd=None):
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path], cwd=cwd)
        except OSError as ex:
            self.error(_("No se pudo abrir:\n{path}\n\n{error}").format(path=path, error=ex))

    def console(self):
        loc = self.panel.loc
        cwd = loc.path if not loc.archive else os.path.dirname(loc.archive)
        self.launch("cmd.exe", cwd=cwd, shell=True)

    def explorer(self):
        loc = self.panel.loc
        self.launch(["explorer.exe", loc.path if not loc.archive else os.path.dirname(loc.archive)])

    # ================================================================== operaciones
    def _need_disk(self, panel=None, what=None):
        p = panel or self.panel
        what = what or _("Esta operación")
        if p.loc.archive:
            dialogs.info(self.root, APP_NAME,
                         _("{what} no está disponible dentro de un archivo ZIP.").format(what=what))
            return False
        if p.loc.remote:
            dialogs.info(self.root, APP_NAME,
                         _("{what} no está disponible en un servidor FTP/SFTP.").format(what=what))
            return False
        return True

    def _run_safely(self, fn, what):
        """Ejecuta una operación sobre ZIP o servidor mostrando el error si falla."""
        try:
            self.root.configure(cursor="watch")
            self.root.update_idletasks()
            return fn()
        except Exception as ex:
            self.error(f"{what}:\n\n{ex}")
            return None
        finally:
            self.root.configure(cursor="")

    def view_file(self, panel=None, e=None):
        p = panel or self.panel
        e = e or p.cursor_entry()
        if not e or e.is_up:
            return
        if e.is_dir:
            if not p.loc.is_disk:
                return
            total, files, dirs = vfs.dir_size(e.full)
            dialogs.info(self.root, _("Tamaño de la carpeta"),
                         _("{path}\n\n{size} bytes\n{files} archivo(s), {dirs} carpeta(s)").format(
                             path=e.full, size=fmt_size(total), files=files, dirs=dirs))
            return
        data = self._run_safely(lambda: vfs.read_bytes(p.loc, e, dialogs.Viewer.MAX),
                                _("No se pudo leer el archivo"))
        if data is None:
            return
        if p.loc.is_disk:
            title = e.full
        elif p.loc.archive:
            title = os.path.join(p.loc.archive, e.full)
        else:
            title = p.loc.display().rstrip("/") + "/" + e.name
        text = self.plugins.view_text(e.ext, e.name, data)
        if text is not None:
            dialogs.Viewer(self.root, title, text.encode("utf-8"), "txt")
        else:
            dialogs.Viewer(self.root, title, data, e.ext)

    def view(self):
        self.view_file()

    def edit(self):
        p = self.panel
        e = p.cursor_entry()
        if not e or e.is_up or e.is_dir:
            return
        if not p.loc.is_disk:
            self._edit_virtual(p, e)
            return
        self.launch([self.cfg.get("editor", "notepad.exe"), e.full])

    def _edit_virtual(self, p, e):
        """F4 dentro de un ZIP o en un servidor: se edita una copia y después se guarda de vuelta."""
        tmpdir = tempfile.mkdtemp(prefix="2commander_")
        path = os.path.join(tmpdir, e.name)

        def fetch():
            with open(path, "wb") as fo:
                fo.write(vfs.read_bytes(p.loc, e))
            if e.mtime:
                os.utime(path, (e.mtime, e.mtime))
            return True
        if not self._run_safely(fetch, _("No se pudo preparar el archivo para editar")):
            return
        before = os.stat(path).st_mtime_ns
        loc = p.loc
        started = time.time()
        try:
            proc = subprocess.Popen([self.cfg.get("editor", "notepad.exe"), path])
        except OSError as ex:
            self.error(_("No se pudo abrir el editor:\n{error}").format(error=ex))
            return

        def save_back():
            try:
                st = os.stat(path)
            except OSError:
                return
            if st.st_mtime_ns == before:
                return
            if loc.archive:
                question = _("«{name}» cambió.\n¿Guardar los cambios en el archivo ZIP?")
            else:
                question = _("«{name}» cambió.\n¿Guardar los cambios en el servidor?")
            if not dialogs.confirm(self.root, _("Editar"), question.format(name=e.name)):
                return
            tmp_entry = vfs.Entry(e.name, False, st.st_size, st.st_mtime, full=path)
            dst = ("zip", loc) if loc.archive else ("remote", loc)
            dialogs.FileOp(self, "transfer", [tmp_entry], src_loc=Location.disk(tmpdir),
                           extra={"dst": dst, "overwrite": "all"})

        def ask_when_done():
            # el editor ya estaba abierto en otra ventana y devolvió el control enseguida
            dialogs.info(self.root, _("Editar"),
                         _("Edite «{name}» y guárdelo.\nCuando termine, pulse Aceptar.").format(
                             name=e.name))
            save_back()

        def wait():
            proc.wait()
            quick = time.time() - started < 2
            self.root.after(0, ask_when_done if quick else save_back)
        threading.Thread(target=wait, daemon=True).start()

    def new_file(self):
        if not self._need_disk(what=_("Crear archivos")):
            return
        name = dialogs.ask_string(self.root, _("Editar"), _("Nombre del archivo nuevo:"), _("nuevo.txt"),
                                  select_stem=True)
        if not name:
            return
        path = os.path.join(self.panel.loc.path, name)
        if not os.path.exists(path):
            open(path, "w").close()
        self.panel.reload(focus_name=name)
        self.launch([self.cfg.get("editor", "notepad.exe"), path])

    def _ask_target(self, move, items):
        """Pregunta el destino. Devuelve (("disk"|"zip"|"remote", dónde), nuevo_nombre) o None."""
        if move:
            verb = _("Mover")
            prompt = _('Mover "{name}" a:') if len(items) == 1 else _("Mover {n} elemento(s) a:")
        else:
            verb = _("Copiar")
            prompt = _('Copiar "{name}" a:') if len(items) == 1 else _("Copiar {n} elemento(s) a:")
        prompt = prompt.format(name=items[0].name, n=len(items))
        other = self.other.loc
        if other.remote:
            initial = other.display().rstrip("/") + "/*.*"
        else:
            initial = os.path.join(other.display(), "*.*")
        s = dialogs.ask_string(self.root, verb, prompt, initial)
        if not s:
            return None
        s = s.strip().strip('"')
        base_name = s.replace("\\", "/").rsplit("/", 1)[-1]
        wild = "*" in base_name or "?" in base_name
        if other.remote and s.startswith(remote.url_of(other.remote)):
            rpath = s[len(remote.url_of(other.remote)):]
            if wild:
                rpath = rpath.rsplit("/", 1)[0]
            rpath = "/" + rpath.strip("/") if rpath.strip("/") else "/"
            return ("remote", Location.at_remote(other.remote, rpath)), None
        if other.archive and s.lower().startswith(other.archive.lower()):
            inner = s[len(other.archive):].replace("\\", "/").strip("/")
            if wild:
                inner = inner.rsplit("/", 1)[0] if "/" in inner else ""
            inner = (inner + "/") if inner else ""
            return ("zip", Location(other.path, other.archive, inner)), None
        base = os.path.dirname(s) if wild else s
        base = os.path.normpath(os.path.join(self.panel.loc.local_dir(), base))
        if os.path.isdir(base) or wild:
            return ("disk", base), None
        if len(items) == 1:
            return ("disk", os.path.dirname(base)), os.path.basename(base)
        os.makedirs(base, exist_ok=True)
        return ("disk", base), None

    def copy(self, move=False):
        p = self.panel
        items = p.selected_entries()
        if not items:
            return
        target = self._ask_target(move, items)
        if not target:
            return
        (kind, where), rename = target
        if kind == "disk" and p.loc.is_disk:
            os.makedirs(where, exist_ok=True)
            dialogs.FileOp(self, "move" if move else "copy", items, dest=where, rename_to=rename,
                           src_loc=p.loc)
            return
        dialogs.FileOp(self, "transfer", items, rename_to=rename, src_loc=p.loc,
                       extra={"dst": (kind, where), "move": move})

    def copy_same(self):
        p = self.panel
        e = p.cursor_entry()
        if not e or e.is_up:
            return
        new = dialogs.ask_string(self.root, _("Copiar"), _('Copiar "{name}" como:').format(name=e.name),
                                 _("Copia de {name}").format(name=e.name), select_stem=True)
        if not new or new == e.name:
            return
        if p.loc.is_disk:
            dialogs.FileOp(self, "copy", [e], dest=p.loc.path, rename_to=new, src_loc=p.loc)
        else:
            dst = ("zip", p.loc) if p.loc.archive else ("remote", p.loc)
            dialogs.FileOp(self, "transfer", [e], rename_to=new, src_loc=p.loc,
                           extra={"dst": dst})

    def move(self):
        self.copy(move=True)

    def rename_entry(self, panel, e, new):
        loc = panel.loc
        if "/" in new or "\\" in new:
            self.error(_("El nombre no puede contener barras."))
            return
        if any(x.name == new for x in panel.entries if x is not e) and not loc.is_disk:
            self.error(_("Ya existe:\n{path}").format(path=new))
            return
        if loc.archive:
            new_full = loc.inner + new + ("/" if e.is_dir else "")
            dialogs.FileOp(self, "ziprename", [e], src_loc=loc,
                           extra={"old": e.full, "new": new_full})
            return
        if loc.remote:
            fs = remote.get(loc.remote)
            dst = posixpath.join(loc.inner or "/", new)
            if self._run_safely(lambda: fs.rename(e.full, dst) or True,
                                _("No se pudo cambiar el nombre")):
                panel.reload(focus_name=new)
            return
        try:
            dst = os.path.join(os.path.dirname(e.full), new)
            if os.path.exists(dst) and os.path.normcase(dst) != os.path.normcase(e.full):
                self.error(_("Ya existe:\n{path}").format(path=dst))
                return
            os.rename(e.full, dst)
        except OSError as ex:
            self.error(str(ex))
            return
        panel.reload(focus_name=new)

    def mkdir(self):
        p = self.panel
        e = p.cursor_entry()
        init = e.name if e and e.is_dir and not e.is_up else ""
        name = dialogs.ask_string(self.root, _("Nueva carpeta"), _("Nueva carpeta (directorio):"), init)
        if not name or not name.strip():
            return
        name = name.strip()
        first = name.replace("\\", "/").split("/")[0]
        loc = p.loc
        if loc.archive:
            arc = loc.inner + name.replace("\\", "/").strip("/") + "/"

            def add_dir():
                with zipfile.ZipFile(loc.archive) as zf:
                    names = {n.replace("\\", "/") for n in zf.namelist()}
                if arc in names or any(n.startswith(arc) for n in names):
                    raise RuntimeError(_("Ya existe dentro del ZIP:\n{path}").format(path=arc))
                with zipfile.ZipFile(loc.archive, "a") as zf:
                    info = zipfile.ZipInfo(arc, time.localtime()[:6])
                    info.external_attr = (0o40775 << 16) | 0x10
                    zf.writestr(info, b"")
                return True
            if self._run_safely(add_dir, _("No se pudo crear la carpeta en el ZIP")):
                p.reload(focus_name=first)
            return
        if loc.remote:
            fs = remote.get(loc.remote)
            target = posixpath.join(loc.inner or "/", name.replace("\\", "/"))
            if self._run_safely(lambda: fs.makedirs(target) or True,
                                _("No se pudo crear la carpeta en el servidor")):
                p.reload(focus_name=first)
            return
        path = os.path.join(loc.path, name)
        try:
            os.makedirs(path, exist_ok=False)
        except OSError as ex:
            self.error(_("No se pudo crear la carpeta:\n{path}\n\n{error}").format(path=path, error=ex))
            return
        p.reload(focus_name=first)

    def delete(self, permanent=False):
        p = self.panel
        items = p.selected_entries()
        if not items:
            return
        one = len(items) == 1
        fmt = {"name": items[0].name, "n": len(items)}
        if not p.loc.is_disk:
            if p.loc.archive:
                question = (_('¿Desea eliminar "{name}" del archivo ZIP?\nNo se puede deshacer.') if one
                            else _("¿Desea eliminar {n} elemento(s) del archivo ZIP?\nNo se puede deshacer."))
            else:
                question = (_('¿Desea eliminar "{name}" del servidor?\nNo se puede deshacer.') if one
                            else _("¿Desea eliminar {n} elemento(s) del servidor?\nNo se puede deshacer."))
            if dialogs.confirm(self.root, _("Eliminar"), question.format(**fmt)):
                dialogs.FileOp(self, "vdelete", items, src_loc=p.loc)
            return
        recycle = self.cfg["recycle"] and not permanent
        if recycle:
            question = (_('¿Desea eliminar "{name}" a la papelera de reciclaje?') if one
                        else _("¿Desea eliminar {n} elemento(s) a la papelera de reciclaje?"))
        else:
            question = (_('¿Desea eliminar "{name}" definitivamente?') if one
                        else _("¿Desea eliminar {n} elemento(s) definitivamente?"))
        if self.cfg["confirm_delete"] or not recycle:
            if not dialogs.confirm(self.root, _("Eliminar"), question.format(**fmt)):
                return
        dialogs.FileOp(self, "delete", items, src_loc=p.loc, extra={"recycle": recycle})

    def pack(self):
        p = self.panel
        items = p.selected_entries()
        if not items or not self._need_disk(what=_("Comprimir")):
            return
        base = items[0].stem if len(items) == 1 else os.path.basename(p.loc.path.rstrip("\\")) or _("archivo")
        other = self.other.loc.path if not self.other.loc.archive else os.path.dirname(self.other.loc.archive)
        s = dialogs.ask_string(self.root, _("Comprimir"),
                               _("Comprimir {n} elemento(s) en el archivo:").format(n=len(items)),
                               os.path.join(other, base + ".zip"), select_stem=False)
        if not s:
            return
        if not s.lower().endswith(".zip"):
            s += ".zip"
        dialogs.FileOp(self, "pack", items, dest=s, src_loc=p.loc)

    def unpack(self):
        p = self.panel
        e = p.cursor_entry()
        items = [x for x in p.selected_entries() if not x.is_dir and vfs.is_zip(x.full)] \
            if not p.loc.archive else []
        if p.loc.archive:  # dentro de un ZIP: extraer lo seleccionado
            return self.copy()
        if not items:
            dialogs.info(self.root, _("Descomprimir"), _("Seleccione uno o más archivos .zip."))
            return
        other = self.other.loc.path if not self.other.loc.archive else os.path.dirname(self.other.loc.archive)
        s = dialogs.ask_string(self.root, _("Descomprimir"),
                               _("Descomprimir {n} archivo(s) en:").format(n=len(items)), other)
        if s:
            os.makedirs(s, exist_ok=True)
            dialogs.FileOp(self, "unpack", items, dest=s, src_loc=p.loc)

    def op_finished(self, op):
        for pnl in (self.left, self.right):
            if op.src_loc is not None and pnl.loc.key() == op.src_loc.key() and not op.cancelled:
                pnl.marked.clear()
            pnl.reload()
        self.set_status(_("Operación terminada.") if not op.cancelled else _("Operación cancelada."))

    def change_attrs(self):
        items = self.panel.selected_entries()
        if items and self._need_disk(what=_("Cambiar atributos")):
            dialogs.change_attributes(self, items)
            self.panel.reload()

    def compare_content(self):
        a = self.panel.cursor_entry()
        b = self.other.cursor_entry()
        marked = [e for e in self.panel.selected_entries() if not e.is_dir]
        if len(marked) == 2:
            a, b = marked
        if not a or not b or a.is_dir or b.is_dir or not self.panel.loc.is_disk or \
                not self.other.loc.is_disk:
            dialogs.info(self.root, _("Comparar por contenido"),
                         _("Coloque el cursor sobre un archivo en cada panel (o marque dos archivos)."))
            return
        dialogs.compare_files(self.root, a.full, b.full)

    def properties(self):
        e = self.panel.cursor_entry()
        if not e or not self.panel.loc.is_disk:
            return
        path = self.panel.loc.path if e.is_up else e.full
        if sys.platform == "win32":
            class SEI(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong),
                            ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
                            ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                            ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
                            ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                            ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                            ("dwHotKey", ctypes.c_ulong), ("hIcon", ctypes.c_void_p),
                            ("hProcess", ctypes.c_void_p)]
            sei = SEI()
            sei.cbSize = ctypes.sizeof(SEI)
            sei.fMask = 0x0000000C  # SEE_MASK_INVOKEIDLIST
            sei.lpVerb = "properties"
            sei.lpFile = path
            sei.nShow = 1
            ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        else:
            st = os.stat(path)
            dialogs.info(self.root, _("Propiedades"), f"{path}\n{fmt_size(st.st_size)} bytes\n{fmt_date(st.st_mtime)}")

    def net_dialog(self, connect):
        if sys.platform != "win32":
            return
        hwnd = int(self.root.wm_frame(), 16)
        if connect:
            ctypes.windll.mpr.WNetConnectionDialog(hwnd, 1)
        else:
            ctypes.windll.mpr.WNetDisconnectDialog(hwnd, 1)
        self.refresh_drives()

    # ------------------------------------------------------------ portapapeles interno
    def clip_set(self, mode):
        items = self.panel.selected_entries()
        if items and self.panel.loc.is_disk:
            self.clip = (mode, self.panel.loc, items)
            msg = _("{n} elemento(s) copiados.") if mode == "copy" else _("{n} elemento(s) cortados.")
            self.set_status(msg.format(n=len(items)))

    def clip_paste(self):
        if not self.clip or not self._need_disk(what=_("Pegar")):
            return
        mode, loc, items = self.clip
        items = [e for e in items if os.path.exists(e.full)]
        if items:
            dialogs.FileOp(self, "copy" if mode == "copy" else "move", items,
                           dest=self.panel.loc.path, src_loc=loc)
        if mode == "cut":
            self.clip = None

    # ------------------------------------------------------------ marcar
    def mark_group(self, on):
        if on:
            title, prompt = _("Seleccionar"), _("Seleccionar los archivos que coincidan con:")
        else:
            title, prompt = _("Deseleccionar"), _("Deseleccionar los archivos que coincidan con:")
        s = dialogs.ask_string(self.root, title, prompt,
                               self.cfg.get("last_mask", "*.*"))
        if s:
            self.cfg["last_mask"] = s
            self.panel.mark_pattern("*" if s.strip() == "*.*" else s, on, dirs=s.endswith("\\"))

    def copy_names(self, full):
        items = self.panel.selected_entries()
        if not items:
            return
        names = [(e.full if full and self.panel.loc.is_disk else e.name) for e in items]
        self.root.clipboard_clear()
        self.root.clipboard_append("\r\n".join(names))
        self.set_status(_("{n} nombre(s) copiados al portapapeles.").format(n=len(names)))

    def compare_dirs(self):
        l, r = self.left, self.right
        ld = {e.name.lower(): e for e in l.entries if not e.is_dir}
        rd = {e.name.lower(): e for e in r.entries if not e.is_dir}
        l.marked.clear()
        r.marked.clear()
        for k, e in ld.items():
            o = rd.get(k)
            if o is None or e.mtime > o.mtime + 2:
                l.marked.add(e.name)
        for k, e in rd.items():
            o = ld.get(k)
            if o is None or e.mtime > o.mtime + 2:
                r.marked.add(e.name)
        for p in (l, r):
            p.populate(p.cursor_entry().name if p.cursor_entry() else None)
        n = len(l.marked) + len(r.marked)
        self.set_status(_("Las carpetas tienen los mismos archivos.") if n == 0 else
                        _("{n} archivo(s) distintos o más nuevos marcados.").format(n=n))

    # ------------------------------------------------------------ vistas
    def set_filter(self, flt):
        self.panel.filter = flt
        self.panel.populate(self.panel.cursor_entry().name if self.panel.cursor_entry() else None)
        self.panel._update_header()

    def custom_filter(self):
        s = dialogs.ask_string(self.root, _("Filtro"), _("Mostrar solo los archivos que coincidan con:"),
                               self.panel.filter or "*.txt")
        if s is not None:
            self.set_filter("" if s.strip() in ("", "*", "*.*") else s.strip())

    def toggle_hidden(self):
        self.hidden_var.set(not self.hidden_var.get())
        self.toggle_hidden_menu()

    def toggle_hidden_menu(self):
        self.cfg["show_hidden"] = self.hidden_var.get()
        self.refresh()

    def toggle_quick(self):
        self.quick_view = not self.quick_view
        if self.quick_view:
            self._show_quick()
        else:
            self.other.hide_quick()
            self.left.hide_quick()
            self.right.hide_quick()

    def _show_quick(self):
        p = self.panel
        e = p.cursor_entry()
        o = self.other
        if not e:
            return
        if e.is_up:
            o.show_quick("..", "")
            return
        if e.is_dir:
            if not p.loc.is_disk:
                o.show_quick(e.name, _("Carpeta dentro del ZIP") if p.loc.archive else
                             _("Carpeta en el servidor"))
                return
            try:
                names = sorted(os.listdir(e.full))
            except OSError as ex:
                names = [str(ex)]
            o.show_quick(e.name, _("Carpeta: {path}\n{n} elemento(s)").format(path=e.full, n=len(names))
                         + "\n\n" + "\n".join(names[:500]))
            return
        try:
            data = vfs.read_bytes(p.loc, e, 256 * 1024)
        except Exception as ex:
            o.show_quick(e.name, str(ex))
            return
        if e.ext.lower() in dialogs.IMG:
            try:
                from io import BytesIO
                from PIL import Image, ImageTk
                im = Image.open(BytesIO(vfs.read_bytes(p.loc, e)))
                im.thumbnail((max(o.winfo_width() - 30, 100), max(o.winfo_height() - 90, 100)))
                o.show_quick(e.name, image=ImageTk.PhotoImage(im))
                return
            except Exception:
                pass
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", "replace")
        if "\0" in text[:2048]:
            text = "".join(c if c.isprintable() or c in "\r\n\t" else "." for c in text)
        o.show_quick(e.name, text)

    # ------------------------------------------------------------ navegación entre paneles
    def swap(self):
        a, b = self.left, self.right
        a.tabs, b.tabs = b.tabs, a.tabs
        a.tab_index, b.tab_index = b.tab_index, a.tab_index
        a.navigate(a.loc, add_history=False)
        b.navigate(b.loc, add_history=False)
        self.activate(b if self.active is a else a)
        self.panel.tree.focus_set()

    def target_eq_source(self):
        self.other.navigate(self.panel.loc)

    def open_in(self, target):
        p = self.panel
        e = p.cursor_entry()
        if e and e.is_dir and not e.is_up and p.loc.is_disk and target is not p:
            target.navigate(Location.disk(e.full))
        elif target is not p:
            target.navigate(p.loc)

    def ask_path(self, panel):
        s = dialogs.ask_string(self.root, _("Ir a"), _("Carpeta:"), panel.loc.display())
        if s and os.path.isdir(s):
            panel.navigate(Location.disk(s))

    def goto_file(self, path):
        folder, name = os.path.split(path)
        if self.panel.navigate(Location.disk(folder), focus_name=name):
            self.root.lift()
            self.panel.tree.focus_set()

    def drive_menu(self, panel):
        m = tk.Menu(self.root, tearoff=0)
        for d in vfs.list_drives():
            lab = vfs.volume_label(d)
            m.add_command(label=f"{d[0].lower()}:  {lab}", image=icons.get("drive"), compound="left",
                          command=lambda d=d: panel.change_drive(d))
        x = panel.drive_combo.winfo_rootx()
        y = panel.drive_combo.winfo_rooty() + panel.drive_combo.winfo_height()
        m.tk_popup(x, y)

    def history_menu(self, panel=None):
        p = panel or self.panel
        m = tk.Menu(self.root, tearoff=0)
        for i, loc in reversed(list(enumerate(p.tab["history"]))):
            m.add_radiobutton(label=loc.display(), value=i, variable=tk.IntVar(value=p.tab["hidx"]),
                              command=lambda i=i: p.history_move(i - p.tab["hidx"]))
        m.tk_popup(p.path_label.winfo_rootx(), p.path_label.winfo_rooty() + p.path_label.winfo_height())

    def hotlist(self, panel=None):
        p = panel or self.panel
        m = tk.Menu(self.root, tearoff=0)
        hl = self.cfg.setdefault("hotlist", [])
        for path in hl:
            m.add_command(label=path, image=icons.get("folder"), compound="left",
                          command=lambda path=path: p.navigate(Location.disk(path)))
        if hl:
            m.add_separator()
        cur = p.loc.path
        if not p.loc.is_disk:
            pass
        elif cur in hl:
            m.add_command(label=_("Quitar el directorio actual"), command=lambda: hl.remove(cur))
        else:
            m.add_command(label=_("Agregar el directorio actual"), command=lambda: hl.append(cur))
        m.tk_popup(p.path_label.winfo_rootx(), p.path_label.winfo_rooty() + p.path_label.winfo_height())

    def search(self):
        loc = self.panel.loc
        dialogs.SearchDialog(self, loc.local_dir())

    def sync_dirs(self):
        for p in (self.left, self.right):
            if not self._need_disk(p, _("Sincronizar")):
                return
        SyncDialog(self, self.left.loc.path, self.right.loc.path)

    def multi_rename(self):
        items = self.panel.selected_entries()
        if items and self._need_disk(what=_("Cambiar nombres")):
            dialogs.MultiRename(self, self.panel, items)

    def context_menu(self, panel, x, y):
        A = self.act
        e = panel.cursor_entry()
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=_("Abrir"), command=A(lambda: panel.open_entry(e)))
        m.add_command(label=_("Ver"), accelerator="F3", command=A(self.view))
        m.add_command(label=_("Editar"), accelerator="F4", command=A(self.edit))
        m.add_separator()
        m.add_command(label=_("Marcar / desmarcar"), accelerator="Insert",
                      command=A(lambda: panel.toggle_mark()))
        m.add_command(label=_("Copiar..."), accelerator="F5", command=A(self.copy))
        m.add_command(label=_("Mover..."), accelerator="F6", command=A(self.move))
        m.add_command(label=_("Cambiar nombre"), accelerator="Shift+F6",
                      command=A(lambda: panel.start_rename()))
        m.add_command(label=_("Eliminar"), accelerator="F8", command=A(self.delete))
        m.add_separator()
        m.add_command(label=_("Comprimir..."), accelerator="Alt+F5", command=A(self.pack))
        m.add_command(label=_("Cambiar atributos..."), command=A(self.change_attrs))
        m.add_command(label=_("Copiar nombre con ruta"), command=A(lambda: self.copy_names(True)))
        m.add_separator()
        m.add_command(label=_("Propiedades"), accelerator="Alt+Enter", command=A(self.properties))
        m.tk_popup(x, y)

    # ------------------------------------------------------------ configuración
    def options(self):
        old_lang = self.cfg.get("language", "auto")
        if dialogs.options_dialog(self):
            self.hidden_var.set(self.cfg["show_hidden"])
            self._style()
            self.apply_layout()
            self.refresh()
            if self.cfg.get("language", "auto") != old_lang:
                # los textos se arman al crear la ventana: el idioma nuevo pide reiniciar
                if dialogs.confirm(self.root, _("Idioma"),
                                   _("El idioma nuevo se aplica al reiniciar el programa.\n"
                                     "¿Reiniciar ahora?")):
                    self.restart()

    def restart(self):
        """Guarda la configuración, cierra y vuelve a abrir el programa."""
        self.save_config()
        args, skip = [], False
        for a in sys.argv[1:]:          # sin --lang, para que valga el idioma recién elegido
            if skip:
                skip = False
            elif a == "--lang":
                skip = True
            elif not a.startswith("--lang="):
                args.append(a)
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "main.py")
        try:
            subprocess.Popen([sys.executable, main_py] + args)
        except OSError as ex:
            self.error(_("No se pudo reiniciar:\n{error}").format(error=ex))
            return
        remote.close_all()
        self.root.destroy()

    def save_config(self):
        if self.screenshot:
            return
        self.cfg["geometry"] = self.root.geometry()
        self.cfg["geometry_saved"] = True
        for p, key in ((self.left, "left"), (self.right, "right")):
            loc = p.loc
            self.cfg[key] = loc.path if not loc.archive else os.path.dirname(loc.archive)
            self.cfg[key + "_sort"] = [p.sort_key, p.sort_rev]
            self.cfg[key + "_tabs"] = [t["loc"].path if not t["loc"].archive
                                       else os.path.dirname(t["loc"].archive) for t in p.tabs]
            if p.tab_index:
                self.cfg[key] = self.cfg[key + "_tabs"][p.tab_index]
                tabs = self.cfg[key + "_tabs"]
                tabs.insert(0, tabs.pop(p.tab_index))
        try:
            os.makedirs(os.path.dirname(config_path()), exist_ok=True)
            with open(config_path(), "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=1, ensure_ascii=False)
        except OSError as ex:
            self.error(_("No se pudo guardar la configuración:\n{error}").format(error=ex))

    def quit(self):
        if self.cfg.get("save_on_exit", True):
            self.save_config()
        remote.close_all()
        self.root.destroy()

    # ------------------------------------------------------------ servidores FTP / SFTP
    def ftp_connect(self):
        conf = connect.ConnectionManager(self).run()
        if conf:
            self._open_remote(conf)

    def ftp_quick(self):
        s = dialogs.ask_string(self.root, _("Conexión rápida"),
                               _("Dirección del servidor, por ejemplo:\n"
                                 "ftp://usuario@servidor/carpeta   o   sftp://usuario@servidor:22/"),
                               self.cfg.get("last_ftp_url", "ftp://"))
        if not s:
            return
        conf = connect.parse_url(s)
        if conf is None:
            self.error(_("La dirección no es válida. Use ftp://, ftps:// o sftp://."))
            return
        self.cfg["last_ftp_url"] = s.strip()
        self._open_remote(conf)

    def _open_remote(self, conf):
        conf = dict(conf)
        conf.pop("_password", None)
        needs_pw = conf.get("user") and conf.get("user") != "anonymous" and \
            not conf.get("password") and not conf.get("keyfile")
        if needs_pw:
            pw = connect.ask_password(self.root, _("Contraseña"),
                                      _("Contraseña de {user} en {host}:").format(
                                          user=conf["user"], host=conf["host"]))
            if pw is None:
                return
            conf["password"] = pw
        self.set_status(_("Conectando con {host}...").format(host=conf["host"]))
        cid = self._run_safely(lambda: remote.open_connection(
            conf, connect.host_key_asker(self.root)), _("No se pudo conectar con {host}").format(host=conf["host"]))
        if not cid:
            return
        fs = remote.get(cid)
        if self.panel.navigate(Location.at_remote(cid, fs.home)):
            self.set_status(_("Conectado a {server}.").format(server=fs.title()))
            self.panel.tree.focus_set()
        else:
            remote.close(cid)

    def ftp_disconnect(self):
        cid = self.panel.loc.remote or self.other.loc.remote
        if not cid:
            dialogs.info(self.root, _("Desconectar"), _("Ningún panel está conectado a un servidor."))
            return
        remote.close(cid)
        for p in (self.left, self.right):
            for tb in p.tabs:
                if tb["loc"].remote == cid:
                    back = next((loc for loc in reversed(tb["history"]) if loc.remote is None),
                                Location.disk(os.path.expanduser("~")))
                    tb["loc"] = back
                    tb["history"] = [loc for loc in tb["history"] if loc.remote != cid] or [back]
                    tb["hidx"] = len(tb["history"]) - 1
            p.navigate(p.loc, add_history=False)
        self.set_status(_("Desconectado del servidor."))

    # ------------------------------------------------------------ complementos
    def _fill_plugin_menu(self):
        m = self.plugin_menu
        m.delete(0, "end")
        cmds = self.plugins.commands()
        for pl, label, fn, sc in cmds:
            m.add_command(label=label, accelerator=sc or "", command=self.act(
                lambda fn=fn, pl=pl: self._run_plugin(pl, fn)))
        if cmds:
            m.add_separator()
        m.add_command(label=_("Administrar complementos..."), command=self.act(self.plugin_dialog))

    def _run_plugin(self, pl, fn):
        try:
            fn()
        except Exception as ex:
            self.error(_("El complemento «{name}» falló:\n\n{error}").format(name=pl.name, error=ex))

    @staticmethod
    def _tk_sequence(shortcut):
        """'Ctrl+Shift+H' -> '<Control-H>'; 'Alt+F9' -> '<Alt-F9>'."""
        parts = [x.strip() for x in shortcut.split("+") if x.strip()]
        if not parts:
            return None
        key, mods = parts[-1], [x.lower() for x in parts[:-1]]
        out = []
        for mname in mods:
            out.append({"ctrl": "Control", "control": "Control", "alt": "Alt",
                        "shift": "Shift"}.get(mname, ""))
        if len(key) == 1:
            if "Shift" in out:
                out.remove("Shift")
                key = key.upper()
            else:
                key = key.lower()
        return "<" + "-".join([o for o in out if o] + [key]) + ">"

    def bind_plugin_keys(self):
        for t in (self.left.tree, self.right.tree):
            for seq in getattr(self, "_plugin_seqs", []):
                t.unbind(seq)
        self._plugin_seqs = []
        for pl, label, fn, sc in self.plugins.commands():
            seq = self._tk_sequence(sc) if sc else None
            if not seq:
                continue
            for t in (self.left.tree, self.right.tree):
                try:
                    t.bind(seq, lambda e, fn=fn, pl=pl: (self.act(
                        lambda: self._run_plugin(pl, fn))(), "break")[-1])
                except tk.TclError:
                    continue
            self._plugin_seqs.append(seq)

    def plugin_dialog(self):
        from .plugins import user_plugin_dir
        d = dialogs.Modal(self.root, _("Complementos"), resizable=True)
        tk.Label(d, text=_("Cada complemento es un archivo .py con una función register(api).\n"
                           "Los suyos van en la carpeta «Mis complementos»."),
                 font=dialogs.UI_FONT, justify="left").pack(anchor="w", padx=10, pady=(10, 4))
        lst = tk.Frame(d)
        lst.pack(fill="both", expand=True, padx=10)
        changed = [False]

        def fill():
            for w in lst.winfo_children():
                w.destroy()
            if not self.plugins.plugins:
                tk.Label(lst, text=_("(no hay complementos instalados)"), font=dialogs.UI_FONT,
                         fg="#707070").pack(anchor="w")
            for pl in self.plugins.plugins:
                var = tk.BooleanVar(value=pl.enabled)
                row = tk.Frame(lst)
                row.pack(fill="x", pady=2)

                def toggle(pl=pl, var=var):
                    self.plugins.set_enabled(pl.id, var.get())
                    changed[0] = True
                ttk.Checkbutton(row, text=pl.name, variable=var, command=toggle).pack(anchor="w")
                note = pl.description or ""
                if pl.error:
                    note = _("Error al cargar: {error}").format(error=pl.error)
                n_cmd, n_view = len(pl.commands), len(pl.viewers)
                extra = ", ".join(x for x in (_("{n} comando(s)").format(n=n_cmd) if n_cmd else "",
                                             _("{n} visor(es)").format(n=n_view) if n_view else "") if x)
                tk.Label(row, text=f"{note}\n{pl.path}" + (f"   [{extra}]" if extra else ""),
                         font=("Segoe UI", 8), fg="#a00000" if pl.error else "#505050",
                         justify="left", wraplength=520).pack(anchor="w", padx=(22, 0))

        def open_folder():
            folder = user_plugin_dir()
            os.makedirs(folder, exist_ok=True)
            self.shell_open(folder)

        def reload_():
            self.plugins.load_all()
            self.bind_plugin_keys()
            changed[0] = False
            fill()

        def close():
            if changed[0]:
                reload_()
            d.ok(True)
        fill()
        d.protocol("WM_DELETE_WINDOW", close)
        d.buttons(d, [(_("Mis complementos..."), open_folder), (_("Recargar"), reload_),
                      (_("Cerrar"), close)]).pack(pady=10)
        d.run()

    def help_keys(self):
        dialogs.info(self.root, _("Atajos de teclado"), _(
            "Tab  cambiar de panel          Enter  abrir / ejecutar\n"
            "Retroceso  carpeta superior     Insert / Espacio  marcar\n"
            "F3 ver    F4 editar    F5 copiar    F6 mover\n"
            "F7 nueva carpeta    F8 / Supr eliminar    Shift+F8 eliminar sin papelera\n"
            "Shift+F4 nuevo archivo    Shift+F6 cambiar nombre    Shift+F5 copiar aquí\n"
            "Alt+F1 / Alt+F2  elegir unidad    Alt+F5 comprimir    Alt+F9 descomprimir\n"
            "Alt+F7 buscar    Ctrl+M renombrado múltiple    Shift+F2 comparar carpetas\n"
            "Ctrl+Q vista rápida    Ctrl+U intercambiar    Ctrl+D favoritos\n"
            "Ctrl+T / Ctrl+W pestañas    Ctrl+Tab siguiente pestaña\n"
            "Ctrl+F1 breve  Ctrl+F2 completo  Ctrl+F3..F7 ordenar\n"
            "Num + / Num - / Num *  marcar grupo, desmarcar, invertir\n"
            "Alt+← / Alt+→ historial    Ctrl+H ocultos    Ctrl+R actualizar\n"
            "Ctrl+F conectar a FTP/SFTP    Ctrl+N conexión rápida    Ctrl+Shift+F desconectar\n"
            "Dentro de un ZIP: F5/F6 agregan o extraen, F7 crea carpetas, F8 borra,\n"
            "Shift+F6 cambia el nombre y F4 edita y guarda de vuelta.\n"
            "Escribir sobre la lista envía el texto a la línea de comandos."))

    def about(self):
        dialogs.info(self.root, _("Acerca de"),
                     _("{app} {version}\n\nAdministrador de archivos de dos paneles hecho en Python.").format(
                         app=APP_NAME, version=VERSION))
