"""
抖音视频解析核心逻辑
基于 nonebot-plugin-parser 的技术方案：
  1. 短链接 → 302 重定向追踪拿到真实 URL
  2. 请求 m.douyin.com 移动端页面
  3. 从 HTML 正则提取 window._ROUTER_DATA JSON
  4. 用 msgspec 反序列化出视频/图文数据
  5. playwm → play 去水印
"""

import re
import random
from typing import Any

import httpx
from msgspec import Struct, field
from msgspec.json import Decoder


# ── 异常 ──────────────────────────────────────────────

class ParseError(Exception):
    """解析失败"""
    def __init__(self, message: str, detail: str = ""):
        self.message = message
        self.detail = detail
        super().__init__(f"{message} {detail}".strip())


# ── 数据模型（完整对应抖音 _ROUTER_DATA 结构）───────────

class Avatar(Struct):
    url_list: list[str]

class AuthorInfo(Struct):
    nickname: str
    avatar_thumb: Avatar | None = None
    avatar_medium: Avatar | None = None

class PlayAddr(Struct):
    url_list: list[str]

class Cover(Struct):
    url_list: list[str]

class VideoInfo(Struct):
    play_addr: PlayAddr
    cover: Cover
    duration: int

class ImageItem(Struct):
    video: VideoInfo | None = None
    url_list: list[str] = field(default_factory=list)

class VideoData(Struct):
    """视频/图集的完整数据"""
    create_time: int
    author: AuthorInfo
    desc: str
    images: list[ImageItem] | None = None
    video: VideoInfo | None = None

    @property
    def image_urls(self) -> list[str]:
        """随机选一张 CDN 地址（淘宝风格均衡负载）"""
        return [random.choice(img.url_list) for img in self.images] if self.images else []

    @property
    def video_url(self) -> str | None:
        """无水印视频地址：playwm → play"""
        if self.video:
            return random.choice(self.video.play_addr.url_list).replace("playwm", "play")
        return None

    @property
    def cover_url(self) -> str | None:
        if self.video:
            return random.choice(self.video.cover.url_list)
        return None

    @property
    def duration(self) -> int | None:
        """转为秒"""
        return self.video.duration // 1000 if self.video else None

    @property
    def avatar_url(self) -> str | None:
        if av := self.author.avatar_thumb:
            return random.choice(av.url_list)
        if av := self.author.avatar_medium:
            return random.choice(av.url_list)
        return None


class VideoInfoRes(Struct):
    item_list: list[VideoData] = field(default_factory=list)

    @property
    def first(self) -> VideoData:
        if not self.item_list:
            raise ParseError("视频数据为空")
        return self.item_list[0]

class VideoOrNotePage(Struct):
    video_info_res: VideoInfoRes = field(name="videoInfoRes", default_factory=VideoInfoRes)

class LoaderData(Struct):
    video_page: VideoOrNotePage | None = field(name="video_(id)/page", default=None)
    note_page: VideoOrNotePage | None = field(name="note_(id)/page", default=None)

class RouterData(Struct):
    """window._ROUTER_DATA 顶层结构"""
    loader_data: LoaderData = field(name="loaderData", default_factory=LoaderData)
    errors: dict[str, Any] | None = None

    @property
    def video_data(self) -> VideoData:
        if page := self.loader_data.video_page:
            return page.video_info_res.first
        if page := self.loader_data.note_page:
            return page.video_info_res.first
        raise ParseError("页面数据中未找到 video_(id)/page 或 note_(id)/page")


_router_decoder = Decoder(RouterData)


# ── 图集（slides）数据模型 ─────────────────────────────

class SlidesAvatar(Struct):
    url_list: list[str]

class SlidesAuthor(Struct):
    nickname: str
    avatar_thumb: SlidesAvatar

class SlidesImageItem(Struct):
    video: VideoInfo | None = None
    url_list: list[str] = field(default_factory=list)

class SlidesData(Struct):
    author: SlidesAuthor
    desc: str
    create_time: int
    images: list[SlidesImageItem]

    @property
    def name(self) -> str:
        return self.author.nickname

    @property
    def avatar_url(self) -> str:
        return random.choice(self.author.avatar_thumb.url_list)

    @property
    def image_urls(self) -> list[str]:
        return [random.choice(img.url_list) for img in self.images]

    @property
    def dynamic_urls(self) -> list[str]:
        """图集中含视频的动图"""
        return [random.choice(img.video.play_addr.url_list) for img in self.images if img.video]

class SlidesInfo(Struct):
    aweme_details: list[SlidesData] = field(default_factory=list)

_slides_decoder = Decoder(SlidesInfo)


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


# ── 短链接 → 真实 URL ─────────────────────────────────

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
        # 如果没 Location 但 200 了（极小概率），用当前 URL
        return str(resp.url)


# ── URL 匹配 ──────────────────────────────────────────

# 短链接匹配
SHORT_URL_PATTERN = re.compile(r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
# 标准 URL 匹配（提取 type + id）
STANDARD_URL_PATTERN = re.compile(
    r"douyin\.com/(?P<ty>video|note)/(?P<vid>\d+)"           # www.douyin.com
    r"|iesdouyin\.com/share/(?P<ty2>slides|video|note)/(?P<vid2>\d+)"  # iesdouyin
    r"|jingxuan\.douyin\.com/m/(?P<ty3>slides|video|note)/(?P<vid3>\d+)"  # 精选页
)

def match_url(url: str) -> tuple[str, str] | None:
    """
    匹配抖音链接，返回 (type, id)
    type: 'video' | 'note' | 'slides'
    不匹配则返回 None
    """
    m = STANDARD_URL_PATTERN.search(url)
    if m:
        groups = m.groupdict()
        ty = groups.get("ty") or groups.get("ty2") or groups.get("ty3") or "video"
        vid = groups.get("vid") or groups.get("vid2") or groups.get("vid3") or ""
        return ty, vid
    return None


# ── 核心解析函数 ──────────────────────────────────────

# ── 从混杂文本中提取 URL ──────────────────────────────────

_URL_EXTRACT_PATTERN = re.compile(
    r"https?://[a-zA-Z0-9./?=&\-_%]+"
)


def _extract_url(text: str) -> str | None:
    """从分享文本中提取第一个 http(s) 链接"""
    m = _URL_EXTRACT_PATTERN.search(text.strip())
    if m:
        return m.group(0).rstrip("/")
    return None


# ── 核心解析函数 ──────────────────────────────────────

async def parse_douyin_url(raw_url: str) -> dict:
    """
    解析抖音分享链接，返回结构化的解析结果

    支持：
      - 短链接：https://v.douyin.com/xxxxx/
      - 标准链接：https://www.douyin.com/video/xxx
      - 标准图文：https://www.douyin.com/note/xxx
      - 历史域名：iesdouyin.com, jingxuan.douyin.com
      - 也可以传整段分享文本（自动从中提取链接）
    """
    # 第一步：从输入中提取干净的 URL（兼容整段分享文本）
    url = _extract_url(raw_url)
    if not url:
        raise ParseError("输入中未找到 http 链接，请提供正确的抖音分享链接")

    # 第二步：短链接 → 重定向追踪
    if SHORT_URL_PATTERN.search(url):
        redirected = await resolve_short_url(url)
        if redirected and redirected != url:
            url = redirected

    # ── 第二步：匹配标准 URL ──
    matched = match_url(url)
    if not matched:
        raise ParseError("无法识别的抖音链接", url)

    ty, vid = matched

    # ── 第三步：slides（图集）走专用 API ──
    if ty == "slides":
        return await _parse_slides(vid)

    # ── 第四步：视频/图文走 HTML 提取 ──
    # 先尝试 m.douyin.com，再试 iesdouyin.com
    candidate_urls = [
        f"https://m.douyin.com/share/{ty}/{vid}",
        f"https://www.iesdouyin.com/share/{ty}/{vid}",
    ]

    last_error: Exception | None = None
    for candidate in candidate_urls:
        try:
            return _build_result(video_data, source_url=url, item_id=vid)
        except ParseError as e:
            last_error = e
            continue

    raise ParseError(
        "解析失败，作品可能已删除或链接无效",
        str(last_error) if last_error else ""
    )


async def _parse_from_page(url: str) -> VideoData:
    """请求移动端页面，从 HTML 提取 _ROUTER_DATA"""
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

    # 反序列化
    try:
        router_data: RouterData = _router_decoder.decode(matched.group(1).strip())
    except Exception as e:
        raise ParseError("_ROUTER_DATA JSON 解析失败", str(e))

    video_data = router_data.video_data

    return video_data


async def _parse_slides(vid: str) -> dict:
    """走 slides 专用 API"""
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

    slides_info: SlidesInfo = _slides_decoder.decode(resp.content)
    if not slides_info.aweme_details:
        raise ParseError("slides API 返回数据为空")

    sd = slides_info.aweme_details[0]

    # 构建返回结果
    contents: list[dict] = []
    # 优先取动图
    if dynamic_urls := sd.dynamic_urls:
        for url in dynamic_urls:
            contents.append({
                "type": "video",
                "url": url.replace("playwm", "play"),
                "is_gif": True,
            })
    elif image_urls := sd.image_urls:
        for url in image_urls:
            contents.append({"type": "image", "url": url})

    return {
        "platform": "抖音",
        "type": "图集",
        "id": vid,
        "title": sd.desc,
        "author": {
            "name": sd.name,
            "avatar_url": sd.avatar_url,
        },
        "create_time": sd.create_time,
        "contents": contents,
    }


def _build_result(video_data: VideoData, source_url: str, item_id: str) -> dict:
    """将 VideoData 转为统一返回格式"""
    contents: list[dict] = []

    image_urls = video_data.image_urls

    # 图集（图片）
    if image_urls:
        for url in image_urls:
            contents.append({"type": "image", "url": url})

    # 视频
    video_info = None
    video_url = video_data.video_url
    if video_url:
        video_info = {
            "url": video_url,
            "cover_url": video_data.cover_url,
            "duration": video_data.duration,
        }
        if not contents:  # 纯视频（无图集）
            contents.append({"type": "video", "url": video_url})

    content_type = "视频" if video_info and not image_urls else "图文" if image_urls else "动态"

    return {
        "platform": "抖音",
        "type": content_type,
        "id": item_id,
        "title": video_data.desc or "(无标题)",
        "author": {
            "name": video_data.author.nickname,
            "avatar_url": video_data.avatar_url,
        },
        "create_time": video_data.create_time,
        "video": video_info,
        "contents": contents,
        "source_url": source_url,
    }