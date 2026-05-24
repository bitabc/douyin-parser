"""
抖音解析服务 — 单元测试
"""

import pytest
import json
from parsers import (
    parse_douyin_url,
    ParseError,
    match_url,
    _extract_url,
    MOBILE_UAS,
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


# ── API 单元测试 ─────────────────────────────────────

@pytest.fixture
def client():
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


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


# ── 集成测试（需网络） ────────────────────────────────

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