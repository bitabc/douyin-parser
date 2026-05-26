"""
抖音视频解析核心逻辑
基于 nonebot-plugin-parser 的技术方案：
  - 抖音：window._ROUTER_DATA 提取
"""

import re
import random
import json
from typing import Any

import httpx


# ── 异常 ──────────────────────────────────────────────

class ParseError(Exception):
    """解析失败"""
    def __init__(self, message: str, detail: str = ""):
        self.message = message
        self.detail = detail
        super().__init__(f"{message} {detail}".strip())


# ── User-Agent 池 ─────────────────────────────────────

MOBILE_UAS = [
    # iOS Safari
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    # Android Chrome
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36",
    # Android 小米
    "Mozilla/5.0 (Linux; Android 13; Mi 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/110.0.5481.153 Mobile Safari/537.36",
]

COMMON_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


# ── 工具函数 ──────────────────────────────────────────

def _safe_choice(lst: list) -> Any | None:
    """安全地从列表中随机选取，空列表返回 None"""
    return random.choice(lst) if lst else None


# ── URL 提取 ──────────────────────────────────────────

_URL_EXTRACT_PATTERN = re.compile(r"https?://[a-zA-Z0-9./?=&\-_%]+")


def _extract_url(text: str) -> str | None:
    """从分享文本中提取第一个 http(s) 链接"""
    m = _URL_EXTRACT_PATTERN.search(text.strip())
    if m:
        return m.group(0).rstrip("/")
    return None


# ── 短链接追踪 ────────────────────────────────────────

async def resolve_short_url(url: str) -> str:
    """追踪 302 重定向，拿到真实 URL（不跟随重定向，只读 Location 头）"""
    headers = {
        "User-Agent": random.choice(MOBILE_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=False,
        timeout=COMMON_TIMEOUT,
    ) as client:
        resp = await client.get(url)
        location = resp.headers.get("Location", "")
        if location:
            return location
        return str(resp.url)


# ── 通用 HTML 解析工具 ────────────────────────────────

def extract_json_from_html(html: str, var_name: str) -> dict | None:
    """
    从 HTML 中提取 window.VAR_NAME = {...}</script> 中的 JSON
    
    处理 JS undefined -> null 等不合法 JSON
    """
    pattern = re.compile(
        rf"window\.{re.escape(var_name)}\s*=\s*(.*?)</script>",
        flags=re.DOTALL,
    )
    matched = pattern.search(html)
    if not matched:
        return None
    
    raw = matched.group(1).strip()
    
    # 找到真正的 JSON 结束位置（匹配花括号）
    depth = 0
    end_pos = 0
    for i, c in enumerate(raw):
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end_pos = i + 1
                break
    
    if end_pos > 0:
        raw = raw[:end_pos]
    
    # 处理 JS undefined -> null
    raw = re.sub(r":\s*undefined(?=[,\s\}\]])", ": null", raw)
    
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ── 抖音解析 ──────────────────────────────────────────

SHORT_URL_PATTERN = re.compile(r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
STANDARD_URL_PATTERN = re.compile(
    r"douyin\.com/(?P<ty>video|note)/(?P<vid>\d+)"
    r"|iesdouyin\.com/share/(?P<ty2>slides|video|note)/(?P<vid2>\d+)"
    r"|jingxuan\.douyin\.com/m/(?P<ty3>slides|video|note)/(?P<vid3>\d+)"
)


def match_douyin_url(url: str) -> tuple[str, str] | None:
    """匹配抖音链接，返回 (type, id)"""
    m = STANDARD_URL_PATTERN.search(url)
    if m:
        groups = m.groupdict()
        ty = groups.get("ty") or groups.get("ty2") or groups.get("ty3") or "video"
        vid = groups.get("vid") or groups.get("vid2") or groups.get("vid3") or ""
        return ty, vid
    return None


async def parse_douyin_url(raw_url: str) -> dict:
    """解析抖音分享链接"""
    url = _extract_url(raw_url)
    if not url:
        raise ParseError("输入中未找到 http 链接，请提供正确的抖音分享链接")
    
    # 短链接 → 重定向追踪
    if SHORT_URL_PATTERN.search(url):
        redirected = await resolve_short_url(url)
        if redirected and redirected != url:
            url = redirected
    
    matched = match_douyin_url(url)
    if not matched:
        raise ParseError("无法识别的抖音链接", url)
    
    ty, vid = matched
    
    # slides（图集）走专用 API
    if ty == "slides":
        return await _parse_douyin_slides(vid)
    
    # 视频/图文走 HTML 提取
    candidate_urls = [
        f"https://m.douyin.com/share/{ty}/{vid}",
        f"https://www.iesdouyin.com/share/{ty}/{vid}",
    ]
    
    last_error: Exception | None = None
    for candidate in candidate_urls:
        try:
            result = await _parse_douyin_page(candidate, url, vid)
            return result
        except ParseError as e:
            last_error = e
            continue
    
    raise ParseError(
        "解析失败，作品可能已删除或链接无效",
        str(last_error) if last_error else ""
    )


async def _parse_douyin_page(url: str, source_url: str, item_id: str) -> dict:
    """请求抖音移动端页面，从 HTML 提取 _ROUTER_DATA"""
    headers = {
        "User-Agent": random.choice(MOBILE_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": "__ac_nonce=0; __ac_signature=_02B4Z6wo00f01lT6fsgAAIDBOyKlTIMe-2MpzkKAAI4280;",
    }
    
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=False,
        timeout=COMMON_TIMEOUT,
    ) as client:
        resp = await client.get(url)
    
    if resp.status_code != 200:
        raise ParseError(f"页面请求失败 (HTTP {resp.status_code})", url)
    
    text = resp.text
    
    # 复用 extract_json_from_html 提取 window._ROUTER_DATA
    router_data = extract_json_from_html(text, "_ROUTER_DATA")
    if not router_data:
        raise ParseError("页面中未找到 _ROUTER_DATA，可能页面结构已更新", url)
    
    # 提取视频数据
    loader_data = router_data.get("loaderData", {})
    video_page = loader_data.get("video_(id)/page") or loader_data.get("note_(id)/page")
    
    if not video_page:
        raise ParseError("页面数据中未找到视频信息")
    
    video_info_res = video_page.get("videoInfoRes", {})
    item_list = video_info_res.get("item_list", [])
    
    if not item_list:
        raise ParseError("视频数据为空")
    
    item = item_list[0]
    
    # 构建结果
    author = item.get("author", {})
    avatar = author.get("avatar_thumb") or author.get("avatar_medium")
    avatar_url = _safe_choice(avatar.get("url_list", [])) if avatar else None
    
    video_data = item.get("video", {})
    play_addr = video_data.get("play_addr", {})
    video_url_raw = _safe_choice(play_addr.get("url_list", []))
    video_url = video_url_raw.replace("playwm", "play") if video_url_raw else None
    
    cover_data = video_data.get("cover", {})
    cover_url = _safe_choice(cover_data.get("url_list", [])) if cover_data else None
    
    duration = video_data.get("duration", 0) // 1000 if video_data else None
    
    # 图集
    image_list = item.get("images", [])
    contents = []
    
    if image_list:
        for img in image_list:
            urls = img.get("url_list", [])
            if urls:
                img_url = _safe_choice(urls)
                if img_url:
                    contents.append({"type": "image", "url": img_url})
    
    if video_url:
        if not contents:  # 纯视频（无图集）
            contents.append({"type": "video", "url": video_url})
    
    content_type = "视频" if video_url and not image_list else "图文" if image_list else "动态"
    
    return {
        "platform": "抖音",
        "type": content_type,
        "id": item_id,
        "title": item.get("desc", "(无标题)"),
        "author": {
            "name": author.get("nickname", ""),
            "avatar_url": avatar_url,
        },
        "create_time": item.get("create_time"),
        "video": {
            "url": video_url,
            "cover_url": cover_url,
            "duration": duration,
        } if video_url else None,
        "contents": contents,
        "source_url": source_url,
    }


async def _parse_douyin_slides(vid: str) -> dict:
    """走抖音 slides 专用 API"""
    api_url = "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/"
    params = {
        "aweme_ids": f"[{vid}]",
        "request_source": "200",
    }
    headers = {
        "User-Agent": random.choice(MOBILE_UAS),
        "Referer": f"https://www.iesdouyin.com/share/slides/{vid}",
        "Accept": "application/json, text/plain, */*",
    }
    
    async with httpx.AsyncClient(
        headers=headers,
        timeout=COMMON_TIMEOUT,
    ) as client:
        resp = await client.get(api_url, params=params)
        if resp.status_code != 200:
            raise ParseError(f"slides API 请求失败 (HTTP {resp.status_code})")
    
    slides_info = resp.json()
    aweme_details = slides_info.get("aweme_details", [])
    
    if not aweme_details:
        raise ParseError("slides API 返回数据为空")
    
    sd = aweme_details[0]
    contents = []
    
    # 优先取动图
    for img in sd.get("images", []):
        video_data = img.get("video")
        if video_data:
            play_addr = video_data.get("play_addr", {})
            urls = play_addr.get("url_list", [])
            if urls:
                raw_url = _safe_choice(urls)
                if raw_url:
                    contents.append({
                        "type": "video",
                        "url": raw_url.replace("playwm", "play"),
                        "is_gif": True,
                    })
        else:
            urls = img.get("url_list", [])
            if urls:
                img_url = _safe_choice(urls)
                if img_url:
                    contents.append({"type": "image", "url": img_url})
    
    # 作者头像
    author_data = sd.get("author", {})
    avatar_thumb = author_data.get("avatar_thumb", {})
    avatar_url = _safe_choice(avatar_thumb.get("url_list", []))
    
    return {
        "platform": "抖音",
        "type": "图集",
        "id": vid,
        "title": sd.get("desc", "(无标题)"),
        "author": {
            "name": sd.get("author", {}).get("nickname", ""),
            "avatar_url": avatar_url,
        },
        "create_time": sd.get("create_time"),
        "contents": contents,
    }


# ── 统一入口 ──────────────────────────────────────────

_PLATFORM_PARSERS = {
    "douyin": parse_douyin_url,
}


def detect_platform(url: str) -> str | None:
    """自动检测链接所属平台（精确域名匹配）"""
    from urllib.parse import urlsplit
    try:
        hostname = urlsplit(url).hostname or ""
    except Exception:
        return None
    
    douyin_domains = {"douyin.com", "iesdouyin.com", "v.douyin.com", "jingxuan.douyin.com"}
    if hostname in douyin_domains or any(hostname.endswith("." + d) for d in douyin_domains):
        return "douyin"
    return None


async def parse_any_url(raw_input: str) -> dict:
    """
    统一解析入口：自动检测平台并路由到对应解析器
    
    支持：
      - 抖音：短链接、标准链接、整段分享文本
    """
    url = _extract_url(raw_input)
    if not url:
        raise ParseError("输入中未找到 http 链接，请提供正确的分享链接")
    
    platform = detect_platform(url)
    if not platform:
        raise ParseError(f"无法识别的平台，当前仅支持：抖音", url)
    
    parser = _PLATFORM_PARSERS[platform]
    return await parser(raw_input)
