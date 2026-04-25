FROM python:3.14-slim-bookworm

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements from exporter and install to system python
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY main.py ./

ENV SLEEP_INTERVAL=21600
# 默认开启服务模式，但在 GH Actions 中会被 compose 或 env 覆盖为 true
ENV RUN_ONCE=false 

CMD ["python", "main.py"]
