"""Listado de carpetas (disco y archivos ZIP) y utilidades de formato."""
import ctypes
import os
import posixpath
import stat
import string
import sys
import time
import zipfile
from dataclasses import dataclass, field

from . import i18n

IS_WIN = sys.platform == "win32"

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_ARCHIVE = 0x20


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    attrs: int = 0
    is_up: bool = False
    full: str = ""  # ruta completa en disco o ruta interna del ZIP

    @property
    def stem(self):
        if self.is_dir or self.is_up:
            return self.name
        base, ext = split_ext(self.name)
        return base

    @property
    def ext(self):
        if self.is_dir or self.is_up:
            return ""
        return split_ext(self.name)[1]

    @property
    def hidden(self):
        return bool(self.attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM))


def split_ext(name):
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    i = name.rfind(".")
    if i <= 0:
        return name, ""
    return name[:i], name[i + 1:]


@dataclass
class Location:
    """Una carpeta en disco, dentro de un ZIP o en un servidor FTP/SFTP."""
    path: str
    archive: str = None  # ruta del .zip cuando se navega dentro
    inner: str = ""      # ZIP: carpeta interna con '/' final ('' = raíz); remoto: carpeta '/...'
    remote: str = None   # id de la conexión FTP/SFTP cuando el panel está en un servidor

    @property
    def is_disk(self):
        return not self.archive and not self.remote

    def local_dir(self):
        """Carpeta del disco más cercana (para consolas, búsquedas, configuración)."""
        if self.archive:
            return os.path.dirname(self.archive)
        return self.path

    def display(self):
        if self.remote:
            from . import remote
            return remote.url_of(self.remote) + self.inner
        if self.archive:
            inner = self.inner.replace("/", "\\")
            return os.path.join(self.archive, inner) if inner else self.archive + "\\"
        return self.path

    def key(self):
        return (self.path, self.archive, self.inner, self.remote)

    @staticmethod
    def disk(path):
        return Location(path=os.path.normpath(path) if not path.endswith(":\\") else path)

    @staticmethod
    def at_remote(cid, rpath):
        return Location(path=os.path.expanduser("~"), remote=cid, inner=rpath or "/")


# ---------------------------------------------------------------- formatos

def fmt_size(n):
    return f"{n:,}".replace(",", i18n.separators()[0])


def fmt_k(n):
    return fmt_size((n + 1023) // 1024) + " k"


def fmt_human(n):
    """Tamaño corto con unidad: 288 b, 1,62 Kb, 99,57 Mb, 23,66 Gb."""
    if n < 1024:
        return f"{n} b"
    for unit in ("Kb", "Mb", "Gb", "Tb"):
        n /= 1024
        if n < 1024 or unit == "Tb":
            return f"{n:.2f}".replace(".", i18n.separators()[1]) + " " + unit


def fmt_date(t):
    if not t:
        return ""
    return time.strftime(i18n.date_format(), time.localtime(t))


def fmt_attrs(a):
    return ("r" if a & FILE_ATTRIBUTE_READONLY else "-") + \
           ("a" if a & FILE_ATTRIBUTE_ARCHIVE else "-") + \
           ("h" if a & FILE_ATTRIBUTE_HIDDEN else "-") + \
           ("s" if a & FILE_ATTRIBUTE_SYSTEM else "-")


# ---------------------------------------------------------------- unidades

def list_drives():
    if not IS_WIN:
        return ["/"]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask & (1 << i)]


DRIVE_TYPES = {2: "removible", 3: "fijo", 4: "red", 5: "cd", 6: "ram"}


def drive_type(root):
    if not IS_WIN:
        return "fijo"
    return DRIVE_TYPES.get(ctypes.windll.kernel32.GetDriveTypeW(root), "fijo")


def volume_label(root):
    if not IS_WIN:
        return ""
    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), buf, 261, None, None, None, None, 0)
    return buf.value if ok else ""


def drive_of(path):
    d = os.path.splitdrive(path)[0]
    return (d + "\\") if d else "/"


def disk_free(root):
    try:
        import shutil
        u = shutil.disk_usage(root)
        return u.free, u.total
    except OSError:
        return None


# ---------------------------------------------------------------- listados

def list_dir(loc, show_hidden=False):
    if loc.remote:
        return list_remote(loc, show_hidden)
    if loc.archive:
        return list_zip(loc)
    entries = []
    parent = os.path.dirname(loc.path.rstrip("\\/"))
    if parent and os.path.normcase(parent) != os.path.normcase(loc.path.rstrip("\\/")) \
            and not loc.path.endswith(":\\"):
        entries.append(Entry("..", True, is_up=True, full=parent))
    with os.scandir(loc.path) as it:
        for de in it:
            try:
                st = de.stat(follow_symlinks=False)
            except OSError:
                continue
            attrs = getattr(st, "st_file_attributes", 0)
            if not IS_WIN and de.name.startswith("."):
                attrs |= FILE_ATTRIBUTE_HIDDEN
            e = Entry(de.name, stat.S_ISDIR(st.st_mode) or de.is_dir(),
                      0 if de.is_dir() else st.st_size, st.st_mtime, attrs, full=de.path)
            if e.hidden and not show_hidden:
                continue
            entries.append(e)
    return entries


def zip_tree(archive):
    """Devuelve {carpeta_interna: {nombre: (is_dir, size, mtime)}}."""
    tree = {"": {}}
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if not parts:
                continue
            is_dir = name.endswith("/")
            mt = time.mktime(info.date_time + (0, 0, -1)) if info.date_time[0] >= 1980 else 0
            prefix = ""
            for i, p in enumerate(parts):
                last = i == len(parts) - 1
                folder = tree.setdefault(prefix, {})
                if last and not is_dir:
                    folder[p] = (False, info.file_size, mt)
                else:
                    if p not in folder or not folder[p][0]:
                        folder[p] = (True, 0, mt if last else 0)
                    prefix = prefix + p + "/"
                    tree.setdefault(prefix, {})
    return tree


def list_zip(loc):
    tree = zip_tree(loc.archive)
    entries = []
    if loc.inner:
        parent = loc.inner.rstrip("/").rpartition("/")[0]
        entries.append(Entry("..", True, is_up=True, full=(parent + "/") if parent else ""))
    else:
        entries.append(Entry("..", True, is_up=True, full=os.path.dirname(loc.archive)))
    for name, (is_dir, size, mt) in tree.get(loc.inner, {}).items():
        entries.append(Entry(name, is_dir, size, mt, FILE_ATTRIBUTE_ARCHIVE,
                             full=loc.inner + name + ("/" if is_dir else "")))
    return entries


def list_remote(loc, show_hidden=False):
    from . import remote
    fs = remote.get(loc.remote)
    folder = loc.inner or "/"
    entries = []
    if folder != "/":
        parent = posixpath.dirname(folder.rstrip("/")) or "/"
        entries.append(Entry("..", True, is_up=True, full=parent))
    for it in fs.listdir(folder):
        attrs = FILE_ATTRIBUTE_HIDDEN if it.name.startswith(".") else 0
        if attrs and not show_hidden:
            continue
        entries.append(Entry(it.name, it.is_dir, 0 if it.is_dir else it.size, it.mtime, attrs,
                             full=posixpath.join(folder, it.name)))
    return entries


def is_zip(path):
    return path.lower().endswith((".zip", ".jar", ".cbz")) and os.path.isfile(path)


def read_bytes(loc, entry, limit=None):
    if loc.remote:
        from . import remote
        return remote.get(loc.remote).read(entry.full, limit)
    if loc.archive:
        with zipfile.ZipFile(loc.archive) as zf:
            with zf.open(entry.full) as f:
                return f.read(limit if limit else -1)
    with open(entry.full, "rb") as f:
        return f.read(limit if limit else -1)


def dir_size(path):
    total = files = dirs = 0
    for root, ds, fs in os.walk(path):
        dirs += len(ds)
        for f in fs:
            files += 1
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total, files, dirs


def set_attrs(path, attrs):
    if IS_WIN:
        ctypes.windll.kernel32.SetFileAttributesW(path, attrs or 0x80)
    else:
        mode = os.stat(path).st_mode
        os.chmod(path, mode & ~0o222 if attrs & FILE_ATTRIBUTE_READONLY else mode | 0o200)


def get_attrs(path):
    if IS_WIN:
        a = ctypes.windll.kernel32.GetFileAttributesW(path)
        return 0 if a == 0xFFFFFFFF else a
    return 0


# ---------------------------------------------------------------- papelera

class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p), ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]


def recycle(paths):
    """Envía a la papelera. Devuelve True si salió bien."""
    if not IS_WIN:
        return False
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = "\0".join(paths) + "\0\0"
    op.fFlags = 0x40 | 0x10 | 0x4 | 0x400  # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
