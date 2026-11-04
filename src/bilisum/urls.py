"""链接解析：支持 BV / av / b23.tv 短链 / 番剧 ep / 带分P。"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"av(\d+)", re.I)
EP_RE = re.compile(r"ep(\d+)", re.I)
PAGE_RE = re.compile(r"[?&]p=(\d+)")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class VideoRef:
    bvid: str = ""
    aid: int = 0
    ep_id: int = 0
    page: int = 1
    raw: str = ""

    def describe(self) -> str:
        if self.bvid:
            return f"{self.bvid}" + (f" p{self.page}" if self.page > 1 else "")
        if self.ep_id:
            return f"ep{self.ep_id}"
        return f"av{self.aid}"


class ParseError(ValueError):
    pass


def _hint_for(url: str) -> str:
    """给常见的「不是视频页」的链接一句人话提示。"""
    low = url.lower()
    if "search.bilibili.com" in low or "/search?" in low:
        return "这是搜索页或搜索链接。请先点进具体视频，再复制那个视频的链接或 BV 号。"
    if "space.bilibili.com" in low:
        return "这是 UP 主的主页。请进入具体的视频页面再复制链接。"
    if "favlist" in low or "/list/" in low or "medialist" in low or "collectiondetail" in low:
        return "这是收藏夹或合集页面，目前只支持单个视频。请给我具体某一集的链接。"
    if "t.bilibili.com" in low or "/read/" in low or "cv" in low:
        return "这是动态或专栏文章，不是视频。工具只能处理视频。"
    if "live.bilibili.com" in low:
        return "这是直播间，不是投稿视频，拿不到录像内容。"
    if "bangumi" in low and "ep" not in low:
        return "番剧请给带 ep 号的播放链接，例如 https://www.bilibili.com/bangumi/play/ep123456"
    return "没能在链接里找到 BV 号或 ep 号。可以直接贴在视频页地址栏里 bilibili.com 之后那段，或者只贴 BV 号。"


def parse_input(text: str, *, timeout: float = 15.0) -> VideoRef:
    """把用户粘贴的东西解析成 VideoRef。短链会做一次网络跳转。"""
    raw = (text or "").strip().strip('"').strip("'")
    if not raw:
        raise ParseError("请输入 B 站视频链接或 BV 号")

    # 纯 BV / av / ep 号
    if BV_RE.fullmatch(raw):
        return VideoRef(bvid=raw, raw=raw)
    # 口语写法："BV1xxx p3" / "BV1xxx P3" / "BV1xxx 第3p" / "BV1xxx 第p3"
    m = re.fullmatch(
        r"(BV[0-9A-Za-z]{10})\s*(?:第)?\s*(?:[pP]\s*(\d+)|(\d+)\s*[pP])", raw
    )
    if m:
        page = m.group(2) or m.group(3)
        return VideoRef(bvid=m.group(1), page=max(1, int(page)), raw=raw)
    if re.fullmatch(r"av\d+", raw, re.I):
        return VideoRef(aid=int(raw[2:]), raw=raw)
    if re.fullmatch(r"ep\d+", raw, re.I):
        return VideoRef(ep_id=int(raw[2:]), raw=raw)

    url = raw
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    # b23.tv / bili2233.cn 短链：跟随跳转拿到真实 URL
    if re.search(r"(b23\.tv|bili2233\.cn|bili22\.cn|bili23\.cn)", url, re.I):
        try:
            with httpx.Client(
                headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
                follow_redirects=True,
                timeout=timeout,
            ) as client:
                url = str(client.get(url).url)
        except Exception as exc:  # noqa: BLE001
            raise ParseError(f"短链展开失败：{exc}") from exc

    page = 1
    m = PAGE_RE.search(url)
    if m:
        page = max(1, int(m.group(1)))

    m = BV_RE.search(url)
    if m:
        return VideoRef(bvid=m.group(1), page=page, raw=raw)

    m = EP_RE.search(url)
    if m:
        return VideoRef(ep_id=int(m.group(1)), page=page, raw=raw)

    m = AV_RE.search(url)
    if m:
        return VideoRef(aid=int(m.group(1)), page=page, raw=raw)

    raise ParseError(_hint_for(url))
