FROM python:3.12-slim

WORKDIR /app
ENV PIP_PROGRESS_BAR=off
RUN pip install --no-cache-dir "pymongo[srv]==4.7.2"
COPY flip_finder /app/flip_finder

# The scheduler supplies --write and, only after SMTP has been configured,
# --send-alerts.  The image itself has no credentials or automatic network job.
CMD ["python", "-m", "flip_finder.daily"]
