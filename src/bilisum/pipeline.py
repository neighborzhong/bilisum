"""编排：链接 → 元数据 → 字幕/音频 → 转写 → 清洗 → 本地模型总结。"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import asr, clean, frames, llm
from .bili import BiliClient, BiliError, VideoInfo
from .config import DATA_DIR, Config
from .urls import ParseError, parse_input

TMP_DIR = DATA_DIR / "tmp"

Emit = Callable[[str, str, float], None]


@dataclass
class SummaryResult:
    info: VideoInfo
    source: str
    source_detail: str
    segments: list[clean.Segment]
    markdown: str
    chars: int = 0
    elapsed: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video": {
                "bvid": self.info.bvid,
                "title": self.info.title,
                "owner": self.info.owner,
                "duration": self.info.duration,
                "duration_text": self.info.duration_text,
                "page": self.info.page,
                "page_count": self.info.page_count,
                "page_title": self.info.page_title,
                "url": self.info.url,
                "pic": self.info.pic,
                "desc": self.info.desc[:400],
            },
            "source": self.source,
            "source_detail": self.source_detail,
            "markdown": self.markdown,
            "chars": self.chars,
            "segments": [
                {"t": s.stamp, "text": s.text} for s in self.segments
            ],
            "elapsed": round(self.elapsed, 1),
            "warnings": self.warnings,
        }


def _threadsafe_emit(loop: asyncio.AbstractEventLoop, emit: Emit) -> Emit:
    def _emit(stage: str, message: str, percent: float) -> None:
        loop.call_soon_threadsafe(emit, stage, message, percent)

    return _emit


async def summarize_url(raw: str, cfg: Config, emit: Emit) -> SummaryResult:
    loop = asyncio.get_running_loop()
    emit = _threadsafe_emit(loop, emit)
    started = time.time()
    warnings: list[str] = []

    # 1. 解析链接
    emit("parse", "解析链接…", 2)
    ref = parse_input(raw)
    emit("parse", f"已识别 {ref.describe()}，获取视频信息…", 5)

    async with BiliClient(cfg.sessdata) as bili:
        info = await bili.video_info(
            bvid=ref.bvid, aid=ref.aid, ep_id=ref.ep_id, page=ref.page
        )
        suffix = f"（P{info.page}/{info.page_count} {info.page_title}）" if info.page_count > 1 else ""
        emit("meta", f"《{info.title}》{suffix} · {info.duration_text}", 10)

        if info.page_count > 1:
            warnings.append(
                f"这是 {info.page_count} P 的合集，本次只总结第 {info.page} P"
                f"「{info.page_title}」。要换集就在链接末尾加 ?p=N，或直接写「{info.bvid} p3」"
            )

        if info.duration > 3600 * 2:
            warnings.append(
                f"视频时长 {info.duration_text}，若走 ASR 会很久，建议配置 SESSDATA 用官方字幕"
            )

        # 2. 字幕优先
        track = None
        if cfg.sessdata:
            emit("subtitle", "检查字幕轨…", 14)
            try:
                tracks = await bili.subtitle_tracks(info)
                track = bili.pick_track(tracks, cfg.subtitle_langs)
                if track is None:
                    warnings.append(
                        f"字幕不可用，改用 ASR（{bili.subtitle_error or '未返回字幕轨'}）"
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"字幕接口异常，改用 ASR：{exc}")
        else:
            emit("subtitle", "未配置 SESSDATA，跳过字幕，改用本地语音识别", 14)
            warnings.append("未配置 SESSDATA：未登录状态下 B站不下发字幕，本次走 ASR")

        cues: list[dict] = []
        source = "asr"
        source_detail = ""

        if track is not None:
            emit("subtitle", f"下载字幕轨 {track.lan_doc or track.lan}…", 20)
            cues = await bili.load_cues(track)
            if cues:
                source = "subtitle"
                source_detail = f"{track.lan_doc or track.lan}（{track.lan}）"
                emit("subtitle", f"拿到 {len(cues)} 条字幕", 26)
            else:
                warnings.append("字幕轨为空，改用 ASR")
                track = None

        if not cues:
            # 3. 音频 → ASR
            emit("audio", "获取音频流…", 16)
            stream = await bili.audio_stream(info)
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            audio_path = TMP_DIR / f"{info.bvid}-{info.cid}.m4a"

            if audio_path.exists() and audio_path.stat().st_size > 100 * 1024:
                emit("audio", "复用已下载音频", 26)
            else:
                mb = stream.bandwidth * info.duration / 8 / 1024 / 1024 if stream.bandwidth else 0
                emit("audio", f"下载音频（约 {mb:.0f} MB）…", 18)

                def dl_progress(p: float) -> None:
                    emit("audio", f"下载音频 {p * 100:.0f}%", 18 + 12 * p)

                try:
                    await bili.download_audio(stream, audio_path, on_progress=dl_progress)
                except Exception as exc:  # noqa: BLE001
                    raise BiliError(-500, f"音频下载失败：{exc}", "download") from exc

            emit("asr", f"本地语音识别中（{cfg.whisper_model}，先加载模型可能要等一会）…", 31)

            cache_path = TMP_DIR / f"{info.bvid}-{info.cid}-{cfg.whisper_model}.cues.json"
            if cache_path.exists():
                try:
                    cues = json.loads(cache_path.read_text("utf-8"))
                    emit("asr", f"复用已缓存的转写结果（{len(cues)} 条）", 74)
                except Exception:
                    cues = []

            if not cues:

                def asr_progress(p: float) -> None:
                    emit("asr", f"语音识别中 {p * 100:.0f}%", 32 + 42 * p)

                cues = await asyncio.to_thread(
                    asr.transcribe,
                    audio_path,
                    model_name=cfg.whisper_model,
                    device=cfg.whisper_device,
                    compute_type=cfg.whisper_compute,
                    language="zh",
                    on_progress=asr_progress,
                )
                try:
                    cache_path.write_text(json.dumps(cues, ensure_ascii=False), "utf-8")
                except Exception:
                    pass
            source_detail = f"faster-whisper {cfg.whisper_model}（{cfg.whisper_device}/{cfg.whisper_compute}）"
            if not cfg.keep_audio:
                try:
                    audio_path.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass

    # 4. 清洗 + 分块
    emit("clean", "合并断句、清洗文本…", 76)
    # 字幕是碎片化短句（8-20 字），ASR 已经是整句，分段参数要区分
    if source == "subtitle":
        segments = clean.merge_cues(cues, gap=1.2, max_chars=180)
    else:
        segments = clean.merge_cues(cues, gap=2.5, max_chars=120)

    # 4.5 可选的画面文字提取（教程/PPT/代码类视频的关键信息往往只在屏幕上）
    if cfg.extract_frames:
        emit("frames", "准备提取画面文字…", 77)
        try:
            async with BiliClient(cfg.sessdata) as bili2:
                frame_segments, frame_detail = await frames.extract(
                    bili2,
                    info,
                    interval=cfg.frame_interval,
                    max_frames=cfg.max_frames,
                    on_progress=lambda msg, p: emit("frames", msg, 77 + 8 * p),
                )
            if frame_segments:
                segments = sorted(segments + frame_segments, key=lambda s: s.start)
                source_detail = f"{source_detail} + {frame_detail}"
            else:
                warnings.append("画面提取没拿到有效文字，已忽略")
        except Exception as exc:  # noqa: BLE001
            # 完整堆栈写到文件，方便事后定位；给用户只留一句短提示
            try:
                (DATA_DIR / "last_frames_error.log").write_text(
                    traceback.format_exc(), "utf-8"
                )
            except Exception:  # noqa: BLE001
                pass
            warnings.append(f"画面文字提取失败（不影响正文摘要）：{exc}")
    if not segments:
        raise RuntimeError(
            "没有拿到任何文本内容。可能是纯音乐/舞蹈视频没有口播，或字幕与音轨都不可用。"
        )
    chars = clean.total_chars(segments)
    chunks = clean.chunk_segments(segments, max_chars=cfg.chunk_chars)
    emit("clean", f"整理出 {chars} 字，切成 {len(chunks)} 段准备总结", 82)

    # 5. 本地模型总结
    async with llm.OllamaClient(
        base=cfg.ollama_base,
        model=cfg.ollama_model,
        num_ctx=cfg.num_ctx,
        temperature=cfg.temperature,
    ) as client:
        health = await client.health()
        if not health["ok"]:
            raise llm.OllamaError(
                f"Ollama 不可用：{health['error']}。请先启动 Ollama 服务。"
            )
        if cfg.ollama_model not in health["models"] and health["models"]:
            warnings.append(
                f"Ollama 里没找到 {cfg.ollama_model}，现有模型：{', '.join(health['models'][:5])}"
            )

        markdown = await llm.summarize(
            client,
            chunks,
            title=info.title,
            chunk_chars=cfg.chunk_chars,
            mode=cfg.output_mode,
            on_step=lambda msg, p: emit("llm", msg, 82 + 17 * p),
        )

        # 换过模型后很容易撞上输出长度上限，截断的笔记会突然断在半句话
        if getattr(client, "truncated", 0) > 0:
            warnings.append(
                f"有 {client.truncated} 段输出被长度上限截断，笔记可能不完整。"
                f"换更大的模型、或把 config.toml 里的 chunk_chars 调小可以缓解"
            )

    emit("done", "完成", 100)
    return SummaryResult(
        info=info,
        source=source,
        source_detail=source_detail,
        segments=segments,
        markdown=markdown,
        chars=chars,
        elapsed=time.time() - started,
        warnings=warnings,
    )


def render_note(result: SummaryResult) -> str:
    """把结果渲染成可落盘的 Markdown 笔记。"""
    v = result.info
    lines = [
        f"# {v.title}",
        "",
        f"- 链接：{v.url}" + (f"?p={v.page}" if v.page > 1 else ""),
        f"- UP 主：{v.owner}",
        f"- 时长：{v.duration_text}"
        + (f"（P{v.page}/{v.page_count} {v.page_title}）" if v.page_count > 1 else ""),
        f"- 文本来源：{result.source_detail or result.source}，共 {result.chars} 字",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}",
    ]
    if result.warnings:
        lines.append("- 提示：" + "；".join(result.warnings))
    lines += ["", "---", "", result.markdown.strip(), "", "---", "", "## 带时间戳的原文", ""]
    lines += [f"- `{s.stamp}` {s.text}" for s in result.segments]
    return "\n".join(lines) + "\n"
