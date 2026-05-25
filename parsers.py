"""
多平台视频解析核心逻辑
基于 nonebot-plugin-parser 的技术方案扩展：
  - 抖音：window._ROUTER_DATA 提取
  - B站：window.__INITIAL_STATE__ 提取
  - 小红书：移动端页面 + API 提取
"""

import re
import random
import json
from typing import Any

import httpx
from msgspec import Struct, field


# ── 异常 ──────────────────────────────────────────────

class ParseError(Exception):
    """解析失败"""
    def __init__(self, message: str, detail: str = ""):
        self.message = message
        self.detail = detail
        super().__init__(f"{message} {detail}".strip())


# ── 数据模型 ──────────────────────────────────────────

class Author(Struct):
    name: str
    avatar_url: str | None = None


class VideoResult(Struct):
    platform: str
    type: str  # "视频" | "图文" | "图集"
    id: str
    title: str
    author: Author
    create_time: int | None = None
    cover_url: str | None = None
    video_url: str | None = None
    contents: list[dict] = field(default_factory=list)
    source_url: str | None = None


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
    
    # 正则提取 window._ROUTER_DATA
    pattern = re.compile(
        r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
        flags=re.DOTALL,
    )
    matched = pattern.search(text)
    if not matched:
        raise ParseError("页面中未找到 _ROUTER_DATA，可能页面结构已更新", url)
    
    raw = matched.group(1).strip()
    # 处理 JS undefined
    raw = re.sub(r":\s*undefined(?=[,\s\}\]])", ": null", raw)
    
    try:
        router_data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ParseError("_ROUTER_DATA JSON 解析失败", str(e))
    
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
    avatar_url = random.choice(avatar.get("url_list", [])) if avatar else None
    
    video_data = item.get("video", {})
    play_addr = video_data.get("play_addr", {})
    video_url = random.choice(play_addr.get("url_list", [])).replace("playwm", "play") if play_addr else None
    
    cover_data = video_data.get("cover", {})
    cover_url = random.choice(cover_data.get("url_list", [])) if cover_data else None
    
    duration = video_data.get("duration", 0) // 1000 if video_data else None
    
    # 图集
    image_list = item.get("images", [])
    contents = []
    
    if image_list:
        for img in image_list:
            urls = img.get("url_list", [])
            if urls:
                contents.append({"type": "image", "url": random.choice(urls)})
    
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
        resp.raise_for_status()
    
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
                contents.append({
                    "type": "video",
                    "url": random.choice(urls).replace("playwm", "play"),
                    "is_gif": True,
                })
        else:
            urls = img.get("url_list", [])
            if urls:
                contents.append({"type": "image", "url": random.choice(urls)})
    
    return {
        "platform": "抖音",
        "type": "图集",
        "id": vid,
        "title": sd.get("desc", "(无标题)"),
        "author": {
            "name": sd.get("author", {}).get("nickname", ""),
            "avatar_url": random.choice(sd.get("author", {}).get("avatar_thumb", {}).get("url_list", [])),
        },
        "create_time": sd.get("create_time"),
        "contents": contents,
    }


# ── B站解析 ──────────────────────────────────────────

BILI_SHORT_PATTERN = re.compile(r"b23\.tv/[a-zA-Z0-9_\-]+")
BILI_STANDARD_PATTERN = re.compile(
    r"bilibili\.com/video/BV(?P<bvid>[a-zA-Z0-9]+)"
    r"|bilibili\.com/video/av(?P<aid>\d+)"
    r"|bili\.link/[a-zA-Z0-9_\-]+"
)


def match_bilibili_url(url: str) -> tuple[str, str] | None:
    """匹配 B站 链接，返回 (bvid_or_aid, type)"""
    m = BILI_STANDARD_PATTERN.search(url)
    if m:
        bvid = m.group("bvid")
        aid = m.group("aid")
        if bvid:
            return f"BV{bvid}", "video"
        if aid:
            return aid, "video"
    return None


async def _get_bilibili_playurl(aid: int, cid: int, bvid: str, headers: dict) -> tuple[str, str | None, int | None]:
    """
    调用 B站 playurl API 获取视频直链
    
    需要先访问视频页获取 cookie，再用该 cookie 调用 API
    返回 (video_url, audio_url, duration)
    """
    # 使用桌面端 UA（B站 API 对移动端 UA 限制更严）
    desktop_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    async with httpx.AsyncClient(
        headers={"User-Agent": desktop_ua},
        follow_redirects=True,
        timeout=COMMON_TIMEOUT,
    ) as client:
        # 先访问视频页获取 cookie
        try:
            await client.get(f"https://www.bilibili.com/video/{bvid}")
        except Exception:
            pass  # 即使失败也继续
        
        # 调用 playurl API
        api_url = "https://api.bilibili.com/x/player/playurl"
        params = {
            "avid": aid,
            "cid": cid,
            "bvid": bvid,
            "qn": 80,
            "fnval": 0,  # 单文件 mp4（dash 需要额外签名）
            "fnver": 0,
            "fourk": 0,
        }
        
        resp = await client.get(api_url, params=params, headers={
            "Referer": f"https://www.bilibili.com/video/{bvid}",
        })
        resp.raise_for_status()
    
    data = resp.json()
    if data.get("code") != 0:
        return "", None, None
    
    d = data.get("data", {})
    duration = d.get("timelength") // 1000  # ms -> s
    
    # 优先尝试 dash（音视频分离，更高画质）
    dash = d.get("dash", {})
    videos = dash.get("video", [])
    audios = dash.get("audio", [])
    
    if videos:
        best_video = videos[-1]
        video_url = best_video.get("baseUrl", "")
        audio_url = None
        if audios:
            best_audio = audios[-1]
            audio_url = best_audio.get("baseUrl", "")
        return video_url, audio_url, duration
    
    # 回退到 durl（单文件 mp4）
    durl = d.get("durl", [])
    if durl:
        return durl[0].get("url", ""), None, duration
    
    return "", None, duration


async def parse_bilibili_url(raw_url: str) -> dict:
    """解析 B站 分享链接"""
    url = _extract_url(raw_url)
    if not url:
        raise ParseError("输入中未找到 http 链接，请提供正确的 B站 链接")
    
    # 短链接 → 重定向追踪
    if BILI_SHORT_PATTERN.search(url):
        redirected = await resolve_short_url(url)
        if redirected and redirected != url:
            url = redirected
    
    matched = match_bilibili_url(url)
    if not matched:
        raise ParseError("无法识别的 B站 链接", url)
    
    identifier, _ = matched
    
    # 请求 B站 移动端页面
    headers = {
        "User-Agent": random.choice(MOBILE_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    
    # 尝试用 BV 号访问
    if identifier.startswith("BV"):
        page_url = f"https://www.bilibili.com/video/{identifier}"
    else:
        page_url = f"https://www.bilibili.com/video/av{identifier}"
    
    last_error: Exception | None = None
    for attempt_url in [page_url]:
        try:
            return await _parse_bilibili_page(attempt_url, url, identifier)
        except ParseError as e:
            last_error = e
            continue
    
    raise ParseError(
        "解析失败，作品可能已删除或链接无效",
        str(last_error) if last_error else ""
    )


async def _parse_bilibili_page(url: str, source_url: str, identifier: str) -> dict:
    """请求 B站 页面，从 HTML 提取 __INITIAL_STATE__"""
    headers = {
        "User-Agent": random.choice(MOBILE_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=COMMON_TIMEOUT,
    ) as client:
        resp = await client.get(url)
    
    if resp.status_code != 200:
        raise ParseError(f"页面请求失败 (HTTP {resp.status_code})", url)
    
    text = resp.text
    
    # 提取 window.__INITIAL_STATE__
    pattern = re.compile(
        r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>",
        flags=re.DOTALL,
    )
    matched = pattern.search(text)
    if not matched:
        raise ParseError("页面中未找到 __INITIAL_STATE__，可能页面结构已更新", url)
    
    raw = matched.group(1).strip()
    # 处理 JS undefined
    raw = re.sub(r":\s*undefined(?=[,\s\}\]])", ": null", raw)
    
    # 更稳健的 JSON 提取：找到第一个完整的顶层对象
    depth = 0
    end_pos = 0
    in_string = False
    escape_next = False
    for i, c in enumerate(raw):
        if escape_next:
            escape_next = False
            continue
        if c == '\\':
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end_pos = i + 1
                break
    
    if end_pos > 0:
        raw = raw[:end_pos]
    
    try:
        initial_state = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ParseError("__INITIAL_STATE__ JSON 解析失败", str(e))
    
    # 导航到视频数据
    video_section = initial_state.get("video", {})
    view_info = video_section.get("viewInfo", {})
    
    if not view_info:
        raise ParseError("页面数据中未找到视频信息")
    
    # 提取基本信息
    title = view_info.get("title", "(无标题)")
    desc = view_info.get("desc", "")
    owner = view_info.get("owner", {})
    stat = view_info.get("stat", {})
    pages = view_info.get("pages", [])
    pic = view_info.get("pic", "")
    
    # 提取最高画质视频流
    video_url = None
    audio_url = None
    duration = None
    
    if pages:
        first_page = pages[0]
        dash = first_page.get("dash", {})
        
        if dash and dash.get("video"):
            # HTML 中已包含 dash 数据（较少见）
            videos = dash.get("video", [])
            audios = dash.get("audio", [])
            
            if videos:
                best_video = videos[-1]
                video_url = best_video.get("baseUrl", "")
            if audios:
                best_audio = audios[-1]
                audio_url = best_audio.get("baseUrl", "")
            duration = dash.get("duration")
        else:
            # 需要调用 playurl API 获取视频直链
            cid = first_page.get("cid")
            aid = view_info.get("aid")
            bvid = view_info.get("bvid", identifier)
            if cid and aid:
                video_url, audio_url, duration = await _get_bilibili_playurl(
                    aid, cid, bvid, headers
                )
    
    # 构建结果
    contents = []
    if video_url:
        contents.append({"type": "video", "url": video_url})
    
    return {
        "platform": "B站",
        "type": "视频",
        "id": identifier,
        "title": title,
        "author": {
            "name": owner.get("name", ""),
            "avatar_url": owner.get("face", ""),
        },
        "create_time": view_info.get("pubdate"),
        "cover_url": pic,
        "video": {
            "url": video_url,
            "cover_url": pic,
            "duration": int(duration) if duration else None,
            "audio_url": audio_url,  # 音视频分离时提供音频 URL
        } if video_url else None,
        "contents": contents,
        "source_url": source_url,
        "description": desc,
        "stats": {
            "views": stat.get("view"),
            "danmaku": stat.get("danmaku"),
            "reply": stat.get("reply"),
            "like": stat.get("like"),
        },
    }


# ── 小红书解析 ────────────────────────────────────────

XHS_SHORT_PATTERN = re.compile(r"xhslink\.com/[a-zA-Z0-9_\-]+")
XHS_STANDARD_PATTERN = re.compile(
    r"xiaohongshu\.com/explore/(?P<note_id>\w+)"
    r"|xiaohongshu\.com/discovery/item/(?P<note_id2>\w+)"
    r"|xhsdiscovery\.com/goto/\w+"
)


def match_xiaohongshu_url(url: str) -> str | None:
    """匹配小红书链接，返回 note_id"""
    m = XHS_STANDARD_PATTERN.search(url)
    if m:
        return m.group("note_id") or m.group("note_id2")
    return None


async def parse_xiaohongshu_url(raw_url: str) -> dict:
    """解析小红书分享链接"""
    url = _extract_url(raw_url)
    if not url:
        raise ParseError("输入中未找到 http 链接，请提供正确的小红书分享链接")
    
    # 短链接 → 重定向追踪
    if XHS_SHORT_PATTERN.search(url):
        redirected = await resolve_short_url(url)
        if redirected and redirected != url:
            url = redirected
    
    note_id = match_xiaohongshu_url(url)
    if not note_id:
        raise ParseError("无法识别的小红书链接", url)
    
    # 请求小红书笔记页面
    headers = {
        "User-Agent": random.choice(MOBILE_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
    }
    
    page_url = f"https://www.xiaohongshu.com/explore/{note_id}"
    
    last_error: Exception | None = None
    # 尝试多个 URL 变体
    for attempt_url in [page_url, f"https://m.xiaohongshu.com/explore/{note_id}"]:
        try:
            return await _parse_xiaohongshu_page(attempt_url, url, note_id)
        except ParseError as e:
            last_error = e
            continue
    
    raise ParseError(
        "解析失败，作品可能已删除或链接无效",
        str(last_error) if last_error else ""
    )


async def _parse_xiaohongshu_page(url: str, source_url: str, note_id: str) -> dict:
    """请求小红书页面，从 HTML 提取 __INITIAL_STATE__"""
    headers = {
        "User-Agent": random.choice(MOBILE_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
    }
    
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=COMMON_TIMEOUT,
    ) as client:
        resp = await client.get(url)
    
    if resp.status_code != 200:
        raise ParseError(f"页面请求失败 (HTTP {resp.status_code})", url)
    
    text = resp.text
    
    # 提取 window.__INITIAL_STATE__
    pattern = re.compile(
        r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>",
        flags=re.DOTALL,
    )
    matched = pattern.search(text)
    if not matched:
        raise ParseError("页面中未找到 __INITIAL_STATE__，可能页面结构已更新", url)
    
    raw = matched.group(1).strip()
    # 处理 JS undefined
    raw = re.sub(r":\s*undefined(?=[,\s\}\]])", ": null", raw)
    
    try:
        initial_state = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ParseError("__INITIAL_STATE__ JSON 解析失败", str(e))
    
    # 导航到笔记数据
    note_data = initial_state.get("noteData", {})
    note = note_data.get("data", {})
    
    if not note:
        raise ParseError("页面数据中未找到笔记内容，可能该笔记需要登录才能查看")
    
    # 提取基本信息
    media_list = note.get("mediaList", [])
    image_list = note.get("imageList", [])
    user = note.get("user", {})
    
    # 提取图片/视频 URL
    contents = []
    
    if media_list:
        media = media_list[0]
        # 检查 images 数组
        images = media.get("images", [])
        if images:
            for img in images:
                if isinstance(img, dict):
                    url = _extract_image_url(img)
                    if url:
                        contents.append({"type": "image", "url": url})
        else:
            # 直接取 media 的 URL
            url = _extract_image_url(media)
            if url:
                contents.append({"type": "image", "url": url})
    elif image_list:
        for img in image_list:
            if isinstance(img, dict):
                url = _extract_image_url(img)
                if url:
                    contents.append({"type": "image", "url": url})
    
    # 如果没有提取到内容，尝试其他字段
    if not contents:
        # 尝试从 note 的其他字段找
        for key in ["mediaList", "imageList"]:
            items = note.get(key, [])
            if items and isinstance(items, list):
                for item in items[:1]:
                    if isinstance(item, dict):
                        url = _extract_image_url(item)
                        if url:
                            contents.append({"type": "image", "url": url})
                            break
    
    return {
        "platform": "小红书",
        "type": "图文" if contents else "动态",
        "id": note_id,
        "title": note.get("desc", "(无标题)"),
        "author": {
            "name": user.get("nickname", ""),
            "avatar_url": user.get("avatarUrl", user.get("avatar_url", "")),
        },
        "create_time": note.get("time"),
        "contents": contents,
        "source_url": source_url,
    }


def _extract_image_url(item: dict) -> str:
    """从小红书图片对象中提取 CDN URL，找不到返回空字符串"""
    # 尝试多种可能的 URL 字段
    url_fields = [
        "url", "image_url", "cdnUrl", "imgUrl", "src",
        "origin_image_list", "download_url", "file_name",
    ]
    
    for field in url_fields:
        val = item.get(field)
        if isinstance(val, str) and val.startswith("http"):
            return val
        if isinstance(val, dict):
            for sub_field in ["url", "cdnUrl", "imgUrl", "src"]:
                if sub_field in val and isinstance(val[sub_field], str) and val[sub_field].startswith("http"):
                    return val[sub_field]
    
    # 检查 transformUrls
    transform_urls = item.get("transformUrls", [])
    if isinstance(transform_urls, list) and transform_urls:
        for tu in transform_urls:
            if isinstance(tu, str) and tu.startswith("http"):
                return tu
    
    # 检查 original 字段
    original = item.get("original")
    if isinstance(original, str) and original.startswith("http"):
        return original
    if isinstance(original, dict):
        for k in ["url", "cdnUrl", "imgUrl"]:
            if k in original and isinstance(original[k], str) and original[k].startswith("http"):
                return original[k]
    
    return ""


# ── 统一入口 ──────────────────────────────────────────

_PLATFORM_PARSERS = {
    "douyin": parse_douyin_url,
    "bilibili": parse_bilibili_url,
    "xiaohongshu": parse_xiaohongshu_url,
}


def detect_platform(url: str) -> str | None:
    """自动检测链接所属平台"""
    if "douyin.com" in url or "iesdouyin.com" in url or "v.douyin.com" in url:
        return "douyin"
    if "bilibili.com" in url or "b23.tv" in url or "bili.link" in url:
        return "bilibili"
    if "xiaohongshu.com" in url or "xhslink.com" in url or "xhsdiscovery.com" in url:
        return "xiaohongshu"
    return None


async def parse_any_url(raw_input: str) -> dict:
    """
    统一解析入口：自动检测平台并路由到对应解析器
    
    支持：
      - 抖音：短链接、标准链接、整段分享文本
      - B站：BV号、AV号、短链接
      - 小红书：探索页链接、详情页链接
    """
    url = _extract_url(raw_input)
    if not url:
        raise ParseError("输入中未找到 http 链接，请提供正确的分享链接")
    
    platform = detect_platform(url)
    if not platform:
        raise ParseError(f"无法识别的平台，当前支持：抖音、B站、小红书", url)
    
    parser = _PLATFORM_PARSERS[platform]
    return await parser(raw_input)
