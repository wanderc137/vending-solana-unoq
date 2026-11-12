#!/usr/bin/env python3
# scripts/dispense_test.py — Prueba manual del pulso del dispensador (GPIO).
"""Uso:
    python scripts/dispense_test.py                  # pulso 2.5 s en D7 (config)
    python scripts/dispense_test.py --pin 7 --seconds 1

En la UNO Q el pulso sale por el header (relé en D7) a través del MCU; en la
Raspberry Pi por el pin BCM configurado. En un host sin hardware el controlador
actúa en modo STUB y solo registra el evento.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config  # noqa: E402
from dispenser import Dispenser  # noqa: E402


def main() -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Test del dispensador GPIO")
    parser.add_argument("--pin", type=int, default=cfg.dispense_pin)
    parser.add_argument("--seconds", type=float, default=cfg.dispense_pulse_seconds)
    parser.add_argument("--force-stub", action="store_true", help="No tocar GPIO")
    args = parser.parse_args()

    d = Dispenser(cfg, force_stub=args.force_stub)
    print(f"Backend: {d.backend} | disponible={d.is_available} | stub={d.stub}")
    if d.stub_reason:
        print(f"Motivo del stub: {d.stub_reason}")
    try:
        d.dispense_sync(args.pin, args.seconds)
    finally:
        d.cleanup()
    print("Pulso completado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())