#!/usr/bin/env bash
# Executar NO SERVIDOR (Ubuntu 24.04), como root.
# Não mexe em finmas, gerador_sequencial nem c-vps-agent.
set -euo pipefail

APP_DIR="${APP_DIR:-/root/trusicas}"
REPO_URL="https://github.com/korgloriws/trusicas.git"
HOST_PORT="${HOST_PORT:-5090}"

echo "==> Pasta do app: ${APP_DIR}"
mkdir -p "${APP_DIR}"
cd "${APP_DIR}"

if [ ! -d .git ]; then
  git clone "${REPO_URL}" .
else
  git pull origin main || git pull origin master
fi

echo "==> Preparando dados persistentes"
mkdir -p data output

if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "ATENÇÃO: edite ${APP_DIR}/.env antes de usar em produção:"
  echo "  OPENROUTER_API_KEY, TRUSICAS_ADMIN_PASSWORD, TRUSICAS_SECRET_KEY"
  echo ""
fi

echo "==> Subindo container (porta ${HOST_PORT})"
docker-compose build
docker-compose down 2>/dev/null || true
docker-compose up -d
docker builder prune -f

echo ""
echo "Pronto. Acesse: http://SEU_IP:${HOST_PORT}"
echo "Exemplo: http://31.97.167.75:${HOST_PORT}"
echo ""
echo "Atualizar depois (mesmo fluxo do finmas):"
echo "  cd ${APP_DIR}"
echo "  git pull"
echo "  docker-compose build"
echo "  docker-compose down"
echo "  docker-compose up -d"
echo "  docker builder prune -f"
