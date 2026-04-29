FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install noVNC dependencies
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    x11vnc \
    xvfb \
    fluxbox \
    novnc \
    websockify \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Cloudflare tunnel
RUN curl -sL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy app
COPY main.py .

# Create downloads directory
RUN mkdir -p /app/zomato_downloads

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
