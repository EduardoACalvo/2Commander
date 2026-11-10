"""Un panel de archivos: unidades, pestañas, ruta, lista y línea de estado."""
import fnmatch
import os
import posixpath
import tkinter as tk
from tkinter import ttk

from . import icons, remote, vfs
from .i18n import _, _c
from .vfs import Location, fmt_human, fmt_date, fmt_attrs

COLS = ("ext", "size", "date", "attr")
SORT_KEYS = {"#0": "name", "ext": "ext", "size": "size", "date": "date"}

ACTIVE_BG = "#99b4d1"     # barra de ruta del panel activo
INACTIVE_BG = "#bfcddb"   # barra de ruta del otro panel
BAR_BG = "#ffffff"        # fondo de la barra de unidades
DRIVE_SEL = "#cce8ff"     # unidad actual
UI = ("Segoe UI", 9)


def headings():
    """Títulos de las columnas en el idioma activo."""
    return {"#0": _("Nombre"), "ext": _("Ext"), "size": _("Tamaño"), "date": _("Fecha"),
            "attr": _("Atrib.")}


def _flat_button(parent, bg, **kw):
    b = tk.Button(parent, relief="flat", bd=0, bg=bg, activebackground="#e5f3ff",
                  takefocus=False, highlightthickness=0, **kw)
    b.bind("<Enter>", lambda e: e.widget.configure(bg="#e5f3ff"), add="+")
    b.bind("<Leave>", lambda e: e.widget.configure(bg=e.widget._bg), add="+")
    b._bg = bg
    return b


class Panel(tk.Frame):
    def __init__(self, master, app, side, start):
        super().__init__(master, bd=0)
        self.app = app
        self.side = side
        self.entries = []
        self.marked = set()
        default_sort = ["size", True] if side == "left" else ["ext", False]
        saved = (app.cfg.get(side + "_sort") if not app.screenshot else None) or default_sort
        self.sort_key, self.sort_rev = saved[0], bool(saved[1])
        self.view_mode = "full"
        self.filter = ""
        self.active = False
        self.quick = None
        self._mtime = None
        self.last_on_drive = {}
        self.tabs = [self._new_tab_state(Location.disk(start))]
        self.tab_index = 0
        self._build()
        self.navigate(self.loc, add_history=False)

    # ------------------------------------------------------------ estado
    @staticmethod
    def _new_tab_state(loc):
        return {"loc": loc, "history": [loc], "hidx": 0, "locked": False}

    @property
    def tab(self):
        return self.tabs[self.tab_index]

    @property
    def loc(self):
        return self.tab["loc"]

    # ------------------------------------------------------------ interfaz
    def _build(self):
        bar = tk.Frame(self, bg=BAR_BG, bd=0)
        bar.pack(fill="x")
        self.drive_bar = tk.Frame(bar, bg=BAR_BG)
        self.drive_bar.pack(side="left", fill="x", pady=(2, 2))
        tk.Frame(self, height=1, bg="#dadada").pack(fill="x")

        row = tk.Frame(self)
        row.pack(fill="x", pady=(3, 3))
        self.drive_var = tk.StringVar()
        self.drive_combo = ttk.Combobox(row, textvariable=self.drive_var, width=4,
                                        state="readonly", font=UI)
        self.drive_combo.pack(side="left")
        self.drive_combo.bind("<<ComboboxSelected>>", self._on_drive_combo)
        self.free_label = tk.Label(row, anchor="w", font=UI, padx=4)
        self.free_label.pack(side="left", fill="x", expand=True, padx=(2, 2))
        for txt, cmd in (("..", self.go_up), ("\\", self.go_root)):
            _flat_button(row, "#f0f0f0", text=txt, font=UI, width=2, padx=0, pady=0,
                         command=cmd).pack(side="right", padx=(2, 0))
        ttk.Separator(row, orient="vertical").pack(side="right", fill="y", padx=(4, 2))

        self.tab_bar = tk.Frame(self)
        self.tab_bar.pack(fill="x")

        self.path_row = tk.Frame(self, bd=0)
        self.path_row.pack(fill="x")
        self.path_label = tk.Label(self.path_row, anchor="w", font=UI, padx=3, pady=0)
        self.path_label.pack(side="left", fill="x", expand=True)
        self.path_btns = []
        for txt, cmd in (("▼", lambda: self.app.history_menu(self)),
                         ("*", lambda: self.app.hotlist(self))):
            b = tk.Label(self.path_row, text=txt, font=UI, width=2, pady=0)
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, cmd=cmd: (self.app.activate(self), cmd()))
            self.path_btns.append(b)
        self.path_label.bind("<Button-1>", lambda e: self.app.activate(self))
        self.path_label.bind("<Double-Button-1>", lambda e: self.app.ask_path(self))

        self.body = tk.Frame(self)
        self.body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(self.body, columns=COLS, show="tree headings",
                                 selectmode="browse", style="Files.Treeview")
        head = headings()
        for c in ("#0",) + COLS:
            self.tree.heading(c, text=head[c], anchor="w", command=lambda c=c: self.sort_by_col(c))
        self.tree.column("#0", width=150, minwidth=80, stretch=True)
        self.tree.column("ext", width=36, minwidth=26, stretch=False)
        self.tree.column("size", width=56, minwidth=40, stretch=False, anchor="e")
        self.tree.column("date", width=96, minwidth=60, stretch=False)
        # la última columna es más ancha porque la barra de desplazamiento va encima de ella
        self.tree.column("attr", width=52, minwidth=44, stretch=False)
        self.tree.tag_configure("marked", foreground="#e00000")
        self.tree.tag_configure("hidden", foreground="#808080")
        self.tree.tag_configure("markedhidden", foreground="#ff8080")
        self.tree.pack(fill="both", expand=True)
        # barra de desplazamiento dentro del marco de la lista, debajo de los títulos
        self.vsb = ttk.Scrollbar(self.body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.vsb.set)
        self._head_h = None
        self._place_vsb()
        self.tree.bind("<Configure>", lambda e: self._place_vsb(), add="+")

        self.status = tk.Label(self, anchor="w", font=UI, padx=3, pady=1)
        self.status.pack(fill="x", pady=(1, 0))

        t = self.tree
        t.bind("<FocusIn>", lambda e: self.app.activate(self))
        t.bind("<Double-Button-1>", self._on_double)
        t.bind("<Control-Button-1>", self._on_ctrl_click)
        t.bind("<Shift-Button-1>", self._on_shift_click)
        t.bind("<Button-3>", self._on_right_click)
        t.bind("<<TreeviewSelect>>", lambda e: self.app.cursor_moved(self))
        self.app.bind_panel_keys(self, t)
        self.refresh_drives()

    def _place_vsb(self):
        """Ubica la barra vertical adentro de la lista, desde abajo de los títulos."""
        t = self.tree
        if self._head_h is None:
            kids = t.get_children()
            bb = t.bbox(kids[0]) if kids and t.yview()[0] == 0 else ""
            if bb:
                self._head_h = bb[1]
        head = self._head_h or 20
        self.vsb.place(in_=t, relx=1.0, x=-1, y=head, anchor="ne",
                       relheight=1.0, height=-(head + 1))

    def refresh_drives(self):
        for w in self.drive_bar.winfo_children():
            w.destroy()
        self.drives = vfs.list_drives()
        self.drive_btns = {}
        for d in self.drives:
            img = icons.drive_icon(d, vfs.drive_type(d))
            b = _flat_button(self.drive_bar, BAR_BG, text=" " + d[0].lower(), image=img,
                             compound="left", padx=4, pady=1, font=UI,
                             command=lambda d=d: self.change_drive(d))
            b.pack(side="left", padx=(0, 2))
            self.drive_btns[d[0].upper()] = b
        b = _flat_button(self.drive_bar, BAR_BG, text=" \\", image=icons.get("tb_network"),
                         compound="left", padx=4, pady=1, font=UI,
                         command=lambda: self.go_root())
        b.pack(side="left")
        self.drive_combo["values"] = [d[0].lower() for d in self.drives]

    def _mark_drive(self):
        cur = "" if self.loc.remote else vfs.drive_of(self.loc.archive or self.loc.path)[:1].upper()
        for letter, b in self.drive_btns.items():
            b._bg = DRIVE_SEL if letter == cur else BAR_BG
            b.configure(bg=b._bg)

    def set_active(self, active):
        self.active = active
        bg = ACTIVE_BG if active else INACTIVE_BG
        self.path_row.configure(bg=bg)
        for w in [self.path_label] + self.path_btns:
            w.configure(bg=bg, fg="black")
        self.tree.configure(style="Active.Files.Treeview" if active else "Files.Treeview")
        self._render_tabs()

    # ------------------------------------------------------------ pestañas
    def _render_tabs(self):
        for w in self.tab_bar.winfo_children():
            w.destroy()
        if len(self.tabs) < 2:
            self.tab_bar.configure(height=1)
            return
        for i, tb in enumerate(self.tabs):
            loc = tb["loc"]
            name = os.path.basename(loc.display().rstrip("\\/")) or loc.display()
            if tb["locked"]:
                name = "*" + name
            sel = i == self.tab_index
            lab = tk.Label(self.tab_bar, text=" " + name + " ", font=("Segoe UI", 8),
                           relief="raised" if sel else "groove", bd=1,
                           bg="#ffffff" if sel else "#e4e4e4", padx=2)
            lab.pack(side="left", padx=(0, 1))
            lab.bind("<Button-1>", lambda e, i=i: self.switch_tab(i))
            lab.bind("<Double-Button-1>", lambda e, i=i: self.close_tab(i))

    def new_tab(self):
        self.tabs.insert(self.tab_index + 1, self._new_tab_state(self.loc))
        self.switch_tab(self.tab_index + 1)

    def close_tab(self, i=None):
        if len(self.tabs) < 2:
            return
        i = self.tab_index if i is None else i
        del self.tabs[i]
        self.tab_index = min(self.tab_index if i > self.tab_index else max(0, self.tab_index - 1),
                             len(self.tabs) - 1)
        self.navigate(self.loc, add_history=False)

    def switch_tab(self, i):
        self.tab_index = i % len(self.tabs)
        self.navigate(self.loc, add_history=False)
        self.app.activate(self)
        self.tree.focus_set()

    def next_tab(self, step=1):
        if len(self.tabs) > 1:
            self.switch_tab(self.tab_index + step)

    def toggle_lock(self):
        self.tab["locked"] = not self.tab["locked"]
        self._render_tabs()

    # ------------------------------------------------------------ navegación
    def navigate(self, loc, focus_name=None, add_history=True):
        if isinstance(loc, str):
            loc = Location.disk(loc)
        if self.tab["locked"] and add_history and loc.key() != self.loc.key():
            self.tabs.insert(self.tab_index + 1, self._new_tab_state(loc))
            self.tab_index += 1
        try:
            entries = vfs.list_dir(loc, self.app.cfg["show_hidden"])
        except Exception as ex:  # sin permisos, unidad vacía, ZIP dañado...
            self.app.error(_("No se puede leer:\n{path}\n\n{error}").format(path=loc.display(), error=ex))
            if not self.entries:
                fallback = Location.disk(os.path.expanduser("~"))
                if fallback.key() != loc.key():
                    self.navigate(fallback, add_history=False)
            return False
        tb = self.tab
        tb["loc"] = loc
        if add_history:
            del tb["history"][tb["hidx"] + 1:]
            tb["history"].append(loc)
            tb["hidx"] = len(tb["history"]) - 1
            if len(tb["history"]) > 50:
                tb["history"].pop(0)
                tb["hidx"] -= 1
        if loc.is_disk:
            self.last_on_drive[vfs.drive_of(loc.path).upper()] = loc.path
        self.marked.clear()
        self.entries = entries
        self._mtime = self._dir_mtime()
        self.populate(focus_name)
        self._update_header()
        self._render_tabs()
        self.app.panel_changed(self)
        return True

    def _dir_mtime(self):
        if self.loc.remote:
            return None  # los servidores se actualizan solo a pedido (Ctrl+R)
        try:
            p = self.loc.archive or self.loc.path
            return os.stat(p).st_mtime_ns
        except OSError:
            return None

    def poll_changes(self):
        if self.quick is None:
            m = self._dir_mtime()
            if m is not None and m != self._mtime:
                self.reload()

    def reload(self, focus_name=None):
        cur = focus_name or (self.cursor_entry().name if self.cursor_entry() else None)
        marked = set(self.marked)
        try:
            self.entries = vfs.list_dir(self.loc, self.app.cfg["show_hidden"])
        except Exception:
            self.navigate(Location.disk(vfs.drive_of(self.loc.path)), add_history=False)
            return
        names = {e.name for e in self.entries}
        self.marked = marked & names
        self._mtime = self._dir_mtime()
        self.populate(cur)
        self._update_header()

    def go_up(self):
        loc = self.loc
        if loc.remote:
            cur = loc.inner.rstrip("/")
            if cur:
                parent, name = posixpath.split(cur)
                self.navigate(Location.at_remote(loc.remote, parent or "/"), focus_name=name)
            return
        if loc.archive:
            if loc.inner:
                parent = loc.inner.rstrip("/").rpartition("/")[0]
                name = loc.inner.rstrip("/").rpartition("/")[2]
                self.navigate(Location(loc.path, loc.archive, (parent + "/") if parent else ""),
                              focus_name=name)
            else:
                self.navigate(Location.disk(os.path.dirname(loc.archive)),
                              focus_name=os.path.basename(loc.archive))
            return
        p = loc.path.rstrip("\\/")
        parent = os.path.dirname(p)
        if parent and parent != p:
            self.navigate(Location.disk(parent if not parent.endswith(":") else parent + "\\"),
                          focus_name=os.path.basename(p))

    def go_root(self):
        if self.loc.remote:
            self.navigate(Location.at_remote(self.loc.remote, "/"))
        elif self.loc.archive:
            self.navigate(Location(self.loc.path, self.loc.archive, ""))
        else:
            self.navigate(Location.disk(vfs.drive_of(self.loc.path)))

    def change_drive(self, root):
        last = self.last_on_drive.get(root.upper())
        target = last if last and os.path.isdir(last) else root
        if self.navigate(Location.disk(target)):
            self.app.activate(self)
            self.tree.focus_set()

    def _on_drive_combo(self, _e=None):
        letter = self.drive_var.get()
        for d in self.drives:
            if d[0].lower() == letter:
                self.change_drive(d)
                break

    def history_move(self, step):
        tb = self.tab
        i = tb["hidx"] + step
        if 0 <= i < len(tb["history"]):
            tb["hidx"] = i
            self.navigate(tb["history"][i], add_history=False)

    def open_entry(self, e):
        if e is None:
            return
        if e.is_up:
            self.go_up()
        elif self.loc.remote:
            if e.is_dir:
                self.navigate(Location.at_remote(self.loc.remote, e.full))
            else:
                self.app.view_file(self, e)
        elif self.loc.archive:
            if e.is_dir:
                self.navigate(Location(self.loc.path, self.loc.archive, e.full))
            else:
                self.app.view_file(self, e)
        elif e.is_dir:
            self.navigate(Location.disk(e.full))
        elif vfs.is_zip(e.full):
            self.navigate(Location(e.full, e.full, ""))
        else:
            self.app.shell_open(e.full, cwd=self.loc.path)

    # ------------------------------------------------------------ lista
    def _sorted(self):
        ups = [e for e in self.entries if e.is_up]
        dirs = [e for e in self.entries if e.is_dir and not e.is_up]
        files = [e for e in self.entries if not e.is_dir]
        k = self.sort_key
        if k == "ext":
            fk = lambda e: (e.ext.lower(), e.name.lower())
        elif k == "size":
            fk = lambda e: (e.size, e.name.lower())
        elif k == "date":
            fk = lambda e: (e.mtime, e.name.lower())
        elif k == "none":
            fk = None
        else:
            fk = lambda e: e.name.lower()
        dk = (lambda e: (e.mtime, e.name.lower())) if k == "date" else (lambda e: e.name.lower())
        if fk:
            files.sort(key=fk, reverse=self.sort_rev)
            dirs.sort(key=dk, reverse=self.sort_rev and k in ("name", "date"))
        return ups + dirs + files

    def populate(self, focus_name=None):
        t = self.tree
        t.delete(*t.get_children())
        self.entries = self._sorted()
        flt = self.filter
        focus_iid = None
        brief = self.view_mode == "brief"
        on_disk = self.loc.is_disk
        up_time = 0
        if on_disk:
            try:
                up_time = os.stat(self.loc.path).st_mtime
            except OSError:
                pass
        for i, e in enumerate(self.entries):
            if flt and not e.is_dir and not any(
                    fnmatch.fnmatch(e.name.lower(), f.strip().lower()) for f in flt.split(";")):
                continue
            if e.is_up:
                vals = ("", "<DIR>", fmt_date(up_time), "----" if up_time else "")
                text = "[..]"
            elif e.is_dir:
                vals = ("", "<DIR>", fmt_date(e.mtime), fmt_attrs(e.attrs))
                text = f"[{e.name}]"
            else:
                vals = (e.ext, fmt_human(e.size), fmt_date(e.mtime), fmt_attrs(e.attrs))
                text = e.name if brief else e.stem
            t.insert("", "end", iid=str(i), text=" " + text, values=vals,
                     image=icons.for_entry(e, on_disk), tags=self._tags(e))
            if focus_name is not None and e.name == focus_name:
                focus_iid = str(i)
        kids = t.get_children()
        if kids:
            iid = focus_iid or kids[0]
            t.selection_set(iid)
            t.focus(iid)
            t.see(iid)
        self._update_headings()
        self.update_status()
        if self._head_h is None:
            self.after_idle(self._place_vsb)

    def _tags(self, e):
        m = e.name in self.marked
        if e.hidden:
            return ("markedhidden",) if m else ("hidden",)
        return ("marked",) if m else ()

    def _update_headings(self):
        head = headings()
        for c in ("#0",) + COLS:
            arrow = ""
            if SORT_KEYS.get(c) == self.sort_key:
                arrow = "↓" if self.sort_rev else "↑"
            self.tree.heading(c, text=arrow + head[c])
        self.tree.configure(displaycolumns=() if self.view_mode == "brief" else COLS)

    def _update_header(self):
        loc = self.loc
        disp = loc.display()
        if loc.remote:
            mask = self.filter or "*.*"
            self.path_label.configure(text=disp.rstrip("/") + "/" + mask)
            self.free_label.configure(
                text=_("[{name}]  conectado").format(name=remote.get(loc.remote).title())
                if loc.remote in remote.open_ids() else _("[desconectado]"))
            self._mark_drive()
            self.set_active(self.active)
            return
        if len(disp) > 1 and disp[1] == ":":
            disp = disp[0].lower() + disp[1:]
        mask = self.filter or "*.*"
        self.path_label.configure(text=os.path.join(disp, mask) if not disp.endswith("\\")
                                  else disp + mask)
        root = vfs.drive_of(loc.archive or loc.path)
        self.drive_var.set(root[0].lower())
        free = vfs.disk_free(root)
        label = vfs.volume_label(root)
        if free:
            self.free_label.configure(
                text=_("[{label}]  {free} libres de {total} totales.").format(
                    label=label or _c("volumen", "_ninguno_"), free=fmt_human(free[0]),
                    total=fmt_human(free[1])))
        else:
            self.free_label.configure(text=f"[{label}]")
        self._mark_drive()
        self.set_active(self.active)

    def set_view_mode(self, mode):
        self.view_mode = mode
        self.populate(self.cursor_entry().name if self.cursor_entry() else None)
        self.app._update_view_buttons()

    def sort_by_col(self, col):
        key = SORT_KEYS.get(col)
        if not key:
            return
        self.set_sort(key)

    def set_sort(self, key, reverse=None):
        if reverse is None:
            reverse = (not self.sort_rev) if key == self.sort_key else (key in ("size", "date"))
        self.sort_key, self.sort_rev = key, reverse
        cur = self.cursor_entry()
        self.populate(cur.name if cur else None)

    # ------------------------------------------------------------ cursor y marcas
    def cursor_iid(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def cursor_entry(self):
        iid = self.cursor_iid()
        return self.entries[int(iid)] if iid is not None else None

    def selected_entries(self):
        if self.marked:
            return [e for e in self.entries if e.name in self.marked]
        e = self.cursor_entry()
        return [e] if e and not e.is_up else []

    def _refresh_row(self, iid):
        e = self.entries[int(iid)]
        self.tree.item(iid, tags=self._tags(e))

    def toggle_mark(self, iid=None, move=False):
        iid = iid or self.cursor_iid()
        if iid is None:
            return
        e = self.entries[int(iid)]
        if not e.is_up:
            self.marked.symmetric_difference_update({e.name})
            self._refresh_row(iid)
        if move:
            nxt = self.tree.next(iid)
            if nxt:
                self.tree.selection_set(nxt)
                self.tree.focus(nxt)
                self.tree.see(nxt)
        self.update_status()

    def mark_where(self, pred, on=True):
        for iid in self.tree.get_children():
            e = self.entries[int(iid)]
            if e.is_up or not pred(e):
                continue
            if on:
                self.marked.add(e.name)
            else:
                self.marked.discard(e.name)
            self._refresh_row(iid)
        self.update_status()

    def mark_pattern(self, pattern, on=True, dirs=False):
        pats = [p.strip().lower() for p in pattern.replace(",", ";").split(";") if p.strip()]
        self.mark_where(lambda e: (dirs or not e.is_dir) and
                        any(fnmatch.fnmatch(e.name.lower(), p) for p in pats), on)

    def invert_marks(self):
        for iid in self.tree.get_children():
            e = self.entries[int(iid)]
            if e.is_up or e.is_dir:
                continue
            self.marked.symmetric_difference_update({e.name})
            self._refresh_row(iid)
        self.update_status()

    def update_status(self):
        files = [e for e in self.entries if not e.is_dir]
        dirs = [e for e in self.entries if e.is_dir and not e.is_up]
        mf = [e for e in files if e.name in self.marked]
        md = [e for e in dirs if e.name in self.marked]
        self.status.configure(
            text=_("{sel_size} de {total_size} en {sel_files} de {files} fichero(s), "
                   "{sel_dirs} / {dirs} dir(s)").format(
                sel_size=fmt_human(sum(e.size for e in mf)),
                total_size=fmt_human(sum(e.size for e in files)),
                sel_files=len(mf), files=len(files), sel_dirs=len(md), dirs=len(dirs)))

    def focus_name(self, name):
        for iid in self.tree.get_children():
            if self.entries[int(iid)].name == name:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                return True
        return False

    # ------------------------------------------------------------ ratón
    def _on_double(self, ev):
        if self.tree.identify_region(ev.x, ev.y) in ("tree", "cell"):
            self.open_entry(self.cursor_entry())

    def _on_ctrl_click(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.toggle_mark(iid)
        self.tree.focus_set()
        return "break"

    def _on_shift_click(self, ev):
        iid = self.tree.identify_row(ev.y)
        cur = self.cursor_iid()
        if iid and cur:
            kids = list(self.tree.get_children())
            a, b = sorted((kids.index(cur), kids.index(iid)))
            for k in kids[a:b + 1]:
                e = self.entries[int(k)]
                if not e.is_up:
                    self.marked.add(e.name)
                    self._refresh_row(k)
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.update_status()
        self.tree.focus_set()
        return "break"

    def _on_right_click(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
        self.tree.focus_set()
        self.app.activate(self)
        self.app.context_menu(self, ev.x_root, ev.y_root)

    # ------------------------------------------------------------ renombrar en el sitio
    def start_rename(self):
        iid = self.cursor_iid()
        e = self.cursor_entry()
        if not iid or e is None or e.is_up:
            return
        self.tree.see(iid)
        self.update_idletasks()
        box = self.tree.bbox(iid, "#0")
        if not box:
            return
        x, y, w, h = box
        var = tk.StringVar(value=e.name)
        ent = tk.Entry(self.tree, textvariable=var, font=("Segoe UI", 9), relief="solid", bd=1)
        ent.place(x=x + 20, y=y, width=max(w - 20, 200), height=h)
        ent.focus_set()
        stem_len = len(e.stem) if not e.is_dir else len(e.name)
        ent.selection_range(0, stem_len)
        ent.icursor(stem_len)

        def finish(ok):
            new = var.get().strip()
            ent.destroy()
            self.tree.focus_set()
            if ok and new and new != e.name:
                self.app.rename_entry(self, e, new)

        ent.bind("<Return>", lambda _e: finish(True))
        ent.bind("<Escape>", lambda _e: finish(False))
        ent.bind("<FocusOut>", lambda _e: finish(False) if ent.winfo_exists() else None)

    # ------------------------------------------------------------ vista rápida
    def show_quick(self, title, content=None, image=None):
        if self.quick is None:
            self.tree.pack_forget()
            self.vsb.place_forget()
            fr = tk.Frame(self.body)
            fr.pack(fill="both", expand=True)
            txt = tk.Text(fr, wrap="none", font=("Consolas", 9), bd=1, relief="sunken")
            sb = ttk.Scrollbar(fr, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(fill="both", expand=True)
            self.quick = (fr, txt)
        fr, txt = self.quick
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        if image is not None:
            txt.image_create("1.0", image=image)
            txt._img = image
        else:
            txt.insert("1.0", content or "")
        txt.configure(state="disabled")
        self.path_label.configure(text=_("Vista rápida: {title}").format(title=title))

    def hide_quick(self):
        if self.quick is not None:
            self.quick[0].destroy()
            self.quick = None
            self.tree.pack(fill="both", expand=True)
            self._place_vsb()
            self._update_header()
            self.reload()
