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

### 方式一：Docker Compose（推荐）

```bash
# 克隆仓库
git clone https://github.com/bitabc/douyin-parser.git
cd douyin-parser

# 启动服务（后台运行）
docker compose up -d

# 查看日志
docker compose logs -f

# 停止服务
docker compose down

# 停止并删除数据卷
docker compose down -v
```

访问 **http://localhost:8899** 打开工具页面。

### 方式二：使用预构建镜像

从 GitHub Container Registry 拉取，无需本地构建：

```bash
docker run -d \
  --name douyin-parser \
  -p 8899:8899 \
  --restart unless-stopped \
  ghcr.io/bitabc/douyin-parser:latest
```

### 方式三：直接运行（开发用）

```bash
# 安装依赖
pip install -r requirements.txt

# 启动
python api.py
# 或
uvicorn api:app --host 0.0.0.0 --port 8899 --reload
```

> `--reload` 仅开发模式使用，文件修改后自动重载。

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

### 3️⃣ 健康检查

```
GET /health
```

```json
{"status": "ok", "service": "douyin-parser", "version": "2.1.0"}
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

## 🐳 Docker 部署指南

### 快速部署

#### 使用 Docker Compose（单机最快）

```bash
# 1. 克隆仓库
git clone https://github.com/bitabc/douyin-parser.git
cd douyin-parser

# 2. 启动服务（前台查看日志）
docker compose up

# 3. 确认无报错后，切后台运行
#    Ctrl+C 停止，然后：
docker compose up -d

# 4. 验证服务状态
curl http://localhost:8899/health
```

#### 使用 Docker Run（跳过克隆）

```bash
docker run -d \
  --name douyin-parser \
  -p 8899:8899 \
  --restart unless-stopped \
  ghcr.io/bitabc/douyin-parser:latest
```

### 配置详解

#### 环境变量

可通过 `environment` 或 `.env` 文件自定义：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `TZ` | `Asia/Shanghai` | 容器时区 |
| `WORKERS` | `1` | Uvicorn worker 数量（多核服务器可调高） |
| `LOG_LEVEL` | `info` | 日志级别：`debug`, `info`, `warning`, `error` |

**使用方式：**

```yaml
# docker-compose.yml 中添加
services:
  douyin-parser:
    # ...
    environment:
      - TZ=Asia/Shanghai
      - WORKERS=2
      - LOG_LEVEL=info
```

或创建 `.env` 文件：

```bash
# .env
TZ=Asia/Shanghai
WORKERS=2
LOG_LEVEL=info
```

#### 端口映射

默认映射 `8899:8899`，如需改端口：

```bash
# 映射到宿主机 8080 端口
docker run -d -p 8080:8899 ghcr.io/bitabc/douyin-parser:latest
```

```yaml
# docker-compose.yml
ports:
  - "8080:8899"
```

#### 数据卷

媒体代理会缓存临时数据，建议持久化：

```yaml
volumes:
  - douyin_cache:/tmp/douyin_cache
```

### 生产部署

#### Docker Compose（生产推荐）

```yaml
version: "3.8"

services:
  douyin-parser:
    image: ghcr.io/bitabc/douyin-parser:latest
    container_name: douyin-parser
    ports:
      - "8899:8899"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:8899/health"]
      interval: 30s
      timeout: 5s
      retries: 2
      start_period: 10s
    environment:
      - TZ=Asia/Shanghai
    volumes:
      - douyin_cache:/tmp/douyin_cache
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1"

volumes:
  douyin_cache:
```

保存为 `docker-compose.prod.yml`，然后：

```bash
docker compose -f docker-compose.prod.yml up -d
```

#### 使用预构建镜像（无需本地构建）

每次推送到 `main` 分支，GitHub Actions 会自动构建并推送镜像到 `ghcr.io`。

```bash
# 拉取最新镜像
docker pull ghcr.io/bitabc/douyin-parser:latest

# 启动
docker run -d \
  --name douyin-parser \
  -p 8899:8899 \
  --restart unless-stopped \
  ghcr.io/bitabc/douyin-parser:latest
```

#### 指定版本

镜像标签规则：

| 标签 | 示例 | 说明 |
|------|------|------|
| `latest` | `ghcr.io/bitabc/douyin-parser:latest` | 最新稳定版本 |
| `sha-xxxxx` | `ghcr.io/bitabc/douyin-parser:main-abc1234` | 具体 commit SHA（短格式） |
| `YYYYMMDD` | `ghcr.io/bitabc/douyin-parser:20250321` | 构建日期 |

```bash
# 使用特定版本
docker pull ghcr.io/bitabc/douyin-parser:20250321
```

#### 灰度发布 / 回滚

```bash
# 回滚到指定版本
docker run -d \
  --name douyin-parser \
  -p 8899:8899 \
  ghcr.io/bitabc/douyin-parser:main-abc1234
```

### 反向代理

#### Nginx

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 10m;
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8899;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;             # 视频流式传输必须关闭
        proxy_request_buffering off;     # 同上
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

> **注意**: 代理视频流时务必关闭 `proxy_buffering` 和 `proxy_request_buffering`，否则视频播放会卡顿。

#### Caddy（自动 HTTPS）

```caddyfile
your-domain.com {
    reverse_proxy localhost:8899 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
    }
}
```

#### 宝塔面板

在宝塔面板中：
1. 网站 → 添加站点 → 填入域名
2. 设置 → 反向代理 → 添加
   - 代理名称：`douyin-parser`
   - 目标 URL：`http://127.0.0.1:8899`
3. 配置文件 → 加入以下关键配置：

```nginx
proxy_buffering off;
proxy_request_buffering off;
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
```

### Docker Compose 参考

#### 基础命令

```bash
# 构建并启动
docker compose up -d

# 仅构建（不启动）
docker compose build

# 查看日志（持续跟踪）
docker compose logs -f

# 查看日志（最近 100 行）
docker compose logs --tail=100

# 重启
docker compose restart

# 停止
docker compose stop

# 停止并删除容器
docker compose down

# 停止、删容器、删数据卷
docker compose down -v

# 重新构建镜像并启动
docker compose up -d --build
```

#### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 端口被占用 | `8899` 已被其他进程占用 | 修改 `docker-compose.yml` 中左侧端口映射，或 `lsof -i :8899` 查看占用进程 |
| 视频无法播放 | 反向代理未关闭 `proxy_buffering` | 参考上方 Nginx 配置，加上 `proxy_buffering off` |
| 容器反复重启 | 健康检查失败 | `docker logs douyin-parser` 查看错误日志 |
| 解析无响应 | 抖音 CDN 检测到异常请求 | 服务会自动重试 3 次并轮换 UA，一般等待几秒即可 |

### CI/CD 自动构建

推送到 `main` 分支后，GitHub Actions 自动：

1. 检出代码
2. 设置 Docker Buildx
3. 登录 ghcr.io（使用 `GITHUB_TOKEN`）
4. 构建多标签镜像（`latest`, `sha-xxxxx`, `YYYYMMDD`）
5. 推送到 `ghcr.io/bitabc/douyin-parser`
6. 利用 GitHub Actions Cache 加速后续构建

> 首次使用需要到 GitHub 仓库 Settings → Actions → General 中，确保 **Workflow permissions** 设为 **Read and write permissions**。

#### 手动触发构建

到 GitHub 仓库的 Actions 页面，选择 **🐳 Docker Build & Push** 工作流，点击 **Run workflow** 即可。

## 🏗 项目结构

```
douyin-parser/
├── api.py                 # FastAPI 服务 + 媒体代理
├── parsers.py             # 核心解析引擎（_ROUTER_DATA 提取）
├── static/
│   └── index.html         # 前端工具页面
├── tests/
│   └── test_api.py        # 单元测试（22 个）
├── Dockerfile             # 多阶段 Docker 构建
├── docker-compose.yml     # Docker Compose 配置（开发）
├── docker-compose.prod.yml# Docker Compose 配置（生产参考）
├── requirements.txt       # Python 依赖
├── pyproject.toml         # 项目元数据
└── .github/workflows/
    └── docker-build.yml   # GitHub Actions CI/CD
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

## 📄 License

MIT