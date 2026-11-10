"""Copiar, mover, borrar y renombrar entre el disco, archivos ZIP y servidores FTP/SFTP.

Un origen (disco, ZIP o servidor) arma la lista de archivos y carpetas a pasar y sabe
leerlos; un destino sabe si ya existen y cómo escribirlos. Los ZIP se modifican
reescribiéndolos en un temporal que después reemplaza al original, así un corte a
mitad de camino nunca deja el archivo roto.
"""
import os
import posixpath
import shutil
import tempfile
import time
import zipfile

from . import remote, vfs
from .i18n import _
from .remote import Cancelled

ZIP64_LIMIT = (1 << 31) - 1


class Node:
    """Un archivo o carpeta a transferir; rel son las partes de la ruta relativa."""
    __slots__ = ("rel", "is_dir", "size", "mtime", "handle")

    def __init__(self, rel, is_dir, size=0, mtime=0.0, handle=None):
        self.rel, self.is_dir, self.size, self.mtime, self.handle = \
            tuple(rel), is_dir, size, mtime, handle

    @property
    def name(self):
        return "/".join(self.rel)


class Meter:
    """Cuenta bytes, avisa el avance a la ventana de progreso y corta si se canceló."""

    def __init__(self, op, total):
        self.op, self.total, self.done = op, max(total, 1), 0
        self.name = self.target = ""
        self._last = 0

    def label(self, name, target=""):
        self.name, self.target = name, target
        self._send(True)

    def __call__(self, n):
        if self.op.cancelled:
            raise Cancelled()
        self.done += n
        self._send()

    def check(self):
        if self.op.cancelled:
            raise Cancelled()

    def _send(self, force=False):
        now = time.time()
        if force or now - self._last > 0.08:
            self._last = now
            self.op._progress(self.done, self.total, self.name, self.target)


def _pump(fi, fo, cb):
    while True:
        buf = fi.read(1 << 20)
        if not buf:
            break
        fo.write(buf)
        if cb:
            cb(len(buf))


def _zip_time(t):
    t = time.localtime(t or time.time())
    return (max(t.tm_year, 1980),) + tuple(t[1:6])


def zip_mtime(info):
    try:
        return time.mktime(info.date_time + (0, 0, -1)) if info.date_time[0] >= 1980 else 0
    except (OverflowError, ValueError):
        return 0


def _norm(name):
    return name.replace("\\", "/")


# ===================================================================== orígenes

class DiskSource:
    kind = "disk"

    def __init__(self, loc):
        self.loc = loc

    def plan(self, entries):
        for e in entries:
            if not e.is_dir:
                yield Node((e.name,), False, e.size, e.mtime, e.full)
                continue
            yield Node((e.name,), True, 0, e.mtime, e.full)
            for root, ds, fs in os.walk(e.full):
                rel = os.path.relpath(root, e.full)
                parts = (e.name,) + (() if rel == "." else tuple(rel.split(os.sep)))
                for d in ds:
                    yield Node(parts + (d,), True, 0, 0, os.path.join(root, d))
                for f in fs:
                    p = os.path.join(root, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    yield Node(parts + (f,), False, st.st_size, st.st_mtime, p)

    def open(self, node, cb=None):
        return open(node.handle, "rb")

    def copy_to(self, node, fo, cb):
        with open(node.handle, "rb") as fi:
            _pump(fi, fo, cb)

    def delete(self, entries, meter):
        for e in entries:
            meter.check()
            meter.label(e.full)
            delete_disk_entry(e)

    def close(self):
        pass


def delete_disk_entry(e):
    if e.is_dir:
        def onerr(func, path, _exc):
            vfs.set_attrs(path, 0x80)
            func(path)
        shutil.rmtree(e.full, onerror=onerr)
    else:
        if vfs.get_attrs(e.full) & vfs.FILE_ATTRIBUTE_READONLY:
            vfs.set_attrs(e.full, vfs.get_attrs(e.full) & ~vfs.FILE_ATTRIBUTE_READONLY)
        os.remove(e.full)


class ZipSource:
    kind = "zip"

    def __init__(self, loc):
        self.loc = loc
        self.zf = zipfile.ZipFile(loc.archive)

    def plan(self, entries):
        infos = [(_norm(i.filename), i) for i in self.zf.infolist()]
        by_name = dict(infos)
        for e in entries:
            if not e.is_dir:
                info = by_name.get(e.full)
                if info is None:
                    raise RuntimeError(_("No se encontró dentro del ZIP:\n{path}").format(path=e.full))
                yield Node((e.name,), False, info.file_size, zip_mtime(info), info)
                continue
            yield Node((e.name,), True, 0, e.mtime, None)
            seen = set()
            for n, info in infos:
                if not n.startswith(e.full) or n == e.full:
                    continue
                parts = [p for p in n[len(e.full):].split("/") if p]
                for i in range(1, len(parts) + (1 if info.is_dir() else 0)):
                    d = tuple(parts[:i])
                    if d not in seen:
                        seen.add(d)
                        yield Node((e.name,) + d, True, 0, zip_mtime(info), None)
                if not info.is_dir():
                    yield Node((e.name,) + tuple(parts), False, info.file_size,
                               zip_mtime(info), info)

    def member_filter(self, entries):
        """Función que dice si un nombre interno pertenece a lo seleccionado."""
        files = {e.full for e in entries if not e.is_dir}
        dirs = tuple(e.full for e in entries if e.is_dir)
        return lambda n: n in files or (bool(dirs) and n.startswith(dirs))

    def open(self, node, cb=None):
        return self.zf.open(node.handle)

    def copy_to(self, node, fo, cb):
        with self.zf.open(node.handle) as fi:
            _pump(fi, fo, cb)

    def delete(self, entries, meter):
        self.close()
        zip_rewrite(self.loc.archive, meter, drop=self.member_filter(entries))

    def close(self):
        if self.zf is not None:
            self.zf.close()
            self.zf = None


class RemoteSource:
    kind = "remote"

    def __init__(self, loc):
        self.loc = loc
        self.fs = remote.get(loc.remote)

    def plan(self, entries):
        for e in entries:
            if not e.is_dir:
                yield Node((e.name,), False, e.size, e.mtime, e.full)
                continue
            yield Node((e.name,), True, 0, e.mtime, e.full)
            for root, dirs, files in self.fs.walk(e.full):
                rel = posixpath.relpath(root, e.full)
                parts = (e.name,) + (() if rel == "." else tuple(rel.split("/")))
                for d in dirs:
                    yield Node(parts + (d.name,), True, 0, d.mtime, posixpath.join(root, d.name))
                for f in files:
                    yield Node(parts + (f.name,), False, f.size, f.mtime,
                               posixpath.join(root, f.name))

    def open(self, node, cb=None):
        return self.fs.download_temp(node.handle)

    def copy_to(self, node, fo, cb):
        self.fs.download(node.handle, fo, cb)

    def delete(self, entries, meter):
        for e in entries:
            meter.check()
            meter.label(e.full)
            if e.is_dir:
                self.fs.remove_tree(e.full, progress=lambda p: meter.label(p),
                                    cancelled=lambda: meter.op.cancelled)
            else:
                self.fs.remove(e.full)

    def close(self):
        pass


def make_source(loc):
    if loc.remote:
        return RemoteSource(loc)
    if loc.archive:
        return ZipSource(loc)
    return DiskSource(loc)


# ===================================================================== destinos

class _Sink:
    def __init__(self, rename_to, single):
        self.rename_to = rename_to if single else None

    def parts(self, node):
        parts = list(node.rel)
        if self.rename_to:
            parts[0] = self.rename_to
        return parts


class DiskSink(_Sink):
    kind = "disk"

    def __init__(self, dest, rename_to=None, single=False):
        super().__init__(rename_to, single)
        self.dest = dest

    def target(self, node):
        return os.path.join(self.dest, *self.parts(node))

    def exists(self, node):
        return os.path.exists(self.target(node))

    def display(self, node):
        return self.target(node)

    def check(self, src, entries):
        if src.kind != "disk":
            return
        for e in entries:
            if e.is_dir and os.path.normcase(os.path.abspath(self.dest) + os.sep).startswith(
                    os.path.normcase(os.path.abspath(e.full) + os.sep)):
                raise RuntimeError(_("No se puede copiar una carpeta dentro de sí misma:\n{path}")
                                   .format(path=e.full))

    def write_all(self, src, nodes, meter, errors):
        os.makedirs(self.dest, exist_ok=True)
        for n in nodes:
            meter.check()
            t = self.target(n)
            if n.is_dir:
                os.makedirs(t, exist_ok=True)
                continue
            meter.label(n.name, t)
            try:
                os.makedirs(os.path.dirname(t), exist_ok=True)
                if os.path.exists(t):
                    vfs.set_attrs(t, vfs.get_attrs(t) & ~vfs.FILE_ATTRIBUTE_READONLY)
                ok = False
                try:
                    with open(t, "wb") as fo:
                        src.copy_to(n, fo, meter)
                    ok = True
                finally:
                    if not ok:
                        try:
                            os.remove(t)
                        except OSError:
                            pass
                if n.mtime:
                    os.utime(t, (n.mtime, n.mtime))
            except Cancelled:
                raise
            except Exception as ex:
                errors.append(f"{n.name}: {ex}")


class ZipSink(_Sink):
    kind = "zip"

    def __init__(self, loc, rename_to=None, single=False):
        super().__init__(rename_to, single)
        self.loc = loc
        self.archive = loc.archive
        with zipfile.ZipFile(self.archive) as zf:
            self.existing = {_norm(n) for n in zf.namelist()}
        self.drop = None  # al mover dentro del mismo ZIP se quitan los originales aquí

    def arcname(self, node):
        return self.loc.inner + "/".join(self.parts(node)) + ("/" if node.is_dir else "")

    def exists(self, node):
        return self.arcname(node) in self.existing

    def display(self, node):
        return os.path.join(self.archive, self.arcname(node).replace("/", "\\"))

    def check(self, src, entries):
        if src.kind == "zip" and os.path.normcase(src.loc.archive) == os.path.normcase(self.archive):
            for e in entries:
                if e.is_dir and self.loc.inner.startswith(e.full):
                    raise RuntimeError(_("No se puede copiar una carpeta dentro de sí misma:\n{path}")
                                       .format(path=e.full))
                if self.loc.inner == src.loc.inner and not self.rename_to:
                    raise RuntimeError(_("El origen y el destino son la misma carpeta del ZIP."))

    def write_all(self, src, nodes, meter, errors):
        adds = [(self.arcname(n), n) for n in nodes
                if not (n.is_dir and self.arcname(n) in self.existing)]
        zip_rewrite(self.archive, meter, drop=self.drop, adds=adds, src=src,
                    before_replace=src.close if src.kind == "zip" else None)


class RemoteSink(_Sink):
    kind = "remote"

    def __init__(self, loc, rename_to=None, single=False):
        super().__init__(rename_to, single)
        self.loc = loc
        self.fs = remote.get(loc.remote)
        self.base = loc.inner or "/"

    def target(self, node):
        return posixpath.join(self.base, *self.parts(node))

    def exists(self, node):
        return self.fs.stat(self.target(node)) is not None

    def display(self, node):
        return self.fs.url() + self.target(node)

    def check(self, src, entries):
        if src.kind == "remote" and src.fs is self.fs:
            for e in entries:
                if e.is_dir and (self.base + "/").startswith(e.full.rstrip("/") + "/"):
                    raise RuntimeError(_("No se puede copiar una carpeta dentro de sí misma:\n{path}")
                                       .format(path=e.full))
                if self.base == (src.loc.inner or "/") and not self.rename_to:
                    raise RuntimeError(_("El origen y el destino son la misma carpeta del servidor."))

    def write_all(self, src, nodes, meter, errors):
        made = set()
        for n in nodes:
            meter.check()
            t = self.target(n)
            try:
                if n.is_dir:
                    if t not in made:
                        self.fs.makedirs(t)
                        made.add(t)
                    continue
                parent = posixpath.dirname(t)
                if parent not in made:
                    self.fs.makedirs(parent)
                    made.add(parent)
                meter.label(n.name, self.fs.url() + t)
                with src.open(n) as fi:
                    self.fs.upload(fi, t, meter, n.mtime)
            except Cancelled:
                raise
            except Exception as ex:
                errors.append(f"{n.name}: {ex}")


def make_sink(dst, rename_to, single):
    kind, where = dst
    if kind == "zip":
        return ZipSink(where, rename_to, single)
    if kind == "remote":
        return RemoteSink(where, rename_to, single)
    return DiskSink(where, rename_to, single)


# ===================================================================== reescritura de ZIP

def _clone_info(info, name):
    ni = zipfile.ZipInfo(name, info.date_time)
    ni.compress_type = info.compress_type if info.compress_type in (
        zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA) \
        else zipfile.ZIP_DEFLATED
    ni.external_attr = info.external_attr
    ni.create_system = info.create_system
    ni.comment = info.comment
    return ni


def zip_rewrite(archive, meter, drop=None, rename=None, adds=(), src=None, before_replace=None):
    """Reescribe el ZIP: quita lo que drop(nombre) acepte, renombra con rename(nombre)
    y agrega adds [(nombre_interno, Node)] leyendo cada Node desde src."""
    added = {a for a, _src in adds}
    folder = os.path.dirname(os.path.abspath(archive))
    fd, tmp = tempfile.mkstemp(prefix="~zip", suffix=".tmp", dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(archive) as zin:
            keep = []
            for info in zin.infolist():
                n = _norm(info.filename)
                if drop and drop(n):
                    continue
                new = rename(n) if rename else n
                if new in added:
                    continue
                if info.flag_bits & 0x1:
                    raise RuntimeError(_("El ZIP tiene archivos protegidos con contraseña; "
                                         "no se puede modificar."))
                keep.append((info, new))
            meter.total = max(sum(i.file_size for i, _ in keep) +
                              sum(n.size for _, n in adds if not n.is_dir), 1)
            meter.label(os.path.basename(archive), archive)
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
                zout.comment = zin.comment
                for info, new in keep:
                    meter.check()
                    ni = _clone_info(info, new)
                    if info.is_dir():
                        zout.writestr(ni, b"")
                        continue
                    with zin.open(info) as fi, \
                            zout.open(ni, "w", force_zip64=info.file_size >= ZIP64_LIMIT) as fo:
                        _pump(fi, fo, meter)
                for arc, node in adds:
                    meter.check()
                    ni = zipfile.ZipInfo(arc, _zip_time(node.mtime))
                    if node.is_dir:
                        ni.external_attr = (0o40775 << 16) | 0x10
                        zout.writestr(ni, b"")
                        continue
                    ni.compress_type = zipfile.ZIP_DEFLATED
                    ni.external_attr = (0o100664 << 16) | 0x20
                    meter.label(node.name, os.path.join(archive, arc.replace("/", "\\")))
                    with zout.open(ni, "w", force_zip64=node.size >= ZIP64_LIMIT) as fo:
                        src.copy_to(node, fo, meter)
        if before_replace:
            before_replace()
        os.replace(tmp, archive)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ===================================================================== operaciones

def run_transfer(op):
    """Copia o mueve op.items de op.src_loc a op.extra['dst']."""
    move = op.extra.get("move", False)
    src = make_source(op.src_loc)
    try:
        if move and _fast_move(op, src):
            return
        sink = make_sink(op.extra["dst"], op.rename_to, len(op.items) == 1)
        sink.check(src, op.items)
        nodes = list(src.plan(op.items))
        chosen, skipped_tops = [], set()
        for n in nodes:
            if op.cancelled:
                return
            if not n.is_dir and sink.exists(n):
                if not op._ask((n.name, n.size, n.mtime), sink.display(n)):
                    skipped_tops.add(n.rel[0])
                    continue
            chosen.append(n)
        if op.cancelled:
            return
        meter = Meter(op, sum(n.size for n in chosen if not n.is_dir))
        same_zip = (move and src.kind == "zip" and sink.kind == "zip" and
                    os.path.normcase(src.loc.archive) == os.path.normcase(sink.archive))
        movable = [e for e in op.items if e.name not in skipped_tops]
        if same_zip:
            sink.drop = src.member_filter(movable)
        errors = []
        sink.write_all(src, chosen, meter, errors)
        for err in errors:
            op.q.put(("error", err))
        if move and not same_zip and not errors and movable:
            src.delete(movable, meter)
    except Cancelled:
        pass
    finally:
        src.close()


def _fast_move(op, src):
    """Mover dentro del mismo servidor es solo cambiar el nombre."""
    kind, where = op.extra["dst"]
    if src.kind != "remote" or kind != "remote" or remote.get(where.remote) is not src.fs:
        return False
    base = where.inner or "/"
    for e in op.items:
        if op.cancelled:
            break
        name = op.rename_to if (op.rename_to and len(op.items) == 1) else e.name
        target = posixpath.join(base, name)
        if target == e.full:
            continue
        if e.is_dir and (base + "/").startswith(e.full.rstrip("/") + "/"):
            op.q.put(("error", _("No se puede mover una carpeta dentro de sí misma:\n{path}").format(
                path=e.full)))
            continue
        if src.fs.stat(target) is not None:
            if not op._ask((e.name, e.size, e.mtime), src.fs.url() + target):
                continue
            if not e.is_dir:
                src.fs.remove(target)
        op._progress(0, 1, e.full, target)
        src.fs.rename(e.full, target)
    return True


def run_delete(op):
    """Borra dentro de un ZIP o en un servidor."""
    src = make_source(op.src_loc)
    meter = Meter(op, len(op.items))
    try:
        src.delete(op.items, meter)
    except Cancelled:
        pass
    finally:
        src.close()


def run_zip_rename(op):
    """Cambia el nombre de un archivo o carpeta dentro de un ZIP (op.extra: old, new)."""
    old, new = op.extra["old"], op.extra["new"]
    if old.endswith("/"):
        rename = lambda n: new + n[len(old):] if n.startswith(old) else n
    else:
        rename = lambda n: new if n == old else n
    try:
        zip_rewrite(op.src_loc.archive, Meter(op, 1), rename=rename)
    except Cancelled:
        pass
