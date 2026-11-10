"""Sincronizar carpetas: compara dos carpetas y copia lo que falta o es más nuevo."""
import os
import shutil
import tkinter as tk
from tkinter import ttk

from . import vfs
from .dialogs import UI_FONT, confirm, info
from .i18n import _
from .vfs import fmt_size, fmt_date

ARROWS = {"->": "→", "<-": "←", "=": "=", "!=": "≠"}


class SyncDialog(tk.Toplevel):
    def __init__(self, app, left, right):
        super().__init__(app.root)
        self.app = app
        self.title(_("Sincronizar carpetas"))
        self.geometry("900x560")
        self.transient(app.root)
        self.rows = []
        top = tk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(4, weight=1)
        self.lp = tk.StringVar(value=left)
        self.rp = tk.StringVar(value=right)
        self.subdirs = tk.BooleanVar(value=True)
        self.by_content = tk.BooleanVar(value=False)
        self.asym = tk.BooleanVar(value=False)
        self.show = {k: tk.BooleanVar(value=k != "=") for k in ("->", "<-", "=", "!=")}
        tk.Label(top, text=_("Izquierda:"), font=UI_FONT).grid(row=0, column=0)
        ttk.Entry(top, textvariable=self.lp).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(top, text=_("Comparar"), command=self.compare).grid(row=0, column=2, padx=6)
        tk.Label(top, text=_("Derecha:"), font=UI_FONT).grid(row=0, column=3)
        ttk.Entry(top, textvariable=self.rp).grid(row=0, column=4, sticky="ew", padx=4)
        opt = tk.Frame(self)
        opt.pack(fill="x", padx=8)
        ttk.Checkbutton(opt, text=_("Subcarpetas"), variable=self.subdirs).pack(side="left")
        ttk.Checkbutton(opt, text=_("Por contenido"), variable=self.by_content).pack(side="left", padx=8)
        ttk.Checkbutton(opt, text=_("Asimétrico (izquierda → derecha)"), variable=self.asym).pack(
            side="left")
        tk.Label(opt, text="   " + _("Mostrar:"), font=UI_FONT).pack(side="left")
        for k in ("->", "=", "!=", "<-"):
            ttk.Checkbutton(opt, text=ARROWS[k], variable=self.show[k], command=self.fill).pack(
                side="left")
        lf = tk.Frame(self)
        lf.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("lsize", "ldate", "dir", "rdate", "rsize")
        self.tv = ttk.Treeview(lf, columns=cols, show="tree headings")
        self.tv.heading("#0", text=_("Nombre"))
        for c, t, w in (("lsize", _("Tamaño"), 80), ("ldate", _("Fecha"), 115), ("dir", "", 34),
                        ("rdate", _("Fecha"), 115), ("rsize", _("Tamaño"), 80)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, stretch=False, anchor="center" if c == "dir" else "e")
        self.tv.column("#0", width=300)
        self.tv.tag_configure("->", foreground="#007000")
        self.tv.tag_configure("<-", foreground="#0040c0")
        self.tv.tag_configure("!=", foreground="#c00000")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<Double-Button-1>", self.flip)
        bot = tk.Frame(self)
        bot.pack(fill="x", padx=8, pady=8)
        ttk.Button(bot, text=_("Cerrar"), command=self.destroy).pack(side="right")
        ttk.Button(bot, text=_("Sincronizar..."), command=self.synchronize).pack(side="right", padx=6)
        self.msg = tk.Label(bot, font=UI_FONT, anchor="w",
                            text=_("Pulse Comparar. Doble clic en una fila cambia la dirección."))
        self.msg.pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())

    def _scan(self, root):
        out = {}
        if not os.path.isdir(root):
            return out
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                rel = os.path.relpath(p, root)
                out[rel.lower()] = (rel, st.st_size, st.st_mtime)
            if not self.subdirs.get():
                break
        return out

    @staticmethod
    def _same_content(a, b):
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x, y = fa.read(1 << 20), fb.read(1 << 20)
                if x != y:
                    return False
                if not x:
                    return True

    def compare(self):
        lroot, rroot = self.lp.get(), self.rp.get()
        L, R = self._scan(lroot), self._scan(rroot)
        self.rows = []
        for key in sorted(set(L) | set(R)):
            l, r = L.get(key), R.get(key)
            if l and not r:
                d = "->"
            elif r and not l:
                d = None if self.asym.get() else "<-"
            elif l[1] == r[1] and abs(l[2] - r[2]) <= 2:
                d = "="
            elif l[1] == r[1] and self.by_content.get() and self._same_content(
                    os.path.join(lroot, l[0]), os.path.join(rroot, r[0])):
                d = "="
            elif l[2] > r[2] + 2:
                d = "->"
            elif r[2] > l[2] + 2 and not self.asym.get():
                d = "<-"
            else:
                d = "!="
            if d:
                self.rows.append([(l or r)[0], l, r, d])
        self.fill()

    def fill(self):
        self.tv.delete(*self.tv.get_children())
        n = dict.fromkeys(ARROWS, 0)
        for i, (name, l, r, d) in enumerate(self.rows):
            n[d] += 1
            if not self.show[d].get():
                continue
            self.tv.insert("", "end", iid=str(i), text=name, tags=(d,), values=(
                fmt_size(l[1]) if l else "", fmt_date(l[2]) if l else "", ARROWS[d],
                fmt_date(r[2]) if r else "", fmt_size(r[1]) if r else ""))
        self.msg.configure(text=_("→ {right}   ← {left}   ≠ {diff}   = {same}    "
                                  "(doble clic cambia la dirección)").format(
            right=n["->"], left=n["<-"], diff=n["!="], same=n["="]))

    def flip(self, ev):
        iid = self.tv.identify_row(ev.y)
        if not iid:
            return
        row = self.rows[int(iid)]
        order = [d for d in ("->", "<-", "!=") if (d != "->" or row[1]) and (d != "<-" or row[2])]
        row[3] = order[(order.index(row[3]) + 1) % len(order)] if row[3] in order else order[0]
        self.fill()

    def synchronize(self, ask=True):
        todo = [r for r in self.rows if r[3] in ("->", "<-")]
        if not todo:
            info(self, _("Sincronizar"), _("No hay nada para copiar."))
            return
        lr = sum(1 for r in todo if r[3] == "->")
        if ask and not confirm(self, _("Sincronizar"),
                               _("Se copiarán {lr} archivo(s) de izquierda a derecha y "
                                 "{rl} de derecha a izquierda.\n¿Continuar?").format(
                                   lr=lr, rl=len(todo) - lr)):
            return
        lroot, rroot = self.lp.get(), self.rp.get()
        errors = 0
        for name, _l, _r, d in todo:
            src, dst = (lroot, rroot) if d == "->" else (rroot, lroot)
            s, t = os.path.join(src, name), os.path.join(dst, name)
            try:
                os.makedirs(os.path.dirname(t), exist_ok=True)
                if os.path.exists(t):
                    vfs.set_attrs(t, vfs.get_attrs(t) & ~vfs.FILE_ATTRIBUTE_READONLY)
                shutil.copy2(s, t)
            except OSError:
                errors += 1
            self.update()
        self.app.refresh()
        self.compare()
        copied = len(todo) - errors
        if errors:
            msg = _("Sincronización terminada. {n} copiado(s), {errors} error(es).").format(
                n=copied, errors=errors)
        else:
            msg = _("Sincronización terminada. {n} copiado(s).").format(n=copied)
        info(self, _("Sincronizar"), msg)
