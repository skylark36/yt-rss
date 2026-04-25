# Stage 1: Export requirements
FROM python:3.12-slim AS exporter

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
# 导出 requirements.txt
RUN uv export --format requirements-txt > requirements.txt

# Stage 2: Final runtime stage
FROM python:3.12-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements from exporter and install to system python
COPY --from=exporter /app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY main.py ./

ENV SLEEP_INTERVAL=21600
# 默认开启服务模式，但在 GH Actions 中会被 compose 或 env 覆盖为 true
ENV RUN_ONCE=false 

CMD ["python", "main.py"]
