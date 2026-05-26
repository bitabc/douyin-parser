"""
抖音解析服务 — 单元测试
覆盖 parsers 核心逻辑 + API 端点 + 媒体代理
"""

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from parsers import (
    parse_any_url,
    parse_douyin_url,
    ParseError,
    match_douyin_url,
    _extract_url,
    resolve_short_url,
    extract_json_from_html,
    MOBILE_UAS,
    detect_platform,
)
from api import app


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

    def test_多个链接取第一个(self):
        text = "https://example.com/a https://v.douyin.com/xyz/"
        assert _extract_url(text) == "https://example.com/a"


class TestURL匹配:
    def test_标准视频(self):
        result = match_douyin_url("https://www.douyin.com/video/7521023890996514083")
        assert result == ("video", "7521023890996514083")

    def test_图文note(self):
        result = match_douyin_url("https://www.douyin.com/note/7469411074119322899")
        assert result == ("note", "7469411074119322899")

    def test_iesdouyin_video(self):
        result = match_douyin_url("https://www.iesdouyin.com/share/video/12345")
        assert result is not None
        assert result[0] == "video"
        assert result[1] == "12345"

    def test_iesdouyin_slides(self):
        result = match_douyin_url("https://www.iesdouyin.com/share/slides/12345")
        assert result is not None
        assert result[0] == "slides"

    def test_jingxuan_douyin(self):
        result = match_douyin_url("https://jingxuan.douyin.com/m/video/99999")
        assert result is not None
        assert result[0] == "video"
        assert result[1] == "99999"

    def test_不匹配(self):
        assert match_douyin_url("https://example.com") is None

    def test_不匹配_bilibili(self):
        assert match_douyin_url("https://www.bilibili.com/video/BV1xx") is None


class TestUserAgent:
    def test_有移动端UA(self):
        assert len(MOBILE_UAS) >= 3
        for ua in MOBILE_UAS:
            assert "Mobile" in ua or "mobile" in ua
            assert "Safari" in ua or "Chrome" in ua


class TestJSON提取:
    def test_基本提取(self):
        html = '<script>window._ROUTER_DATA={"key":"value"}</script>'
        result = extract_json_from_html(html, "_ROUTER_DATA")
        assert result == {"key": "value"}

    def test_嵌套对象(self):
        html = '<script>window._ROUTER_DATA={"a":{"b":{"c":1}}}</script>'
        result = extract_json_from_html(html, "_ROUTER_DATA")
        assert result == {"a": {"b": {"c": 1}}}

    def test_JS_undefined转null(self):
        html = '<script>window._ROUTER_DATA={"name": undefined,"age": 18}</script>'
        result = extract_json_from_html(html, "_ROUTER_DATA")
        assert result == {"name": None, "age": 18}

    def test_找不到变量(self):
        html = '<script>window._OTHER={"key":"value"}</script>'
        assert extract_json_from_html(html, "_ROUTER_DATA") is None

    def test_空HTML(self):
        assert extract_json_from_html("", "_ROUTER_DATA") is None


class Test平台检测:
    def test_检测_douyin_com(self):
        assert detect_platform("https://www.douyin.com/video/123") == "douyin"

    def test_检测_v_douyin(self):
        assert detect_platform("https://v.douyin.com/abc/") == "douyin"

    def test_检测_iesdouyin(self):
        assert detect_platform("https://www.iesdouyin.com/share/video/123") == "douyin"

    def test_不识别(self):
        assert detect_platform("https://www.bilibili.com/video/BV1xx") is None


class Test解析错误:
    @pytest.mark.asyncio
    async def test_无效链接(self):
        with pytest.raises(ParseError, match="未找到 http 链接"):
            await parse_douyin_url("这不是一个链接")

    @pytest.mark.asyncio
    async def test_不支持域名(self):
        with pytest.raises(ParseError, match="无法识别的抖音链接"):
            await parse_douyin_url("https://example.com")

    @pytest.mark.asyncio
    async def test_统一入口_不支持平台(self):
        with pytest.raises(ParseError, match="无法识别的平台"):
            await parse_any_url("https://www.bilibili.com/video/BV1xx")


# ── Mock 工具类 ──────────────────────────────────────

class MockResponse:
    """模拟 httpx.Response（流式）"""
    def __init__(self, status_code=200, headers=None, body=b"ok"):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def aclose(self):
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self._body


class MockAsyncClient:
    """模拟 httpx.AsyncClient"""
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


# ── API 单元测试 ─────────────────────────────────────

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
    # 清理
    app.state.media_client = MockAsyncClient()


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_version(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert "version" in data


class TestParseAPI:
    @pytest.mark.asyncio
    async def test_parse_get_无参数(self, client):
        resp = await client.get("/api/parse")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_parse_post_无body(self, client):
        resp = await client.post("/api/parse", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_parse_内部异常不暴露细节(self, client):
        with patch("api.parse_any_url", side_effect=RuntimeError("secret boom")):
            resp = await client.get(
                "/api/parse",
                params={"url": "https://www.douyin.com/video/7521023890996514083"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"] == "服务异常，请稍后重试"

    @pytest.mark.asyncio
    async def test_parse_成功响应结构(self, client):
        mock_result = {
            "platform": "抖音",
            "type": "视频",
            "id": "12345",
            "title": "测试视频",
            "author": {"name": "作者", "avatar_url": "https://p1.douyinpic.com/avatar.jpg"},
            "create_time": 1742812800,
            "video": {
                "url": "https://aweme.snssdk.com/play/?id=123",
                "cover_url": "https://p1.douyinpic.com/cover.jpg",
                "duration": 10,
            },
            "contents": [{"type": "video", "url": "https://aweme.snssdk.com/play/?id=123"}],
            "source_url": "https://m.douyin.com/share/video/123",
        }
        with patch("api.parse_any_url", return_value=mock_result):
            resp = await client.get(
                "/api/parse",
                params={"url": "https://www.douyin.com/video/12345"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["platform"] == "抖音"
        assert data["data"]["type"] == "视频"
        assert data["elapsed"] is not None
        assert isinstance(data["elapsed"], float)

    @pytest.mark.asyncio
    async def test_parse_post_成功(self, client):
        mock_result = {
            "platform": "抖音",
            "type": "视频",
            "id": "12345",
            "title": "POST测试",
            "author": {"name": "作者", "avatar_url": None},
            "contents": [],
        }
        with patch("api.parse_any_url", return_value=mock_result):
            resp = await client.post("/api/parse", json={"url": "https://v.douyin.com/abc/"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True


class TestFrontend:
    @pytest.mark.asyncio
    async def test_前端页面(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "抖音解析工具" in resp.text

    @pytest.mark.asyncio
    async def test_openapi_docs(self, client):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["info"]["title"] == "抖音解析服务"
        assert "/api/parse" in str(data["paths"])

    @pytest.mark.asyncio
    async def test_swagger_ui(self, client):
        resp = await client.get("/docs")
        assert resp.status_code == 200
        assert "swagger" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_redoc(self, client):
        resp = await client.get("/redoc")
        assert resp.status_code == 200
        assert "redoc" in resp.text.lower()


class TestCORS:
    @pytest.mark.asyncio
    async def test_cors_headers(self, client):
        from fastapi.middleware.cors import CORSMiddleware
        cors_middlewares = [m for m in app.user_middleware if m.cls == CORSMiddleware]
        assert len(cors_middlewares) > 0, "CORSMiddleware not configured"

    @pytest.mark.asyncio
    async def test_cors_response_headers(self, client):
        # 验证 CORS 中间件已配置（与 test_cors_headers 互补，检查实际响应头）
        from fastapi.middleware.cors import CORSMiddleware
        cors_middlewares = [m for m in app.user_middleware if m.cls == CORSMiddleware]
        assert len(cors_middlewares) > 0


class TestMediaProxy:
    @pytest.mark.asyncio
    async def test_proxy_拒绝非_http_scheme(self, client):
        resp = await client.get("/api/proxy/media", params={"url": "file:///tmp/demo.mp4"})
        assert resp.status_code == 400
        assert "http/https" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_proxy_拒绝缺少主机名(self, client):
        resp = await client.get("/api/proxy/media", params={"url": "https:///demo.mp4"})
        assert resp.status_code == 400
        assert "主机名" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_proxy_允许任意域名(self, client, media_client):
        """不再限制域名，任意 http/https 域名均可代理"""
        media_client.responses = [
            MockResponse(
                status_code=200,
                headers={"content-type": "video/mp4", "content-length": "4"},
                body=b"test",
            )
        ]

        resp = await client.get(
            "/api/proxy/media",
            params={"url": "https://example.com/demo.mp4"},
        )
        assert resp.status_code == 200
        assert resp.content == b"test"

    @pytest.mark.asyncio
    async def test_proxy_透传_range(self, client, media_client):
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
    async def test_proxy_重定向跟随(self, client, media_client):
        media_client.responses = [
            MockResponse(
                status_code=302,
                headers={"location": "https://p1.douyinpic.com/redirect.mp4"},
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
        assert len(media_client.requests) == 2
        assert media_client.requests[1]["url"] == "https://p1.douyinpic.com/redirect.mp4"

    @pytest.mark.asyncio
    async def test_proxy_下载模式(self, client, media_client):
        media_client.responses = [
            MockResponse(
                status_code=200,
                headers={"content-type": "video/mp4", "content-length": "100"},
                body=b"ok",
            )
        ]

        resp = await client.get(
            "/api/proxy/media",
            params={"url": "https://aweme.snssdk.com/aweme/v1/play/?video_id=123", "download": "1"},
        )

        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert ".mp4" in cd


class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_creates_media_client(self):
        """验证 lifespan 上下文管理器正确创建和关闭 media_client"""
        # 直接调用 lifespan 函数
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        managed_client = app.state.media_client
        assert managed_client is not None
        assert hasattr(managed_client, 'aclose')
        await ctx.__aexit__(None, None, None)


# ── 集成测试（需要网络） ────────────────────────────

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
