"""Onde o Cortaí acha seus arquivos, rodando do código-fonte ou da pasta portátil."""
from __future__ import annotations

import glob
import os
import sys
import sysconfig
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
# Pasta do Cortai.exe na versão portátil; raiz do repositório no código-fonte.
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
# Onde ficam os arquivos que acompanham o programa (_internal na versão portátil).
BUNDLE = Path(getattr(sys, "_MEIPASS", APP_DIR))
STATIC = BUNDLE / "static"


def _jobs_dir() -> Path:
    """Na versão portátil os dados ficam fora da pasta do programa, para sobreviver a uma
    atualização. Um dados.txt ao lado do Cortai.exe escolhe outro lugar (um HD maior, por exemplo)."""
    chosen = os.environ.get("CORTAI_JOBS_DIR")
    if chosen:
        return Path(chosen)
    if not FROZEN:
        return APP_DIR / "jobs"
    pointer = APP_DIR / "dados.txt"
    if pointer.exists():
        chosen = pointer.read_text(encoding="utf-8-sig").strip()
        if chosen:
            return Path(chosen)
    return Path.home() / "Videos" / "Cortai"


JOBS_DIR = _jobs_dir()


def expose_tools() -> None:
    """Põe o ffmpeg/ffprobe empacotados à frente do PATH, para valer também nos processos filhos."""
    if FROZEN:
        os.environ["PATH"] = str(BUNDLE / "bin") + os.pathsep + os.environ.get("PATH", "")


def nvidia_dll_dirs() -> list[str]:
    root = str(BUNDLE) if FROZEN else sysconfig.get_paths()["purelib"]
    return glob.glob(os.path.join(root, "nvidia", "*", "bin"))


def yt_dlp_command() -> list[str]:
    """Numa versão empacotada não existe "python -m": o próprio Cortai.exe executa o yt-dlp."""
    return [sys.executable, "--run-yt-dlp"] if FROZEN else [sys.executable, "-m", "yt_dlp"]
