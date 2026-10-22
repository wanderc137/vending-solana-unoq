# unoq_leds.py — LEDs de estado de la placa UNO Q vía sysfs.
"""Los LEDs de la Arduino UNO Q cuelgan del pinctrl del MPU y se exponen por
`/sys/class/leds/`. Los relevantes:

    green:user   LED verde de usuario  -> "listo"
    blue:user    LED azul de usuario   -> "pagado" (encendido durante el pulso)
    red:user     LED rojo de usuario   -> "error"

Detalle importante: los LEDs vienen con un *trigger* (heartbeat/panic/etc.).
Mientras haya trigger, `brightness` no se puede fijar desde user space; primero
hay que escribir `none` en `trigger`. El usuario `arduino` pertenece al grupo
`gpiod`, que tiene permiso de escritura sobre esos ficheros (no requiere sudo).

El sketch del MCU **no** puede tocar estos LEDs: son del MPU, no del header.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ROOT = Path("/sys/class/leds")


class BoardLed:
    """Un LED de la placa controlado por sysfs (encendido/apagado)."""

    def __init__(self, name: str, root: Path | str = DEFAULT_ROOT) -> None:
        self.name = name
        self.root = Path(root)
        self.path = self.root / name
        self._prepared = False
        self.last_error: str = ""

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return (self.path / "brightness").exists()

    def _prepare(self) -> None:
        """Desactiva el trigger para poder controlar el brillo a mano."""
        if self._prepared:
            return
        trigger = self.path / "trigger"
        try:
            if trigger.exists():
                current = trigger.read_text().strip()
                # El trigger activo aparece entre corchetes: "[none] ...".
                if "[none]" not in current:
                    trigger.write_text("none\n")
                    logger.debug("LED %s: trigger -> none", self.name)
        except OSError as exc:  # permiso o LED inexistente: se intenta igual
            logger.debug("LED %s: no se pudo fijar trigger (%s)", self.name, exc)
        self._prepared = True

    def set(self, on: bool) -> None:
        """Enciende (on=True) o apaga el LED. Nunca lanza: loguea el fallo."""
        if not self.available:
            self.last_error = f"{self.path} no existe"
            logger.debug("LED %s no disponible", self.name)
            return
        self._prepare()
        try:
            (self.path / "brightness").write_text("1\n" if on else "0\n")
            self.last_error = ""
        except OSError as exc:
            self.last_error = str(exc)
            logger.warning("LED %s: no se pudo escribir brightness (%s)", self.name, exc)

    def on(self) -> None:
        self.set(True)

    def off(self) -> None:
        self.set(False)

    def status(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "available": self.available,
            "last_error": self.last_error,
        }


class BoardLeds:
    """Trio de LEDs de estado (ready/paid/error) de la UNO Q."""

    def __init__(self, ready: str, paid: str, error: str, root: Path | str = DEFAULT_ROOT) -> None:
        self.ready = BoardLed(ready, root)
        self.paid = BoardLed(paid, root)
        self.error = BoardLed(error, root)
        self._map = {"ready": self.ready, "paid": self.paid, "error": self.error}

    def get(self, name: str) -> BoardLed | None:
        return self._map.get(name)

    def set(self, name: str, on: bool) -> None:
        led = self._map.get(name)
        if led is None:
            logger.debug("LED desconocido: %s", name)
            return
        led.set(on)

    def all_off(self) -> None:
        for led in self._map.values():
            led.set(False)

    def status(self) -> dict:
        return {name: led.status() for name, led in self._map.items()}
