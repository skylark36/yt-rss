FROM python:3.12-slim-bookworm

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Add missing requests dependency
RUN pip install --no-cache-dir requests

# Copy all source code
COPY *.py ./

CMD ["python", "main.py"]
