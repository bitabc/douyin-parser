# =============================================================
# Dockerfile — 抖音解析服务
# 基于 Python 3.11 轻量镜像，多阶段构建
# =============================================================

# ---- 构建阶段 ----
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ---- 运行阶段 ----
FROM python:3.11-slim

WORKDIR /app

# 从 builder 复制已安装的包
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制项目代码
COPY api.py parsers.py ./
COPY static/ ./static/

# 创建缓存目录
RUN mkdir -p /tmp/douyin_cache

# 端口
EXPOSE 8899

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=2 \
    CMD curl -sf http://localhost:8899/health || exit 1

# 启动
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8899"]