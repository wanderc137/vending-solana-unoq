# config.py — Carga de configuración (config.yaml + .env + env vars).
# Preferencia: variables de entorno > .env > config.yaml.

"""Carga la configuración de la máquina expendedora.

Fuentes (de mayor a menor prioridad):
1. Variables de entorno del proceso (p.ej. RPC_HTTP=...)
2. Archivo .env (cargado con python-dotenv)
3. config.yaml

Los campos ``mcu_*``, ``unoq_led_*`` y ``merchant_pubkey`` existen para el
puerto a la Arduino UNO Q; los campos ``led_*_pin`` siguen siendo los pines BCM
del backend Raspberry Pi.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Valores de Mint USDC.
USDC_MINT_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_MINT_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"


@dataclass
class Config:
    # --- Red Solana ---------------------------------------------------
    network: str = "devnet"
    rpc_http: str = "https://api.devnet.solana.com"
    rpc_ws: str = "wss://api.devnet.solana.com"
    rpc_http_backup: str = "https://api.devnet.solana.com"
    usdc_mint: str = USDC_MINT_DEVNET
    usdc_decimals: int = 6

    # --- Wallet --------------------------------------------------------
    wallet_path: str = "wallet.json"
    # Modo "solo pubkey": cobrar sin tener la clave privada en el dispositivo.
    merchant_pubkey: str = ""

    # --- Tiempos -------------------------------------------------------
    order_timeout_seconds: int = 60
    dispense_pulse_seconds: float = 2.5
    poll_interval_ms: int = 1500

    # --- Dispensador ---------------------------------------------------
    dispense_pin: int = 7
    dispenser_mode: str = "auto"  # auto | unoq | gpiozero | stub
    relay_active_high: bool = True

    # Pines BCM (backend gpiozero / Raspberry Pi)
    led_ready_pin: int = 27
    led_paid_pin: int = 22
    led_error_pin: int = 23

    # Puente con el MCU de la UNO Q (backend unoq)
    mcu_host: str = "127.0.0.1"
    mcu_port: int = 7500
    mcu_timeout_ms: int = 2000

    # LEDs de placa de la UNO Q (sysfs /sys/class/leds)
    unoq_led_ready: str = "green:user"
    unoq_led_paid: str = "blue:user"
    unoq_led_error: str = "red:user"

    # --- Varios --------------------------------------------------------
    test_token: str = ""
    http_port: int = 8000
    base_dir: Path = field(default=BASE_DIR)

    @property
    def wallet_abs_path(self) -> Path:
        p = Path(self.wallet_path)
        return p if p.is_absolute() else self.base_dir / p


# Mapeo env var -> atributo. El tipo se deduce del dataclass.
ENV_MAP: dict[str, str] = {
    "NETWORK": "network",
    "RPC_HTTP": "rpc_http",
    "RPC_WS": "rpc_ws",
    "RPC_HTTP_BACKUP": "rpc_http_backup",
    "USDC_MINT": "usdc_mint",
    "USDC_DECIMALS": "usdc_decimals",
    "WALLET_PATH": "wallet_path",
    "MERCHANT_PUBKEY": "merchant_pubkey",
    "ORDER_TIMEOUT_SECONDS": "order_timeout_seconds",
    "DISPENSE_PULSE_SECONDS": "dispense_pulse_seconds",
    "POLL_INTERVAL_MS": "poll_interval_ms",
    "DISPENSE_PIN": "dispense_pin",
    "DISPENSER_MODE": "dispenser_mode",
    "RELAY_ACTIVE_HIGH": "relay_active_high",
    "LED_READY_PIN": "led_ready_pin",
    "LED_PAID_PIN": "led_paid_pin",
    "LED_ERROR_PIN": "led_error_pin",
    "MCU_HOST": "mcu_host",
    "MCU_PORT": "mcu_port",
    "MCU_TIMEOUT_MS": "mcu_timeout_ms",
    "UNOQ_LED_READY": "unoq_led_ready",
    "UNOQ_LED_PAID": "unoq_led_paid",
    "UNOQ_LED_ERROR": "unoq_led_error",
    "TEST_TOKEN": "test_token",
    "HTTP_PORT": "http_port",
}

_FLOAT_FIELDS = {"dispense_pulse_seconds"}
_INT_FIELDS = {
    "usdc_decimals",
    "order_timeout_seconds",
    "poll_interval_ms",
    "dispense_pin",
    "led_ready_pin",
    "led_paid_pin",
    "led_error_pin",
    "mcu_port",
    "mcu_timeout_ms",
    "http_port",
}
_BOOL_FIELDS = {"relay_active_high"}

_TRUE = {"1", "true", "yes", "y", "on", "si", "sí"}


def _coerce(attr: str, value: Any) -> Any:
    """Convierte el valor (típicamente str de env) al tipo del campo."""
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    if attr in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in _TRUE
    if attr in _INT_FIELDS:
        return int(float(value))
    if attr in _FLOAT_FIELDS:
        return float(value)
    return value


def load_config() -> Config:
    load_dotenv(BASE_DIR / ".env")

    cfg = Config()

    yaml_path = BASE_DIR / "config.yaml"
    if yaml_path.exists():
        data = yaml.safe_load(yaml_path.read_text()) or {}
        for key, value in data.items():
            if hasattr(cfg, key) and key != "base_dir" and value is not None:
                try:
                    setattr(cfg, key, _coerce(key, value))
                except (TypeError, ValueError):
                    setattr(cfg, key, value)

    for env_name, attr in ENV_MAP.items():
        raw = os.getenv(env_name)
        if raw is None or raw.strip() == "":
            continue
        try:
            coerced = _coerce(attr, raw)
        except (TypeError, ValueError):
            continue
        if coerced is not None:
            setattr(cfg, attr, coerced)

    cfg.base_dir = BASE_DIR
    return cfg
