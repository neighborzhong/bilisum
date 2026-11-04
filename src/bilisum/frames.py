"""画面文字提取：下载视频流 → 按间隔抽帧 → 批量 OCR → 去重去噪。

为什么不是均匀抽帧后直接交给模型：
- 教程/PPT 类视频信息几乎全在屏幕上（代码、终端、浏览器），字幕完全拿不到；
- 但 OCR 会带出大量固定 UI 噪声（PPT 标题栏、网站名、浏览器界面），必须按出现频率剔除；
- 相邻帧内容常常完全一样，必须去重，否则同一段代码会重复塞给模型。

实测要点：360p 分辨率下 OCR 完全不可用（代码全糊），必须 720p 以上，优先 1080p。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .bili import BiliClient, VideoInfo
from .clean import Segment, fmt_time
from .config import DATA_DIR, RESOURCE_ROOT

FRAMES_ROOT = DATA_DIR / "tmp" / "frames"
OCR_SCRIPT = RESOURCE_ROOT / "tools" / "win_ocr_batch.ps1"

CJK_GAP = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
CJK_PUNCT_GAP = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[，。、；：！？（）《》])")

# 每帧最多保留多少字（超长会挤占模型上下文）
MAX_CHARS_PER_FRAME = 600


@dataclass
class FrameText:
    at: float
    text: str

    @property
    def stamp(self) -> str:
        return fmt_time(self.at)


def _safe_rmtree(path: Path, retries: int = 3) -> None:
    """删除目录，容忍 Windows 上的文件占用与只读属性。

    之前固定目录名在并发时会被另一个任务占用，导致 WinError 5。
    """
    for _ in range(retries):
        if not path.exists():
            return
        for f in path.rglob("*"):
            try:
                if f.is_file():
                    f.chmod(0o666)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.2)


def _clean_text(raw: str) -> str:
    """WinRT OCR 会在每个汉字之间插空格，先去空格再按行整理。"""
    lines: list[str] = []
    for line in raw.splitlines():
        line = CJK_GAP.sub("", line.strip())
        line = CJK_PUNCT_GAP.sub("", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _drop_common_lines(texts: list[str], threshold: float = 0.55) -> list[str]:
    """删掉「几乎每帧都出现」的行——那多半是 PPT 标题栏、网站名、浏览器 UI。"""
    if len(texts) < 3:
        return texts
    counts: dict[str, int] = {}
    for t in texts:
        for line in set(t.splitlines()):
            counts[line] = counts.get(line, 0) + 1
    limit = max(2, int(len(texts) * threshold))
    noisy = {line for line, n in counts.items() if n >= limit}
    out: list[str] = []
    for t in texts:
        kept = [line for line in t.splitlines() if line not in noisy]
        out.append("\n".join(kept))
    return out


def _similar(a: str, b: str) -> float:
    """字符集合 Jaccard，够快也够用。"""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, len(sa | sb))


def _dedupe(items: list[FrameText], threshold: float = 0.8) -> list[FrameText]:
    """相邻帧内容几乎一样就丢掉，避免同一段代码反复喂给模型。"""
    kept: list[FrameText] = []
    for item in items:
        if not item.text.strip():
            continue
        if kept and _similar(kept[-1].text, item.text) >= threshold:
            continue
        kept.append(item)
    return kept


def _prune_old_videos(days: int = 3) -> None:
    """清理旧的视频流缓存（每个 20-200MB，不清会无限涨）。抽帧目录用完就删，不在此列。"""
    cutoff = time.time() - days * 86400
    for p in FRAMES_ROOT.glob("*.mp4"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
        except OSError:
            pass


async def _download_video(
    bili: BiliClient, info: VideoInfo, on_progress: Callable[[float], None] | None = None
) -> tuple[Path, str]:
    signed = await bili._signed(
        "https://api.bilibili.com/x/player/wbi/playurl",
        {"bvid": info.bvid, "cid": info.cid, "fnval": 4048, "fnver": 0, "fourk": 1},
    )
    data = await bili._get_json(signed, where="wbi/playurl")
    videos = (data.get("dash") or {}).get("video") or []
    if not videos:
        raise RuntimeError("这个视频没有可用的视频流（可能是纯音频/互动视频）")

    # OCR 对分辨率极其敏感：优先 1080p，其次 720p，最后 480p
    cands: list[dict] = []
    for qid in (80, 64, 32):
        cands = [v for v in videos if v.get("id") == qid]
        if cands:
            break
    if not cands:
        cands = videos
    avc = [v for v in cands if "avc1" in (v.get("codecs") or "")]
    best = (avc or cands)[0]
    label = f"{best.get('width')}x{best.get('height')}"

    FRAMES_ROOT.mkdir(parents=True, exist_ok=True)
    _prune_old_videos()
    dest = FRAMES_ROOT / f"{info.bvid}-{info.cid}-{best.get('id')}.mp4"
    if dest.exists() and dest.stat().st_size > 512 * 1024:
        return dest, label

    urls = [best.get("baseUrl") or best.get("base_url")] + list(
        best.get("backupUrl") or best.get("backup_url") or []
    )
    # 先下到临时名再原子重命名，避免并发任务互写同一个文件
    part = dest.with_suffix(dest.suffix + f".{uuid.uuid4().hex[:6]}.part")
    last_err: Exception | None = None
    for url in [u for u in urls if u]:
        try:
            async with bili._client.stream(
                "GET", url, headers={"Referer": "https://www.bilibili.com/"}
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                last_pct = -1
                with part.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(1 << 16):
                        fh.write(chunk)
                        done += len(chunk)
                        if on_progress and total:
                            pct = int(done * 100 / total)
                            if pct != last_pct:
                                last_pct = pct
                                on_progress(done / total)
            if part.exists() and part.stat().st_size > 512 * 1024:
                try:
                    os.replace(part, dest)
                except OSError:
                    pass  # 另一个任务刚好写完了，直接用它那份
                return dest, label
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            await asyncio.sleep(0.3)
    part.unlink(missing_ok=True)
    raise RuntimeError(f"视频流下载失败：{last_err}")


def _grab_frames(video: Path, out_dir: Path, interval: float, max_frames: int) -> list[tuple[float, Path]]:
    import av

    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[float, Path]] = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        try:
            duration = float(container.duration / 1000000) if container.duration else 0.0
        except Exception:  # noqa: BLE001
            duration = 0.0
        if duration <= 0:
            duration = 600.0
        step = max(interval, duration / max_frames)
        t = min(5.0, duration / 2)
        while t < duration and len(saved) < max_frames:
            try:
                container.seek(int(t / stream.time_base), stream=stream, backward=True)
            except Exception:  # noqa: BLE001
                t += step
                continue
            frame = None
            for f in container.decode(stream):
                frame = f
                break
            if frame is not None:
                path = out_dir / f"f{int(t):06d}.jpg"
                try:
                    frame.to_image().save(str(path), quality=85)
                    saved.append((t, path))
                except Exception:  # noqa: BLE001
                    pass
            t += step
    return saved


def _ocr_dir(frame_dir: Path) -> dict[str, str]:
    out = frame_dir / "ocr.json"
    if out.exists():
        out.unlink()
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(OCR_SCRIPT), "-Dir", str(frame_dir), "-OutFile", str(out),
        "-Lang", "zh-Hans-CN",
    ]
    subprocess.run(cmd, capture_output=True, timeout=600)
    if not out.exists():
        raise RuntimeError("OCR 没产出结果，检查 tools/win_ocr_batch.ps1 是否可用")
    data = json.loads(out.read_text("utf-8"))
    if isinstance(data, dict):  # 只有一张图时 ConvertTo-Json 会返回对象
        data = [data]
    return {item.get("name", ""): item.get("text", "") for item in data}


async def extract(
    bili: BiliClient,
    info: VideoInfo,
    *,
    interval: int = 30,
    max_frames: int = 120,
    on_progress: Callable[[str, float], None] | None = None,
) -> tuple[list[Segment], str]:
    """返回 (画面文字片段, 描述)，失败时抛异常由调用方决定是否忽略。"""
    if not OCR_SCRIPT.exists():
        raise RuntimeError(f"找不到 OCR 脚本：{OCR_SCRIPT}")

    def emit(msg: str, pct: float) -> None:
        if on_progress:
            on_progress(msg, pct)

    emit("准备下载视频流…", 0.02)

    def dl_progress(p: float) -> None:
        emit(f"下载视频流 {p * 100:.0f}%", 0.02 + 0.38 * p)

    video, label = await _download_video(bili, info, on_progress=dl_progress)
    emit(f"视频流就绪（{label}，{video.stat().st_size / 1e6:.0f} MB）", 0.42)

    run_id = uuid.uuid4().hex[:8]
    frame_dir = FRAMES_ROOT / f"{info.bvid}-{info.cid}-{run_id}"
    try:
        frames = await asyncio.to_thread(_grab_frames, video, frame_dir, float(interval), max_frames)
        if not frames:
            raise RuntimeError("抽帧失败，可能是编码格式不支持")
        emit(f"抽出 {len(frames)} 帧", 0.5)

        ocr_map = await asyncio.to_thread(_ocr_dir, frame_dir)
        emit(f"OCR 完成（{len(ocr_map)} 帧）", 0.85)

        items: list[FrameText] = []
        for at, path in frames:
            raw = ocr_map.get(path.name, "")
            text = _clean_text(raw)
            if len(text) > MAX_CHARS_PER_FRAME:
                text = text[:MAX_CHARS_PER_FRAME] + "…"
            items.append(FrameText(at=at, text=text))

        items = [FrameText(at=i.at, text=t) for i, t in zip(items, _drop_common_lines([i.text for i in items]))]
        before = len(items)
        items = _dedupe(items)
        emit(f"去噪去重后保留 {len(items)}/{before} 帧有效画面", 0.95)

        segments = [
            Segment(start=it.at, end=it.at, text=f"〔画面〕{it.text.replace(chr(10), ' / ')}")
            for it in items
            if it.text.strip()
        ]
        detail = f"画面 OCR（{label}，{len(frames)} 帧 → {len(segments)} 段有效文字）"
        return segments, detail
    finally:
        # 每次运行用独立目录，结束即清，并发任务之间互不干扰
        _safe_rmtree(frame_dir)
