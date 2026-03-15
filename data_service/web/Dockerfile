FROM python:3.12-slim

WORKDIR /app

# Install Flask and pymongo — no browser/Selenium needed for the web server
RUN pip install --no-cache-dir flask==3.0.3 pymongo==4.7.2 gunicorn==22.0.0

# Copy the web application
COPY . .

EXPOSE 5000

# Use gunicorn for a proper production-grade server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
