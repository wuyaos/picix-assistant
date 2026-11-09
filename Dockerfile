# picix-assistant — 多阶段构建
# 阶段1: 构建前端 (Vue3 + Vite) → dist
FROM node:22-alpine AS fe
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm install --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# 阶段2: Python 运行时（CloakBrowser 隐身 Chromium 传输层）
FROM python:3.12-slim
WORKDIR /app/backend

# 应用路径与运行参数（compose/本地可覆盖）
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    FRONTEND_DIST=/app/static \
    PORT=8000 \
    PICIX_HEADLESS=1 \
    CLOAKBROWSER_SUPPRESS_FONT_WARNING=1 \
    CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser
# Chromium 运行时共享库 + xvfb(headed 兜底) + CJK 字体（防验证页乱码/字体指纹）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libasound2 libpango-1.0-0 libcairo2 libx11-6 libxcb1 \
        libxext6 libxi6 libxtst6 libglib2.0-0 libdbus-1-3 libexpat1 \
        fonts-liberation fonts-noto-cjk \
        xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Chromium 二进制不再打进镜像：由 entrypoint 按需就绪到持久卷
# （缓存命中 -> 种子包解包 -> 在线下载回退，见 docker/picix-entrypoint.sh）
COPY docker/picix-entrypoint.sh /usr/local/bin/picix-entrypoint.sh
RUN chmod +x /usr/local/bin/picix-entrypoint.sh
COPY backend/ /app/backend/
COPY --from=fe /fe/dist /app/static
ENTRYPOINT ["/usr/local/bin/picix-entrypoint.sh"]

VOLUME /data

