# ── MLS worker container ──────────────────────────────────────────────────────
# This image deliberately has no browser.  It reuses the authorized MLS session
# stored in MongoDB and runs request-based imports/backfills on AWS.
FROM python:3.12-slim

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
# Docker 18.09 on the AWS host applies an old seccomp profile during builds
# that blocks pip's progress-rendering thread.  Plain output is compatible.
ENV PIP_PROGRESS_BAR=off
COPY requirements.worker.txt .
RUN pip install --no-cache-dir -r requirements.worker.txt

# ── Application scripts ───────────────────────────────────────────────────────
COPY mls_session.py .
COPY search_and_store.py .
COPY fetch_details.py .
COPY repair_galleries.py .
COPY gallery_progress.py .
COPY entrypoint.worker.sh .
RUN chmod +x /app/entrypoint.worker.sh

# ── Runtime env ───────────────────────────────────────────────────────────────
ENV MONGO_URI=mongodb://mongo:27017

# ── Default command ───────────────────────────────────────────────────────────
CMD ["/app/entrypoint.worker.sh"]
