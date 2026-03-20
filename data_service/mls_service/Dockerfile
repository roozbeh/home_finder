# ── App container ─────────────────────────────────────────────────────────────
# Uses Chromium from Debian's repo — available on both amd64 and arm64,
# so this builds on Apple Silicon (M1/M2/M3) and Linux servers alike.
FROM python:3.12-slim

# ── System deps + Chromium ────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        cron \
        ca-certificates \
        fonts-liberation \
        fonts-noto-color-emoji \
        chromium \
        chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application scripts ───────────────────────────────────────────────────────
COPY get_cookies.py .
COPY search_and_store.py .
COPY fetch_details.py .
COPY status_server.py .
COPY entrypoint.sh .
RUN chmod +x /app/entrypoint.sh

# ── Runtime env ───────────────────────────────────────────────────────────────
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_BIN=/usr/bin/chromedriver
ENV HEADLESS=true
ENV MONGO_URI=mongodb://mongo:27017

EXPOSE 8081

# ── Default command ───────────────────────────────────────────────────────────
CMD ["/app/entrypoint.sh"]
