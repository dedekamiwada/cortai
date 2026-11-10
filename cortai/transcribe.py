"""Transcrição local com faster-whisper (GPU quando disponível), com timestamps por palavra."""
from __future__ import annotations

import glob
import os
import sysconfig
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np


def _expose_nvidia_dlls() -> None:
    """Os pacotes pip nvidia-cublas-cu12/nvidia-cudnn-cu12 trazem as DLLs da CUDA, mas o
    Windows não procura nas pastas deles. Sem isso o ctranslate2 não acha cublas64_12.dll
    e a transcrição cai para a CPU (~10x mais lenta)."""
    if os.name != "nt":
        return
    for d in glob.glob(os.path.join(sysconfig.get_paths()["purelib"], "nvidia", "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_expose_nvidia_dlls()
from faster_whisper import WhisperModel  # noqa: E402  (precisa vir depois das DLLs)

_models: dict[tuple[str, str], WhisperModel] = {}
_lock = threading.Lock()


def _load(name: str, device: str) -> WhisperModel:
    key = (name, device)
    with _lock:
        if key not in _models:
            compute = "float16" if device == "cuda" else "int8"
            _models[key] = WhisperModel(name, device=device, compute_type=compute)
        return _models[key]


def transcribe(
    audio: Path,
    model_name: str,
    language: str | None,
    duration: float,
    on_progress: Callable[[float], None] = lambda p: None,
    on_fallback: Callable[[str], None] = lambda reason: None,
) -> dict:
    try:
        return _run(_load(model_name, "cuda"), audio, language, duration, on_progress, "cuda")
    except Exception as exc:  # CUDA/cuDNN ausente só aparece na hora de transcrever
        gpu_error = str(exc)
    on_fallback(gpu_error)
    try:
        result = _run(_load(model_name, "cpu"), audio, language, duration, on_progress, "cpu")
    except Exception as exc:
        raise RuntimeError(f"falha na transcrição (GPU: {gpu_error}; CPU: {exc})") from exc
    result["gpu_error"] = gpu_error
    return result


def _load_wav(path: Path) -> np.ndarray:
    """Lê o WAV PCM 16 kHz mono gerado pelo ffmpeg (evita o decodificador PyAV do faster-whisper)."""
    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise RuntimeError("áudio precisa ser PCM 16 kHz mono")
        pcm = wav.readframes(wav.getnframes())
    return np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0


def _run(model, audio, language, duration, on_progress, device) -> dict:
    segments_iter, info = model.transcribe(
        _load_wav(audio),
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )
    segments = []
    for seg in segments_iter:
        words = [
            {"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
            for w in (seg.words or [])
            if w.word.strip()
        ]
        if not words:
            continue
        segments.append({"s": round(seg.start, 3), "e": round(seg.end, 3), "text": seg.text.strip(), "words": words})
        if duration:
            on_progress(min(seg.end / duration, 1.0))
    return {"language": info.language, "device": device, "segments": segments}


def all_words(transcript: dict) -> list[dict]:
    return [w for seg in transcript["segments"] for w in seg["words"]]
