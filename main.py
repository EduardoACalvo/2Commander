"""2 Commander: administrador de archivos de dos paneles.

Uso:  python main.py                      abre el programa
      python main.py --screenshot f.png   abre sin tomar el foco, guarda una foto y sale
      python main.py --lang en            abre en otro idioma (es, en, ...)
"""
import argparse
import ctypes
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fm.app import App  # noqa: E402
from fm import i18n  # noqa: E402
from fm.i18n import _  # noqa: E402

IS_WIN = sys.platform == "win32"


def show_without_focus(root):
    """Muestra la ventana detrás de las demás y sin activarla (solo Windows)."""
    root.update_idletasks()
    hwnd = int(root.wm_frame(), 16)
    u = ctypes.windll.user32
    GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
    u.SetWindowLongW(hwnd, GWL_EXSTYLE, u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
    u.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
    u.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_BOTTOM, sin mover/activar
    return hwnd


def capture_window(hwnd):
    """Copia la ventana con PrintWindow (funciona aunque esté tapada)."""
    from PIL import Image
    from ctypes import wintypes
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]
    bi = BMIH(ctypes.sizeof(BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    # recortar el borde invisible que Windows 10 agrega alrededor de la ventana
    ext = wintypes.RECT()
    try:
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(ext),
                                                      ctypes.sizeof(ext)) == 0:
            img = img.crop((ext.left - rect.left, ext.top - rect.top,
                            ext.right - rect.left, ext.bottom - rect.top))
    except OSError:
        pass
    return img


def take_screenshot(root, app, path, frames=120):
    hwnd = show_without_focus(root) if IS_WIN else None
    if not IS_WIN:
        root.deiconify()
    for _ in range(frames):
        root.update()
        time.sleep(1 / 60)
    root.update()
    if IS_WIN:
        img = capture_window(hwnd)
    else:
        from PIL import ImageGrab
        x, y = root.winfo_rootx(), root.winfo_rooty()
        img = ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height()))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, "PNG")
    root.destroy()


def main():
    # idioma provisorio para los textos de --help; App activa después el definitivo
    pre_ap = argparse.ArgumentParser(add_help=False)
    pre_ap.add_argument("--lang")
    pre, _rest = pre_ap.parse_known_args()
    i18n.set_language(pre.lang or "auto")
    ap = argparse.ArgumentParser(description=_("Administrador de archivos de dos paneles"))
    ap.add_argument("--screenshot", metavar=_("ARCHIVO.png"),
                    help=_("guarda una foto de la ventana y sale"))
    ap.add_argument("--lang", metavar=_("CÓDIGO"),
                    help=_("idioma de la interfaz (es, en, ...); por omisión, el configurado"))
    args = ap.parse_args()
    if IS_WIN:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    if args.screenshot:
        root.withdraw()
    app = App(root, screenshot=bool(args.screenshot), lang=args.lang)
    if args.screenshot:
        take_screenshot(root, app, args.screenshot)
        return
    root.mainloop()


if __name__ == "__main__":
    main()
