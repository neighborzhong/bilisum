"""配置加载：环境变量 > 页面保存的 data/settings.json > config.toml > 默认值。"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def _roots() -> tuple[Path, Path]:
    """返回 (只读资源根, 可写数据根)。

    打包成 exe 后：静态文件/脚本在 PyInstaller 的 _MEIPASS 里，
    data/ 和 config.toml 放在 exe 旁边（用户可写、可持续累积）。
    """
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle, Path(sys.executable).parent
    base = Path(__file__).resolve().parents[2]
    return base, base


RESOURCE_ROOT, APP_ROOT = _roots()
ROOT = APP_ROOT
DATA_DIR = APP_ROOT / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
CONFIG_FILE = APP_ROOT / "config.toml"

# 字幕语言优先级：人工 CC 优先，其次 B 站 AI 字幕，最后是外语
LANG_PRIORITY = ["zh-Hans", "zh-CN", "zh", "ai-zh", "en-US", "ai-en"]


@dataclass
class Config:
    # --- B站凭证：留空则跳过字幕、直接走音频 ASR ---
    sessdata: str = ""

    # --- 本地模型 ---
    ollama_base: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    num_ctx: int = 8192
    temperature: float = 0.25
    chunk_chars: int = 3200

    # --- ASR 兜底 ---
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"

    # --- 画面文字提取（OCR，适用于教程/PPT/代码类视频）---
    extract_frames: bool = False
    frame_interval: int = 15      # 每隔多少秒抽一帧
    max_frames: int = 240         # 最多抽多少帧

    # --- 输出形态 ---
    # note = 完整学习笔记（保留所有知识点、代码、命令、参数，不压缩）
    # summary = 精简摘要（5-9 条要点，适合快速了解视频讲什么）
    output_mode: str = "note"

    # --- 其他 ---
    subtitle_langs: list[str] = field(default_factory=lambda: list(LANG_PRIORITY))
    keep_audio: bool = False
    host: str = "127.0.0.1"
    port: int = 8848


def _extract_sessdata(raw: str) -> str:
    """允许用户整段粘贴浏览器 Cookie，自动抠出 SESSDATA。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    m = re.search(r"SESSDATA\s*=\s*([^;\s]+)", raw)
    if m:
        return m.group(1)
    return raw


def _coerce(raw: dict) -> dict:
    """把 settings.json / config.toml 的松散值对齐到 dataclass 字段类型。"""
    types = {f.name: f.type for f in fields(Config)}
    out: dict = {}
    for k, v in raw.items():
        if k not in types:
            continue
        if k == "sessdata":
            out[k] = _extract_sessdata(str(v))
        elif k == "subtitle_langs":
            out[k] = [str(x) for x in v] if isinstance(v, (list, tuple)) else LANG_PRIORITY
        elif k in ("num_ctx", "chunk_chars", "port", "frame_interval", "max_frames"):
            out[k] = int(v)
        elif k == "temperature":
            out[k] = float(v)
        elif k in ("keep_audio", "extract_frames"):
            out[k] = bool(v)
        else:
            out[k] = str(v)
    return out


def load_config() -> Config:
    merged: dict = {}

    # config.toml（用户手写，作为长期配置）
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("rb") as fh:
                merged.update(_coerce(tomllib.load(fh)))
        except Exception:
            pass

    # data/settings.json（Web 页面保存，优先级更高）
    if SETTINGS_FILE.exists():
        try:
            merged.update(_coerce(json.loads(SETTINGS_FILE.read_text("utf-8"))))
        except Exception:
            pass

    # 环境变量最高优先
    env_map = {
        "BILISUM_SESSDATA": "sessdata",
        "BILISUM_OLLAMA_BASE": "ollama_base",
        "BILISUM_OLLAMA_MODEL": "ollama_model",
        "BILISUM_WHISPER_MODEL": "whisper_model",
        "BILISUM_WHISPER_DEVICE": "whisper_device",
    }
    for env_key, field_name in env_map.items():
        val = os.environ.get(env_key)
        if val:
            merged.update(_coerce({field_name: val}))

    return Config(**merged)


def save_settings(patch: dict) -> Config:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    current: dict = {}
    if SETTINGS_FILE.exists():
        try:
            current = json.loads(SETTINGS_FILE.read_text("utf-8"))
        except Exception:
            current = {}
    current.update(_coerce(patch))
    SETTINGS_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), "utf-8")
    return load_config()


def public_view(cfg: Config) -> dict:
    """给前端看的配置（SESSDATA 打码）。"""
    data = asdict(cfg)
    sd = cfg.sessdata
    data["sessdata"] = ("****" + sd[-4:]) if len(sd) > 8 else ("已设置" if sd else "")
    data["sessdata_set"] = bool(sd)
    return data
