#!/usr/bin/env bash
# Atualizar trusicas no VPS (mesmo fluxo do finmas).
set -euo pipefail

cd "$(dirname "$0")"

git pull
docker-compose build
docker-compose down
docker-compose up -d
docker builder prune -f

echo ""
echo "Trusicas no ar: http://$(hostname -I 2>/dev/null | awk '{print $1}'):5090"
