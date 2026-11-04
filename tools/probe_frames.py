"""原型验证：从 B站视频流抽帧，并用本机 Windows OCR 识别画面文字。

用法：.venv\\Scripts\\python.exe tools\\probe_frames.py <BV号> [P号] [抽帧秒数...]
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bilisum.bili import BiliClient  # noqa: E402
from bilisum.config import DATA_DIR, load_config  # noqa: E402

OUT = DATA_DIR / "tmp" / "frames"
# 本机 WinRT OCR 脚本：优先用项目内的，其次借用工作区 .reasonix 里已有的
_OCR_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "tools" / "win_ocr.ps1",
    Path(__file__).resolve().parents[2] / ".reasonix" / "tools" / "win_ocr.ps1",
]
OCR_PS1 = next((p for p in _OCR_CANDIDATES if p.exists()), _OCR_CANDIDATES[0])


async def download_video_head(info, bili: BiliClient, limit_mb: int = 12) -> Path:
    """只下视频流开头一小段，够解码前若干帧即可。"""
    signed = await bili._signed(
        "https://api.bilibili.com/x/player/wbi/playurl",
        {"bvid": info.bvid, "cid": info.cid, "fnval": 4048, "fnver": 0, "fourk": 1},
    )
    data = await bili._get_json(signed, where="playurl")
    videos = (data.get("dash") or {}).get("video") or []
    # 优先 1080p，其次 720p（OCR 对分辨率非常敏感，360p 完全读不出代码）
    for qid in (80, 64, 32):
        cands = [v for v in videos if v.get("id") == qid]
        if cands:
            break
    else:
        cands = videos
    avc = [v for v in cands if "avc1" in (v.get("codecs") or "")]
    best = (avc or cands)[0]
    print(f"选用清晰度 id={best.get('id')} {best.get('width')}x{best.get('height')} {best.get('codecs')}")

    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{info.bvid}-{info.cid}-{best.get('id')}.mp4"
    if dest.exists() and dest.stat().st_size > 1024 * 1024:
        print(f"复用已下载片段 {dest.stat().st_size / 1e6:.1f} MB")
        return dest

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bilibili.com/",
    }
    async with bili._client.stream("GET", best["baseUrl"], headers=headers) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes(1 << 16):
                fh.write(chunk)
    print(f"已下载 {dest.stat().st_size / 1e6:.1f} MB -> {dest.name}")
    return dest


def grab_frames(video: Path, seconds: list[float]) -> list[Path]:
    import av

    OUT.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        for t in seconds:
            try:
                container.seek(int(t / stream.time_base), stream=stream, backward=True)
            except Exception:
                pass
            got = None
            for frame in container.decode(stream):
                got = frame
                break
            if got is None:
                continue
            path = OUT / f"frame-{int(t):05d}.jpg"
            got.to_image().save(str(path), quality=90)
            out.append(path)
    return out


def ocr(path: Path) -> str:
    if not OCR_PS1.exists():
        return "(win_ocr.ps1 不存在)"
    txt = path.with_suffix(".txt")
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(OCR_PS1), "-Path", str(path), "-Lang", "zh-Hans-CN", "-OutFile", str(txt),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
        raw = txt.read_text("utf-8", errors="replace").strip() if txt.exists() else ""
    except Exception as exc:  # noqa: BLE001
        return f"(OCR 失败: {exc})"
    # WinRT OCR 会在每个汉字之间插空格，必须去掉才能看
    import re as _re

    raw = _re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", raw)
    raw = _re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[，。、；：！？（）《》])", "", raw)
    return raw.strip()


async def main() -> None:
    bvid = sys.argv[1] if len(sys.argv) > 1 else "BV1yGydYEE3H"
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 79
    secs = [float(x) for x in sys.argv[3:]] or [5, 30, 120, 300]

    cfg = load_config()
    async with BiliClient(cfg.sessdata) as bili:
        info = await bili.video_info(bvid=bvid, page=page)
        print(f"《{info.title}》 P{info.page} {info.duration_text}")
        video = await download_video_head(info, bili)

    frames = grab_frames(video, secs)
    print(f"抽出 {len(frames)} 帧")
    for p in frames:
        text = ocr(p)
        one_line = " ".join(text.split())
        print(f"\n--- {p.name} ({p.stat().st_size // 1024} KB) ---")
        print(one_line[:400] if one_line else "(空)")


asyncio.run(main())
