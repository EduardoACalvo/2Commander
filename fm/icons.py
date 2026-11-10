"""Iconos: los del sistema cuando hay Windows, y dibujados con Pillow como respaldo."""
import ctypes
import sys

from PIL import Image, ImageDraw, ImageTk

_cache = {}


def _img(size=16):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def _folder(open_=False):
    im, d = _img()
    d.rectangle([1, 3, 7, 5], fill="#e8b84a", outline="#a07818")
    d.rectangle([1, 5, 14, 13], fill="#f6d36b", outline="#a07818")
    if open_:
        d.polygon([(3, 8), (15, 8), (13, 13), (1, 13)], fill="#fbe39a", outline="#a07818")
    return im


def _file(color="#ffffff", band=None, mark=None):
    im, d = _img()
    d.polygon([(3, 1), (10, 1), (13, 4), (13, 15), (3, 15)], fill=color, outline="#6b6b6b")
    d.line([(10, 1), (10, 4), (13, 4)], fill="#6b6b6b")
    if band:
        d.rectangle([4, 9, 12, 13], fill=band)
    if mark == "lines":
        for y in (6, 8, 10, 12):
            d.line([(5, y), (11, y)], fill="#8a8a8a")
    return im


def _up():
    im, d = _img()
    d.rectangle([1, 7, 14, 14], fill="#c8ccd2", outline="#6c7480")
    d.rectangle([2, 5, 7, 7], fill="#c8ccd2", outline="#6c7480")
    d.polygon([(8, 1), (13, 6), (10, 6), (10, 11), (6, 11), (6, 6), (3, 6)],
              fill="#5b7fb8", outline="#2c4a7c")
    return im


def _zip():
    im, d = _img()
    d.rectangle([2, 3, 13, 14], fill="#c9a2e8", outline="#6a3d8f")
    d.rectangle([2, 3, 13, 6], fill="#9b6bc4", outline="#6a3d8f")
    for y in range(7, 14, 2):
        d.line([(7, y), (8, y)], fill="#3a2050")
    return im


def _exe():
    im, d = _img()
    d.rectangle([1, 2, 14, 13], fill="#ffffff", outline="#2b5797")
    d.rectangle([1, 2, 14, 4], fill="#2b5797")
    d.rectangle([4, 7, 11, 10], fill="#9ec3f0")
    return im


def _image():
    im, d = _img()
    d.rectangle([1, 2, 14, 13], fill="#d6ecff", outline="#4a6e8f")
    d.polygon([(2, 12), (6, 7), (9, 10), (11, 8), (13, 12)], fill="#4b9a4b")
    d.ellipse([10, 3, 12, 5], fill="#f2c200")
    return im


def _drive(kind="fijo"):
    im, d = _img()
    color = {"removible": "#7aa7d6", "red": "#7cbf7c", "cd": "#d9d9d9"}.get(kind, "#b8b8b8")
    d.rectangle([1, 5, 14, 11], fill=color, outline="#505050")
    d.ellipse([11, 7, 12, 8], fill="#30d030")
    return im


# ------------------------------------------------- barra de herramientas (20 px)

def _tb(name):
    im, d = _img(20)
    if name == "refresh":
        d.arc([3, 3, 16, 16], 30, 330, fill="#1f6f1f", width=2)
        d.polygon([(13, 1), (18, 6), (12, 7)], fill="#1f6f1f")
    elif name == "brief":
        for x in (2, 11):
            for y in (3, 8, 13):
                d.rectangle([x, y, x + 7, y + 3], fill="#5b8bd0")
    elif name == "full":
        for y in (3, 7, 11, 15):
            d.rectangle([2, y, 17, y + 2], fill="#5b8bd0")
    elif name == "swap":
        d.polygon([(2, 7), (7, 2), (7, 5), (17, 5), (17, 9), (7, 9), (7, 12)], fill="#c07020")
        d.polygon([(18, 13), (13, 8), (13, 11), (3, 11), (3, 15), (13, 15), (13, 18)],
                  fill="#2070c0")
    elif name == "back":
        d.polygon([(2, 10), (10, 3), (10, 7), (18, 7), (18, 13), (10, 13), (10, 17)],
                  fill="#2a7a2a")
    elif name == "fwd":
        d.polygon([(18, 10), (10, 3), (10, 7), (2, 7), (2, 13), (10, 13), (10, 17)],
                  fill="#2a7a2a")
    elif name == "search":
        d.ellipse([2, 2, 12, 12], outline="#303030", width=2, fill="#d8ecff")
        d.line([(11, 11), (17, 17)], fill="#303030", width=3)
    elif name == "rename":
        d.rectangle([2, 6, 17, 13], fill="#ffffff", outline="#404040")
        d.line([(5, 8), (5, 11)], fill="#000000")
        d.text((8, 5), "ab", fill="#c00000")
    elif name == "pack":
        d.rectangle([3, 4, 16, 17], fill="#c9a2e8", outline="#6a3d8f")
        d.rectangle([3, 4, 16, 8], fill="#9b6bc4", outline="#6a3d8f")
        d.polygon([(10, 0), (14, 4), (6, 4)], fill="#2a7a2a")
    elif name == "unpack":
        d.rectangle([3, 7, 16, 18], fill="#c9a2e8", outline="#6a3d8f")
        d.rectangle([3, 7, 16, 10], fill="#9b6bc4", outline="#6a3d8f")
        d.polygon([(6, 1), (14, 1), (10, 6)], fill="#c02020")
    elif name == "console":
        d.rectangle([1, 3, 18, 16], fill="#101010", outline="#606060")
        d.line([(4, 7), (7, 10), (4, 13)], fill="#e0e0e0", width=1)
        d.line([(9, 13), (14, 13)], fill="#e0e0e0")
    elif name == "notepad":
        d.rectangle([4, 2, 16, 18], fill="#fffbe0", outline="#707070")
        for y in (6, 9, 12, 15):
            d.line([(6, y), (14, y)], fill="#7090c0")
    elif name == "hotlist":
        d.polygon([(10, 1), (12, 7), (19, 7), (13, 11), (15, 18), (10, 14), (5, 18), (7, 11),
                   (1, 7), (8, 7)], fill="#f5c400", outline="#9a7a00")
    elif name == "compare":
        d.rectangle([1, 3, 8, 16], fill="#ffffff", outline="#505050")
        d.rectangle([11, 3, 18, 16], fill="#ffffff", outline="#505050")
        d.line([(3, 7), (6, 7)], fill="#c00000")
        d.line([(13, 7), (16, 7)], fill="#008000")
        d.line([(3, 11), (6, 11)], fill="#808080")
        d.line([(13, 11), (16, 11)], fill="#808080")
    elif name == "sync":
        d.rectangle([1, 4, 7, 15], fill="#f6d36b", outline="#a07818")
        d.rectangle([12, 4, 18, 15], fill="#f6d36b", outline="#a07818")
        d.polygon([(8, 6), (11, 8), (8, 10)], fill="#007000")
        d.polygon([(11, 10), (8, 12), (11, 14)], fill="#0040c0")
    elif name == "network":
        d.rectangle([2, 4, 12, 11], fill="#cfe6fa", outline="#2c5d9a")
        d.rectangle([4, 6, 10, 9], fill="#4f93d8")
        d.line([(7, 12), (7, 15)], fill="#404040")
        d.line([(2, 16), (17, 16)], fill="#404040", width=2)
        d.rectangle([13, 7, 18, 14], fill="#e9e9e9", outline="#606060")
    elif name in ("ftp", "ftpurl"):
        d.text((1, -2), "FTP" if name == "ftp" else "URL",
               fill="#c00000" if name == "ftp" else "#1f4f9f")
        d.rectangle([2, 10, 9, 15], fill="#cfe6fa", outline="#2c5d9a")
        d.rectangle([11, 10, 18, 15], fill="#cfe6fa", outline="#2c5d9a")
        d.line([(5, 16), (5, 18), (14, 18), (14, 16)], fill="#404040")
    elif name in ("view", "copy", "move"):
        if name != "view":
            d.rectangle([7, 1, 17, 13], fill="#ffffff", outline="#4a6fa8")
        d.rectangle([2, 5, 12, 18], fill="#ffffff", outline="#4a6fa8")
        for y in (8, 11, 14):
            d.line([(4, y), (10, y)], fill="#9db3d6")
        if name == "view":
            d.ellipse([9, 8, 16, 15], outline="#1f4f9f", width=2, fill="#dcecff")
            d.line([(15, 14), (18, 18)], fill="#1f4f9f", width=2)
        elif name == "move":
            d.polygon([(11, 12), (17, 12), (17, 9), (19, 14), (17, 19), (17, 16), (11, 16)],
                      fill="#c02020")
    elif name == "thumbs":
        for x in (2, 11):
            for y in (2, 11):
                d.rectangle([x, y, x + 6, y + 6], fill="#ffffff", outline="#4a7a2a")
                d.rectangle([x + 2, y + 3, x + 4, y + 4], fill="#6aaa3a")
    elif name == "tree":
        d.rectangle([2, 2, 7, 6], fill="#f6d36b", outline="#a07818")
        d.line([(4, 7), (4, 15), (9, 15)], fill="#606060")
        d.line([(4, 10), (9, 10)], fill="#606060")
        d.rectangle([10, 8, 15, 12], fill="#f6d36b", outline="#a07818")
        d.rectangle([10, 13, 15, 17], fill="#f6d36b", outline="#a07818")
    elif name == "invert":
        for i, y in enumerate((3, 8, 13)):
            d.rectangle([2, y, 17, y + 3], fill="#c03030" if i % 2 == 0 else "#5b8bd0")
    elif name == "newfolder":
        f = _folder().resize((18, 18))
        im.paste(f, (0, 2), f)
        d.text((12, 0), "+", fill="#007000")
    return im


def app_icon(size=32):
    """Ícono propio de la ventana: un marco con dos paneles."""
    im, d = _img(size)
    s = size / 32
    d.rounded_rectangle([1 * s, 3 * s, 30 * s, 29 * s], radius=3 * s, fill="#2f5f9e",
                        outline="#1c3c66")
    d.rectangle([4 * s, 9 * s, 15 * s, 26 * s], fill="#ffffff")
    d.rectangle([17 * s, 9 * s, 27 * s, 26 * s], fill="#ffffff")
    d.rectangle([4 * s, 11 * s, 15 * s, 13 * s], fill="#99b4d1")
    for y in (16, 19, 22):
        d.line([(6 * s, y * s), (13 * s, y * s)], fill="#7a8aa0")
        d.line([(19 * s, y * s), (25 * s, y * s)], fill="#7a8aa0")
    d.rectangle([4 * s, 5 * s, 9 * s, 7 * s], fill="#f6d36b")
    return ImageTk.PhotoImage(im)


_MAKERS = {
    "folder": _folder, "file": _file, "up": _up, "zip": _zip, "exe": _exe,
    "image": _image, "text": lambda: _file(mark="lines"),
    "drive": _drive, "drive_removible": lambda: _drive("removible"),
    "drive_red": lambda: _drive("red"), "drive_cd": lambda: _drive("cd"),
}


def get(name):
    if name not in _cache:
        if name.startswith("tb_"):
            _cache[name] = ImageTk.PhotoImage(_tb(name[3:]))
        else:
            _cache[name] = ImageTk.PhotoImage(_MAKERS[name]())
    return _cache[name]


EXE_EXT = {"exe", "bat", "cmd", "com", "msi", "ps1"}
IMG_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "tif", "tiff"}
TXT_EXT = {"txt", "log", "ini", "cfg", "md", "csv", "py", "json", "xml", "html", "htm",
           "css", "js", "c", "h", "cpp", "rs", "toml", "yaml", "yml", "bas", "cob", "sql"}
ZIP_EXT = {"zip", "jar", "cbz", "rar", "7z", "gz", "tar"}


# ------------------------------------------------- iconos del sistema (Windows)

_OWN_ICON_EXT = {"exe", "lnk", "ico", "url", "msc", "cpl", "scr", "appref-ms"}
_shell = None


def _shell_api():
    """Funciones de Windows para pedir el ícono que muestra el Explorador."""
    global _shell
    if _shell is None:
        _shell = False
        if sys.platform == "win32":
            try:
                from ctypes import wintypes

                class SHFILEINFOW(ctypes.Structure):
                    _fields_ = [("hIcon", ctypes.c_void_p), ("iIcon", ctypes.c_int),
                                ("dwAttributes", wintypes.DWORD),
                                ("szDisplayName", ctypes.c_wchar * 260),
                                ("szTypeName", ctypes.c_wchar * 80)]

                sh, u, g = (ctypes.WinDLL(n) for n in ("shell32", "user32", "gdi32"))
                sh.SHGetFileInfoW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD,
                                              ctypes.POINTER(SHFILEINFOW), ctypes.c_uint,
                                              ctypes.c_uint]
                sh.SHGetFileInfoW.restype = ctypes.c_size_t
                for name in ("GetDC", "CreateCompatibleDC", "CreateCompatibleBitmap",
                             "SelectObject", "CreateSolidBrush"):
                    fn = getattr(u if name == "GetDC" else g, name)
                    fn.restype = ctypes.c_void_p
                u.GetDC.argtypes = [ctypes.c_void_p]
                u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                u.FillRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
                u.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
                u.DestroyIcon.argtypes = [ctypes.c_void_p]
                g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
                g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
                g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                g.DeleteObject.argtypes = [ctypes.c_void_p]
                g.DeleteDC.argtypes = [ctypes.c_void_p]
                g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_uint]
                ctypes.windll.ole32.CoInitialize(None)
                _shell = (sh, u, g, SHFILEINFOW)
            except (OSError, AttributeError):
                _shell = False
    return _shell


def _draw_icon(api, hicon, bg, size=16):
    _sh, u, g, _ = api
    hdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, size, size)
    old = g.SelectObject(mdc, bmp)
    rect = (ctypes.c_long * 4)(0, 0, size, size)
    brush = g.CreateSolidBrush(bg)
    u.FillRect(mdc, rect, brush)
    g.DeleteObject(brush)
    u.DrawIconEx(mdc, 0, 0, hicon, size, size, 0, None, 3)  # DI_NORMAL
    g.SelectObject(mdc, old)
    hdr = (ctypes.c_uint32 * 10)(40, size, (-size) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(size * size * 4)
    g.GetDIBits(mdc, bmp, 0, size, buf, hdr, 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(None, hdc)
    return Image.frombuffer("RGB", (size, size), buf, "raw", "BGRX", 0, 1)


def _shell_image(path, attrs=0, use_attrs=True):
    """Ícono chico del sistema como imagen RGBA (o None si no se puede)."""
    api = _shell_api()
    if not api:
        return None
    sh, u, _g, SHFILEINFOW = api
    info = SHFILEINFOW()
    flags = 0x100 | 0x1 | (0x10 if use_attrs else 0)  # ICON | SMALLICON | USEFILEATTRIBUTES
    if not sh.SHGetFileInfoW(path, attrs, ctypes.byref(info), ctypes.sizeof(info), flags) \
            or not info.hIcon:
        return None
    try:
        # dibujar sobre negro y sobre blanco para recuperar la transparencia
        black = _draw_icon(api, info.hIcon, 0x000000)
        white = _draw_icon(api, info.hIcon, 0xFFFFFF)
    finally:
        u.DestroyIcon(info.hIcon)
    out = Image.new("RGBA", black.size)
    bp, wp = black.load(), white.load()
    op = out.load()
    for y in range(black.size[1]):
        for x in range(black.size[0]):
            b, w = bp[x, y], wp[x, y]
            a = 255 - max(0, min(255, (w[0] - b[0] + w[1] - b[1] + w[2] - b[2]) // 3))
            if a:
                op[x, y] = (min(255, b[0] * 255 // a), min(255, b[1] * 255 // a),
                            min(255, b[2] * 255 // a), a)
    return out


def _shell_photo(key, path, attrs=0, use_attrs=True):
    if key not in _cache:
        im = _shell_image(path, attrs, use_attrs)
        _cache[key] = ImageTk.PhotoImage(im) if im is not None else None
    return _cache[key]


def drive_icon(root, kind):
    img = _shell_photo("drv:" + root.upper(), root, use_attrs=False)
    return img or get("drive" if kind in ("fijo", "ram") else "drive_" + kind)


def for_entry(e, on_disk=True):
    if e.is_up:
        return get("up")
    if e.is_dir:
        return _shell_photo("dir", "carpeta", 0x10) or get("folder")
    ext = e.ext.lower()
    if _shell_api():
        if on_disk and ext in _OWN_ICON_EXT and e.full:
            img = _shell_photo("f:" + e.full.lower(), e.full, use_attrs=False)
        else:
            img = _shell_photo("x:" + ext, "archivo." + ext if ext else "archivo", 0x80)
        if img:
            return img
    return _drawn_for(e)


def _drawn_for(e):
    if e.is_dir:
        return get("folder")
    ext = e.ext.lower()
    if ext in ZIP_EXT:
        return get("zip")
    if ext in EXE_EXT:
        return get("exe")
    if ext in IMG_EXT:
        return get("image")
    if ext in TXT_EXT:
        return get("text")
    return get("file")
