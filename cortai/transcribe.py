"""Transcrição local com faster-whisper (GPU quando disponível), com timestamps por palavra."""
from __future__ import annotations

import os
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from . import paths


def _expose_nvidia_dlls() -> None:
    """Os pacotes pip nvidia-cublas-cu12/nvidia-cudnn-cu12 trazem as DLLs da CUDA, mas o
    Windows não procura nas pastas deles. Sem isso o ctranslate2 não acha cublas64_12.dll
    e a transcrição cai para a CPU (~10x mais lenta)."""
    if os.name != "nt":
        return
    for d in paths.nvidia_dll_dirs():
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


SAMPLE_RATE = 16000
# O faster-whisper monta o espectrograma do áudio inteiro de uma vez: numa live de 3 h isso
# passa de 2 GB de RAM. Em blocos de 10 min o pico fica em ~0,3 GB, qualquer que seja a duração.
BLOCK_SECONDS = 600
CUT_SEARCH_SECONDS = 15  # o corte entre blocos cai no trecho mais silencioso desta janela


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
    except MemoryError:
        raise RuntimeError("memória RAM insuficiente para transcrever; feche outros programas e tente de novo") from None
    except Exception as exc:  # CUDA/cuDNN ausente ou sem memória de vídeo só aparece na hora de transcrever
        gpu_error = str(exc)
    on_fallback(gpu_error)
    try:
        result = _run(_load(model_name, "cpu"), audio, language, duration, on_progress, "cpu")
    except Exception as exc:
        raise RuntimeError(f"falha na transcrição (GPU: {gpu_error}; CPU: {exc})") from exc
    result["gpu_error"] = gpu_error
    return result


def _read(wav: wave.Wave_read, start: int, count: int) -> np.ndarray:
    wav.setpos(start)
    return np.frombuffer(wav.readframes(count), np.int16).astype(np.float32) / 32768.0


def _quietest(samples: np.ndarray) -> int:
    """Índice do centro do trecho de 100 ms com menos energia (onde ninguém está no meio de uma palavra)."""
    hop = SAMPLE_RATE // 10
    usable = len(samples) // hop * hop
    if not usable:
        return len(samples) // 2
    energy = np.square(samples[:usable].reshape(-1, hop)).mean(axis=1)
    return int(energy.argmin()) * hop + hop // 2


def _blocks(wav: wave.Wave_read):
    """Lê o WAV PCM 16 kHz mono (gerado pelo ffmpeg) em blocos, sem carregar o arquivo inteiro.
    Devolve (início em amostras, amostras do bloco)."""
    if wav.getframerate() != SAMPLE_RATE or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
        raise RuntimeError("áudio precisa ser PCM 16 kHz mono")
    total, start = wav.getnframes(), 0
    block, search = BLOCK_SECONDS * SAMPLE_RATE, CUT_SEARCH_SECONDS * SAMPLE_RATE
    while start < total:
        end = start + block
        if end + search >= total:
            end = total
        else:
            end = end - search + _quietest(_read(wav, end - search, 2 * search))
        yield start, _read(wav, start, end - start)
        start = end


def _run(model, audio, language, duration, on_progress, device) -> dict:
    segments, detected, context = [], language or None, None
    with wave.open(str(audio), "rb") as wav:
        for first_sample, samples in _blocks(wav):
            offset = first_sample / SAMPLE_RATE
            segments_iter, info = model.transcribe(
                samples,
                language=detected,
                word_timestamps=True,
                vad_filter=True,
                beam_size=5,
                initial_prompt=context,  # continuidade de nomes e gírias entre blocos
            )
            detected = detected or info.language  # o 1º bloco decide; evita trocar de idioma no meio
            for seg in segments_iter:
                words = [
                    {"w": w.word.strip(), "s": round(w.start + offset, 3), "e": round(w.end + offset, 3)}
                    for w in (seg.words or [])
                    if w.word.strip()
                ]
                if not words:
                    continue
                segments.append({"s": round(seg.start + offset, 3), "e": round(seg.end + offset, 3),
                                 "text": seg.text.strip(), "words": words})
                if duration:
                    on_progress(min((seg.end + offset) / duration, 1.0))
            context = " ".join(s["text"] for s in segments[-3:])[-200:] or None
    return {"language": detected, "device": device, "segments": segments}


def all_words(transcript: dict) -> list[dict]:
    return [w for seg in transcript["segments"] for w in seg["words"]]
