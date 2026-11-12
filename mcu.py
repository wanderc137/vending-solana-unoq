# mcu.py — Puente TCP con el MCU STM32U585 de la Arduino UNO Q.
"""Cliente del protocolo del sketch `unoq_gpio_control` flasheado en el MCU.

En la UNO Q el header Arduino **no** está cableado al Linux: lo controla el MCU
STM32U585 (Zephyr). El daemon `arduino-router` publica un *monitor proxy* en
`127.0.0.1:7500` que reenvía líneas de texto al `Serial` del sketch. Protocolo:

    READ <pin>            -> OK <0|1>
    WRITE <pin> <0|1>     -> OK <0|1>
    ANALOG <pin>          -> OK <0..1023>
    PWM <pin> <0..255>    -> OK <val>

Notas de campo (medidas en la placa, 2026-10):
- Latencia ~65-75 ms por comando.
- Abrir un socket nuevo por comando es estable; ocasionalmente la primera
  respuesta llega vacía (el proxy cierra la conexión) -> se reintenta una vez.
- Las respuestas terminan en `\\r\\n` y puede quedar el `\\r\\n` de la anterior:
  se toma la ÚLTIMA línea no vacía.
"""
from __future__ import annotations

import logging
import socket
import threading
import time

logger = logging.getLogger(__name__)

# Rango de pines del header UNO Q (D0-D13 + A0-A5 mapeados como 14-19).
PIN_MIN = 0
PIN_MAX = 19


class McuError(RuntimeError):
    """Fallo de comunicación con el MCU."""


class McuLink:
    """Comandos al sketch del MCU por TCP (monitor del arduino-router)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7500,
        timeout: float = 2.0,
        retries: int = 1,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = max(0, retries)
        self._lock = threading.Lock()
        self.last_error: str = ""

    # ------------------------------------------------------------------
    def _command_once(self, line: str) -> str:
        payload = (line + "\n").encode("ascii")
        with self._lock:
            try:
                with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                    sock.settimeout(self.timeout)
                    sock.sendall(payload)
                    buf = b""
                    deadline = time.monotonic() + self.timeout
                    while b"\n" not in buf:
                        if time.monotonic() > deadline:
                            raise McuError(f"timeout esperando respuesta a {line!r}")
                        chunk = sock.recv(256)
                        if not chunk:
                            break
                        buf += chunk
            except OSError as exc:
                raise McuError(f"{self.host}:{self.port} inaccesible: {exc}") from exc

        text = buf.decode("ascii", errors="replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            raise McuError(f"respuesta vacía a {line!r}")
        return lines[-1]

    def command(self, line: str) -> str:
        """Envía un comando y devuelve la línea `OK ...` (reintenta si viene vacía)."""
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._command_once(line)
                if not resp.upper().startswith("OK"):
                    raise McuError(f"respuesta inesperada a {line!r}: {resp!r}")
                self.last_error = ""
                return resp
            except McuError as exc:
                last = exc
                self.last_error = str(exc)
                logger.debug("MCU intento %d/%d falló: %s", attempt + 1, self.retries + 1, exc)
                time.sleep(0.05)
        raise last if last else McuError("fallo desconocido")

    # ------------------------------------------------------------------
    @staticmethod
    def _check_pin(pin: int) -> int:
        if not isinstance(pin, int):
            raise McuError(f"pin inválido: {pin!r}")
        if not (PIN_MIN <= pin <= PIN_MAX):
            raise McuError(f"pin fuera de rango ({PIN_MIN}-{PIN_MAX}): {pin}")
        return pin

    @staticmethod
    def _parse_value(resp: str) -> int:
        parts = resp.split()
        if len(parts) < 2:
            raise McuError(f"respuesta sin valor: {resp!r}")
        try:
            return int(parts[1])
        except ValueError as exc:
            raise McuError(f"valor no numérico: {resp!r}") from exc

    def write_pin(self, pin: int, value: bool | int) -> int:
        """Escribe 0/1 en un pin digital. Devuelve el estado aplicado."""
        pin = self._check_pin(int(pin))
        raw = 1 if value else 0
        return self._parse_value(self.command(f"WRITE {pin} {raw}"))

    def read_pin(self, pin: int) -> int:
        """Lee un pin digital (0/1)."""
        pin = self._check_pin(int(pin))
        return self._parse_value(self.command(f"READ {pin}"))

    def read_analog(self, pin: int) -> int:
        """Lee una entrada analógica (0..1023)."""
        pin = self._check_pin(int(pin))
        return self._parse_value(self.command(f"ANALOG {pin}"))

    def pwm(self, pin: int, value: int) -> int:
        """Fija un PWM (0..255)."""
        pin = self._check_pin(int(pin))
        value = max(0, min(255, int(value)))
        return self._parse_value(self.command(f"PWM {pin} {value}"))

    def pulse(self, pin: int, seconds: float, active_high: bool = True) -> None:
        """Pulso digital: ON `seconds` y OFF garantizado al salir."""
        on, off = (1, 0) if active_high else (0, 1)
        self.write_pin(pin, on)
        try:
            time.sleep(max(0.0, float(seconds)))
        finally:
            self.write_pin(pin, off)

    # ------------------------------------------------------------------
    def probe(self) -> bool:
        """Comprueba que el MCU responde."""
        try:
            self.read_pin(0)
            return True
        except McuError as exc:
            self.last_error = str(exc)
            return False

    def status(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "timeout_s": self.timeout,
            "last_error": self.last_error,
        }
