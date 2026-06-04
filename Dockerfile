FROM python:3.12-slim-bookworm

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy uv lock file and pyproject.toml
COPY uv.lock pyproject.toml ./

# Install dependencies using uv
RUN uv pip install --system --no-cache-dir -r uv.lock

# Copy all source code
COPY *.py ./

CMD ["python", "main.py"]
