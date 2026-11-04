"""验证 Ollama 连接与 map-reduce 总结链路（不依赖 B站）。"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bilisum import clean, llm  # noqa: E402
from bilisum.config import load_config  # noqa: E402

FAKE = (
    "大家好，今天我们来聊一个很多人关心的问题：本地大模型到底能不能替代云端API。"
    "先说结论，在大部分个人场景下，答案是能，但有前提。"
    "第一个前提是显存。七B参数的模型量化到四个比特之后大概需要四点五G显存，"
    "如果你的显卡有八G显存，跑起来是比较舒服的。"
    "第二个前提是上下文长度。本地推理的上下文窗口受显存限制，"
    "八G显存一般只能开到八千个token左右，超过就会开始频繁换页，速度掉得很厉害。"
    "第三个部分是成本，云端API按token收费，一千万token大概几十块钱，"
    "而本地推理的电费成本几乎可以忽略，但你要算上显卡的折旧。"
    "所以我给出的建议是：日常高频的简单任务放本地，复杂的推理任务仍然走云端。"
    "最后提醒一句，本地模型的幻觉比云端大模型更明显，重要结论一定要人工复核。"
)


async def main() -> None:
    cfg = load_config()
    cues = [
        {"from": i * 6.0, "to": i * 6.0 + 5.0, "text": FAKE[i * 40 : (i + 1) * 40]}
        for i in range(len(FAKE) // 40 + 1)
    ]
    segments = clean.merge_cues(cues, gap=30.0)
    chunks = clean.chunk_segments(segments, max_chars=200)
    print(f"segments={len(segments)} chunks={len(chunks)}")

    async with llm.OllamaClient(
        base=cfg.ollama_base,
        model=cfg.ollama_model,
        num_ctx=cfg.num_ctx,
        temperature=cfg.temperature,
    ) as client:
        health = await client.health()
        print("health:", health)
        if not health["ok"]:
            return
        t0 = time.time()
        out = await llm.summarize(
            client,
            chunks,
            title="本地大模型能否替代云端API",
            chunk_chars=200,
            on_step=lambda msg, p: print(f"  [{p:.0%}] {msg}"),
        )
        print(f"\n--- 摘要（耗时 {time.time() - t0:.1f}s）---\n{out}")


asyncio.run(main())
