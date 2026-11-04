"""冒烟测试：不跑 LLM，只验证 B站取数与 ASR 链路。

用法：.venv\\Scripts\\python.exe tools\\smoke.py BV1xxxxxxxxx
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bilisum.bili import BiliClient  # noqa: E402
from bilisum.config import load_config  # noqa: E402
from bilisum.urls import parse_input  # noqa: E402


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    raw = args[0] if args else "BV1Jonu6mEwN"
    with_asr = "--asr" in flags
    info_only = "--info-only" in flags
    cfg = load_config()
    ref = parse_input(raw)
    print("parsed:", ref)

    async with BiliClient(cfg.sessdata) as bili:
        info = await bili.video_info(bvid=ref.bvid, aid=ref.aid, ep_id=ref.ep_id, page=ref.page)
        print(f"title: {info.title}")
        print(f"owner: {info.owner} | dur: {info.duration_text} | cid: {info.cid}")
        print(f"分P: 第 {info.page} / 共 {info.page_count} P | 本P标题: {info.page_title}")

        if info.page_count > 1:
            pages = await bili.page_list(info.bvid)
            for p in pages[:5]:
                mark = " <== 当前" if int(p.get("page", 0)) == info.page else ""
                print(f"  P{p.get('page')} | {p.get('duration')}s | {p.get('part')}{mark}")
            if len(pages) > 5:
                print(f"  … 其余 {len(pages) - 5} P 省略")

        if info_only:
            return

        tracks = await bili.subtitle_tracks(info)
        print(f"subtitle tracks: {len(tracks)} (cookie={'yes' if cfg.sessdata else 'no'})")
        if bili.subtitle_error:
            print(f"  subtitle_error: {bili.subtitle_error}")
        for t in tracks:
            print("  -", t.lan, t.lan_doc, t.url[:60])

        pick = bili.pick_track(tracks, cfg.subtitle_langs)
        if pick:
            cues = await bili.load_cues(pick)
            print(f"cues: {len(cues)}  first: {cues[0] if cues else None}")

        stream = await bili.audio_stream(info)
        print(f"audio: bw={stream.bandwidth} urls={len(stream.urls)} mime={stream.mime}")
        dest = Path("data/tmp/probe.m4a")
        await bili.download_audio(stream, dest, on_progress=lambda p: None)
        print(f"downloaded: {dest} {dest.stat().st_size / 1024 / 1024:.1f} MB")

    if with_asr:
        from bilisum import asr, clean

        print("running ASR (this may download the model on first run)...")
        cues = await asyncio.to_thread(
            asr.transcribe,
            dest,
            model_name=cfg.whisper_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute,
            on_progress=lambda p: None,
        )
        print(f"asr cues: {len(cues)}")
        for seg in clean.merge_cues(cues)[:5]:
            print(f"  {seg.stamp} | {seg.text}")


asyncio.run(main())
