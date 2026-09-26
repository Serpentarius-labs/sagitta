# syntax=docker/dockerfile:1
# Demo 应用镜像。构建与环境变量注入约定见 docs/config.md 第六章。
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖，利用 Docker 层缓存（依赖不变时不重装）
COPY requirements.txt .
RUN pip install -r requirements.txt

# 非 root 运行
RUN useradd -r -u 10001 appuser

# 只拷贝运行所需内容；.env / .venv 等由 .dockerignore 排除
COPY --chown=appuser sagitta/ ./sagitta/
COPY --chown=appuser app/ ./app/
COPY --chown=appuser conf/config.yaml ./conf/config.yaml
COPY --chown=appuser main.py ./main.py

# 构建版本：CI 中 --build-arg BUILD_COMMIT=$(git rev-parse --short HEAD)
ARG BUILD_COMMIT=""
ENV SG_BUILD_COMMIT=${BUILD_COMMIT}

USER appuser
EXPOSE 8000

# SG_MASTER_KEY / SG_DB_DEFAULT_URL / SG_REDIS_DEFAULT_URL 等
# 一律在运行时通过环境变量注入，镜像不含任何密钥。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
