"""Ventanas de conexión a servidores: lista de conexiones guardadas, edición y conexión rápida."""
import tkinter as tk
from tkinter import filedialog, ttk
from urllib.parse import unquote, urlsplit

from . import remote
from .dialogs import UI_FONT, Modal, confirm
from .i18n import _

PROTO_KEYS = list(remote.PROTOCOLS)


def proto_labels():
    """Nombres visibles de PROTO_KEYS (mismo orden), en el idioma activo."""
    return [remote.protocol_label(k) for k in PROTO_KEYS]


def parse_url(s):
    """'ftp://usuario:clave@servidor:21/carpeta' -> dict de conexión (o None)."""
    s = s.strip()
    if "://" not in s:
        s = "ftp://" + s
    u = urlsplit(s)
    proto = u.scheme.lower()
    if proto not in remote.PROTOCOLS or not u.hostname:
        return None
    try:
        port = u.port
    except ValueError:
        return None
    return {"name": "", "protocol": proto, "host": u.hostname,
            "port": port or remote.DEFAULT_PORTS[proto],
            "user": unquote(u.username or ""), "password": unquote(u.password or ""),
            "remote_dir": unquote(u.path or ""), "passive": True, "encoding": "utf-8"}


def ask_password(master, title, prompt):
    d = Modal(master, title)
    tk.Label(d, text=prompt, font=UI_FONT, anchor="w", justify="left").pack(
        fill="x", padx=10, pady=(10, 2))
    var = tk.StringVar()
    ent = ttk.Entry(d, textvariable=var, show="•", width=40, font=UI_FONT)
    ent.pack(fill="x", padx=10)
    ent.focus_set()
    ent.bind("<Return>", lambda e: d.ok(var.get()))
    d.buttons(d, [(_("Aceptar"), lambda: d.ok(var.get())), (_("Cancelar"), d.cancel)]).pack(pady=10)
    return d.run()


def host_key_asker(master):
    def ask(host, keytype, fingerprint):
        return confirm(master, _("Servidor desconocido"),
                       _("Es la primera vez que se conecta a {host}.\n\n"
                         "Huella de la clave ({keytype}):\n{fingerprint}\n\n"
                         "Si no está seguro de que sea el servidor correcto, elija No.\n"
                         "¿Confiar en este servidor y recordarlo?").format(
                           host=host, keytype=keytype, fingerprint=fingerprint),
                       yes=_("Confiar"), no=_("No"))
    return ask


def edit_connection(master, conf=None):
    """Formulario de una conexión. Devuelve el dict editado o None."""
    conf = dict(conf or {"protocol": "ftp", "passive": True, "encoding": "utf-8"})
    d = Modal(master, _("Conexión a servidor"))
    fr = tk.Frame(d)
    fr.pack(padx=12, pady=10, fill="both")
    vars_ = {}
    row = [0]

    def add(label, widget):
        tk.Label(fr, text=label, font=UI_FONT, anchor="w").grid(row=row[0], column=0, sticky="w",
                                                                 pady=2, padx=(0, 8))
        widget.grid(row=row[0], column=1, sticky="ew", pady=2)
        row[0] += 1

    def entry(key, label, width=40, show=None, value=None):
        v = tk.StringVar(value=value if value is not None else str(conf.get(key) or ""))
        vars_[key] = v
        e = ttk.Entry(fr, textvariable=v, width=width, font=UI_FONT, show=show or "")
        add(label, e)
        return e

    first = entry("name", _("Nombre de la conexión:"))
    labels = proto_labels()
    proto = tk.StringVar(value=remote.protocol_label(conf.get("protocol", "ftp")))
    cb = ttk.Combobox(fr, textvariable=proto, values=labels, state="readonly", font=UI_FONT)
    add(_("Tipo:"), cb)
    entry("host", _("Servidor:"))
    port_e = entry("port", _("Puerto:"), 8)
    port_e.grid_configure(sticky="w")
    entry("user", _("Usuario:"))
    has_saved = bool(conf.get("password_enc"))
    pw_e = entry("password", _("Contraseña:"), show="•",
                 value=remote.unprotect_password(conf.get("password_enc", "")) if has_saved else "")
    save_pw = tk.BooleanVar(value=has_saved)
    ttk.Checkbutton(fr, text=_("Guardar la contraseña (cifrada para este usuario de Windows)"),
                    variable=save_pw).grid(row=row[0], column=1, sticky="w")
    row[0] += 1
    key_fr = tk.Frame(fr)
    key_v = tk.StringVar(value=conf.get("keyfile") or "")
    vars_["keyfile"] = key_v
    ttk.Entry(key_fr, textvariable=key_v, width=34, font=UI_FONT).pack(side="left", fill="x",
                                                                      expand=True)
    ttk.Button(key_fr, text="...", width=3, command=lambda: key_v.set(
        filedialog.askopenfilename(parent=d, title=_("Clave privada")) or key_v.get())).pack(
        side="left", padx=(2, 0))
    add(_("Clave privada (SFTP):"), key_fr)
    entry("remote_dir", _("Carpeta remota inicial:"))
    passive = tk.BooleanVar(value=conf.get("passive", True))
    ttk.Checkbutton(fr, text=_("Modo pasivo (recomendado para FTP)"), variable=passive).grid(
        row=row[0], column=1, sticky="w")
    row[0] += 1
    enc = tk.StringVar(value=conf.get("encoding") or "utf-8")
    add(_("Codificación de nombres:"), ttk.Combobox(fr, textvariable=enc, values=["utf-8", "latin-1"],
                                                  width=10, font=UI_FONT))
    fr.columnconfigure(1, weight=1)

    def proto_key():
        return PROTO_KEYS[labels.index(proto.get())] if proto.get() in labels else "ftp"

    def on_proto(_e=None):
        k = proto_key()
        if vars_["port"].get() in ("", "21", "22"):
            vars_["port"].set(str(remote.DEFAULT_PORTS[k]))
    cb.bind("<<ComboboxSelected>>", on_proto)
    if not vars_["port"].get():
        on_proto()

    def accept():
        host = vars_["host"].get().strip()
        if not host:
            return
        k = proto_key()
        out = dict(conf)
        out.update({key: v.get().strip() for key, v in vars_.items() if key != "password"})
        out["protocol"] = k
        out["host"] = host
        try:
            out["port"] = int(out.get("port") or remote.DEFAULT_PORTS[k])
        except ValueError:
            out["port"] = remote.DEFAULT_PORTS[k]
        out["passive"] = passive.get()
        out["encoding"] = enc.get().strip() or "utf-8"
        out["name"] = out.get("name") or f"{out['user'] + '@' if out.get('user') else ''}{host}"
        pw = vars_["password"].get()
        out.pop("password", None)
        out["password_enc"] = remote.protect_password(pw) if save_pw.get() and pw else ""
        out["_password"] = pw  # solo para conectar ahora; no se guarda
        d.ok(out)

    d.buttons(d, [(_("Aceptar"), accept), (_("Cancelar"), d.cancel)]).pack(pady=(0, 10))
    d.bind("<Return>", lambda e: accept())
    first.focus_set()
    return d.run()


class ConnectionManager:
    """Lista de conexiones guardadas. run() devuelve el dict elegido para conectar o None."""

    def __init__(self, app):
        self.app = app
        self.conns = app.cfg.setdefault("ftp_connections", [])
        d = self.d = Modal(app.root, _("Conectar a servidor FTP / SFTP"))
        body = tk.Frame(d)
        body.pack(padx=10, pady=10, fill="both", expand=True)
        self.lb = tk.Listbox(body, width=42, height=12, font=UI_FONT, activestyle="none",
                             exportselection=False)
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<Double-Button-1>", lambda e: self.connect())
        self.lb.bind("<Return>", lambda e: self.connect())
        btns = tk.Frame(body)
        btns.pack(side="left", fill="y", padx=(8, 0))
        for text, cmd in ((_("Conectar"), self.connect), (_("Nueva conexión..."), self.new),
                          (_("Editar..."), self.edit), (_("Duplicar"), self.duplicate),
                          (_("Eliminar"), self.remove), (_("Cerrar"), d.cancel)):
            ttk.Button(btns, text=text, command=cmd, width=18).pack(pady=2)
        tk.Label(d, text=_("Tipos: FTP, FTP con TLS (FTPS) y SFTP (SSH)."), font=("Segoe UI", 8),
                 fg="#606060").pack(anchor="w", padx=10, pady=(0, 8))
        self.fill()

    def fill(self, sel=0):
        self.lb.delete(0, "end")
        for c in self.conns:
            self.lb.insert("end", f"{c.get('name')}   ({remote.protocol_label(c.get('protocol'))})")
        if self.conns:
            sel = max(0, min(sel, len(self.conns) - 1))
            self.lb.selection_set(sel)
            self.lb.activate(sel)
        self.lb.focus_set()

    def current(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def new(self):
        c = edit_connection(self.d)
        if c:
            c.pop("_password", None)
            self.conns.append(c)
            self.fill(len(self.conns) - 1)

    def edit(self):
        i = self.current()
        if i is None:
            return
        c = edit_connection(self.d, self.conns[i])
        if c:
            c.pop("_password", None)
            self.conns[i] = c
            self.fill(i)

    def duplicate(self):
        i = self.current()
        if i is not None:
            c = dict(self.conns[i])
            c["name"] = c.get("name", "") + _(" (copia)")
            self.conns.insert(i + 1, c)
            self.fill(i + 1)

    def remove(self):
        i = self.current()
        if i is not None and confirm(self.d, _("Eliminar"),
                                     _("¿Eliminar la conexión «{name}»?").format(
                                         name=self.conns[i].get("name"))):
            del self.conns[i]
            self.fill(i)

    def connect(self):
        i = self.current()
        if i is not None:
            c = dict(self.conns[i])
            c["password"] = remote.unprotect_password(c.get("password_enc", ""))
            self.d.ok(c)

    def run(self):
        return self.d.run()
