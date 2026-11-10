"""Conexiones a servidores FTP, FTPS (FTP con TLS) y SFTP (SSH).

Cada conexión abierta tiene un id; un panel que muestra un servidor guarda ese id en
su Location. Todas las operaciones de una conexión pasan por un candado, así una copia
en segundo plano y un listado del panel nunca se pisan.
"""
import base64
import ctypes
import ftplib
import os
import posixpath
import re
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .i18n import _

# las claves son las que se guardan en la configuración; el texto visible sale de protocol_label()
PROTOCOLS = {"ftp": "FTP", "ftps": "FTP con TLS (FTPS)", "sftp": "SFTP (SSH)"}
DEFAULT_PORTS = {"ftp": 21, "ftps": 21, "sftp": 22}
CHUNK = 64 * 1024


def protocol_label(key):
    """Nombre visible de un protocolo en el idioma activo ('' si no se conoce)."""
    return {"ftp": "FTP", "ftps": _("FTP con TLS (FTPS)"), "sftp": "SFTP (SSH)"}.get(key, "")


class Cancelled(Exception):
    """La persona canceló la transferencia."""


@dataclass
class RemoteItem:
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


def _strip_slash(p):
    return p.rstrip("/") or "/"


# ===================================================================== base

class RemoteFS:
    protocol = ""

    def __init__(self, conf):
        self.conf = dict(conf)
        self.lock = threading.RLock()
        self.home = "/"
        self._dir_cache = {}

    # --------------------------------------------------- datos de la conexión
    @property
    def host(self):
        return self.conf.get("host", "")

    @property
    def port(self):
        return int(self.conf.get("port") or DEFAULT_PORTS[self.protocol])

    @property
    def user(self):
        return self.conf.get("user") or ""

    def url(self):
        user = (self.user + "@") if self.user and self.user != "anonymous" else ""
        port = f":{self.port}" if self.port != DEFAULT_PORTS[self.protocol] else ""
        return f"{self.protocol}://{user}{self.host}{port}"

    def title(self):
        return self.conf.get("name") or self.url()

    # --------------------------------------------------- operaciones comunes
    def listdir(self, path):
        with self.lock:
            items = self._listdir(path)
            self._dir_cache[_strip_slash(path)] = {i.name: i for i in items}
            return items

    def stat(self, path):
        """RemoteItem o None; usa el último listado de la carpeta si lo hay."""
        parent, name = posixpath.split(_strip_slash(path))
        parent = parent or "/"
        cache = self._dir_cache.get(parent)
        if cache is None:
            try:
                self.listdir(parent)
            except Exception:
                self._dir_cache[parent] = {}  # la carpeta no existe (todavía)
                return None
            cache = self._dir_cache.get(parent, {})
        return cache.get(name)

    def forget(self, path):
        self._dir_cache.pop(_strip_slash(path), None)

    def walk(self, top):
        """Como os.walk: (carpeta, [subcarpetas], [RemoteItem de archivos])."""
        items = self.listdir(top)
        dirs = [i for i in items if i.is_dir]
        files = [i for i in items if not i.is_dir]
        yield top, dirs, files
        for d in dirs:
            yield from self.walk(posixpath.join(top, d.name))

    def makedirs(self, path):
        path = _strip_slash(path)
        if path == "/":
            return
        it = self.stat(path)
        if it is not None and it.is_dir:
            return
        self.makedirs(posixpath.dirname(path))
        try:
            self.mkdir(path)
        except Exception:
            it = self.stat(path)
            if it is None or not it.is_dir:
                raise

    def remove_tree(self, path, progress=None, cancelled=lambda: False):
        for item in self.listdir(path):
            if cancelled():
                return
            full = posixpath.join(path, item.name)
            if item.is_dir:
                self.remove_tree(full, progress, cancelled)
            else:
                if progress:
                    progress(full)
                self.remove(full)
        self.rmdir(path)

    def download_temp(self, path, cb=None):
        """Baja un archivo a un temporal y lo devuelve abierto para leer."""
        tmp = tempfile.TemporaryFile()
        self.download(path, tmp, cb)
        tmp.seek(0)
        return tmp

    # --------------------------------------------------- a implementar
    def connect(self, ask_host_key=None):
        raise NotImplementedError

    def close(self):
        pass

    def _listdir(self, path):
        raise NotImplementedError

    def read(self, path, limit=None):
        raise NotImplementedError

    def download(self, path, fo, cb=None):
        raise NotImplementedError

    def upload(self, fi, path, cb=None, mtime=None):
        raise NotImplementedError

    def mkdir(self, path):
        raise NotImplementedError

    def rmdir(self, path):
        raise NotImplementedError

    def remove(self, path):
        raise NotImplementedError

    def rename(self, old, new):
        raise NotImplementedError


# ===================================================================== FTP / FTPS

_UNIX_LIST = re.compile(
    r"^([\-dlbcps])[rwxsStT\-]{9}\S*\s+\d+\s+\S+\s+\S+\s+(\d+)\s+"
    r"(\w{3})\s+(\d{1,2})\s+(\d{1,2}:\d{2}|\d{4})\s+(.+)$")
_DOS_LIST = re.compile(
    r"^(\d{2})-(\d{2})-(\d{2,4})\s+(\d{1,2}):(\d{2})\s*([AP]M)?\s+(<DIR>|\d+)\s+(.+)$", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_list_line(line):
    """Interpreta una línea de LIST (formato Unix o Windows). Devuelve RemoteItem o None."""
    m = _UNIX_LIST.match(line)
    if m:
        kind, size, mon, day, yt, name = m.groups()
        month = _MONTHS.get(mon.lower()[:3], 1)
        now = datetime.now()
        try:
            if ":" in yt:
                hh, mm = (int(x) for x in yt.split(":"))
                dt = datetime(now.year, month, int(day), hh, mm)
                if dt > now.replace(microsecond=0) and (dt - now).days > 1:
                    dt = dt.replace(year=now.year - 1)
            else:
                dt = datetime(int(yt), month, int(day))
            mtime = dt.timestamp()
        except ValueError:
            mtime = 0
        is_dir = kind == "d"
        if kind == "l":
            name = name.split(" -> ")[0]
            is_dir = True  # los enlaces en servidores suelen apuntar a carpetas
        if name in (".", ".."):
            return None
        return RemoteItem(name, is_dir, int(size), mtime)
    m = _DOS_LIST.match(line)
    if m:
        mo, dd, yy, hh, mi, ampm, size, name = m.groups()
        year = int(yy) + (2000 if len(yy) == 2 and int(yy) < 70 else 1900 if len(yy) == 2 else 0)
        hour = int(hh) % 12 + (12 if ampm and ampm.upper() == "PM" else 0) if ampm else int(hh)
        try:
            mtime = datetime(year, int(mo), int(dd), hour, int(mi)).timestamp()
        except ValueError:
            mtime = 0
        is_dir = size.upper() == "<DIR>"
        return RemoteItem(name, is_dir, 0 if is_dir else int(size), mtime)
    return None


def _mlsd_time(s):
    try:
        dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0


class FTPFS(RemoteFS):
    protocol = "ftp"

    def __init__(self, conf):
        super().__init__(conf)
        self.protocol = conf.get("protocol", "ftp")
        self.ftp = None
        self._mlsd = None
        self._last = 0

    def connect(self, ask_host_key=None):
        with self.lock:
            self._open()
            start = self.conf.get("remote_dir") or ""
            self.home = start if start.startswith("/") else (
                posixpath.join(self.ftp.pwd(), start) if start else self.ftp.pwd())

    def _open(self):
        c = self.conf
        cls = ftplib.FTP_TLS if self.protocol == "ftps" else ftplib.FTP
        ftp = cls(timeout=30, encoding=c.get("encoding") or "utf-8")
        ftp.connect(self.host, self.port)
        ftp.login(self.user or "anonymous", c.get("password") or "")
        if self.protocol == "ftps":
            ftp.prot_p()
        ftp.set_pasv(c.get("passive", True))
        try:
            ftp.sendcmd("OPTS UTF8 ON")
        except ftplib.all_errors:
            pass
        self.ftp = ftp
        self._last = time.time()

    def _drop(self):
        if self.ftp is not None:
            try:
                self.ftp.close()
            except Exception:
                pass
        self.ftp = None

    def _ensure(self):
        """Reconecta si la conexión se cayó o el servidor la cerró por inactividad."""
        if self.ftp is not None and time.time() - self._last > 45:
            try:
                self.ftp.voidcmd("NOOP")
            except Exception:
                self._drop()
        if self.ftp is None:
            self._open()
        self._last = time.time()
        return self.ftp

    def _retry(self, fn):
        with self.lock:
            try:
                return fn(self._ensure())
            except (OSError, EOFError, ftplib.error_temp, ftplib.error_reply) as ex:
                if isinstance(ex, ftplib.error_temp) and not str(ex).startswith("421"):
                    raise
                self._drop()
                return fn(self._ensure())

    def close(self):
        with self.lock:
            if self.ftp is not None:
                try:
                    self.ftp.quit()
                except Exception:
                    pass
            self._drop()

    def _listdir(self, path):
        def run(ftp):
            if self._mlsd is not False:
                try:
                    out = []
                    for name, facts in ftp.mlsd(path, facts=["type", "size", "modify"]):
                        t = facts.get("type", "").lower()
                        if t in ("cdir", "pdir") or name in (".", ".."):
                            continue
                        is_dir = t == "dir" or t.startswith("os.unix=symlink")
                        out.append(RemoteItem(name, is_dir, int(facts.get("size") or 0),
                                              _mlsd_time(facts.get("modify", ""))))
                    self._mlsd = True
                    return out
                except ftplib.error_perm as ex:
                    if not str(ex)[:3] in ("500", "501", "502", "504"):
                        raise
                    self._mlsd = False
            lines = []
            ftp.cwd(path)
            ftp.retrlines("LIST", lines.append)
            return [i for i in (parse_list_line(ln) for ln in lines) if i is not None]
        return self._retry(run)

    def _transfer_end(self, ftp, ok):
        try:
            ftp.voidresp()
        except ftplib.error_temp:
            pass  # 426: transferencia cortada a pedido, la conexión sigue sirviendo
        except Exception:
            self._drop()
            if ok:
                raise

    def read(self, path, limit=None):
        def run(ftp):
            ftp.voidcmd("TYPE I")
            chunks, n = [], 0
            conn = ftp.transfercmd("RETR " + path)
            try:
                while limit is None or n < limit:
                    buf = conn.recv(CHUNK)
                    if not buf:
                        break
                    chunks.append(buf)
                    n += len(buf)
            finally:
                conn.close()
            self._transfer_end(ftp, False)
            data = b"".join(chunks)
            return data[:limit] if limit else data
        return self._retry(run)

    def download(self, path, fo, cb=None):
        with self.lock:
            ftp = self._ensure()
            ftp.voidcmd("TYPE I")
            conn = ftp.transfercmd("RETR " + path)
            ok = False
            try:
                while True:
                    buf = conn.recv(CHUNK)
                    if not buf:
                        break
                    fo.write(buf)
                    if cb:
                        cb(len(buf))
                ok = True
            finally:
                conn.close()
                if ok:
                    self._transfer_end(ftp, True)
                else:
                    self._drop()

    def upload(self, fi, path, cb=None, mtime=None):
        with self.lock:
            ftp = self._ensure()
            ftp.voidcmd("TYPE I")
            conn = ftp.transfercmd("STOR " + path)
            ok = False
            try:
                while True:
                    buf = fi.read(CHUNK)
                    if not buf:
                        break
                    conn.sendall(buf)
                    if cb:
                        cb(len(buf))
                ok = True
            finally:
                conn.close()
                if ok:
                    self._transfer_end(ftp, True)
                else:
                    self._drop()
            if mtime:
                stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(mtime))
                try:
                    ftp.sendcmd(f"MFMT {stamp} {path}")
                except ftplib.all_errors:
                    pass
            self.forget(posixpath.dirname(path))

    def mkdir(self, path):
        self._retry(lambda ftp: ftp.mkd(path))
        self.forget(posixpath.dirname(_strip_slash(path)))

    def rmdir(self, path):
        self._retry(lambda ftp: ftp.rmd(path))
        self.forget(posixpath.dirname(_strip_slash(path)))

    def remove(self, path):
        self._retry(lambda ftp: ftp.delete(path))
        self.forget(posixpath.dirname(path))

    def rename(self, old, new):
        self._retry(lambda ftp: ftp.rename(old, new))
        self.forget(posixpath.dirname(_strip_slash(old)))
        self.forget(posixpath.dirname(_strip_slash(new)))


# ===================================================================== SFTP

def sftp_available():
    try:
        import paramiko  # noqa: F401
        return True
    except ImportError:
        return False


def known_hosts_path():
    from .app import config_path
    return os.path.join(os.path.dirname(config_path()), "known_hosts")


class SFTPFS(RemoteFS):
    protocol = "sftp"

    def __init__(self, conf):
        super().__init__(conf)
        self.client = None
        self.sftp = None
        self._ask_host_key = None

    def connect(self, ask_host_key=None):
        self._ask_host_key = ask_host_key
        with self.lock:
            self._open()
            start = self.conf.get("remote_dir") or ""
            self.home = start if start.startswith("/") else self.sftp.normalize(start or ".")

    def _open(self):
        try:
            import paramiko
        except ImportError:
            raise RuntimeError(
                _("Para SFTP falta el componente «paramiko» en este Python.\n"
                  "Se instala con:\n{python} -m pip install paramiko").format(
                    python=sys.executable))
        ask = self._ask_host_key
        kh = known_hosts_path()

        class AskPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = base64.b64encode(
                    __import__("hashlib").sha256(key.asbytes()).digest()).decode().rstrip("=")
                if ask is None or not ask(hostname, key.get_name(), "SHA256:" + fp):
                    raise paramiko.SSHException(_("Se rechazó la clave del servidor."))
                client.get_host_keys().add(hostname, key.get_name(), key)
                try:
                    os.makedirs(os.path.dirname(kh), exist_ok=True)
                    client.save_host_keys(kh)
                except OSError:
                    pass

        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except (OSError, IOError):
            pass
        if os.path.exists(kh):
            try:
                client.load_host_keys(kh)
            except (OSError, IOError):
                pass
        client.set_missing_host_key_policy(AskPolicy())
        c = self.conf
        password = c.get("password") or None
        keyfile = c.get("keyfile") or None
        client.connect(self.host, port=self.port, username=self.user or None,
                       password=password, key_filename=keyfile,
                       look_for_keys=not password and not keyfile, allow_agent=True,
                       timeout=20, banner_timeout=20, auth_timeout=30)
        self.client = client
        self.sftp = client.open_sftp()

    def _ensure(self):
        t = self.client.get_transport() if self.client else None
        if t is None or not t.is_active():
            self.close()
            self._open()
        return self.sftp

    def close(self):
        with self.lock:
            for x in (self.sftp, self.client):
                try:
                    if x is not None:
                        x.close()
                except Exception:
                    pass
            self.sftp = self.client = None

    def _listdir(self, path):
        with self.lock:
            sftp = self._ensure()
            out = []
            for a in sftp.listdir_attr(path):
                if a.filename in (".", ".."):
                    continue
                mode = a.st_mode or 0
                is_dir = stat.S_ISDIR(mode)
                size = a.st_size or 0
                if stat.S_ISLNK(mode):
                    try:
                        t = sftp.stat(posixpath.join(path, a.filename))
                        is_dir, size = stat.S_ISDIR(t.st_mode or 0), t.st_size or 0
                    except IOError:
                        pass
                out.append(RemoteItem(a.filename, is_dir, size, float(a.st_mtime or 0)))
            return out

    def read(self, path, limit=None):
        with self.lock:
            with self._ensure().open(path, "rb") as f:
                return f.read(limit) if limit else f.read()

    def download(self, path, fo, cb=None):
        with self.lock:
            with self._ensure().open(path, "rb") as f:
                f.prefetch()
                while True:
                    buf = f.read(CHUNK)
                    if not buf:
                        break
                    fo.write(buf)
                    if cb:
                        cb(len(buf))

    def upload(self, fi, path, cb=None, mtime=None):
        with self.lock:
            sftp = self._ensure()
            with sftp.open(path, "wb") as f:
                f.set_pipelined(True)
                while True:
                    buf = fi.read(CHUNK)
                    if not buf:
                        break
                    f.write(buf)
                    if cb:
                        cb(len(buf))
            if mtime:
                try:
                    sftp.utime(path, (mtime, mtime))
                except IOError:
                    pass
            self.forget(posixpath.dirname(path))

    def _op(self, fn, *paths):
        with self.lock:
            fn(self._ensure())
        for p in paths:
            self.forget(posixpath.dirname(_strip_slash(p)))

    def mkdir(self, path):
        self._op(lambda s: s.mkdir(path), path)

    def rmdir(self, path):
        self._op(lambda s: s.rmdir(path), path)

    def remove(self, path):
        self._op(lambda s: s.remove(path), path)

    def rename(self, old, new):
        self._op(lambda s: s.rename(old, new), old, new)


# ===================================================================== registro

_conns = {}
_counter = [0]


def open_connection(conf, ask_host_key=None):
    """Conecta y devuelve el id de la conexión. Lanza una excepción si no se puede."""
    proto = conf.get("protocol", "ftp")
    fs = SFTPFS(conf) if proto == "sftp" else FTPFS(conf)
    fs.connect(ask_host_key)
    _counter[0] += 1
    cid = f"c{_counter[0]}"
    _conns[cid] = fs
    return cid


def get(cid):
    fs = _conns.get(cid)
    if fs is None:
        raise RuntimeError(_("La conexión con el servidor está cerrada."))
    return fs


def url_of(cid):
    fs = _conns.get(cid)
    return fs.url() if fs else _("(desconectado)")


def close(cid):
    fs = _conns.pop(cid, None)
    if fs is not None:
        fs.close()


def close_all():
    for cid in list(_conns):
        close(cid)


def open_ids():
    return list(_conns)


# ===================================================================== contraseñas

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    if sys.platform != "win32":
        return None
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect_password(pw):
    """Cifra la contraseña para este usuario de Windows. '' si no se puede."""
    enc = _dpapi(pw.encode("utf-8"), True) if pw else None
    return base64.b64encode(enc).decode("ascii") if enc else ""


def unprotect_password(s):
    if not s:
        return ""
    try:
        dec = _dpapi(base64.b64decode(s), False)
    except (ValueError, OSError):
        return ""
    return dec.decode("utf-8") if dec else ""
