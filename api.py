"""
抖音解析 API 服务
────────────────────────────────────────────────
解析抖音分享链接，提取无水印视频/图文/图集。

技术原理:
  1. 从分享文本中提取真实 URL
  2. 短链接 → 302 重定向追踪
  3. 请求 m.douyin.com 移动端页面
  4. 正则提取 window._ROUTER_DATA JSON
  5. msgspec 高性能反序列化
  6. playwm → play 去除水印
"""

import time
import random
import asyncio
import logging
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from parsers import parse_douyin_url, ParseError, MOBILE_UAS

logger = logging.getLogger(__name__)

app = FastAPI(
    title="抖音解析服务",
    description="""
    解析抖音分享链接，提取无水印视频 / 图文 / 图集。

    ## ✨ 功能特性
    - 支持短链接、标准链接、整段分享文本
    - 自动追踪 302 重定向
    - 提取无水印视频直链
    - 支持图集（slides）/ 图文（note）/ 视频
    - 媒体代理转发（解决 Referer/CORS 拦截）
    - 视频流式播放 + 进度条拖动
    - 一键下载 MP4

    ## 🚀 快速开始
    ```bash
    curl "http://localhost:8899/api/parse?url=https://v.douyin.com/xxxxx/"
    ```
    """,
    version="2.0.0",
    contact={
        "name": "抖音解析工具",
        "url": "https://github.com/bitabc/douyin-parser",
    },
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


# ── 数据模型 ─────────────────────────────────────────

class ParseRequest(BaseModel):
    """解析请求"""
    url: str = Field(
        ...,
        description="抖音分享链接或整段分享文本",
        json_schema_extra={"example": "https://v.douyin.com/mXosMBsOHF0/"},
    )


class AuthorInfo(BaseModel):
    """作者信息"""
    name: str = Field(..., description="作者昵称", json_schema_extra={"example": "月野喵🌙"})
    avatar_url: str | None = Field(None, description="作者头像 URL")


class VideoInfo(BaseModel):
    """视频信息"""
    url: str = Field(..., description="无水印视频直链")
    cover_url: str | None = Field(None, description="视频封面 URL")
    duration: int | None = Field(None, description="视频时长（秒）")


class MediaItem(BaseModel):
    """媒体内容项"""
    type: str = Field(..., description="媒体类型: video / image", json_schema_extra={"example": "video"})
    url: str = Field(..., description="媒体文件 URL")


class ParseData(BaseModel):
    """解析结果数据"""
    platform: str = Field(..., description="平台", json_schema_extra={"example": "抖音"})
    type: str = Field(..., description="内容类型: 视频 / 图文 / 图集", json_schema_extra={"example": "视频"})
    id: str = Field("", description="视频/图文 ID")
    title: str = Field(..., description="作品标题/文案")
    author: AuthorInfo = Field(..., description="作者信息")
    create_time: int | None = Field(None, description="发布时间戳（秒）")
    video: VideoInfo | None = Field(None, description="视频信息（纯视频时存在）")
    contents: list[MediaItem] = Field(default_factory=list, description="媒体列表")
    source_url: str | None = Field(None, description="解析来源 URL")


class ParseResponse(BaseModel):
    """解析响应（通用包装）"""
    success: bool = Field(..., description="是否成功", json_schema_extra={"example": True})
    data: ParseData | None = Field(None, description="解析结果数据")
    error: str | None = Field(None, description="错误信息（失败时）")
    elapsed: float | None = Field(None, description="耗时（秒）")


# ── 媒体代理 ─────────────────────────────────────────

ALLOWED_MEDIA_HOSTS = {
    "aweme.snssdk.com",
}
ALLOWED_MEDIA_HOST_SUFFIXES = (
    ".douyinpic.com",
    ".douyinvod.com",
    ".byteimg.com",
    ".byted-static.com",
    ".bytegoofy.com",
)
MAX_REDIRECTS = 3
PROXY_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


@app.on_event("startup")
async def startup_event() -> None:
    app.state.media_client = httpx.AsyncClient(timeout=PROXY_TIMEOUT, follow_redirects=False)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    client = getattr(app.state, "media_client", None)
    if client is not None:
        await client.aclose()


def get_media_headers():
    """生成随机的请求头（降低被拦截概率）"""
    return {
        "User-Agent": random.choice(MOBILE_UAS),
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.douyin.com",
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }


def _is_allowed_media_host(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    return host in ALLOWED_MEDIA_HOSTS or host.endswith(ALLOWED_MEDIA_HOST_SUFFIXES)


def validate_media_proxy_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="仅支持代理 http/https 媒体地址")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="媒体地址缺少主机名")

    if not _is_allowed_media_host(hostname):
        raise HTTPException(status_code=400, detail="仅支持代理抖音媒体地址")

    return parsed.geturl()


async def fetch_media_with_redirects(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> httpx.Response:
    current_url = validate_media_proxy_url(url)

    for _ in range(MAX_REDIRECTS + 1):
        req = client.build_request("GET", current_url, headers=headers)
        resp = await client.send(req, stream=True)

        if resp.status_code in {301, 302, 303, 307, 308}:
            location = resp.headers.get("location")
            await resp.aclose()
            if not location:
                raise HTTPException(status_code=502, detail="上游重定向缺少 Location")
            current_url = validate_media_proxy_url(urljoin(current_url, location))
            continue

        return resp

    raise HTTPException(status_code=502, detail=f"媒体重定向次数超过 {MAX_REDIRECTS} 次")


@app.get(
    "/api/proxy/media",
    tags=["媒体代理"],
    summary="代理媒体文件",
    description="""
    流式代理抖音视频/图片文件。

    **解决以下问题：**
    - 抖音 CDN 的 Referer 检查
    - 浏览器跨域（CORS）拦截
    - 视频进度条拖动（Range 请求支持）

    **使用方式：**
    将解析接口返回的 `video.url` 或 `contents[].url` 传入 `url` 参数即可。

    添加 `&download=1` 可触发浏览器下载。
    """,
)
async def proxy_media(
    request: Request,
    url: str = Query(..., description="抖音媒体文件直链（来自解析接口返回的 url 字段）"),
    download: str = Query("", description="设为 1 触发浏览器下载"),
):
    is_download = download.lower() in ("1", "true", "yes")
    target_url = validate_media_proxy_url(url)
    req_headers = get_media_headers()
    media_client = request.app.state.media_client

    if range_header := request.headers.get("range"):
        req_headers["Range"] = range_header

    last_error = None
    for attempt in range(3):
        try:
            resp = await fetch_media_with_redirects(media_client, target_url, req_headers)

            if resp.status_code >= 400:
                await resp.aclose()
                last_error = f"HTTP {resp.status_code}"
                req_headers["User-Agent"] = random.choice(MOBILE_UAS)
                continue

            content_type = resp.headers.get("content-type", "application/octet-stream")
            resp_headers = {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Content-Type": content_type,
                "Accept-Ranges": "bytes",
            }

            for h in ("content-range", "content-length", "content-disposition",
                      "cache-control", "etag", "last-modified"):
                if val := resp.headers.get(h):
                    resp_headers[h] = val

            if is_download:
                filename = f"douyin_video_{int(time.time())}.mp4"
                resp_headers["Content-Disposition"] = f'attachment; filename="{filename}"'

            async def gen(resp_=resp):
                try:
                    async for chunk in resp_.aiter_bytes():
                        yield chunk
                finally:
                    await resp_.aclose()

            return StreamingResponse(
                gen(),
                status_code=resp.status_code,
                headers=resp_headers,
                media_type=content_type,
            )

        except HTTPException:
            raise
        except Exception as e:
            last_error = str(e)
            logger.warning("media proxy attempt %s failed", attempt + 1, exc_info=True)
            req_headers["User-Agent"] = random.choice(MOBILE_UAS)
            await asyncio.sleep(0.5)
            continue

    raise HTTPException(
        status_code=502,
        detail=f"媒体代理失败（重试 {3} 次）: {last_error}",
    )


# ── 解析 API ─────────────────────────────────────────

@app.get(
    "/api/parse",
    response_model=ParseResponse,
    tags=["解析接口"],
    summary="解析抖音链接（GET）",
    description="""
    解析抖音分享链接，返回无水印视频 / 图文 / 图集信息。

    **支持以下格式的输入：**

    1. **短链接**: `https://v.douyin.com/xxxxx/`
    2. **标准视频**: `https://www.douyin.com/video/7521023890996514083`
    3. **标准图文**: `https://www.douyin.com/note/7469411074119322899`
    4. **整段分享文本**: 自动提取其中的 URL

    **返回结果包含：**
    - 作者昵称 + 头像
    - 作品标题 / 文案
    - 无水印视频直链（playwm → play）
    - 视频封面 + 时长
    - 图集图片列表
    - 发布时间
    """,
    responses={
        200: {
            "description": "解析成功",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "data": {
                            "platform": "抖音",
                            "type": "视频",
                            "title": "我心里的虞姬不是一个只会流泪的弱女子#虞姬",
                            "author": {
                                "name": "月野喵🌙",
                                "avatar_url": "https://p11.douyinpic.com/aweme/100x100/...",
                            },
                            "create_time": 1742812800,
                            "video": {
                                "url": "https://aweme.snssdk.com/aweme/v1/play/?video_id=...",
                                "cover_url": "https://p26-sign.douyinpic.com/tos-cn-p-0015/...",
                                "duration": 9,
                            },
                            "contents": [{"type": "video", "url": "https://aweme.snssdk.com/..."}],
                            "source_url": "https://m.douyin.com/share/video/...",
                        },
                        "elapsed": 1.357,
                    }
                }
            },
        },
        400: {"description": "参数错误"},
        502: {"description": "上游请求失败"},
    },
)
async def api_parse_get(
    url: str = Query(
        ...,
        description="抖音分享链接或整段分享文本",
    ),
):
    return await _do_parse(url)


@app.post(
    "/api/parse",
    response_model=ParseResponse,
    tags=["解析接口"],
    summary="解析抖音链接（POST）",
    description="POST 方式解析，适合传入较长的分享文本。功能与 GET 相同。",
)
async def api_parse_post(req: ParseRequest):
    return await _do_parse(req.url)


async def _do_parse(url: str) -> ParseResponse:
    if not url:
        raise HTTPException(status_code=400, detail="url 参数不能为空")
    start = time.time()
    try:
        result = await parse_douyin_url(url)
        elapsed = round(time.time() - start, 3)
        return ParseResponse(success=True, data=result, elapsed=elapsed)
    except ParseError as e:
        elapsed = round(time.time() - start, 3)
        return ParseResponse(success=False, error=e.message, elapsed=elapsed)
    except Exception:
        elapsed = round(time.time() - start, 3)
        logger.exception("parse request failed")
        return ParseResponse(success=False, error="服务异常，请稍后重试", elapsed=elapsed)


# ── 健康检查 ─────────────────────────────────────────

@app.get(
    "/health",
    tags=["系统"],
    summary="健康检查",
    description="检查服务是否正常运行",
)
async def health():
    return {"status": "ok", "service": "douyin-parser", "version": "2.0.0"}


# ── 前端页面 ────────────────────────────────────────

@app.get(
    "/",
    tags=["系统"],
    summary="前端工具页面",
    description="抖音解析工具的浏览器 UI 页面",
    include_in_schema=False,
)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>抖音解析工具</h1><p>页面文件缺失</p>")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── 启动入口 ──────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8899, reload=True)