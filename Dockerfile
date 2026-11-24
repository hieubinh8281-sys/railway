FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV NO_BROWSER=1

# Cài đặt FFmpeg, Node.js, npm, git và bộ Font Quốc tế / Tiếng Việt / CJK cho Railway
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    npm \
    git \
    curl \
    ca-certificates \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -U -r requirements.txt

# Cài đặt PO Token server (bgutil-ytdlp-pot-provider) chạy ẩn danh không cần tài khoản YouTube
RUN git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server \
    && npm ci \
    && npx tsc

COPY . /app

EXPOSE 5000

CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js --port 4416 & sleep 2 && python 1.py"]
