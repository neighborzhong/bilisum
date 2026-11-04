"""Ollama 本地模型总结层：map（分段提要点）→ reduce（合并成结构化摘要）。"""

from __future__ import annotations

import re
from typing import Callable

import httpx

MAP_SYSTEM = """你是一个严谨的视频内容整理助手。输入是同一段视频里的一小段口播文字，可能含语音识别错字。

规则：
1. 只依据给定文字，绝不补充外部知识、不猜测。
2. 输出 3-6 条要点，每条单独一行，以 "- " 开头，不超过 50 字。
3. 每条必须是完整陈述句，要包含具体信息：谁、做了什么、关键数字、结论或步骤。
4. 保留具体数字、专有名词、给出的结论或步骤。
5. 若这一小段只是寒暄、玩笑、广告口播或与主题无关，只输出 "- （无实质内容）"。
6. 不要写开场白、总结句或任何解释。"""

MERGE_SYSTEM = """你是视频内容整理助手。把给出的若干组片段要点合并成 5-8 条要点。

规则：只使用给定内容，合并重复项，每条一行以 "- " 开头，不超过 50 字，不要开场白。"""

FINAL_SYSTEM = """你是严格的视频内容整理助手，负责把同一段视频的内容整理成一份结构化摘要。

只使用给定内容，不编造、不推测、不补充外部知识。合并重复项，按主题归类。

时间戳规则（很重要）：
- 输入里形如 [mm:ss] 或 (mm:ss) 的标记是「分:秒」时间点。
- 只能原样引用输入里已经出现的时间标记，绝不能自己推算、换算或编造。
- 某条信息在输入里没有时间标记，就不要写时间戳。

严格按以下 Markdown 结构输出。四个章节**一个都不能少**，尤其「关键名词」必须写出（没有内容就写「无」）：

## 一句话总结
（一句不超过 60 字的话：这视频讲了什么、结论或主张是什么）

## 核心要点
- **最多 9 条，超过就必须合并归类**，宁可少而精，绝不罗列零散细节
- 每条不超过 60 字，直接陈述，不要「小标题：内容」的格式
- 同类信息合并成一条（例如多个配置项合成「NGINX 里配置 location/rewrite/proxy_pass 完成转发」）

## 内容脉络
- **4-8 段**，按内容真正推进的阶段划分，不要把整段视频笼统压成两三段
- 段落分界必须从下面给出的「可用时间锚点」里挑 4-8 个，不要只用分块边界
- **必须带两端双星号加粗，格式照抄下面这行**：
  - **mm:ss–mm:ss｜阶段名**：一句话说明这一段在讲什么
- 时间范围必须来自输入里的时间标记

## 关键名词
（从核心要点和给定内容中提取：产品名、工具/技术名（如 NGINX、Tomcat）、人名、具体数字或指标）
（用「、」分隔。视频里确实有技术名词时写「无」是错的；真的一个都没有才写「无」）

禁止使用"本视频""UP主介绍了""综上所述""希望对大家有帮助"这类套话。"""

NOTE_MAP_SYSTEM = """你是学习笔记整理助手。输入是视频里的一小段文字：口播字幕，和/或屏幕画面 OCR 文字（每行以〔画面〕开头）。文字可能含语音识别错字。

把这一小段整理成【可以直接复习的学习笔记】：不是概括，也不是逐句听写。

规则：
1. 知识点要全：定义、原理、操作步骤、命令、代码、配置项、参数取值、注意事项、报错与解决、演示结果。
2. 代码、命令、配置、路径 **原样保留**，用 ``` 围成代码块，绝不改写成一句话。
3. **归纳，不要逐句抄口播**。同一个意思说三遍，只写一次。
4. **只写知识点，不要记录界面元素**：按钮名、菜单项、浏览器标签、书签栏、性能面板数字、PPT 标题栏、插件名（如 Lighthouse/Adblock）、网址导航，一律忽略。识别出来的乱码字（如「却灯片放映」「数址」「朕调」）也不要写。
5. 只依据输入内容，不补充外部知识，不编造。
6. 寒暄、玩笑、广告口播、重复口水话直接丢弃。
7. 标题格式严格用：`### mm:ss 主题名`（只一个时间点，不要写时间范围）。
   主题名必须是这一段实际讲的东西，**禁止写「小标题」「内容」「介绍」这类占位词**。
8. 如果这一小段确实没有新知识点，只输出一行：- （本段无知识点）

格式示例：
### 06:08 配置 Nginx 反向代理
- `location /api/`：定义路径匹配规则，`^/api/` 表示匹配以 /api/ 开头的路径
- `rewrite`：重写路径，把 `/api/depts` 变成 `/depts`
- `proxy_pass`：把改写后的请求转发给 `http://localhost:8080`"""

NOTE_OVERVIEW_SYSTEM = """你是学习笔记的目录整理助手。用户会给你同一视频各部分笔记的小标题。

只依据给定标题输出三段，不要多余内容：

## 一句话说明
（这视频教了什么，40 字内）

## 知识框架
- **模块名**：这个模块覆盖什么（3-6 条）

## 前置知识
（视频里明确要求、或默认你已掌握的知识；没提到就写「无」）"""

THINK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S)


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        base: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:7b-instruct",
        num_ctx: int = 8192,
        temperature: float = 0.25,
        timeout: float = 900.0,
    ) -> None:
        self.base = base.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self._client = httpx.AsyncClient(timeout=timeout, trust_env=False)

    async def __aenter__(self) -> "OllamaClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def health(self) -> dict:
        """返回 {ok, models, error}。"""
        try:
            resp = await self._client.get(f"{self.base}/api/tags", timeout=5.0)
            resp.raise_for_status()
            models = [m.get("name", "") for m in (resp.json().get("models") or [])]
            return {"ok": True, "models": models, "error": ""}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "models": [], "error": f"{type(exc).__name__}: {exc}"}

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        num_predict: int | None = None,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_ctx": self.num_ctx,
                "num_predict": num_predict or 2400,
            },
        }
        try:
            resp = await self._client.post(f"{self.base}/api/chat", json=payload)
        except Exception as exc:  # noqa: BLE001
            raise OllamaError(
                f"连不上 Ollama（{self.base}）：{exc}。请确认 Ollama 已启动，"
                f"并已执行 ollama pull {self.model}"
            ) from exc
        if resp.status_code != 200:
            hint = ""
            if resp.status_code == 404:
                try:
                    with httpx.Client(timeout=3.0, trust_env=False) as probe:
                        tags = probe.get(f"{self.base}/api/tags").json().get("models") or []
                        available = [m.get("name", "") for m in tags]
                except Exception:  # noqa: BLE001
                    available = []
                if available:
                    hint = (
                        f"这个 Ollama 里没有 {self.model}，现有模型：{', '.join(available)}。"
                        f"在设置里把模型名改成上面任意一个，或执行 ollama pull {self.model}"
                    )
                else:
                    hint = f"这个 Ollama 里一个模型都没有，请先执行 ollama pull {self.model}"
            raise OllamaError(
                f"Ollama 返回 {resp.status_code}。{hint or resp.text[:300]}"
            )
        content = ((resp.json().get("message") or {}).get("content") or "").strip()
        return THINK_RE.sub("", content).strip()


def _spans(chunk: str) -> tuple[str, str]:
    stamps = re.findall(r"^\[([\d:]+)\]", chunk, re.M)
    if not stamps:
        return "", ""
    return stamps[0], stamps[-1]


def _pack(parts: list[str], limit: int) -> list[list[str]]:
    groups: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for p in parts:
        if cur and size + len(p) > limit:
            groups.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p) + 1
    if cur:
        groups.append(cur)
    return groups


async def summarize(
    client: OllamaClient,
    chunks: list[str],
    *,
    title: str = "",
    chunk_chars: int = 3200,
    mode: str = "note",
    on_step: Callable[[str, float], None] | None = None,
) -> str:
    """按模式分发。note = 完整学习笔记；summary = 精简摘要。"""
    if mode == "summary":
        return await _summarize_compact(
            client, chunks, title=title, chunk_chars=chunk_chars, on_step=on_step
        )
    return await _summarize_note(client, chunks, title=title, on_step=on_step)


async def _summarize_compact(
    client: OllamaClient,
    chunks: list[str],
    *,
    title: str = "",
    chunk_chars: int = 3200,
    on_step: Callable[[str, float], None] | None = None,
) -> str:
    """分段提炼 + 合并（精简摘要模式）。"""
    total = len(chunks)
    partials: list[str] = []

    if total > 1:
        for i, chunk in enumerate(chunks, 1):
            a, b = _spans(chunk)
            head = f"视频标题：《{title}》\n片段 {i}/{total}"
            if a:
                head += f"（时间 {a}–{b}）"
            text = await client.chat(
                [
                    {"role": "system", "content": MAP_SYSTEM},
                    {"role": "user", "content": f"{head}：\n{chunk}"},
                ]
            )
            partials.append(f"【片段 {i} {a}–{b}】\n{text}")
            if on_step:
                on_step(f"提炼片段 {i}/{total}", 0.08 + 0.72 * i / total)

        # 层次合并：要点总量超过单次上下文就分组合并
        budget = max(2000, chunk_chars * 2)
        groups = _pack(partials, budget)
        if len(groups) > 1:
            merged: list[str] = []
            for gi, group in enumerate(groups, 1):
                text = await client.chat(
                    [
                        {"role": "system", "content": MERGE_SYSTEM},
                        {"role": "user", "content": "\n\n".join(group)},
                    ]
                )
                merged.append(f"【第 {gi} 组】\n{text}")
                if on_step:
                    on_step(f"合并要点 {gi}/{len(groups)}", 0.8 + 0.1 * gi / len(groups))
            body = "各片段要点：\n" + "\n\n".join(merged)
        else:
            body = "各片段要点：\n" + "\n\n".join(partials)
    else:
        # 只有一段时不要「先压缩再压缩」，直接把原文交给最终总结，避免信息损失
        if on_step:
            on_step("内容较短，直接生成摘要", 0.6)
        body = "视频转写文本：\n" + chunks[0]

    if on_step:
        on_step("生成最终摘要", 0.92)
    # 把视频里真实存在的时间锚点交给模型，否则它只能按分块边界给两三段脉络
    anchors = sorted(
        {m for c in chunks for m in re.findall(r"^\[([\d:]+)\]", c, re.M)},
        key=lambda t: [int(x) for x in t.split(":")],
    )
    if anchors:
        # 太多锚点会淹没模型，均匀采样到 24 个
        if len(anchors) > 24:
            step = len(anchors) / 24
            anchors = [anchors[int(i * step)] for i in range(24)]
        body += "\n\n可用时间锚点（按先后顺序，段落分界只能从里面选，不得自造）：\n" + "、".join(anchors)
    final = await client.chat(
        [
            {"role": "system", "content": FINAL_SYSTEM},
            {"role": "user", "content": f"视频标题：《{title}》\n\n{body}"},
        ]
    )
    if on_step:
        on_step("生成最终摘要", 1.0)
    return final


def _similarity(a: str, b: str) -> float:
    """字符集合 Jaccard，用于判定两节笔记是否在讲同一件事。"""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, len(sa | sb))


def _merge_duplicate_sections(body: str, threshold: float = 0.62) -> str:
    """map 按块独立生成笔记，同一主题横跨多块时会重复，这里按内容相似度去掉重复小节。"""
    parts = re.split(r"(?m)^(###\s+.*)$", body)
    sections: list[tuple[str, str]] = []
    if parts and parts[0].strip():
        sections.append(("", parts[0].strip()))
    for i in range(1, len(parts) - 1, 2):
        sections.append((parts[i].strip(), parts[i + 1].strip()))

    kept: list[tuple[str, str]] = []
    kept_sigs: list[str] = []
    for title, content in sections:
        content = content.strip()
        if not content or content.startswith("- （本段无知识点）"):
            continue
        sig = re.sub(r"\s+", "", content)
        if any(_similarity(sig, prev) >= threshold for prev in kept_sigs):
            continue
        kept.append((title, content))
        kept_sigs.append(sig)

    out: list[str] = []
    for title, content in kept:
        out.append(f"{title}\n{content}".strip())
    return "\n\n".join(out)


async def _summarize_note(
    client: OllamaClient,
    chunks: list[str],
    *,
    title: str = "",
    on_step: Callable[[str, float], None] | None = None,
) -> str:
    """学习笔记模式：逐段整理详细笔记 → 拼接 → 由小标题生成知识框架。

    关键差异：不做压缩。每段的代码、命令、参数、步骤都原样保留，
    最终文档由「框架 + 完整笔记」组成，而不是几张要点卡片。
    """
    total = len(chunks)
    notes: list[str] = []

    for i, chunk in enumerate(chunks, 1):
        a, b = _spans(chunk)
        head = f"视频标题：《{title}》\n第 {i}/{total} 段"
        if a:
            head += f"（{a}–{b}）"
        text = await client.chat(
            [
                {"role": "system", "content": NOTE_MAP_SYSTEM},
                {"role": "user", "content": f"{head}：\n{chunk}"},
            ],
            temperature=0.2,
            num_predict=3500,
        )
        if text.strip():
            notes.append(text.strip())
        if on_step:
            on_step(f"整理第 {i}/{total} 段笔记", 0.05 + 0.75 * i / max(1, total))

    raw_body = "\n\n".join(n for n in notes if n.strip()).strip()
    if not raw_body:
        return "（没有提取到可整理的内容）"
    # 同一主题横跨多个块时会被重复记录，按内容相似度合并掉
    body = _merge_duplicate_sections(raw_body) or raw_body

    # 目录只喂小标题，避免让模型重新消化全文（省时间也不容易失真）
    headings = [
        line.strip()
        for note in notes
        for line in note.splitlines()
        if line.strip().startswith("#")
    ]

    overview = ""
    if headings:
        if on_step:
            on_step("生成知识框架", 0.85)
        try:
            overview = await client.chat(
                [
                    {"role": "system", "content": NOTE_OVERVIEW_SYSTEM},
                    {
                        "role": "user",
                        "content": f"视频标题：《{title}》\n\n各部分小标题：\n" + "\n".join(headings[:150]),
                    },
                ],
                temperature=0.2,
                num_predict=700,
            )
        except Exception:  # noqa: BLE001
            overview = ""

    if on_step:
        on_step("组装笔记", 0.95)

    parts: list[str] = []
    if overview.strip():
        parts.append(overview.strip())
        parts.append("---")
    parts.append("## 完整笔记")
    parts.append(body)
    return "\n\n".join(parts)
