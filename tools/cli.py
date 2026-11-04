"""命令行总结：python tools/cli.py <链接> [--save]

Web 之外的另一条入口，也用作端到端验证。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bilisum.config import DATA_DIR, load_config  # noqa: E402
from bilisum.pipeline import render_note, summarize_url  # noqa: E402


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print("用法: python tools/cli.py <B站链接或BV号> [--save]")
        return

    url = args[0]
    cfg = load_config()
    if "--frames" in flags:
        cfg.extract_frames = True
    t0 = time.time()

    def emit(stage: str, message: str, percent: float) -> None:
        print(f"  [{percent:5.1f}%] {message}", flush=True)

    result = await summarize_url(url, cfg, emit)
    note = render_note(result)
    print("\n" + "=" * 60)
    print(note)
    print("=" * 60)
    print(
        f"来源={result.source_detail} 字数={result.chars} "
        f"耗时={time.time() - t0:.1f}s"
    )

    if "--save" in flags:
        notes = DATA_DIR / "notes"
        notes.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in result.info.title if c not in '\\/:*?"<>|')[:60]
        path = notes / f"{safe or 'bilibili'}.md"
        path.write_text(note, "utf-8")
        print(f"已保存：{path}")


asyncio.run(main())
