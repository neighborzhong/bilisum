"""本地 Web 界面：FastAPI + SSE 进度推送。"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

from . import llm
from .config import DATA_DIR, RESOURCE_ROOT, APP_ROOT, Config, load_config, public_view, save_settings
from .pipeline import render_note, summarize_url

STATIC = RESOURCE_ROOT / "static"
NOTES_DIR = DATA_DIR / "notes"

app = FastAPI(title="bilisum", docs_url=None, redoc_url=None)
TASKS: dict[str, dict] = {}
STARTED_AT = time.time()


def _latest_source_mtime() -> float:
    """源码/前端最近一次修改时间，用来判断运行中的进程是不是旧代码。"""
    latest = 0.0
    for path in Path(__file__).resolve().parent.glob("*.py"):
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            pass
    for extra in (STATIC / "index.html", APP_ROOT / "config.toml"):
        try:
            if extra.exists():
                latest = max(latest, extra.stat().st_mtime)
        except OSError:
            pass
    return latest


def _safe_name(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title).strip()
    return (name[:60] or "bilibili") + ".md"


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text("utf-8"))


@app.get("/api/config")
async def get_config() -> JSONResponse:
    return JSONResponse(public_view(load_config()))


@app.post("/api/config")
async def set_config(payload: dict) -> JSONResponse:
    allowed = {
        "sessdata",
        "ollama_base",
        "ollama_model",
        "whisper_model",
        "whisper_device",
        "chunk_chars",
        "num_ctx",
        "temperature",
        "keep_audio",
        "extract_frames",
        "frame_interval",
        "max_frames",
        "output_mode",
    }
    patch = {k: v for k, v in (payload or {}).items() if k in allowed}
    cfg = save_settings(patch)
    return JSONResponse({"ok": True, "config": public_view(cfg)})


@app.get("/api/health")
async def health() -> JSONResponse:
    cfg = load_config()
    async with llm.OllamaClient(cfg.ollama_base, cfg.ollama_model) as client:
        info = await client.health()
    latest = _latest_source_mtime()
    return JSONResponse(
        {
            "ollama": info,
            "model_ready": cfg.ollama_model in info["models"],
            "expected_model": cfg.ollama_model,
            "sessdata_set": bool(cfg.sessdata),
            # 进程比代码旧 → 说明改完代码没重启，行为会跟预期不一致
            "stale": latest > STARTED_AT + 0.5,
            "started_at": time.strftime("%H:%M:%S", time.localtime(STARTED_AT)),
            "code_mtime": time.strftime("%H:%M:%S", time.localtime(latest)),
        }
    )


async def _run_task(task_id: str, url: str, cfg: Config, save_note: bool) -> None:
    queue: asyncio.Queue = TASKS[task_id]["queue"]

    def emit(stage: str, message: str, percent: float) -> None:
        queue.put_nowait(
            {
                "type": "progress",
                "stage": stage,
                "message": message,
                "percent": round(float(percent), 1),
            }
        )

    try:
        result = await summarize_url(url, cfg, emit)
        payload = result.to_dict()
        if save_note:
            NOTES_DIR.mkdir(parents=True, exist_ok=True)
            path = NOTES_DIR / _safe_name(result.info.title)
            path.write_text(render_note(result), "utf-8")
            payload["note_path"] = str(path)
        TASKS[task_id]["result"] = result
        queue.put_nowait({"type": "result", "data": payload})
    except Exception as exc:  # noqa: BLE001
        queue.put_nowait(
            {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        )
    finally:
        TASKS[task_id]["finished"] = True


@app.post("/api/summarize")
async def start(payload: dict) -> JSONResponse:
    url = (payload or {}).get("url", "").strip()
    if not url:
        return JSONResponse({"error": "请输入视频链接"}, status_code=400)
    cfg = load_config()
    # 允许本次请求临时覆盖画面提取开关
    if "extract_frames" in (payload or {}):
        cfg.extract_frames = bool(payload["extract_frames"])
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {
        "queue": asyncio.Queue(),
        "created": time.time(),
        "finished": False,
        "result": None,
    }
    # 必须持有 task 引用，否则可能被 GC 提前回收
    TASKS[task_id]["task"] = asyncio.create_task(
        _run_task(task_id, url, cfg, bool(payload.get("save_note")))
    )
    return JSONResponse({"task_id": task_id})


@app.get("/api/tasks/{task_id}/events")
async def events(task_id: str) -> StreamingResponse:
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    queue: asyncio.Queue = task["queue"]

    async def gen():
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=120)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt["type"] in ("result", "error"):
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/tasks/{task_id}/note", response_class=PlainTextResponse)
async def note(task_id: str) -> PlainTextResponse:
    task = TASKS.get(task_id)
    if not task or not task.get("result"):
        return PlainTextResponse("还没生成完", status_code=404)
    return PlainTextResponse(render_note(task["result"]), media_type="text/markdown; charset=utf-8")


def _port_in_use(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _open_browser(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    import sys
    import threading

    import uvicorn

    cfg = load_config()
    url = f"http://{cfg.host}:{cfg.port}"
    no_browser = "--no-browser" in sys.argv

    # 重复双击时不报错，直接把已经跑着的界面打开
    if _port_in_use(cfg.host, cfg.port):
        print(f"服务已在运行 → {url}")
        if not no_browser:
            _open_browser(url)
        return

    print(f"\n  bilisum 已启动 →  {url}\n")
    if not no_browser:
        threading.Timer(1.5, _open_browser, args=(url,)).start()
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
