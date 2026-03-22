#!/bin/bash
set -e

echo "==> Deploying to ipronto.net..."

ssh ubuntu@ipronto.net << 'EOF'
  set -e
  echo "==> Pulling latest code..."
  cd /home/ubuntu/home_finder/data_service
  git pull

  echo "==> Stopping web container..."
  docker-compose down

  echo "==> Building and starting web container..."
  docker compose up --build -d web
EOF

echo "==> Deploy complete."
