#!/usr/bin/env python3
# scripts/mcu_probe.py — Diagnóstico del header de la Arduino UNO Q.
"""Comprueba el puente Linux -> MCU (127.0.0.1:7500) y ejercita pines.

Uso:
    python scripts/mcu_probe.py                 # lee D7 (config) y A0
    python scripts/mcu_probe.py --pin 7         # lee un pin concreto
    python scripts/mcu_probe.py --pulse 7 1.0   # pulso real de 1.0 s en D7
    python scripts/mcu_probe.py --sweep         # lee D0-D13 y A0-A5

El sketch del MCU (`firmware/unoq_gpio_control.ino`) implementa el protocolo
READ/WRITE/ANALOG/PWM. Si el sketch se reflashea con otro programa, este
diagnóstico lo detecta con un error de protocolo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config  # noqa: E402
from mcu import McuLink, McuError  # noqa: E402


def main() -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Diagnóstico del MCU de la UNO Q")
    parser.add_argument("--pin", type=int, default=cfg.dispense_pin)
    parser.add_argument("--pulse", nargs=2, metavar=("PIN", "SEGUNDOS"), type=float,
                        help="Pulso digital: PIN y duración en segundos")
    parser.add_argument("--sweep", action="store_true", help="Lee D0-D13 y A0-A5")
    parser.add_argument("--host", default=cfg.mcu_host)
    parser.add_argument("--port", type=int, default=cfg.mcu_port)
    args = parser.parse_args()

    link = McuLink(args.host, args.port, timeout=cfg.mcu_timeout_ms / 1000.0)
    print(f"MCU: {args.host}:{args.port} (timeout {cfg.mcu_timeout_ms} ms)")

    if not link.probe():
        print(f"FALLO: el MCU no responde ({link.last_error})")
        print("  - ¿Está el sketch unoq_gpio_control flasheado?")
        print("  - ¿Sigue vivo 'arduino-router'? (systemctl status arduino-router)")
        return 1

    try:
        if args.pulse:
            pin, seconds = int(args.pulse[0]), float(args.pulse[1])
            print(f"Pulso: WRITE {pin} 1 -> espera {seconds}s -> WRITE {pin} 0")
            link.pulse(pin, seconds, active_high=cfg.relay_active_high)
            print(f"  estado final D{pin}: {link.read_pin(pin)}")
            return 0

        if args.sweep:
            for pin in list(range(14)) + list(range(14, 20)):
                try:
                    kind = "A" if pin >= 14 else "D"
                    label = f"{kind}{pin - 14 if pin >= 14 else pin}"
                    print(f"  {label:<4} (proto {pin:>2}): {link.read_pin(pin)}")
                except McuError as exc:
                    print(f"  proto {pin:>2}: ERROR {exc}")
            return 0

        print(f"  READ D{args.pin} -> {link.read_pin(args.pin)}")
        print(f"  ANALOG A0    -> {link.read_analog(14)}")
        print("OK: el bridge Linux -> MCU funciona.")
        return 0
    except McuError as exc:
        print(f"FALLO: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
