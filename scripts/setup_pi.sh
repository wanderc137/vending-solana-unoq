#!/usr/bin/env bash
#
# setup_pi.sh — Preparación del entorno de la máquina expendedora cripto
# sobre una Raspberry Pi (Raspberry Pi OS). Corre en la Pi y en cualquier
# host Linux (dev) donde quieras probar.
#
# Uso:  bash setup_pi.sh
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "==> Proyecto: $PROJECT_DIR"

# ---------------------------------------------------------------
# 1) Dependencias de sistema (Raspberry Pi OS / Debian / Ubuntu)
# ---------------------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
  echo "==> Instalando dependencias de sistema..."
  sudo apt-get update -y
  sudo apt-get install -y python3 python3-venv python3-pip \
    libatlas-base-dev libopenblas-dev \
    wvdial ppp usb-modeswitch || true
  # I-010: backend lgpio para gpiozero en Pi 5 (libgpiod, /dev/gpiochip4).
  sudo apt-get install -y python3-lgpio || true
fi

# ---------------------------------------------------------------
# 2) Habilitar GPIO (si hay /dev/gpiomem)
# ---------------------------------------------------------------
if [ -e /dev/gpiomem ]; then
  echo "==> GPIO disponible en esta placa."
else
  echo "!! /dev/gpiomem no detectado. En un host de desarrollo es normal;"
  echo "   en la Pi ejecuta: sudo raspi-config -> Interfacing -> SPI/I2C."
fi

# ---------------------------------------------------------------
# 3) Entorno virtual Python
# ---------------------------------------------------------------
echo "==> Creando entorno virtual..."
python3 -m venv "$PROJECT_DIR/.venv"

# shellcheck disable=SC1090
source "$PROJECT_DIR/.venv/bin/activate"

echo "==> Actualizando pip..."
pip install --upgrade pip

echo "==> Instalando dependencias Python..."
pip install -r "$PROJECT_DIR/requirements.txt"

# ---------------------------------------------------------------
# 4) Configuración mínima
# ---------------------------------------------------------------
if [ ! -f "$PROJECT_DIR/.env" ] && [ -f "$PROJECT_DIR/.env.example" ]; then
  echo "==> Creando .env desde .env.example (edítalo con tus valores)"
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
fi

echo ""
echo "==> Listo."
echo "    Para ejecutar la web app:"
echo "    source $PROJECT_DIR/.venv/bin/activate"
echo "    uvicorn app:app --host 0.0.0.0 --port 8000"
echo ""
echo "    Recuerda configurar el módem LTE Teltonika (wvdial/ppp) y el RPC."
