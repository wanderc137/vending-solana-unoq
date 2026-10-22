# dispenser.py — Control del relé/motor y LEDs de estado (multi-backend).
"""Fachada del dispensador con TRES backends intercambiables:

- ``unoq``    : Arduino UNO Q. El header lo controla el MCU STM32U585, así que
                el relé se acciona por TCP contra el monitor del arduino-router
                (``mcu.McuLink``, 127.0.0.1:7500) y los LEDs de estado por sysfs
                (``unoq_leds.BoardLeds``).
- ``gpiozero``: Raspberry Pi (backend heredado, lgpio para Pi 5). Se importa de
                forma perezosa para que el mismo código corra en la UNO Q.
- ``stub``    : host de desarrollo sin hardware. Registra y no toca pines.

Selección con ``dispenser_mode``: ``auto`` | ``unoq`` | ``gpiozero`` | ``stub``.
``auto`` prueba ``unoq`` y si no hay MCU cae a ``gpiozero`` y luego a ``stub``.

La API pública es la de siempre (``is_available``, ``set_led``,
``set_led_async``, ``dispense``, ``dispense_sync``, ``cleanup``) para que
``app.py`` no dependa del backend. Se añaden ``backend`` y ``probe()`` para el
healthcheck.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Optional

from config import Config

logger = logging.getLogger(__name__)

VALID_LEDS = ("ready", "paid", "error")


class DispenserError(RuntimeError):
    pass


class Dispenser:
    """Controlador del dispensador. ``force_stub=True`` no toca hardware."""

    def __init__(self, config: Config, force_stub: bool = False):
        self.config = config
        self.available = False
        self.stub = False
        self.backend: str = "none"
        self.stub_reason: str = ""
        self._lock = threading.Lock()
        self._dispensing = False

        # Backend unoq
        self._mcu: Any = None
        self._leds: Any = None

        # Backend gpiozero (Raspberry Pi)
        self._relay: Any = None
        self._gpio_leds: dict[str, Any] = {}

        self._init_backend(force_stub)

    # ==================================================================
    # Inicialización
    # ==================================================================
    def _init_backend(self, force_stub: bool) -> None:
        mode = (self.config.dispenser_mode or "auto").strip().lower()

        if force_stub or mode == "stub":
            self._stub("modo stub forzado por config (dispenser_mode=stub)")
            return

        if mode in ("auto", "unoq"):
            if self._init_unoq():
                return
            if mode == "unoq":
                self._stub(self.stub_reason or "backend unoq no disponible")
                return

        if mode in ("auto", "gpiozero"):
            if self._init_gpiozero():
                return

        if not self.stub:
            self._stub(self.stub_reason or "ningún backend GPIO disponible")

    # ------------------------------------------------------------------
    def _stub(self, reason: str) -> None:
        self.stub = True
        self.available = False
        self.backend = "stub"
        self.stub_reason = reason
        logger.warning("Dispenser en modo STUB: %s", reason)

    # ------------------------------------------------------------------
    def _init_unoq(self) -> bool:
        """Conecta con el MCU y prepara los LEDs de placa."""
        try:
            from mcu import McuLink
        except Exception as exc:  # noqa: BLE001
            self.stub_reason = f"mcu.py no importable: {exc}"
            return False

        try:
            link = McuLink(
                host=self.config.mcu_host,
                port=self.config.mcu_port,
                timeout=self.config.mcu_timeout_ms / 1000.0,
            )
            if not link.probe():
                self.stub_reason = (
                    f"MCU sin respuesta en {self.config.mcu_host}:{self.config.mcu_port} "
                    f"({link.last_error or 'sin detalle'})"
                )
                return False
        except Exception as exc:  # noqa: BLE001
            self.stub_reason = f"MCU no disponible: {exc}"
            return False

        # Fail-safe: dejar el relé apagado al arrancar. Si el proceso murió a
        # mitad de un pulso (o hubo un reinicio), el pin podría quedar activo.
        try:
            link.write_pin(
                self.config.dispense_pin, not self.config.relay_active_high
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo apagar el relé al inicializar: %s", exc)

        # LEDs de la placa (best-effort: si falta uno, se sigue igual).
        try:
            from unoq_leds import BoardLeds

            leds = BoardLeds(
                ready=self.config.unoq_led_ready,
                paid=self.config.unoq_led_paid,
                error=self.config.unoq_led_error,
            )
            for name in VALID_LEDS:
                led = leds.get(name)
                if led is not None and not led.available:
                    logger.warning("LED '%s' (%s) no encontrado en sysfs", name, led.path)
            self._leds = leds
        except Exception as exc:  # noqa: BLE001
            logger.warning("LEDs de placa no disponibles: %s", exc)
            self._leds = None

        self._mcu = link
        self.backend = "unoq"
        self.stub = False
        self.available = True
        self.stub_reason = ""
        self._set_led_sync("ready", True)
        logger.info(
            "GPIO UNO Q listo: relé en pin D%d vía %s:%d, LEDs ready/paid/error = %s/%s/%s",
            self.config.dispense_pin,
            self.config.mcu_host,
            self.config.mcu_port,
            self.config.unoq_led_ready,
            self.config.unoq_led_paid,
            self.config.unoq_led_error,
        )
        return True

    # ------------------------------------------------------------------
    def _init_gpiozero(self) -> bool:
        """Backend heredado de Raspberry Pi (gpiozero + lgpio)."""
        try:
            from gpiozero import OutputDevice  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self.stub_reason = f"gpiozero no instalado ({exc})"
            return False

        try:
            factory = None
            try:
                from gpiozero.pins.lgpio import LGPIOFactory  # type: ignore

                factory = LGPIOFactory()
            except Exception as exc:  # noqa: BLE001
                # En Pi 4 y anteriores funciona el backend por defecto (RPi.GPIO).
                logger.debug("LGPIOFactory no disponible (%s); se usa el backend por defecto", exc)
                factory = None

            kwargs = {"active_high": True}
            if factory is not None:
                kwargs["pin_factory"] = factory

            self._relay = OutputDevice(self.config.dispense_pin, **kwargs)
            self._relay.off()  # fail-safe: relé apagado al arrancar
            self._gpio_leds = {
                "ready": OutputDevice(self.config.led_ready_pin, **kwargs),
                "paid": OutputDevice(self.config.led_paid_pin, **kwargs),
                "error": OutputDevice(self.config.led_error_pin, **kwargs),
            }
            self.backend = "gpiozero"
            self.stub = False
            self.available = True
            self.stub_reason = ""
            self._set_led_sync("ready", True)
            logger.info(
                "GPIO Pi listo (gpiozero). Relé BCM %d, LEDs %d/%d/%d",
                self.config.dispense_pin,
                self.config.led_ready_pin,
                self.config.led_paid_pin,
                self.config.led_error_pin,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._relay = None
            self._gpio_leds = {}
            self.stub_reason = f"gpiozero no pudo abrir los pines: {exc}"
            return False

    # ==================================================================
    # Estado / diagnóstico
    # ==================================================================
    @property
    def is_available(self) -> bool:
        return self.available

    def probe(self) -> bool:
        """Reprueba el hardware en caliente (healthcheck)."""
        if self.backend == "unoq" and self._mcu is not None:
            return bool(self._mcu.probe())
        return self.available

    def status(self) -> dict:
        info: dict = {
            "backend": self.backend,
            "available": self.available,
            "stub": self.stub,
            "stub_reason": self.stub_reason,
            "dispense_pin": self.config.dispense_pin,
            "pulse_seconds": self.config.dispense_pulse_seconds,
            "relay_active_high": self.config.relay_active_high,
        }
        if self.backend == "unoq":
            info["mcu"] = self._mcu.status() if self._mcu else None
            info["leds"] = self._leds.status() if self._leds else None
        return info

    # ==================================================================
    # LEDs
    # ==================================================================
    def _set_led_sync(self, name: str, on: bool) -> None:
        if name not in VALID_LEDS:
            logger.debug("LED desconocido: %s", name)
            return
        if self.stub:
            logger.info("[STUB] LED %s -> %s", name, "ON" if on else "OFF")
            return
        try:
            if self.backend == "unoq" and self._leds is not None:
                self._leds.set(name, on)
            elif self.backend == "gpiozero":
                dev = self._gpio_leds.get(name)
                if dev is not None:
                    dev.on() if on else dev.off()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LED %s: fallo al cambiar estado (%s)", name, exc)

    def set_led(self, name: str, on: bool) -> None:
        """Enciende/apaga un LED ('ready'|'paid'|'error')."""
        self._set_led_sync(name, on)

    async def set_led_async(self, name: str, on: bool) -> None:
        await asyncio.to_thread(self._set_led_sync, name, on)

    # ==================================================================
    # Dispensado
    # ==================================================================
    def _dispense_blocking(self, pin: int, seconds: float) -> None:
        """Pulso de ``seconds`` en el relé. Bloqueo anti-doble disparo."""
        with self._lock:
            if self._dispensing:
                logger.warning("Dispensado ya en curso; ignorando disparo duplicado.")
                return
            self._dispensing = True
        try:
            seconds = max(0.0, float(seconds))
            if self.stub:
                logger.info("[STUB] Dispensado: relé pin %d ON %.1fs", pin, seconds)
                time.sleep(min(seconds, 0.5))
                return

            self._set_led_sync("paid", True)
            try:
                if self.backend == "unoq":
                    if self._mcu is None:
                        raise DispenserError("MCU no inicializado")
                    self._mcu.pulse(
                        int(pin), seconds, active_high=self.config.relay_active_high
                    )
                elif self.backend == "gpiozero":
                    if self._relay is None:
                        raise DispenserError("Relé no inicializado (gpiozero)")
                    self._relay.on()
                    try:
                        time.sleep(seconds)
                    finally:
                        self._relay.off()
                else:
                    raise DispenserError(f"backend desconocido: {self.backend}")
            finally:
                self._set_led_sync("paid", False)
            logger.info("Dispensado completado (backend=%s, pin %d, %.1fs)", self.backend, pin, seconds)
        finally:
            with self._lock:
                self._dispensing = False

    async def dispense(self, pin: Optional[int] = None, seconds: Optional[float] = None) -> None:
        """Dispara el dispensado y espera a que termine el pulso."""
        pin = pin if pin is not None else self.config.dispense_pin
        seconds = seconds if seconds is not None else self.config.dispense_pulse_seconds
        await asyncio.to_thread(self._dispense_blocking, pin, seconds)

    def dispense_sync(self, pin: Optional[int] = None, seconds: Optional[float] = None) -> None:
        pin = pin if pin is not None else self.config.dispense_pin
        seconds = seconds if seconds is not None else self.config.dispense_pulse_seconds
        self._dispense_blocking(pin, seconds)

    # ==================================================================
    # Limpieza
    # ==================================================================
    def cleanup(self) -> None:
        """Deja el relé y los LEDs apagados."""
        try:
            if self.backend == "unoq" and self._mcu is not None:
                try:
                    self._mcu.write_pin(
                        self.config.dispense_pin, not self.config.relay_active_high
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("No se pudo apagar el relé: %s", exc)
            if self._relay is not None:
                try:
                    self._relay.off()
                except Exception:  # noqa: BLE001
                    pass
            for dev in self._gpio_leds.values():
                try:
                    dev.off()
                except Exception:  # noqa: BLE001
                    pass
            if self._leds is not None:
                self._leds.all_off()
        finally:
            self._relay = None
            self._gpio_leds = {}


# Acceso "de un solo uso" para pruebas CLI rápidas
def dispense(pin: int, seconds: float) -> None:
    """Función CLI: pulso de prueba. Ej: python -c "from dispenser import dispense; dispense(7, 2.5)" """
    from config import load_config

    d = Dispenser(load_config())
    try:
        d.dispense_sync(pin, seconds)
    finally:
        d.cleanup()
