#!/usr/bin/env bash
#
# scripts/deploy_unoq.sh — Despliega el proyecto en la UNO Q por USB (ADB).
# Se ejecuta EN EL PC. Empuja el código con tar (sin .venv ni wheels) y,
# opcionalmente, las ruedas y el reinicio del servicio.
#
# Uso:
#   bash scripts/deploy_unoq.sh                 # código + reinicio del servicio
#   bash scripts/deploy_unoq.sh --wheels        # además empuja wheels/ (instalación)
#   bash scripts/deploy_unoq.sh --no-restart    # solo empuja
#   SERIAL=xxxx bash scripts/deploy_unoq.sh     # otro dispositivo
#
set -euo pipefail

SERIAL="${SERIAL:-2480682956}"
REMOTE="${REMOTE:-/home/arduino/vending-solana-unoq}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUSH_WHEELS=0
RESTART=1

for arg in "$@"; do
  case "$arg" in
    --wheels) PUSH_WHEELS=1 ;;
    --no-restart) RESTART=0 ;;
    *) echo "Argumento desconocido: $arg" >&2; exit 2 ;;
  esac
done

ADB=(adb -s "$SERIAL")

echo "==> Dispositivo $SERIAL"
"${ADB[@]}" get-state >/dev/null || { echo "!! La placa no está conectada por ADB" >&2; exit 1; }

echo "==> Creando $REMOTE"
"${ADB[@]}" shell "mkdir -p $REMOTE"

echo "==> Empujando código"
tar czf - -C "$PROJECT_DIR" \
  --exclude='.venv' --exclude='wheels' --exclude='__pycache__' \
  --exclude='.git' --exclude='.env' --exclude='wallet.json' \
  . | "${ADB[@]}" shell "tar xzf - -C $REMOTE"

if [ "$PUSH_WHEELS" = "1" ]; then
  if [ ! -d "$PROJECT_DIR/wheels" ]; then
    echo "!! No existe $PROJECT_DIR/wheels (descárgalas primero con pip download)" >&2
    exit 1
  fi
  echo "==> Empujando wheels/ ($(du -sh "$PROJECT_DIR/wheels" | cut -f1))"
  "${ADB[@]}" shell "mkdir -p $REMOTE/wheels"
  tar czf - -C "$PROJECT_DIR/wheels" . | "${ADB[@]}" shell "tar xzf - -C $REMOTE/wheels"
fi

echo "==> Permisos"
"${ADB[@]}" shell "chmod +x $REMOTE/scripts/*.sh"

if [ "$RESTART" = "1" ]; then
  echo "==> Reiniciando servicio vending-unoq"
  if "${ADB[@]}" shell "systemctl list-unit-files vending-unoq.service" | grep -q vending-unoq; then
    "${ADB[@]}" shell "sudo systemctl restart vending-unoq.service"
    sleep 2
    "${ADB[@]}" shell "systemctl is-active vending-unoq.service" || true
    "${ADB[@]}" shell "curl -s --max-time 3 http://127.0.0.1:8000/healthz" || true
    echo ""
  else
    echo "!! Servicio no instalado en la placa. Corre:"
    echo "   adb shell 'bash $REMOTE/scripts/setup_unoq.sh --systemd'"
  fi
fi

echo "==> Hecho."
