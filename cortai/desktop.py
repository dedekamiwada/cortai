"""O Cortaí como aplicativo de janela própria (WebView2) e o ciclo de vida do processo."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

TITLE = "Cortaí"
_MB_YESNO, _MB_ICONERROR, _MB_ICONWARNING, _MB_TOPMOST, _IDYES = 0x4, 0x10, 0x30, 0x40000, 6


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, _MB_ICONERROR | _MB_TOPMOST)


def confirm(text: str) -> bool:
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, _MB_YESNO | _MB_ICONWARNING | _MB_TOPMOST) == _IDYES


def is_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/studio.html", timeout=2) as page:
            return b"studio.js" in page.read()
    except (urllib.error.URLError, OSError):
        return False


# ------------------------------------------------------------------ processo

def log_to_file() -> Path:
    """Um .exe sem console não tem onde escrever: as mensagens vão para um arquivo de log."""
    folder = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Cortai"
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "cortai.log"
    if log.exists() and log.stat().st_size > 5_000_000:
        log.replace(log.with_suffix(".log.old"))
    stream = open(log, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    return log


def attach_child_output() -> None:
    """No modo --run-yt-dlp o processo filho escreve nos pipes que o Cortaí criou para ele."""
    for fd, name in ((1, "stdout"), (2, "stderr")):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(fd, "w", encoding="utf-8", errors="replace", closefd=False))
            except OSError:
                setattr(sys, name, open(os.devnull, "w"))


def hide_child_consoles() -> None:
    """Sem console próprio, cada ffmpeg/ffprobe/claude abriria uma janela preta piscando.
    Vale para todo subprocess, inclusive os que o yt-dlp abre por dentro."""
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_job_handle = None


def end_children_with_app() -> None:
    """Coloca o app num Job Object do Windows: quando o Cortaí termina, de qualquer jeito, o
    sistema encerra junto os ffmpeg, yt-dlp e claude que ele abriu, em vez de deixá-los órfãos."""
    global _job_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (job and kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            and kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())):
        print(f"aviso: Job Object indisponível (erro {ctypes.get_last_error()})")
        return
    _job_handle = job  # fica aberto até o processo sair


def focus_existing() -> bool:
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


# ------------------------------------------------------------------ janela

def _busy() -> bool:
    from . import jobs, studio

    return jobs.busy() or studio.busy()


def _show_window(url: str, ask_before_close: bool) -> None:
    try:
        import webview
    except ImportError:  # rodando do código-fonte sem o pywebview instalado: usa o navegador
        print(f"pywebview não instalado; abrindo {url} no navegador. Ctrl+C encerra.")
        import webbrowser

        webbrowser.open(url)
        threading.Event().wait()
        return

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(TITLE, url, width=1440, height=900, min_size=(1000, 640), maximized=True,
                                   background_color="#0b0b10")
    if ask_before_close:
        def on_closing():
            if _busy() and not confirm("Ainda tem corte ou exportação em andamento.\n\n"
                                       "Fechar o Cortaí agora interrompe esse trabalho. Fechar mesmo assim?"):
                return False
            return True

        window.events.closing += on_closing
    webview.start(gui="edgechromium", localization={"windows.fileFilter.allFiles": "Todos os arquivos"})


def run_app(port: int) -> None:
    url = f"http://127.0.0.1:{port}"
    if is_running(url):  # segundo duplo clique: traz a janela que já está aberta
        if not focus_existing():
            _show_window(url, ask_before_close=False)
        return

    end_children_with_app()
    import uvicorn

    from .server import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="servidor", daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError(f"não foi possível usar a porta {port}; outro programa pode estar usando")
        time.sleep(0.05)

    _show_window(url, ask_before_close=True)
    server.should_exit = True
    thread.join(timeout=5)
    # Trabalho que o usuário aceitou interromper não deve manter o processo vivo sem janela;
    # o Job Object encerra os processos filhos junto.
    os._exit(0)
