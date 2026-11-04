"""字幕/转写文本的清洗与分块。

B站 AI 字幕是 8-20 字的碎片，直接喂模型效果很差，必须先按语义合并成段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SENT_END = "。！？!?…；;"
FILLER_RE = re.compile(r"^[嗯呃额哦噢啊诶欸]+[，,、\s]*")


@dataclass
class Segment:
    start: float
    end: float
    text: str

    @property
    def stamp(self) -> str:
        return fmt_time(self.start)


def fmt_time(seconds: float) -> str:
    s = int(max(0, seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _strip_filler(text: str) -> str:
    """只清掉段首孤立的语气词，避免误伤正常用词。"""
    prev = None
    out = text
    while prev != out:
        prev = out
        out = FILLER_RE.sub("", out)
    return out.strip()


def merge_cues(
    cues: list[dict],
    *,
    gap: float = 1.2,
    max_chars: int = 180,
) -> list[Segment]:
    """把碎片字幕按「句末标点 / 停顿 / 长度」合并成语义段。"""
    segments: list[Segment] = []
    buf: list[str] = []
    start = end = 0.0
    prev_end = 0.0

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        text = _strip_filler("".join(buf)).strip()
        if text:
            segments.append(Segment(start=start, end=end, text=text))
        buf = []

    for cue in cues:
        text = (cue.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        c_from = float(cue.get("from") or 0)
        c_to = float(cue.get("to") or c_from)

        if buf and (c_from - prev_end > gap or sum(len(b) for b in buf) >= max_chars):
            flush()

        if not buf:
            start = c_from
        buf.append(text)
        end = c_to
        prev_end = c_to

        if text and text[-1] in SENT_END:
            flush()

    flush()
    return segments


def render_line(segments: list[Segment]) -> str:
    """带时间戳的逐行文本，交给模型做「带时间戳的要点」。"""
    return "\n".join(f"[{seg.stamp}] {seg.text}" for seg in segments)


def chunk_segments(segments: list[Segment], max_chars: int = 3200) -> list[str]:
    """按段落边界切块，每块都带时间戳前缀。"""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for seg in segments:
        line = f"[{seg.stamp}] {seg.text}"
        if buf and size + len(line) > max_chars:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def total_chars(segments: list[Segment]) -> int:
    return sum(len(s.text) for s in segments)
