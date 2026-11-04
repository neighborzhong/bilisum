"""本地语音识别兜底：faster-whisper。

不依赖系统 ffmpeg —— 解码走 PyAV 自带的二进制；模型权重下载到项目 data/ 下，
不占 C 盘空间。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from .config import DATA_DIR

MODEL_DIR = DATA_DIR / "models"
_MODEL_LOCK = threading.Lock()
_MODELS: dict[str, object] = {}


def _ensure_model_dir() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(DATA_DIR / "hf"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(DATA_DIR / "hf" / "hub"))
    # 国内网络下 huggingface.co 常不可达；默认走镜像，可用 BILISUM_HF_ENDPOINT 覆盖
    endpoint = os.environ.get("BILISUM_HF_ENDPOINT")
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint
    else:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def is_downloaded(name: str) -> bool:
    marker = MODEL_DIR / f".ready-{name}"
    return marker.exists()


def get_model(name: str, device: str = "cpu", compute_type: str = "int8"):
    """按 (name, device, compute) 缓存模型实例，避免每次重新加载。"""
    key = f"{name}|{device}|{compute_type}"
    with _MODEL_LOCK:
        if key in _MODELS:
            return _MODELS[key]
        _ensure_model_dir()
        from faster_whisper import WhisperModel  # 延迟导入，加快 Web 启动

        model = WhisperModel(
            name,
            device=device,
            compute_type=compute_type,
            download_root=str(MODEL_DIR),
        )
        (MODEL_DIR / f".ready-{name}").write_text("ok", "utf-8")
        _MODELS[key] = model
        return model


def transcribe(
    audio_path: Path,
    *,
    model_name: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str = "zh",
    on_progress=None,
) -> list[dict]:
    """转写音频，返回 [{from, to, text}]，与字幕 cue 结构一致。"""
    model = get_model(model_name, device, compute_type)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )

    total = float(getattr(info, "duration", 0) or 0)
    cues: list[dict] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        cues.append({"from": float(seg.start), "to": float(seg.end), "text": text})
        if on_progress and total > 0:
            on_progress(min(1.0, float(seg.end) / total))

    if on_progress:
        on_progress(1.0)
    return cues
