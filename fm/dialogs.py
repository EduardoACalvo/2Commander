"""Ventanas auxiliares: preguntas, progreso, visor, búsqueda, renombrado múltiple..."""
import fnmatch
import os
import queue
import re
import shutil
import threading
import time
import tkinter as tk
import zipfile
from tkinter import ttk

from . import i18n, transfer, vfs
from .i18n import _, _c
from .vfs import fmt_size, fmt_date

UI_FONT = ("Segoe UI", 9)


class Modal(tk.Toplevel):
    def __init__(self, master, title, resizable=False):
        super().__init__(master)
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(resizable, resizable)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())

    def center(self):
        self.update_idletasks()
        top = self.master.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_reqwidth()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def run(self):
        self.center()
        self.grab_set()
        self.wait_window()
        return self.result

    def ok(self, value=True):
        self.result = value
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

    def buttons(self, parent, spec):
        fr = tk.Frame(parent)
        for text, cmd in spec:
            ttk.Button(fr, text=text, command=cmd, width=max(10, len(text) + 2)).pack(
                side="left", padx=3)
        return fr


def ask_string(master, title, prompt, initial="", select_stem=False, combo_values=None):
    d = Modal(master, title)
    tk.Label(d, text=prompt, font=UI_FONT, anchor="w", justify="left").pack(
        fill="x", padx=10, pady=(10, 2))
    var = tk.StringVar(value=initial)
    if combo_values:
        ent = ttk.Combobox(d, textvariable=var, values=combo_values, width=60, font=UI_FONT)
    else:
        ent = ttk.Entry(d, textvariable=var, width=60, font=UI_FONT)
    ent.pack(fill="x", padx=10)
    ent.focus_set()
    if select_stem and "." in initial:
        ent.selection_range(0, initial.rfind("."))
    else:
        ent.selection_range(0, "end")
    ent.bind("<Return>", lambda e: d.ok(var.get()))
    d.buttons(d, [(_("Aceptar"), lambda: d.ok(var.get())), (_("Cancelar"), d.cancel)]).pack(pady=10)
    return d.run()


def confirm(master, title, text, yes=None, no=None):
    yes = _("Sí") if yes is None else yes
    no = _("No") if no is None else no
    d = Modal(master, title)
    tk.Label(d, text=text, font=UI_FONT, justify="left", anchor="w", wraplength=520).pack(
        padx=16, pady=(14, 8), fill="x")
    fr = d.buttons(d, [(yes, lambda: d.ok(True)), (no, d.cancel)])
    fr.pack(pady=(0, 12))
    fr.winfo_children()[0].focus_set()
    d.bind("<Return>", lambda e: d.ok(True))
    return bool(d.run())


def info(master, title, text):
    d = Modal(master, title)
    tk.Label(d, text=text, font=UI_FONT, justify="left", anchor="w", wraplength=560).pack(
        padx=16, pady=(14, 8), fill="x")
    fr = d.buttons(d, [(_("Aceptar"), lambda: d.ok(True))])
    fr.pack(pady=(0, 12))
    fr.winfo_children()[0].focus_set()
    d.bind("<Return>", lambda e: d.ok(True))
    d.run()


def ask_overwrite(master, src_info, dst_path):
    d = Modal(master, _("Confirmación"))
    st = None
    try:
        st = os.stat(dst_path)
    except OSError:
        pass
    dst_info = f"{fmt_size(st.st_size)} bytes, {fmt_date(st.st_mtime)}\n" if st else ""
    txt = _("¿Sobrescribir el archivo:\n{dst}\n{dst_info}\ncon el archivo\n{src}\n"
            "{src_size} bytes, {src_date}?").format(
        dst=dst_path, dst_info=dst_info, src=src_info[0], src_size=fmt_size(src_info[1]),
        src_date=fmt_date(src_info[2]))
    tk.Label(d, text=txt, font=UI_FONT, justify="left", anchor="w").pack(padx=16, pady=12)
    fr = tk.Frame(d)
    fr.pack(pady=(0, 12), padx=10)
    for i, (t, v) in enumerate([(_("Sobrescribir"), "yes"), (_("Todos"), "all"),
                                (_("Omitir"), "skip"), (_("Omitir todos"), "skipall"),
                                (_("Cancelar"), None)]):
        ttk.Button(fr, text=t, command=lambda v=v: d.ok(v)).grid(row=0, column=i, padx=3)
    return d.run()


# ===================================================================== operaciones

class FileOp:
    """Copia, mueve, borra, comprime o descomprime en segundo plano con barra de progreso."""

    def __init__(self, app, kind, items, dest=None, rename_to=None, src_loc=None, extra=None):
        self.app = app
        self.kind = kind
        self.items = items          # lista de Entry
        self.dest = dest
        self.rename_to = rename_to
        self.src_loc = src_loc
        self.extra = extra or {}
        self.q = queue.Queue()
        self.cancelled = False
        self.overwrite = self.extra.get("overwrite")  # None=preguntar, 'all', 'skipall'
        self._answer = None
        self._event = threading.Event()
        titles = {"copy": _("Copiando"), "move": _("Moviendo"), "delete": _("Eliminando"),
                  "pack": _("Comprimiendo"), "unpack": _("Descomprimiendo"),
                  "transfer": _("Moviendo") if self.extra.get("move") else _("Copiando"),
                  "vdelete": _("Eliminando"), "ziprename": _("Cambiando el nombre")}
        self.win = Modal(app.root, titles[kind])
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.lab1 = tk.Label(self.win, text="", font=UI_FONT, anchor="w", width=70)
        self.lab1.pack(fill="x", padx=12, pady=(12, 0))
        self.lab2 = tk.Label(self.win, text="", font=UI_FONT, anchor="w", width=70)
        self.lab2.pack(fill="x", padx=12)
        self.bar = ttk.Progressbar(self.win, length=480, maximum=1000)
        self.bar.pack(padx=12, pady=8)
        ttk.Button(self.win, text=_("Cancelar"), command=self.cancel).pack(pady=(0, 10))
        self.win.center()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.win.after(40, self._poll)

    def cancel(self):
        self.cancelled = True

    # --------------------------------------------------- hilo principal
    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _k, done, total, name, target = msg
                    self.lab1.configure(text=name)
                    self.lab2.configure(text=target or "")
                    self.bar["value"] = 1000 * done / total if total else 0
                elif kind == "ask":
                    _k, src_info, dst = msg
                    self._answer = ask_overwrite(self.win, src_info, dst)
                    self._event.set()
                elif kind == "error":
                    info(self.win, _("Error"), msg[1])
                elif kind == "done":
                    self.win.destroy()
                    self.app.op_finished(self)
                    return
        except queue.Empty:
            pass
        self.win.after(40, self._poll)

    # --------------------------------------------------- hilo de trabajo
    def _progress(self, done, total, name, target=""):
        self.q.put(("progress", done, total, name, target))

    def _ask(self, src_info, dst):
        if self.overwrite == "all":
            return True
        if self.overwrite == "skipall":
            return False
        self._event.clear()
        self.q.put(("ask", src_info, dst))
        self._event.wait()
        a = self._answer
        if a is None:
            self.cancelled = True
            return False
        if a in ("all", "skipall"):
            self.overwrite = a
        return a in ("yes", "all")

    def _run(self):
        try:
            getattr(self, "_do_" + self.kind)()
        except Exception as ex:
            self.q.put(("error", str(ex)))
        self.q.put(("done",))

    def _plan_disk(self):
        """[(src, dst, size, mtime)] archivos y [(dst)] carpetas."""
        files, dirs = [], []
        for e in self.items:
            name = self.rename_to if (self.rename_to and len(self.items) == 1) else e.name
            target = os.path.join(self.dest, name)
            if e.is_dir:
                if os.path.normcase(os.path.abspath(self.dest) + os.sep).startswith(
                        os.path.normcase(os.path.abspath(e.full) + os.sep)):
                    raise RuntimeError(_("No se puede copiar una carpeta dentro de sí misma:\n{path}").format(
                        path=e.full))
                dirs.append(target)
                for root, ds, fs in os.walk(e.full):
                    rel = os.path.relpath(root, e.full)
                    for d in ds:
                        dirs.append(os.path.normpath(os.path.join(target, rel, d)))
                    for f in fs:
                        s = os.path.join(root, f)
                        try:
                            st = os.stat(s)
                        except OSError:
                            continue
                        files.append((s, os.path.normpath(os.path.join(target, rel, f)),
                                      st.st_size, st.st_mtime))
            else:
                files.append((e.full, target, e.size, e.mtime))
        return files, dirs

    def _copy_file(self, src, dst, size, done, total):
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            raise RuntimeError(_("No se puede copiar un archivo sobre sí mismo:\n{path}").format(path=src))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            vfs.set_attrs(dst, vfs.get_attrs(dst) & ~vfs.FILE_ATTRIBUTE_READONLY)
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while not self.cancelled:
                buf = fi.read(1 << 20)
                if not buf:
                    break
                fo.write(buf)
                done += len(buf)
                self._progress(done, total, src, dst)
        if self.cancelled:
            try:
                os.remove(dst)
            except OSError:
                pass
            return done
        shutil.copystat(src, dst)
        return done

    def _do_copy(self, move=False):
        if self.src_loc is not None and self.src_loc.archive:
            return self._extract_from_zip()
        files, dirs = self._plan_disk()
        if move:
            # en la misma unidad se renombra directamente
            fast = []
            for e in self.items:
                name = self.rename_to if (self.rename_to and len(self.items) == 1) else e.name
                target = os.path.join(self.dest, name)
                same = os.path.splitdrive(e.full)[0].lower() == os.path.splitdrive(target)[0].lower()
                if same and not os.path.exists(target):
                    os.replace(e.full, target)
                    fast.append(e)
            if len(fast) == len(self.items):
                return
            self.items = [e for e in self.items if e not in fast]
            files, dirs = self._plan_disk()
        total = sum(f[2] for f in files) or 1
        done = 0
        for d in dirs:
            os.makedirs(d, exist_ok=True)
        for src, dst, size, mt in files:
            if self.cancelled:
                return
            if os.path.exists(dst) and not self._ask((src, size, mt), dst):
                done += size
                continue
            if self.cancelled:
                return
            try:
                done = self._copy_file(src, dst, size, done, total)
            except OSError as ex:
                self.q.put(("error", f"{src}\n\n{ex}"))
        if move and not self.cancelled:
            for e in self.items:
                try:
                    if e.is_dir:
                        shutil.rmtree(e.full)
                    else:
                        os.remove(e.full)
                except OSError as ex:
                    self.q.put(("error", _("No se pudo eliminar el original:\n{path}\n{error}").format(
                        path=e.full, error=ex)))

    def _do_move(self):
        self._do_copy(move=True)

    # entre disco, ZIP y servidores (ver transfer.py)
    def _do_transfer(self):
        transfer.run_transfer(self)

    def _do_vdelete(self):
        transfer.run_delete(self)

    def _do_ziprename(self):
        transfer.run_zip_rename(self)

    def _extract_from_zip(self):
        loc = self.src_loc
        with zipfile.ZipFile(loc.archive) as zf:
            infos = []
            for e in self.items:
                for info in zf.infolist():
                    n = info.filename.replace("\\", "/")
                    if n == e.full or (e.is_dir and n.startswith(e.full)):
                        infos.append(info)
            total = sum(i.file_size for i in infos) or 1
            done = 0
            for info in infos:
                if self.cancelled:
                    return
                rel = info.filename.replace("\\", "/")[len(loc.inner):]
                dst = os.path.join(self.dest, *[p for p in rel.split("/") if p])
                if info.is_dir():
                    os.makedirs(dst, exist_ok=True)
                    continue
                mt = time.mktime(info.date_time + (0, 0, -1))
                if os.path.exists(dst) and not self._ask((info.filename, info.file_size, mt), dst):
                    done += info.file_size
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with zf.open(info) as fi, open(dst, "wb") as fo:
                    while not self.cancelled:
                        buf = fi.read(1 << 20)
                        if not buf:
                            break
                        fo.write(buf)
                        done += len(buf)
                        self._progress(done, total, info.filename, dst)
                os.utime(dst, (mt, mt))

    def _do_delete(self):
        paths = [e.full for e in self.items]
        if self.extra.get("recycle") and vfs.recycle(paths):
            return
        total = len(paths) or 1
        for i, (e, p) in enumerate(zip(self.items, paths)):
            if self.cancelled:
                return
            self._progress(i, total, p)
            try:
                if e.is_dir:
                    def onerr(func, path, _exc):
                        vfs.set_attrs(path, 0x80)
                        func(path)
                    shutil.rmtree(p, onerror=onerr)
                else:
                    if vfs.get_attrs(p) & vfs.FILE_ATTRIBUTE_READONLY:
                        vfs.set_attrs(p, vfs.get_attrs(p) & ~vfs.FILE_ATTRIBUTE_READONLY)
                    os.remove(p)
            except OSError as ex:
                self.q.put(("error", f"{p}\n\n{ex}"))

    def _do_pack(self):
        files = []
        base = self.src_loc.path
        mode = self.extra.get("mode", "w")
        prefix = self.extra.get("prefix", "")
        for e in self.items:
            if e.is_dir:
                for root, ds, fs in os.walk(e.full):
                    if not fs and not ds:
                        files.append((root, os.path.relpath(root, base) + "/"))
                    for f in fs:
                        p = os.path.join(root, f)
                        files.append((p, os.path.relpath(p, base)))
            else:
                files.append((e.full, e.name))
        total = sum(os.path.getsize(p) for p, a in files if not a.endswith("/")) or 1
        done = 0
        files = [(p, prefix + a) for p, a in files]
        with zipfile.ZipFile(self.dest, mode, zipfile.ZIP_DEFLATED) as zf:
            for p, arc in files:
                if self.cancelled:
                    break
                self._progress(done, total, p, self.dest)
                if arc.endswith("/"):
                    zf.writestr(arc.replace("\\", "/"), b"")
                else:
                    zf.write(p, arc.replace("\\", "/"))
                    done += os.path.getsize(p)
        if self.cancelled and mode == "w":
            os.remove(self.dest)

    def _do_unpack(self):
        for e in self.items:
            with zipfile.ZipFile(e.full) as zf:
                infos = zf.infolist()
                total = sum(i.file_size for i in infos) or 1
                done = 0
                for info in infos:
                    if self.cancelled:
                        return
                    dst = os.path.join(self.dest, *[p for p in info.filename.replace("\\", "/")
                                                    .split("/") if p and p != ".."])
                    if info.is_dir():
                        os.makedirs(dst, exist_ok=True)
                        continue
                    mt = time.mktime(info.date_time + (0, 0, -1))
                    if os.path.exists(dst) and not self._ask((info.filename, info.file_size, mt), dst):
                        continue
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with zf.open(info) as fi, open(dst, "wb") as fo:
                        shutil.copyfileobj(fi, fo)
                    os.utime(dst, (mt, mt))
                    done += info.file_size
                    self._progress(done, total, info.filename, dst)


# ===================================================================== visor (F3)

class Viewer(tk.Toplevel):
    MAX = 8 << 20

    def __init__(self, master, title, data, ext=""):
        super().__init__(master)
        self.title(_("Visor - [{title}]").format(title=title))
        self.geometry("900x620")
        self.data = data
        self.ext = ext.lower()
        self.mode = tk.StringVar(value="image" if self.ext in IMG else "text")
        self.wrap = tk.BooleanVar(value=True)
        self.encoding = tk.StringVar(value="auto")
        self.last_find = ""
        m = tk.Menu(self)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label=_("Cerrar"), accelerator="Esc", command=self.destroy)
        m.add_cascade(label=_("Archivo"), menu=fm)
        em = tk.Menu(m, tearoff=0)
        em.add_command(label=_("Copiar selección"), accelerator="Ctrl+C", command=self.copy_sel)
        em.add_command(label=_("Seleccionar todo"), accelerator="Ctrl+A",
                       command=lambda: self.text.tag_add("sel", "1.0", "end"))
        em.add_separator()
        em.add_command(label=_("Buscar..."), accelerator="F7", command=self.find)
        em.add_command(label=_("Buscar siguiente"), accelerator="F3", command=self.find_next)
        m.add_cascade(label=_("Edición"), menu=em)
        om = tk.Menu(m, tearoff=0)
        om.add_radiobutton(label=_("Texto"), accelerator="1", variable=self.mode, value="text",
                           command=self.render)
        om.add_radiobutton(label=_("Hexadecimal"), accelerator="3", variable=self.mode, value="hex",
                           command=self.render)
        om.add_radiobutton(label=_("Imagen"), accelerator="4", variable=self.mode, value="image",
                           command=self.render)
        om.add_separator()
        om.add_checkbutton(label=_("Ajustar líneas"), accelerator="W", variable=self.wrap,
                           command=self.render)
        om.add_separator()
        for enc, lab in (("auto", _("Automático")), ("utf-8", "UTF-8"), ("cp1252", "ANSI (Windows)"),
                         ("cp850", "ASCII (DOS)")):
            om.add_radiobutton(label=lab, variable=self.encoding, value=enc, command=self.render)
        m.add_cascade(label=_("Opciones"), menu=om)
        self.configure(menu=m)
        fr = tk.Frame(self)
        fr.pack(fill="both", expand=True)
        self.text = tk.Text(fr, font=("Consolas", 10), bd=0, undo=False)
        sy = ttk.Scrollbar(fr, orient="vertical", command=self.text.yview)
        sx = ttk.Scrollbar(fr, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side="right", fill="y")
        sx.pack(side="bottom", fill="x")
        self.text.pack(fill="both", expand=True)
        self.status = tk.Label(self, anchor="w", font=UI_FONT, relief="sunken", bd=1)
        self.status.pack(fill="x")
        self.text.tag_configure("found", background="#ffe060")
        for key, mode in (("1", "text"), ("3", "hex"), ("4", "image")):
            self.bind(key, lambda e, m=mode: (self.mode.set(m), self.render(), "break")[-1])
        self.bind("w", lambda e: (self.wrap.set(not self.wrap.get()), self.render(), "break")[-1])
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("q", lambda e: self.destroy())
        self.bind("<F7>", lambda e: self.find())
        self.bind("<Control-f>", lambda e: self.find())
        self.bind("<F3>", lambda e: self.find_next())
        self.render()
        self.text.focus_set()

    def decoded(self):
        enc = self.encoding.get()
        if enc == "auto":
            if self.data[:3] == b"\xef\xbb\xbf":
                return self.data[3:].decode("utf-8", "replace")
            if self.data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                return self.data.decode("utf-16", "replace")
            try:
                return self.data.decode("utf-8")
            except UnicodeDecodeError:
                return self.data.decode("cp1252", "replace")
        return self.data.decode(enc, "replace")

    def render(self):
        t = self.text
        t.configure(state="normal", wrap="word" if self.wrap.get() else "none")
        t.delete("1.0", "end")
        mode = self.mode.get()
        if mode == "image":
            try:
                from io import BytesIO
                from PIL import Image, ImageTk
                im = Image.open(BytesIO(self.data))
                size = im.size
                im.thumbnail((max(self.winfo_width() - 40, 860), max(self.winfo_height() - 80, 540)))
                self._img = ImageTk.PhotoImage(im)
                t.image_create("1.0", image=self._img)
                self.status.configure(text=_("Imagen {w} x {h}").format(w=size[0], h=size[1]))
            except Exception:
                self.mode.set("text")
                return self.render()
        elif mode == "hex":
            lines = []
            d = self.data[:2 << 20]
            for off in range(0, len(d), 16):
                chunk = d[off:off + 16]
                hx = " ".join(f"{b:02X}" for b in chunk)
                asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{off:08X}: {hx:<48}  {asc}")
            t.insert("1.0", "\n".join(lines))
            self.status.configure(text=_("Hexadecimal - {size} bytes").format(
                size=fmt_size(len(self.data))))
        else:
            s = self.decoded()
            if "\0" in s[:4096]:
                s = "".join(c if c.isprintable() or c in "\r\n\t" else "." for c in s)
            t.insert("1.0", s)
            self.status.configure(text=_("Texto - {size} bytes").format(
                size=fmt_size(len(self.data))))
        t.configure(state="disabled")

    def copy_sel(self):
        try:
            s = self.text.get("sel.first", "sel.last")
            self.clipboard_clear()
            self.clipboard_append(s)
        except tk.TclError:
            pass

    def find(self):
        s = ask_string(self, _("Buscar"), _("Buscar texto:"), self.last_find)
        if s:
            self.last_find = s
            self.text.mark_set("insert", "1.0")
            self.find_next()

    def find_next(self):
        if not self.last_find:
            return self.find()
        t = self.text
        t.tag_remove("found", "1.0", "end")
        pos = t.search(self.last_find, "insert", "end", nocase=True)
        if not pos:
            info(self, _("Buscar"), _("No se encontró \"{text}\".").format(text=self.last_find))
            return
        end = f"{pos}+{len(self.last_find)}c"
        t.tag_add("found", pos, end)
        t.mark_set("insert", end)
        t.see(pos)


IMG = {"png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "tif", "tiff"}


# ===================================================================== búsqueda (Alt+F7)

class SearchDialog(tk.Toplevel):
    def __init__(self, app, start):
        super().__init__(app.root)
        self.app = app
        self.title(_("Buscar archivos"))
        self.geometry("720x520")
        self.transient(app.root)
        self.q = queue.Queue()
        self.running = False
        self.results = []
        f = tk.Frame(self)
        f.pack(fill="x", padx=10, pady=8)
        f.columnconfigure(1, weight=1)
        self.mask = tk.StringVar(value="*.*")
        self.where = tk.StringVar(value=start)
        self.text = tk.StringVar()
        self.use_text = tk.BooleanVar(value=False)
        self.case = tk.BooleanVar(value=False)
        self.subdirs = tk.BooleanVar(value=True)
        tk.Label(f, text=_("Buscar archivos:"), font=UI_FONT).grid(row=0, column=0, sticky="w")
        e = ttk.Entry(f, textvariable=self.mask, font=UI_FONT)
        e.grid(row=0, column=1, sticky="ew", pady=2)
        tk.Label(f, text=_("Buscar en:"), font=UI_FONT).grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.where, font=UI_FONT).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Button(f, text=">>", width=3, command=self.browse).grid(row=1, column=2, padx=2)
        ttk.Checkbutton(f, text=_("Buscar texto:"), variable=self.use_text).grid(row=2, column=0,
                                                                           sticky="w")
        ttk.Entry(f, textvariable=self.text, font=UI_FONT).grid(row=2, column=1, sticky="ew", pady=2)
        opt = tk.Frame(f)
        opt.grid(row=3, column=1, sticky="w")
        ttk.Checkbutton(opt, text=_("Incluir subcarpetas"), variable=self.subdirs).pack(side="left")
        ttk.Checkbutton(opt, text=_("Distinguir mayúsculas"), variable=self.case).pack(side="left",
                                                                                   padx=10)
        bf = tk.Frame(f)
        bf.grid(row=0, column=3, rowspan=4, sticky="n", padx=(8, 0))
        self.btn = ttk.Button(bf, text=_("Iniciar búsqueda"), command=self.toggle)
        self.btn.pack(fill="x", pady=1)
        ttk.Button(bf, text=_("Ir al archivo"), command=self.goto).pack(fill="x", pady=1)
        ttk.Button(bf, text=_("Ver (F3)"), command=self.view).pack(fill="x", pady=1)
        ttk.Button(bf, text=_("Cerrar"), command=self.close).pack(fill="x", pady=1)
        tk.Label(self, text=_("Resultados:"), font=UI_FONT, anchor="w").pack(fill="x", padx=10)
        lf = tk.Frame(self)
        lf.pack(fill="both", expand=True, padx=10)
        self.lb = tk.Listbox(lf, font=UI_FONT, activestyle="dotbox")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(fill="both", expand=True)
        self.lb.bind("<Double-Button-1>", lambda e: self.goto())
        self.lb.bind("<Return>", lambda e: self.goto())
        self.lb.bind("<F3>", lambda e: self.view())
        self.status = tk.Label(self, anchor="w", font=UI_FONT, relief="sunken", bd=1)
        self.status.pack(fill="x", side="bottom")
        self.bind("<Escape>", lambda e: self.close())
        e.bind("<Return>", lambda ev: self.toggle())
        e.focus_set()
        e.selection_range(0, "end")
        self.protocol("WM_DELETE_WINDOW", self.close)

    def browse(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(parent=self, initialdir=self.where.get())
        if d:
            self.where.set(os.path.normpath(d))

    def close(self):
        self.running = False
        self.destroy()

    def toggle(self):
        if self.running:
            self.running = False
            return
        self.lb.delete(0, "end")
        self.results = []
        self.running = True
        self.btn.configure(text=_("Detener"))
        pats = [p.strip() for p in self.mask.get().replace(",", ";").split(";") if p.strip()] or ["*"]
        pats = ["*" if p == "*.*" else p for p in pats]
        text = self.text.get() if self.use_text.get() else ""
        threading.Thread(target=self._worker,
                         args=(self.where.get(), pats, text, self.case.get(), self.subdirs.get()),
                         daemon=True).start()
        self.after(60, self._poll)

    def _worker(self, root, pats, text, case, subdirs):
        needle = text.encode("utf-8") if case else text.lower().encode("utf-8")
        needle2 = text.encode("cp1252", "ignore") if case else text.lower().encode("cp1252", "ignore")
        n = 0
        for dirpath, dirs, files in os.walk(root):
            if not self.running:
                break
            self.q.put(("dir", dirpath))
            names = [(d, True) for d in dirs] + [(f, False) for f in files]
            for name, is_dir in names:
                if not any(fnmatch.fnmatch(name.lower(), p.lower()) for p in pats):
                    continue
                full = os.path.join(dirpath, name)
                if text:
                    if is_dir or not self._contains(full, needle, needle2, case):
                        continue
                n += 1
                self.q.put(("hit", full + ("\\" if is_dir else "")))
            if not subdirs:
                break
        self.q.put(("end", n))

    def _contains(self, path, n1, n2, case):
        try:
            with open(path, "rb") as f:
                prev = b""
                while self.running:
                    buf = f.read(1 << 20)
                    if not buf:
                        return False
                    chunk = prev + buf
                    hay = chunk if case else chunk.lower()
                    if n1 in hay or (n2 and n2 in hay):
                        return True
                    prev = buf[-len(n1):]
        except OSError:
            return False
        return False

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            for _i in range(500):
                kind, val = self.q.get_nowait()
                if kind == "hit":
                    self.results.append(val)
                    self.lb.insert("end", val)
                elif kind == "dir":
                    self.status.configure(text=val)
                elif kind == "end":
                    self.running = False
                    self.btn.configure(text=_("Iniciar búsqueda"))
                    self.status.configure(text=_("Búsqueda terminada: {n} encontrado(s)").format(n=val))
                    return
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def _current(self):
        sel = self.lb.curselection()
        return self.results[sel[0]] if sel else None

    def goto(self):
        p = self._current()
        if p:
            p = p.rstrip("\\")
            self.app.goto_file(p)

    def view(self):
        p = self._current()
        if p and os.path.isfile(p):
            with open(p, "rb") as f:
                data = f.read(Viewer.MAX)
            Viewer(self, p, data, vfs.split_ext(os.path.basename(p))[1])


# ===================================================================== renombrado múltiple

def _case_modes():
    """[(clave interna, texto mostrado)] de las opciones de mayúsculas."""
    return (("none", _("Sin cambios")), ("lower", _("minúsculas")), ("upper", _("MAYÚSCULAS")),
            ("first", _("Primera mayúscula")), ("title", _("Cada Palabra")))


class MultiRename(tk.Toplevel):
    def __init__(self, app, panel, entries):
        super().__init__(app.root)
        self.app = app
        self.panel = panel
        self.entries = entries
        self.title(_("Herramienta de cambio de nombre múltiple"))
        self.geometry("820x520")
        self.transient(app.root)
        top = tk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        self.case_modes = _case_modes()
        self.v = {k: tk.StringVar(value=d) for k, d in (
            ("name", "[N]"), ("ext", "[E]"), ("find", ""), ("repl", ""),
            ("case", self.case_modes[0][1]), ("start", "1"), ("step", "1"), ("digits", "1"))}
        self.regex = tk.BooleanVar(value=False)

        g1 = ttk.LabelFrame(top, text=_("Máscara de nombre"))
        g1.pack(side="left", fill="y", padx=(0, 6))
        ttk.Entry(g1, textvariable=self.v["name"], width=22).pack(padx=6, pady=3)
        bf = tk.Frame(g1)
        bf.pack(padx=4)
        for t in ("[N]", "[N1-3]", "[C]", "[YMD]", "[hms]"):
            ttk.Button(bf, text=t, width=6,
                       command=lambda t=t: self._ins("name", t)).pack(side="left")
        g2 = ttk.LabelFrame(top, text=_("Extensión"))
        g2.pack(side="left", fill="y", padx=(0, 6))
        ttk.Entry(g2, textvariable=self.v["ext"], width=10).pack(padx=6, pady=3)
        g3 = ttk.LabelFrame(top, text=_("Buscar y reemplazar"))
        g3.pack(side="left", fill="y", padx=(0, 6))
        tk.Label(g3, text=_("Buscar:"), font=UI_FONT).grid(row=0, column=0, sticky="w", padx=4)
        ttk.Entry(g3, textvariable=self.v["find"], width=16).grid(row=0, column=1, padx=4, pady=1)
        tk.Label(g3, text=_("Reemplazar:"), font=UI_FONT).grid(row=1, column=0, sticky="w", padx=4)
        ttk.Entry(g3, textvariable=self.v["repl"], width=16).grid(row=1, column=1, padx=4, pady=1)
        ttk.Checkbutton(g3, text=_("Expresión regular"), variable=self.regex,
                        command=self.preview).grid(row=2, column=1, sticky="w")
        g4 = ttk.LabelFrame(top, text=_("Mayúsculas / contador"))
        g4.pack(side="left", fill="y")
        ttk.Combobox(g4, textvariable=self.v["case"], state="readonly", width=16,
                     values=[lab for _k, lab in self.case_modes]).grid(
            row=0, column=0, columnspan=3, padx=4, pady=2)
        for i, (k, lab) in enumerate((("start", _("Inicio")), ("step", _("Paso")),
                                         ("digits", _("Díg.")))):
            tk.Label(g4, text=lab, font=("Segoe UI", 8)).grid(row=1, column=i)
            ttk.Entry(g4, textvariable=self.v[k], width=5).grid(row=2, column=i, padx=2, pady=(0, 4))

        lf = tk.Frame(self)
        lf.pack(fill="both", expand=True, padx=8)
        self.tv = ttk.Treeview(lf, columns=("new",), show="headings")
        self.tv.heading("new", text=_("Nombre nuevo"))
        self.tv["columns"] = ("old", "new")
        self.tv.heading("old", text=_("Nombre anterior"))
        self.tv.heading("new", text=_("Nombre nuevo"))
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        bot = tk.Frame(self)
        bot.pack(fill="x", padx=8, pady=8)
        ttk.Button(bot, text=_("Cerrar"), command=self.destroy).pack(side="right")
        ttk.Button(bot, text=_("¡Comenzar!"), command=self.execute).pack(side="right", padx=6)
        self.msg = tk.Label(bot, font=UI_FONT, anchor="w")
        self.msg.pack(side="left", fill="x")
        for var in self.v.values():
            var.trace_add("write", lambda *a: self.preview())
        self.bind("<Escape>", lambda e: self.destroy())
        self.preview()

    def _ins(self, key, t):
        self.v[key].set(self.v[key].get() + t)

    def _num(self, key, default):
        try:
            return int(self.v[key].get())
        except ValueError:
            return default

    def new_name(self, e, i):
        stem, ext = (e.name, "") if e.is_dir else vfs.split_ext(e.name)
        lt = time.localtime(e.mtime)
        counter = str(self._num("start", 1) + i * self._num("step", 1)).zfill(self._num("digits", 1))

        def expand(mask):
            def rep(m):
                tok = m.group(1)
                if tok == "N":
                    return stem
                if tok == "E":
                    return ext
                if tok == "C":
                    return counter
                if tok == "YMD":
                    return time.strftime("%Y%m%d", lt)
                if tok == "hms":
                    return time.strftime("%H%M%S", lt)
                mm = re.fullmatch(r"N(\d+)(?:-(\d*))?", tok)
                if mm:
                    a = int(mm.group(1)) - 1
                    b = mm.group(2)
                    if b is None:
                        return stem[a:a + 1]
                    return stem[a:int(b)] if b else stem[a:]
                return m.group(0)
            return re.sub(r"\[([^\]]+)\]", rep, mask)

        new_stem = expand(self.v["name"].get())
        new_ext = expand(self.v["ext"].get())
        name = new_stem + ("." + new_ext if new_ext else "")
        f, r = self.v["find"].get(), self.v["repl"].get()
        if f:
            if self.regex.get():
                try:
                    name = re.sub(f, r, name)
                except re.error:
                    pass
            else:
                name = re.sub(re.escape(f), lambda _m: r, name, flags=re.I)
        c = {lab: k for k, lab in self.case_modes}.get(self.v["case"].get(), "none")
        if c == "lower":
            name = name.lower()
        elif c == "upper":
            name = name.upper()
        elif c == "first":
            name = name[:1].upper() + name[1:].lower()
        elif c == "title":
            name = name.title()
        return name

    def preview(self):
        self.tv.delete(*self.tv.get_children())
        for i, e in enumerate(self.entries):
            self.tv.insert("", "end", values=(e.name, self.new_name(e, i)))

    def execute(self):
        folder = os.path.dirname(self.entries[0].full) if self.entries else ""
        plan = [(e, self.new_name(e, i)) for i, e in enumerate(self.entries)]
        news = [n.lower() for _e, n in plan]
        if len(set(news)) != len(news):
            info(self, _("Renombrar"), _("Hay nombres nuevos repetidos. Agregue un contador [C]."))
            return
        ok = 0
        tmp = []
        for e, new in plan:  # dos pasos para permitir intercambios de nombre
            if new == e.name:
                continue
            t = os.path.join(folder, f".~ren{len(tmp)}_{os.getpid()}")
            try:
                os.rename(e.full, t)
                tmp.append((t, os.path.join(folder, new), e))
            except OSError as ex:
                info(self, _("Error"), f"{e.name}\n{ex}")
        for t, dst, e in tmp:
            try:
                os.rename(t, dst)
                ok += 1
            except OSError as ex:
                os.rename(t, e.full)
                info(self, _("Error"), f"{e.name}\n{ex}")
        self.panel.reload()
        self.app.set_status(_("{n} archivo(s) renombrado(s).").format(n=ok))
        self.destroy()


# ===================================================================== atributos

def _parse_date(s):
    """Fecha y hora escrita en el formato del idioma activo (o el español)."""
    for fmt in (i18n.date_format(), "%d/%m/%Y %H:%M"):
        try:
            return time.mktime(time.strptime(s, fmt))
        except ValueError:
            pass
    raise ValueError(s)


def change_attributes(app, entries):
    d = Modal(app.root, _("Cambiar atributos"))
    first = vfs.get_attrs(entries[0].full)
    # "Archivo" también es el menú Archivo: el atributo se traduce con su propio contexto
    archive = _c("atributo", "Archivo")
    bits = [(_("Solo lectura"), vfs.FILE_ATTRIBUTE_READONLY), (archive, vfs.FILE_ATTRIBUTE_ARCHIVE),
            (_("Oculto"), vfs.FILE_ATTRIBUTE_HIDDEN), (_("Sistema"), vfs.FILE_ATTRIBUTE_SYSTEM)]
    tk.Label(d, text=_("{n} elemento(s) seleccionado(s)").format(n=len(entries)), font=UI_FONT).pack(
        anchor="w", padx=12, pady=(10, 4))
    vars_ = []
    g = ttk.LabelFrame(d, text=_("Atributos"))
    g.pack(fill="x", padx=12)
    for lab, b in bits:
        v = tk.BooleanVar(value=bool(first & b))
        ttk.Checkbutton(g, text=lab, variable=v).pack(anchor="w", padx=8)
        vars_.append((v, b))
    dt = tk.BooleanVar(value=False)
    dv = tk.StringVar(value=fmt_date(entries[0].mtime))
    g2 = ttk.LabelFrame(d, text=_("Fecha y hora"))
    g2.pack(fill="x", padx=12, pady=6)
    ttk.Checkbutton(g2, text=_("Cambiar fecha/hora:"), variable=dt).pack(side="left", padx=6)
    ttk.Entry(g2, textvariable=dv, width=18).pack(side="left", padx=6, pady=4)
    ttk.Button(g2, text=_("Ahora"), command=lambda: (dt.set(True), dv.set(fmt_date(time.time())))).pack(
        side="left")
    sub = tk.BooleanVar(value=False)
    ttk.Checkbutton(d, text=_("Incluir contenido de las carpetas"), variable=sub).pack(anchor="w",
                                                                                  padx=12)
    d.buttons(d, [(_("Aceptar"), lambda: d.ok(True)), (_("Cancelar"), d.cancel)]).pack(pady=10)
    if not d.run():
        return
    stamp = None
    if dt.get():
        try:
            stamp = _parse_date(dv.get().strip())
        except ValueError:
            info(app.root, _("Error"), _("Fecha inválida. Use el formato dd/mm/aaaa hh:mm."))
            return

    def apply(path):
        a = vfs.get_attrs(path)
        for v, b in vars_:
            a = (a | b) if v.get() else (a & ~b)
        if stamp is not None:
            try:
                os.utime(path, (stamp, stamp))
            except OSError:
                pass
        vfs.set_attrs(path, a & 0xFFFF)

    for e in entries:
        paths = [e.full]
        if e.is_dir and sub.get():
            for root, ds, fs in os.walk(e.full):
                paths += [os.path.join(root, x) for x in ds + fs]
        for p in paths:
            apply(p)


# ===================================================================== opciones

def options_dialog(app):
    cfg = app.cfg
    d = Modal(app.root, _("Configuración"))
    nb = ttk.Notebook(d)
    nb.pack(fill="both", expand=True, padx=10, pady=10)
    p1 = tk.Frame(nb)
    nb.add(p1, text=_("Pantalla"))
    p2 = tk.Frame(nb)
    nb.add(p2, text=_("Operación"))
    vs = {}
    for parent, key, lab in ((p1, "show_hidden", _("Mostrar archivos ocultos y de sistema")),
                             (p1, "show_toolbar", _("Mostrar barra de herramientas")),
                             (p1, "show_cmdline", _("Mostrar línea de comandos")),
                             (p1, "show_fkeys", _("Mostrar botones de teclas de función")),
                             (p2, "recycle", _("Eliminar enviando a la papelera de reciclaje")),
                             (p2, "confirm_delete", _("Pedir confirmación al eliminar")),
                             (p2, "save_on_exit", _("Guardar posición y carpetas al salir"))):
        vs[key] = tk.BooleanVar(value=cfg.get(key, True))
        ttk.Checkbutton(parent, text=lab, variable=vs[key]).pack(anchor="w", padx=10, pady=3)
    tk.Label(p1, text=_("Tamaño de letra de las listas:"), font=UI_FONT).pack(anchor="w", padx=10,
                                                                           pady=(8, 0))
    fs = tk.IntVar(value=cfg.get("font_size", 9))
    ttk.Spinbox(p1, from_=7, to=16, textvariable=fs, width=5).pack(anchor="w", padx=10)
    # idioma: cada uno se muestra con su propio nombre (Español, English, ...)
    tk.Label(p1, text=_("Idioma:"), font=UI_FONT).pack(anchor="w", padx=10, pady=(8, 0))
    langs = [("auto", _("Automático (idioma de Windows)"))] + i18n.available()
    lang_labels = [name for _code, name in langs]
    cur = cfg.get("language", "auto")
    lang_var = tk.StringVar(value=next((n for c, n in langs if c == cur), lang_labels[0]))
    ttk.Combobox(p1, textvariable=lang_var, values=lang_labels, state="readonly",
                 width=30).pack(anchor="w", padx=10, pady=(0, 8))
    tk.Label(p2, text=_("Editor (F4):"), font=UI_FONT).pack(anchor="w", padx=10, pady=(8, 0))
    ed = tk.StringVar(value=cfg.get("editor", "notepad.exe"))
    ttk.Entry(p2, textvariable=ed, width=40).pack(anchor="w", padx=10, pady=(0, 8))
    d.buttons(d, [(_("Aceptar"), lambda: d.ok(True)), (_("Cancelar"), d.cancel)]).pack(pady=(0, 10))
    if d.run():
        for k, v in vs.items():
            cfg[k] = v.get()
        try:
            cfg["font_size"] = int(fs.get())
        except (ValueError, tk.TclError):
            pass
        cfg["editor"] = ed.get().strip() or "notepad.exe"
        choice = lang_var.get()
        cfg["language"] = langs[lang_labels.index(choice)][0] if choice in lang_labels else "auto"
        return True
    return False


def compare_files(master, a, b):
    if os.path.getsize(a) != os.path.getsize(b):
        info(master, _("Comparar por contenido"),
             _("Los archivos son distintos (tamaños {a} y {b} bytes).").format(
                 a=fmt_size(os.path.getsize(a)), b=fmt_size(os.path.getsize(b))))
        return
    off = 0
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                for i, (c1, c2) in enumerate(zip(x, y)):
                    if c1 != c2:
                        info(master, _("Comparar por contenido"),
                             _("Los archivos son distintos.\nPrimera diferencia en el byte "
                               "{pos}.").format(pos=fmt_size(off + i)))
                        return
            if not x:
                break
            off += len(x)
    info(master, _("Comparar por contenido"), _("Los archivos son idénticos."))
