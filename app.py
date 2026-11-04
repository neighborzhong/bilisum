"""bilisum 桌面应用入口：内嵌 HTTP 服务 + 原生窗口，关闭窗口即停服务。

开发运行：.venv\\Scripts\\pythonw.exe app.py
打包运行：双击 dist\\bilisum\\bilisum.exe

打包成 --windowed 后没有控制台，所有日志写到 <exe目录>\\data\\app.log。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import traceback


def _log(msg: str) -> None:
    """窗口模式下 sys.stdout 可能是 None，日志同时落盘一份便于排查。"""
    try:
        print(msg, flush=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        from bilisum.config import DATA_DIR

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / "app.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _wait_ready(host: str, port: int, timeout: float = 25.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.0, trust_env=False) as c:
                if c.get(f"http://{host}:{port}/api/health").status_code == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def _ollama_models(host: str, port: int) -> list[str]:
    """读取某个 Ollama 实例已有的模型名列表，失败返回空列表。"""
    import httpx

    try:
        with httpx.Client(timeout=2.0, trust_env=False) as c:
            r = c.get(f"http://{host}:{port}/api/tags")
            if r.status_code == 200:
                return [m.get("name", "") for m in (r.json().get("models") or [])]
    except Exception:  # noqa: BLE001
        pass
    return []


def _start_bundled_ollama(model: str):
    """绿色便携包里自带 Ollama 运行时：拉起它并用包内模型目录。

    普通安装版（没有 runtime/ollama）返回 None，沿用系统已装的 Ollama。
    """
    from bilisum.config import APP_ROOT

    exe = APP_ROOT / "runtime" / "ollama" / "ollama.exe"
    if not exe.exists():
        return None

    models_dir = APP_ROOT / "runtime" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    port = 11434
    if _port_in_use("127.0.0.1", 11434):
        existing = _ollama_models("127.0.0.1", 11434)
        if model in existing:
            _log(f"已有 Ollama 且包含模型 {model}，直接复用它")
            return None
        # 关键：系统上的 Ollama 可能是别人装的、模型完全不同，不能盲复用
        _log(f"已有 Ollama 但缺少 {model}（现有：{existing or '无'}），改用包内实例")
        port = 11435
        if _port_in_use("127.0.0.1", port):
            # 上次异常退出（比如被任务管理器强杀）可能留下包内实例，能复用就复用
            leftover = _ollama_models("127.0.0.1", port)
            if model in leftover:
                _log(f"{port} 上已有可用的包内实例（上次残留），直接复用它")
                os.environ["BILISUM_OLLAMA_BASE"] = f"http://127.0.0.1:{port}"
                return None
            _log(f"{port} 被占用且其中没有 {model}，无法启动包内实例")
            return None

    env = os.environ.copy()
    env["OLLAMA_MODELS"] = str(models_dir)
    env["OLLAMA_HOST"] = f"127.0.0.1:{port}"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _log(f"启动包内 Ollama：{exe}（端口 {port}，模型目录 {models_dir}）")
    try:
        proc = subprocess.Popen(
            [str(exe), "serve"],
            env=env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        _log("包内 Ollama 启动失败：\n" + traceback.format_exc())
        return None

    # 让 pipeline 的 load_config() 读到新端口（config.py 支持该环境变量）
    if port != 11434:
        os.environ["BILISUM_OLLAMA_BASE"] = f"http://127.0.0.1:{port}"
        _log(f"应用将连接 http://127.0.0.1:{port}")

    # 等它就绪，最多 40 秒（首次可能要加载 CUDA 库）
    import httpx

    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.0, trust_env=False) as c:
                if c.get(f"http://127.0.0.1:{port}/api/tags").status_code == 200:
                    _log("包内 Ollama 已就绪")
                    return proc
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)

    _log("包内 Ollama 启动超时，继续尝试使用（可能仍会响应）")
    return proc


def _warn_dialog(title: str, message: str) -> None:
    """弹个系统提示框。--windowed 没有控制台，这是唯一能让对方看见的方式。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x30)  # MB_ICONWARNING
    except Exception:  # noqa: BLE001
        pass


def _check_ollama_or_warn(bundled_started: bool) -> None:
    """精简版依赖对方自己装的 Ollama，连不上就直接告诉他要做什么。"""
    if bundled_started:
        return
    if _ollama_models("127.0.0.1", 11434):
        return
    msg = (
        "检测不到本地 Ollama 服务。\n\n"
        "【这个精简版需要你自己安装 Ollama】\n\n"
        "1. 打开 https://ollama.com/download 下载并安装\n"
        "2. 装好后打开「命令提示符」，执行：\n"
        "   ollama pull qwen2.5:7b-instruct\n"
        "   （约需下载 4.7 GB，请耐心等待）\n"
        "3. 重新打开本程序\n\n"
        "如果你不想自己装，请改用「完整版」，它自带 Ollama 和模型，\n"
        "解压后双击就能用，不需要任何额外操作。\n\n"
        "（本提示只出现一次，稍后仍会打开界面，但功能不可用）"
    )
    _log("检测不到 Ollama，向用户弹出引导提示")
    _warn_dialog("bilisum · 需要先安装 Ollama", msg)


def main() -> int:
    import uvicorn
    import webview

    from bilisum.config import load_config

    cfg = load_config()
    host, port = cfg.host, cfg.port
    url = f"http://{host}:{port}"
    _log(f"启动 bilisum，界面地址 {url}")

    # 绿色便携包：先拉内置 Ollama（没有就返回 None，用系统已装的）
    ollama_proc = _start_bundled_ollama(cfg.ollama_model)
    _check_ollama_or_warn(ollama_proc is not None)

    server = None
    thread = None

    if _port_in_use(host, port):
        _log("端口已被占用：复用已在运行的服务，只开窗口")
    else:
        from bilisum.web import app as fastapi_app

        server = uvicorn.Server(
            uvicorn.Config(fastapi_app, host=host, port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        if not _wait_ready(host, port):
            _log("服务启动超时，请检查 data/app.log")
            return 1
        _log("服务已就绪")

    webview.create_window(
        "bilisum · B站学习笔记",
        url,
        width=1200,
        height=880,
        min_size=(880, 600),
    )
    webview.start()  # 阻塞，直到窗口被关闭
    _log("窗口已关闭，正在停止服务")

    if server is not None:
        server.should_exit = True
        if thread is not None:
            thread.join(timeout=6)

    if ollama_proc is not None and ollama_proc.poll() is None:
        _log("停止包内 Ollama")
        # 必须连子进程一起结束：ollama serve 会 fork 出 llama-server 跑推理，
        # 只 terminate ollama.exe 会留下 llama-server 占着 DLL 和显存
        killed = False
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(ollama_proc.pid)],
                creationflags=creationflags,
                capture_output=True,
                timeout=20,
            )
            killed = True
        except Exception:  # noqa: BLE001
            pass
        if not killed:
            try:
                ollama_proc.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            ollama_proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass

    _log("已退出")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        _log("启动失败：\n" + traceback.format_exc())
        sys.exit(1)
