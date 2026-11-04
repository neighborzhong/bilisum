"""B站 API 客户端：元数据 / wbi 签名 / 字幕 / 音频流。

实测要点：
- 元数据与 playurl 不需要登录态即可拿；
- 字幕（含 AI 字幕）在未登录时返回空列表，必须带 SESSDATA；
- dash 音频的 CDN 校验 Referer，必须带 https://www.bilibili.com/。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# B站 wbi 签名混排表
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class BiliError(RuntimeError):
    """B站接口返回了非 0 code。"""

    def __init__(self, code: int, message: str, where: str = "") -> None:
        super().__init__(f"[{where or 'bilibili'}] code={code} {message}")
        self.code = code
        self.message = message


@dataclass
class VideoInfo:
    bvid: str
    aid: int
    title: str
    desc: str
    duration: int
    owner: str
    cid: int
    page: int = 1
    page_title: str = ""
    page_count: int = 1
    pic: str = ""
    url: str = ""

    @property
    def duration_text(self) -> str:
        m, s = divmod(max(0, self.duration), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


@dataclass
class SubtitleTrack:
    lan: str
    lan_doc: str
    url: str
    cues: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AudioStream:
    urls: list[str]
    bandwidth: int = 0
    mime: str = ""


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in _MIXIN_KEY_ENC_TAB if i < len(raw))[:32]


class BiliClient:
    def __init__(self, sessdata: str = "", timeout: float = 20.0) -> None:
        self.sessdata = sessdata.strip()
        self.subtitle_error = ""
        self._wbi_key: str = ""
        self._wbi_expire: float = 0.0
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if self.sessdata:
            headers["Cookie"] = f"SESSDATA={self.sessdata}"
        self._client = httpx.AsyncClient(
            headers=headers, timeout=timeout, follow_redirects=True, trust_env=False
        )

    async def __aenter__(self) -> "BiliClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    # ---------- 底层 ----------

    async def _get_json(self, url: str, params: dict | None = None, where: str = "") -> dict:
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise BiliError(data.get("code", -1), data.get("message", "unknown"), where)
        return data.get("data") or {}

    async def _wbi(self) -> str:
        if self._wbi_key and time.time() < self._wbi_expire:
            return self._wbi_key
        # 注意：未登录时 nav 返回 code=-101，但 data.wbi_img 仍然可用，不能按错误处理
        resp = await self._client.get("https://api.bilibili.com/x/web-interface/nav")
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        wbi = data.get("wbi_img") or {}
        img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if not img or not sub:
            raise BiliError(-1, "拿不到 wbi 签名素材", "nav")
        self._wbi_key = _mixin_key(img, sub)
        self._wbi_expire = time.time() + 3600
        return self._wbi_key

    async def _signed(self, url: str, params: dict[str, Any]) -> str:
        key = await self._wbi()
        params = dict(params)
        params["wts"] = int(time.time())
        items = sorted(params.items())
        clean = [(k, str(v).translate(str.maketrans("", "", "!'()*"))) for k, v in items]
        query = urlencode(clean, quote_via=quote)
        params["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
        return url + "?" + urlencode(sorted(params.items()), quote_via=quote)

    # ---------- 元数据 ----------

    async def resolve_ep(self, ep_id: int) -> tuple[str, int]:
        """番剧 ep -> (bvid, cid)。注意 pgc 接口把数据放在 result 而不是 data。"""
        resp = await self._client.get(
            "https://api.bilibili.com/pgc/view/web/season", params={"ep_id": ep_id}
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            raise BiliError(payload.get("code", -1), payload.get("message", ""), "season")
        result = payload.get("result") or {}
        episodes = result.get("episodes") or []
        for ep in episodes:
            if ep.get("id") == ep_id or ep.get("ep_id") == ep_id:
                return ep.get("bvid", ""), int(ep.get("cid") or 0)
        if episodes:
            first = episodes[0]
            return first.get("bvid", ""), int(first.get("cid") or 0)
        raise BiliError(-404, "番剧信息里没找到这一集", "season")

    async def video_info(
        self, bvid: str = "", aid: int = 0, ep_id: int = 0, page: int = 1
    ) -> VideoInfo:
        if ep_id:
            bvid, cid = await self.resolve_ep(ep_id)
            if not bvid:
                raise BiliError(-404, "番剧解析失败", "season")
            params = {"bvid": bvid}
        elif bvid:
            params = {"bvid": bvid}
        else:
            params = {"aid": aid}

        data = await self._get_json(
            "https://api.bilibili.com/x/web-interface/view", params, where="view"
        )
        pages = data.get("pages") or []
        idx = min(max(page, 1), len(pages)) - 1 if pages else 0
        cur = pages[idx] if pages else {}
        cid = int(cur.get("cid") or data.get("cid") or 0)
        duration = int(cur.get("duration") or data.get("duration") or 0)
        owner = (data.get("owner") or {}).get("name", "")
        return VideoInfo(
            bvid=data.get("bvid", bvid),
            aid=int(data.get("aid") or aid or 0),
            title=data.get("title", ""),
            desc=(data.get("desc") or "").strip(),
            duration=duration,
            owner=owner,
            cid=cid,
            page=idx + 1 if pages else 1,
            page_title=cur.get("part", ""),
            page_count=len(pages) or 1,
            pic=data.get("pic", ""),
            url=f"https://www.bilibili.com/video/{data.get('bvid', bvid)}",
        )

    async def page_list(self, bvid: str) -> list[dict]:
        """多 P 视频的分集列表。注意这个接口的 data 直接是数组，不是对象。"""
        resp = await self._client.get(
            "https://api.bilibili.com/x/player/pagelist", params={"bvid": bvid}
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            raise BiliError(payload.get("code", -1), payload.get("message", ""), "pagelist")
        data = payload.get("data") or []
        if isinstance(data, list):
            return data
        return data.get("pages") or []

    # ---------- 字幕 ----------
    async def subtitle_tracks(self, info: VideoInfo) -> list[SubtitleTrack]:
        self.subtitle_error = ""
        if not self.sessdata:
            self.subtitle_error = "未配置 SESSDATA"
            return []
        try:
            signed = await self._signed(
                "https://api.bilibili.com/x/player/wbi/v2",
                {"bvid": info.bvid, "cid": info.cid},
            )
            data = await self._get_json(signed, where="wbi/v2")
        except Exception as exc:  # noqa: BLE001
            self.subtitle_error = f"{type(exc).__name__}: {exc}"
            return []
        raw = ((data.get("subtitle") or {}).get("subtitles")) or []
        if not raw:
            self.subtitle_error = "接口返回空字幕列表（视频无字幕，或 SESSDATA 已失效）"
        return [
            SubtitleTrack(
                lan=item.get("lan", ""),
                lan_doc=item.get("lan_doc", ""),
                url="https:" + item["subtitle_url"]
                if item.get("subtitle_url", "").startswith("//")
                else item.get("subtitle_url", ""),
            )
            for item in raw
            if item.get("subtitle_url")
        ]

    async def load_cues(self, track: SubtitleTrack) -> list[dict[str, Any]]:
        resp = await self._client.get(track.url, headers={"Referer": "https://www.bilibili.com/"})
        resp.raise_for_status()
        body = (resp.json() or {}).get("body") or []
        return [
            {
                "from": float(c.get("from") or 0),
                "to": float(c.get("to") or 0),
                "text": str(c.get("content") or "").strip(),
            }
            for c in body
            if str(c.get("content") or "").strip()
        ]

    def pick_track(
        self, tracks: list[SubtitleTrack], priority: list[str]
    ) -> SubtitleTrack | None:
        if not tracks:
            return None
        by_lan = {t.lan: t for t in tracks}
        for lan in priority:
            if lan in by_lan:
                return by_lan[lan]
        for t in tracks:
            if t.lan.startswith("zh"):
                return t
        return tracks[0]

    # ---------- 音频 ----------

    async def audio_stream(self, info: VideoInfo) -> AudioStream:
        data = None
        # 优先 wbi 签名接口；失败则退回未签名接口（实测未登录也能取到 dash）
        try:
            signed = await self._signed(
                "https://api.bilibili.com/x/player/wbi/playurl",
                {
                    "bvid": info.bvid,
                    "cid": info.cid,
                    "fnval": 4048,
                    "fnver": 0,
                    "fourk": 1,
                },
            )
            data = await self._get_json(signed, where="wbi/playurl")
        except Exception:
            data = await self._get_json(
                "https://api.bilibili.com/x/player/playurl",
                {"bvid": info.bvid, "cid": info.cid, "fnval": 4048, "fnver": 0, "fourk": 1},
                where="playurl",
            )
        dash = data.get("dash") or {}
        audios = dash.get("audio") or []
        if not audios:
            durl = data.get("durl") or []
            if durl:
                return AudioStream(urls=[durl[0]["url"]])
            raise BiliError(-404, "这个视频没有可下载的音轨", "playurl")

        best = max(audios, key=lambda a: int(a.get("bandwidth") or 0))
        urls: list[str] = []
        base = best.get("baseUrl") or best.get("base_url")
        if base:
            urls.append(base)
        urls += [u for u in (best.get("backupUrl") or best.get("backup_url") or []) if u]
        return AudioStream(
            urls=urls,
            bandwidth=int(best.get("bandwidth") or 0),
            mime=best.get("mimeType") or best.get("mime_type") or "",
        )

    async def download_audio(
        self, stream: AudioStream, dest: Path, on_progress=None
    ) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_err: Exception | None = None
        for url in stream.urls:
            try:
                async with self._client.stream(
                    "GET",
                    url,
                    headers={
                        "Referer": "https://www.bilibili.com/",
                        "User-Agent": UA,
                        "Accept": "*/*",
                    },
                ) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    last_pct = -1
                    with dest.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(1 << 16):
                            fh.write(chunk)
                            done += len(chunk)
                            if on_progress and total:
                                pct = int(done * 100 / total)
                                if pct != last_pct:  # 节流：百分比变化才回调
                                    last_pct = pct
                                    on_progress(done / total)
                if dest.exists() and dest.stat().st_size > 1024:
                    return dest
                last_err = RuntimeError(f"下载的音频过小：{dest.stat().st_size if dest.exists() else 0}")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                await asyncio.sleep(0.3)
        raise RuntimeError(f"音频下载失败：{last_err}")
