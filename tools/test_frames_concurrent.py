"""并发验证：同时跑两次画面提取，确认不再因共享目录冲突报 WinError 5。

用法：.venv\\Scripts\\python.exe tools\\test_frames_concurrent.py [BV号] [P号]
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bilisum import frames  # noqa: E402
from bilisum.bili import BiliClient  # noqa: E402
from bilisum.config import load_config  # noqa: E402


async def main() -> None:
    bvid = sys.argv[1] if len(sys.argv) > 1 else "BV1yGydYEE3H"
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 79
    cfg = load_config()

    async with BiliClient(cfg.sessdata) as bili:
        info = await bili.video_info(bvid=bvid, page=page)
    print(f"《{info.title[:40]}》 P{info.page} {info.duration_text}")

    results: list[str] = []

    async def one(tag: str) -> None:
        t0 = time.time()
        async with BiliClient(cfg.sessdata) as b:
            try:
                segs, detail = await frames.extract(b, info, interval=15, max_frames=240)
                msg = f"[{tag}] 成功 {len(segs)} 段，{time.time() - t0:.1f}s | {detail}"
            except Exception as exc:  # noqa: BLE001
                msg = f"[{tag}] 失败 {type(exc).__name__}: {exc}"
        print(msg)
        results.append(msg)

    # 两个任务同时抽帧、同时 OCR、同时清理——之前就是这里撞车
    await asyncio.gather(one("并发A"), one("并发B"))

    leftover = (
        [p for p in frames.FRAMES_ROOT.iterdir() if p.is_dir()]
        if frames.FRAMES_ROOT.exists()
        else []
    )
    cached = list(frames.FRAMES_ROOT.glob("*.mp4")) if frames.FRAMES_ROOT.exists() else []
    print(f"\n残留帧目录：{len(leftover)} 个（应为 0）")
    for p in leftover:
        print("  ", p.name)
    print(f"视频缓存：{len(cached)} 个，共 {sum(p.stat().st_size for p in cached) / 1e6:.0f} MB（保留以复用，3 天自动清）")

    ok = sum(1 for r in results if "成功" in r)
    print(f"\n结论：{ok}/2 成功" + ("（并发安全）" if ok == 2 else "（仍有问题）"))
    if leftover:
        print("警告：抽帧目录没清干净，会占磁盘")


asyncio.run(main())
