#!/usr/bin/env bash
#
# scripts/setup_unoq.sh — Prepara el entorno Python en el Linux de la UNO Q.
# Se ejecuta EN LA PLACA (o vía `adb shell`). NO usa internet ni apt:
# instala desde el directorio `wheels/` que se empuja por ADB.
#
# Uso:
#   bash scripts/setup_unoq.sh              # venv + dependencias + .env
#   bash scripts/setup_unoq.sh --systemd    # además instala el servicio
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
INSTALL_SERVICE=0
[ "${1:-}" = "--systemd" ] && INSTALL_SERVICE=1

echo "==> Proyecto: $PROJECT_DIR"

# ---------------------------------------------------------------
# 1) Comprobaciones del sistema
# ---------------------------------------------------------------
echo "==> Python: $(python3 -V)  |  Arch: $(uname -m)"
if [ ! -d "$PROJECT_DIR/wheels" ]; then
  echo "!! Falta el directorio wheels/ (se empuja desde el PC con adb push)." >&2
  echo "   En el PC:  pip download -r requirements.txt --platform manylinux2014_aarch64 \\" >&2
  echo "              --python-version 3.13 --implementation cp --abi cp313 \\" >&2
  echo "              --only-binary=:all: -d wheels" >&2
  exit 1
fi

# ---------------------------------------------------------------
# 2) venv (ensurepip NO existe en la UNO Q -> --without-pip)
# ---------------------------------------------------------------
if [ -d "$PROJECT_DIR/.venv" ] && [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "==> Reusando venv existente"
else
  echo "==> Creando venv (sin pip: ensurepip no está en la imagen)"
  python3 -m venv --without-pip "$PROJECT_DIR/.venv"
fi

VENV_PY="$PROJECT_DIR/.venv/bin/python"

# ---------------------------------------------------------------
# 3) Bootstrap de pip desde la rueda local (sin red)
# ---------------------------------------------------------------
if "$VENV_PY" -m pip --version >/dev/null 2>&1; then
  echo "==> pip ya disponible en el venv"
else
  PIP_WHL="$(ls -1 "$PROJECT_DIR"/wheels/pip-*.whl 2>/dev/null | head -n1 || true)"
  if [ -z "$PIP_WHL" ]; then
    echo "!! No hay rueda de pip en wheels/. Descárgala en el PC:" >&2
    echo "   pip download pip --only-binary=:all: -d wheels" >&2
    exit 1
  fi
  echo "==> Bootstrapeando pip desde $(basename "$PIP_WHL")"
  "$VENV_PY" "$PIP_WHL/pip" install --no-index --find-links "$PROJECT_DIR/wheels" pip
fi

# ---------------------------------------------------------------
# 4) Dependencias del proyecto (solo desde wheels/, sin red)
# ---------------------------------------------------------------
echo "==> Instalando dependencias (offline)"
"$VENV_PY" -m pip install --no-index --find-links "$PROJECT_DIR/wheels" -r requirements.txt

echo "==> Verificando imports"
"$VENV_PY" - <<'PY'
import fastapi, uvicorn, jinja2, yaml, qrcode, PIL, httpx, websockets
import solders, solana
from solders.token.associated import get_associated_token_address
print("   fastapi", fastapi.__version__, "| uvicorn", uvicorn.__version__)
print("   solders OK | solana OK | qrcode OK | pillow OK")
PY

# ---------------------------------------------------------------
# 5) .env
# ---------------------------------------------------------------
if [ ! -f "$PROJECT_DIR/.env" ] && [ -f "$PROJECT_DIR/.env.example" ]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "==> .env creado desde .env.example (revísalo)"
fi

# ---------------------------------------------------------------
# 6) Servicio systemd (opcional)
# ---------------------------------------------------------------
if [ "$INSTALL_SERVICE" = "1" ]; then
  echo "==> Instalando servicio vending-unoq.service"
  sudo cp "$PROJECT_DIR/deploy/vending-unoq.service" /etc/systemd/system/vending-unoq.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now vending-unoq.service
  sleep 2
  sudo systemctl --no-pager --lines=5 status vending-unoq.service || true
fi

echo ""
echo "==> Listo."
echo "    Arrancar a mano:  cd $PROJECT_DIR && .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000"
echo "    Prueba de GPIO:   .venv/bin/python scripts/dispense_test.py --seconds 1"
