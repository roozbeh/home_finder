FROM python:3.12-slim

WORKDIR /app

# Install Flask and dependencies — no browser/Selenium needed for the web server
RUN pip install --no-cache-dir flask==3.0.3 "pymongo[srv]==4.7.2" gunicorn==22.0.0 anthropic==0.40.0

# Copy the web application files (templates, app.py, etc.)
COPY web/ .

# Copy the agentic package so `import agentic` resolves at runtime
COPY agentic/ ./agentic/

# Expose favicon under /static/
RUN mkdir -p static && cp favicon.png static/favicon.png

EXPOSE 5000

# Use gunicorn for a proper production-grade server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "--worker-class", "gthread", "--threads", "4", "app:app"]
