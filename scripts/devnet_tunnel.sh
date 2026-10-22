#!/usr/bin/env bash
#
# scripts/devnet_tunnel.sh — Da conectividad TLS a la UNO Q por USB (ADB).
# SOLO PARA PRUEBAS: la placa no tiene ruta a internet, así que se túnela el
# endpoint de devnet a través del PC anfitrión.
#
#   PC:   127.0.0.1:8443  --(proxy splice)-->  api.devnet.solana.com:443
#   Placa: adb reverse tcp:443 tcp:8443  =>  127.0.0.1:443 de la placa llega al PC
#   Placa: /etc/hosts  =>  127.0.0.1 api.devnet.solana.com
#
# TLS/SNI no se toca: el certificado valida normal (solo se copian bytes).
# Al salir (Ctrl-C) se restaura /etc/hosts y se corta el reverse.
#
# Uso:  bash scripts/devnet_tunnel.sh [--host api.devnet.solana.com] [--port 8443]
#
set -euo pipefail

SERIAL="${SERIAL:-2480682956}"
HOST="api.devnet.solana.com"
PORT=8443
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "Argumento desconocido: $1" >&2; exit 2 ;;
  esac
done

ADB=(adb -s "$SERIAL")
HOSTS_BAK="/tmp/hosts.vending.bak"

cleanup() {
  echo ""
  echo "==> Restaurando /etc/hosts y cerrando túnel"
  "${ADB[@]}" shell "sudo sh -c 'if [ -f $HOSTS_BAK ]; then cp $HOSTS_BAK /etc/hosts; fi'" || true
  "${ADB[@]}" reverse --remove-all || true
  [ -n "${PROXY_PID:-}" ] && kill "$PROXY_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo "==> Proxy local $PORT -> $HOST:443"
python3 "$PROJECT_DIR/scripts/tcp_splice_proxy.py" --listen "$PORT" --dest "$HOST:443" &
PROXY_PID=$!
sleep 1

echo "==> adb reverse tcp:443 tcp:$PORT"
"${ADB[@]}" reverse tcp:443 "tcp:$PORT"

echo "==> /etc/hosts en la placa: 127.0.0.1 $HOST"
"${ADB[@]}" shell "sudo sh -c '
  [ -f /etc/hosts ] && cp /etc/hosts $HOSTS_BAK
  sed -i \"/[[:space:]]$HOST\$/d\" /etc/hosts
  echo \"127.0.0.1 $HOST\" >> /etc/hosts
'"

echo "==> Comprobando desde la placa…"
"${ADB[@]}" shell "curl -s -o /dev/null -w 'HTTP %{http_code}\n' --max-time 10 https://$HOST" || true

echo ""
echo "Túnel activo. La app de la placa ya puede hablar con $HOST."
echo "Ctrl-C para cerrar y restaurar /etc/hosts."
wait "$PROXY_PID"
