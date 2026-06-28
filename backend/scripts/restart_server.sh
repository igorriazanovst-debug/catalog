#!/usr/bin/env bash
#
# Перезапуск API-сервера (uvicorn) с подхватом свежего кода.
#
# Зачем: код и mount статики подхватываются только при старте процесса, поэтому
# после git pull сервер нужно перезапустить. Самая частая ловушка — новый uvicorn
# не может занять порт, потому что старый ещё жив; этот скрипт сначала гарантированно
# гасит старый процесс, проверяет, что порт свободен, и только потом стартует новый.
#
# Запуск (из любого каталога):
#   bash /opt/catalog/backend/scripts/restart_server.sh
# Параметры через env: HOST (0.0.0.0), PORT (8001), LOG (<repo>/uvicorn.log).
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8001}"

# Каталоги относительно расположения скрипта: <repo>/backend/scripts/restart_server.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="$(dirname "$BACKEND_DIR")"
LOG="${LOG:-$REPO_DIR/uvicorn.log}"
PATTERN="uvicorn app.main:app"

echo "repo:    $REPO_DIR"
echo "backend: $BACKEND_DIR"
echo "адрес:   http://$HOST:$PORT  (UI: /app/)"
echo "лог:     $LOG"

# 1) Гасим старые процессы uvicorn нашего приложения.
if pgrep -f "$PATTERN" >/dev/null 2>&1; then
  echo "Останавливаю старый uvicorn…"
  pkill -f "$PATTERN" || true
  for _ in $(seq 1 10); do
    pgrep -f "$PATTERN" >/dev/null 2>&1 || break
    sleep 0.5
  done
  if pgrep -f "$PATTERN" >/dev/null 2>&1; then
    echo "Не завершился штатно — kill -9"
    pkill -9 -f "$PATTERN" || true
    sleep 1
  fi
else
  echo "Старых процессов uvicorn нет."
fi

# 2) Проверяем, что порт свободен.
if command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :$PORT )" 2>/dev/null | grep -q ":$PORT"; then
  echo "ВНИМАНИЕ: порт $PORT всё ещё занят. Кто держит:"
  ss -ltnp "( sport = :$PORT )" 2>/dev/null || true
  echo "Освободите порт и запустите скрипт снова."
  exit 1
fi

# 3) Активируем venv и стартуем заново в фоне.
cd "$BACKEND_DIR"
# shellcheck disable=SC1091
source venv/bin/activate
echo "Старт uvicorn…"
nohup uvicorn app.main:app --host "$HOST" --port "$PORT" > "$LOG" 2>&1 &
NEW_PID=$!
sleep 2

if ! kill -0 "$NEW_PID" 2>/dev/null; then
  echo "ОШИБКА: uvicorn не стартовал. Хвост лога:"
  tail -n 30 "$LOG" || true
  exit 1
fi

echo "uvicorn запущен, pid=$NEW_PID"
grep -i "SPA" "$LOG" || echo "(строка про SPA ещё не появилась — это нормально на старте)"
echo "Готово. Открывайте http://$HOST:$PORT/app/"
