# syntax=docker/dockerfile:1
# ---- frontend build -------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- runtime (API and worker share this image) ------------------------------
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY --from=web /web/dist /app/frontend/dist
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin cinevision && mkdir -p /data && chown cinevision /data
USER cinevision
WORKDIR /app/backend
ENV CINEVISION_DATA_DIR=/data CINEVISION_FRONTEND_DIST=/app/frontend/dist CINEVISION_HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "-m", "cinevision.app"]
