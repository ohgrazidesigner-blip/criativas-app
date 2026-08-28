#!/usr/bin/env sh
set -eu
export APP_ENV="${APP_ENV:-development}"
export SESSION_SECRET="${SESSION_SECRET:-dev-only-session-secret-change-me}"
export CRIATIVAS_INITIAL_PASSWORD="${CRIATIVAS_INITIAL_PASSWORD:-Criativas2024!}"
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
