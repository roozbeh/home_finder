FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_PROGRESS_BAR=off

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY categorize_photos.py .
ENTRYPOINT ["python", "/app/categorize_photos.py"]
