"""
抖音解析服务 — 单元测试
"""

from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from parsers import (
    parse_douyin_url,
    ParseError,
    match_url,
    _extract_url,
    MOBILE_UAS,
)
from api import app, startup_event, shutdown_event
from parsers import _build_result, AuthorInfo, Avatar, PlayAddr, Cover, VideoData, VideoInfo


# ── parsers 单元测试 ─────────────────────────────────

class TestURL提取:
    def test_提取标准链接(self):
        text = "https://www.douyin.com/video/7521023890996514083"
        assert _extract_url(text) == text

    def test_提取短链接(self):
        text = "来康康 https://v.douyin.com/mXosMBsOHF0/ 复制链接"
        assert _extract_url(text) == "https://v.douyin.com/mXosMBsOHF0"

    def test_提取分享文本(self):
        text = '1.79 :3pm OKj:/ ... https://v.douyin.com/abc123/ 复制'
        assert _extract_url(text) == "https://v.douyin.com/abc123"

    def test_无链接(self):
        assert _extract_url("纯文本没有链接") is None

    def test_空字符串(self):
        assert _extract_url("") is None


class TestURL匹配:
    def test_标准视频(self):
        match = match_url("https://www.douyin.com/video/7521023890996514083")
        assert match == ("video", "7521023890996514083")

    def test_图文note(self):
        match = match_url("https://www.douyin.com/note/7469411074119322899")
        assert match == ("note", "7469411074119322899")

    def test_iesdouyin(self):
        match = match_url("https://www.iesdouyin.com/share/video/12345")
        assert match is not None
        assert match[1] == "12345"

    def test_不匹配(self):
        assert match_url("https://example.com") is None


class TestUserAgent:
    def test_有移动端UA(self):
        assert len(MOBILE_UAS) >= 3
        for ua in MOBILE_UAS:
            assert "Mobile" in ua or "mobile" in ua
            assert "Safari" in ua or "Chrome" in ua


class Test解析错误:
    @pytest.mark.asyncio
    async def test_无效链接(self):
        with pytest.raises(ParseError, match="未找到 http 链接"):
            await parse_douyin_url("这不是一个链接")

    @pytest.mark.asyncio
    async def test_不支持域名(self):
        with pytest.raises(ParseError, match="无法识别的抖音链接"):
            await parse_douyin_url("https://example.com")


def test_build_result_包含作品_id():
    video_data = VideoData(
        create_time=1742812800,
        author=AuthorInfo(nickname="作者", avatar_thumb=Avatar(url_list=["https://p1.douyinpic.com/avatar.jpg"])),
        desc="测试标题",
        video=VideoInfo(
            play_addr=PlayAddr(url_list=["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=123"]),
            cover=Cover(url_list=["https://p1.douyinpic.com/cover.jpg"]),
            duration=9000,
        ),
    )

    result = _build_result(
        video_data,
        source_url="https://m.douyin.com/share/video/123",
        item_id="7521023890996514083",
    )

    assert result["id"] == "7521023890996514083"
    assert result["video"]["url"].endswith("video_id=123")


# ── API 单元测试 ─────────────────────────────────────


class MockResponse:
    def __init__(self, status_code=200, headers=None, body=b"ok"):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def aclose(self):
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self._body


class MockAsyncClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        self.closed = False

    def build_request(self, method, url, headers=None):
        request = {"method": method, "url": url, "headers": headers or {}}
        self.requests.append(request)
        return request

    async def send(self, request, stream=False):
        if not self.responses:
            raise AssertionError("No mock response configured")
        return self.responses.pop(0)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def client():
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def media_client():
    client = MockAsyncClient()
    app.state.media_client = client
    yield client
    app.state.media_client = MockAsyncClient()


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "douyin-parser"


@pytest.mark.asyncio
async def test_parse_无参数(client):
    resp = await client.get("/api/parse")
    assert resp.status_code == 422  # FastAPI validation error


@pytest.mark.asyncio
async def test_parse_无效链接(client):
    resp = await client.get("/api/parse?url=无效输入")
    data = resp.json()
    assert data["success"] is False
    assert "error" in data


@pytest.mark.asyncio
async def test_parse_内部异常不暴露细节(client):
    with patch("api.parse_douyin_url", side_effect=RuntimeError("secret boom")):
        resp = await client.get(
            "/api/parse",
            params={"url": "https://www.douyin.com/video/7521023890996514083"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["error"] == "服务异常，请稍后重试"


@pytest.mark.asyncio
async def test_parse_post_无body(client):
    resp = await client.post("/api/parse", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_前端页面(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "抖音解析工具" in resp.text


@pytest.mark.asyncio
async def test_openapi_docs(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["info"]["title"] == "抖音解析服务"
    assert "/api/parse" in str(data["paths"])


@pytest.mark.asyncio
async def test_swagger_ui(client):
    resp = await client.get("/docs")
    assert resp.status_code == 200
    assert "swagger" in resp.text.lower()


@pytest.mark.asyncio
async def test_redoc(client):
    resp = await client.get("/redoc")
    assert resp.status_code == 200
    assert "redoc" in resp.text.lower()


@pytest.mark.asyncio
async def test_cors_headers(client):
    """中间件已配置 CORS"""
    from fastapi.middleware.cors import CORSMiddleware
    cors_middlewares = [m for m in app.user_middleware if m.cls == CORSMiddleware]
    assert len(cors_middlewares) > 0, "CORSMiddleware not configured"


@pytest.mark.asyncio
async def test_proxy_media_拒绝非_http_scheme(client):
    resp = await client.get("/api/proxy/media", params={"url": "file:///tmp/demo.mp4"})
    assert resp.status_code == 400
    assert "http/https" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_proxy_media_拒绝缺少主机名(client):
    resp = await client.get("/api/proxy/media", params={"url": "https:///demo.mp4"})
    assert resp.status_code == 400
    assert "主机名" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_proxy_media_允许任意_http_host(client, media_client):
    media_client.responses = [
        MockResponse(
            status_code=200,
            headers={"content-type": "video/mp4", "content-length": "2"},
            body=b"ok",
        )
    ]

    resp = await client.get(
        "/api/proxy/media",
        params={"url": "https://example.com/demo.mp4"},
    )

    assert resp.status_code == 200
    assert media_client.requests[0]["url"] == "https://example.com/demo.mp4"


@pytest.mark.asyncio
async def test_proxy_media_允许白名单域名并透传_range(client, media_client):
    media_client.responses = [
        MockResponse(
            status_code=200,
            headers={"content-type": "video/mp4", "content-length": "2"},
            body=b"ok",
        )
    ]

    resp = await client.get(
        "/api/proxy/media",
        params={"url": "https://aweme.snssdk.com/aweme/v1/play/?video_id=123"},
        headers={"Range": "bytes=0-1"},
    )

    assert resp.status_code == 200
    assert resp.content == b"ok"
    sent_headers = media_client.requests[0]["headers"]
    assert sent_headers["Range"] == "bytes=0-1"


@pytest.mark.asyncio
async def test_proxy_media_允许重定向到任意_http_host(client, media_client):
    media_client.responses = [
        MockResponse(
            status_code=302,
            headers={"location": "https://example.com/evil.mp4"},
        ),
        MockResponse(
            status_code=200,
            headers={"content-type": "video/mp4"},
            body=b"ok",
        ),
    ]

    resp = await client.get(
        "/api/proxy/media",
        params={"url": "https://aweme.snssdk.com/aweme/v1/play/?video_id=123"},
    )

    assert resp.status_code == 200
    assert media_client.requests[1]["url"] == "https://example.com/evil.mp4"


@pytest.mark.asyncio
async def test_media_client_fixture_会清理状态(media_client):
    media_client.responses = [MockResponse(status_code=200)]
    assert app.state.media_client is media_client
    assert media_client.closed is False


@pytest.mark.asyncio
async def test_startup_shutdown_管理共享_media_client():
    original_client = getattr(app.state, "media_client", None)
    try:
        await startup_event()
        managed_client = app.state.media_client
        assert managed_client is not None
        assert managed_client.closed is False

        await shutdown_event()
        assert managed_client.closed is True
    finally:
        if original_client is not None:
            app.state.media_client = original_client


@pytest.mark.integration
@pytest.mark.asyncio
async def test_解析真实短链接(client):
    """集成测试，需要网络连接"""
    url = "https://v.douyin.com/mXosMBsOHF0/"
    resp = await client.get(f"/api/parse?url={url}")
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["platform"] == "抖音"
    assert data["data"]["author"]["name"]
    assert len(data["data"]["video"]["url"]) > 0
    assert data["elapsed"] < 10  # 不应超过10秒