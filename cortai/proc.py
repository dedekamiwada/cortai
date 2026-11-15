"""Processos filhos (ffmpeg, yt-dlp, Claude Code) por projeto, para o botão Cancelar encerrá-los.

Cada tarefa do Cortaí roda numa thread do worker; `set_job` marca de qual projeto ela é. Todo
`subprocess.Popen` aberto nessa thread (inclusive por `subprocess.run`) fica registrado no projeto, e
`kill_job` encerra a árvore inteira de cada um (o Claude Code abre processos filhos próprios)."""
from __future__ import annotations

import subprocess
import sys
import threading

_ctx = threading.local()
_lock = threading.Lock()
_procs: dict[str, set] = {}
_Popen = subprocess.Popen


class _TrackedPopen(_Popen):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        job = getattr(_ctx, "job", None)
        if job:
            with _lock:
                live = {p for p in _procs.get(job, ()) if p.poll() is None}
                live.add(self)
                _procs[job] = live


def install() -> None:
    """Uma vez, ao subir o servidor: `subprocess.run` e `Popen` passam a registrar os processos."""
    subprocess.Popen = _TrackedPopen


def set_job(job_id: str | None) -> None:
    _ctx.job = job_id


def forget(job_id: str) -> None:
    with _lock:
        _procs.pop(job_id, None)


def kill_job(job_id: str) -> int:
    """Encerra os processos vivos do projeto (com os filhos). Devolve quantos encerrou."""
    with _lock:
        procs = [p for p in _procs.get(job_id, ()) if p.poll() is None]
    for p in procs:
        try:
            if sys.platform == "win32":
                _Popen(["taskkill", "/PID", str(p.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).wait(10)
            else:
                p.kill()
        except Exception:
            pass  # já tinha terminado
    return len(procs)
