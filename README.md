# 🎬 抖音解析服务

解析抖音分享链接，提取**无水印视频** / 图文 / 图集。

## ✨ 功能特性

- **智能输入** — 支持短链接、标准链接、整段分享文本
- **无水印直链** — `playwm → play` 转换，自动去水印
- **图集解析** — 支持 slides / note，含动态图
- **媒体代理** — 解决 CDN Referer 检查 + CORS 跨域拦截
- **流式播放** — 浏览器直接播放，支持进度条拖动
- **一键下载** — 点击按钮下载 MP4
- **可视化页面** — 美观的深色主题前端工具
- **OpenAPI 文档** — 自动生成 Swagger / ReDoc 文档

## 🚀 快速启动

### 方式一：Docker（推荐）

```bash
# 启动服务
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

### 方式二：直接运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动
python api.py
# 或
uvicorn api:app --host 0.0.0.0 --port 8899
```

访问 **http://localhost:8899** 打开工具页面。

## 📖 API 文档

服务启动后，Swagger 文档自动可用：

- Swagger UI: [http://localhost:8899/docs](http://localhost:8899/docs)
- ReDoc: [http://localhost:8899/redoc](http://localhost:8899/redoc)

### 1️⃣ 解析接口

解析抖音链接，返回无水印视频 / 图文信息。

```
GET /api/parse?url={抖音链接}
POST /api/parse  {"url": "..."}
```

**示例：**

```bash
# GET 方式
curl "http://localhost:8899/api/parse?url=https://v.douyin.com/mXosMBsOHF0/"

# POST 方式（支持整段分享文本）
curl -X POST http://localhost:8899/api/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "1.79 :3pm OKj:/ ... https://v.douyin.com/mXosMBsOHF0/ 复制此链接"}'
```

**成功响应：**

```json
{
  "success": true,
  "data": {
    "platform": "抖音",
    "type": "视频",
    "title": "我心里的虞姬不是一个只会流泪的弱女子#虞姬",
    "author": {
      "name": "月野喵🌙",
      "avatar_url": "https://p11.douyinpic.com/aweme/100x100/..."
    },
    "create_time": 1742812800,
    "video": {
      "url": "https://aweme.snssdk.com/aweme/v1/play/?video_id=...",
      "cover_url": "https://p26-sign.douyinpic.com/tos-cn-p-0015/...",
      "duration": 9
    },
    "contents": [
      {"type": "video", "url": "https://aweme.snssdk.com/..."}
    ],
    "source_url": "https://m.douyin.com/share/video/..."
  },
  "elapsed": 1.357
}
```

**失败响应：**

```json
{
  "success": false,
  "error": "输入中未找到 http 链接，请提供正确的抖音分享链接",
  "elapsed": 0.002
}
```

### 2️⃣ 媒体代理

解决抖音 CDN 的 Referer 检查和浏览器跨域拦截。

```
GET /api/proxy/media?url={媒体直链}
```

**示例：**

```bash
# 播放视频（浏览器直接打开）
curl "http://localhost:8899/api/proxy/media?url=https%3A%2F%2Faweme.snssdk.com%2F..."

# 下载视频
curl -o video.mp4 "http://localhost:8899/api/proxy/media?url=...&download=1"
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `url` | string | **必填**。媒体文件直链（来自解析接口的 `video.url` 或 `contents[].url`） |
| `download` | string | 可选。设为 `1` 触发浏览器下载 |

> **说明**: 媒体代理仅接受抖音解析结果中预期出现的媒体地址，不支持代理任意第三方 URL。

### 3️⃣ 健康检查

```
GET /health
```

```json
{"status": "ok", "service": "douyin-parser", "version": "2.0.0"}
```

## 🧪 本地测试

```bash
# 解析短链接
curl "http://localhost:8899/api/parse?url=https://v.douyin.com/mXosMBsOHF0/"

# 解析标准视频
curl "http://localhost:8899/api/parse?url=https://www.douyin.com/video/7521023890996514083"

# 下载视频
curl -o douyin_video.mp4 \
  "http://localhost:8899/api/proxy/media?url=https%3A%2F%2Faweme.snssdk.com%2Faweme%2Fv1%2Fplay%2F%3Fvideo_id%3D...&download=1"
```

## 🏗 项目结构

```
douyin-parser/
├── api.py              # FastAPI 服务 + 媒体代理
├── parsers.py          # 核心解析引擎（_ROUTER_DATA 提取）
├── static/
│   └── index.html      # 前端工具页面
├── Dockerfile          # Docker 构建文件
├── docker-compose.yml  # Docker Compose 配置
├── requirements.txt    # Python 依赖
└── .gitignore          # Git 忽略规则
```

## 🔧 技术原理

```
输入链接
  ↓
URL 提取（支持整段分享文本）
  ↓
短链接？ → 302 重定向追踪 → 真实 URL
  ↓
请求 m.douyin.com 移动端页面
  ↓
正则提取 window._ROUTER_DATA JSON
  ↓
msgspec 反序列化
  ↓
playwm → play 去除水印
  ↓
返回结构化结果
```

**关键技巧：**
- 抖音移动端页面是 SSR 渲染，`window._ROUTER_DATA` 中内嵌完整视频/图文数据
- `msgspec` 比标准 `json` + `dataclass` 快 10-100 倍
- 视频地址 `playwm` → `play` 字符串替换即可去水印

## 📦 部署

### Docker

```bash
docker compose up -d
```

### 反向代理（Nginx）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8899;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;  # 流式传输必须
        proxy_request_buffering off;
        proxy_http_version 1.1;
    }
}
```

> **注意**: 代理视频流时务必关闭 `proxy_buffering`，否则视频播放会卡顿。