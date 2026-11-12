#!/usr/bin/env python3
"""
sales.py — Visor de ventas de la máquina expendedora (app de consola).

Solo LECTURA: consulta la blockchain (balance + transacciones de la ATA del
comerciante) y muestra las ventas. NO abre puertos, NO escucha nada, NO
modifica nada. Corre localmente en tu terminal.

Uso:
    python scripts/sales.py              # muestra balance + últimas ventas
    python scripts/sales.py --watch      # modo vigilancia: actualiza cada 5 s
    python scripts/sales.py --last 20    # muestra las últimas 20 transacciones

Seguridad:
    - Solo consulta endpoints RPC públicos (nada de claves privadas).
    - No necesita la wallet.json del comerciante (usa solo la dirección ATA).
    - No expone ningún puerto ni servicio.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Permitir importar config.py / payments.py desde la raíz del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solders.signature import Signature

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.yaml"
WALLET_PATH = PROJECT_DIR / "wallet.json"

USDC_DECIMALS = 6


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg


def merchant_ata() -> Pubkey:
    """Deriva la ATA del comerciante (no necesita la clave privada)."""
    from wallet import derive_ata  # noqa: E402

    from config import load_config as _load_app_config  # noqa: E402
    from wallet import load_merchant  # noqa: E402

    app_cfg = _load_app_config()
    usdc_mint = Pubkey.from_string(app_cfg.usdc_mint)
    # Sirve tanto en modo keypair (wallet.json) como en modo solo-pubkey
    # (MERCHANT_PUBKEY en config.yaml/.env), sin pedir nada por consola.
    owner = load_merchant(app_cfg).pubkey
    return derive_ata(owner, usdc_mint)


def human_amount(raw_amount: str | int) -> str:
    """Convierte unidades base de USDC (6 decimales) a formato humano."""
    try:
        n = int(raw_amount) / (10 ** USDC_DECIMALS)
    except (ValueError, TypeError):
        return f"{raw_amount} (sin decodificar)"
    return f"{n:,.2f} USDC"


def fmt_time(ts: int | None) -> str:
    if not ts:
        return "n/d"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# --------------------------------------------------------------------------
# Consultas (solo lectura)
# --------------------------------------------------------------------------


async def get_balance(client: AsyncClient, ata: Pubkey) -> tuple[str, str]:
    """Devuelve (balance_humano, balance_raw)."""
    try:
        resp = await client.get_token_account_balance(ata)
        if resp.value:
            return human_amount(resp.value.amount), resp.value.amount
        return "0.00 USDC (sin cuenta)", "0"
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "could not find account" in msg:
            return "0.00 USDC (cuenta ATA aún no creada/fondeada)", "0"
        return f"error: {exc}", "0"


async def list_sales(client: AsyncClient, ata: Pubkey, limit: int = 15) -> list[dict]:
    """Obtiene las últimas transacciones entrantes a la ATA y las decodifica."""
    from payments import validate_payment_tx  # noqa: E402

    sales: list[dict] = []
    try:
        sigs_resp = await client.get_signatures_for_address(ata, limit=limit)
        sigs = sigs_resp.value or []
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠  No se pudieron obtener firmas: {exc}")
        return sales

    for item in sigs:
        sig = item.signature
        record = {
            "signature": str(sig),
            "slot": item.slot,
            "time": item.block_time,
            "err": item.err,
            "amount": None,
        }
        try:
            tx = await client.get_transaction(
                Signature.from_string(str(sig)),
                encoding="jsonParsed",
                max_supported_transaction_version=0,
            )
            if tx.value is None:
                sales.append(record)
                continue
            validated = validate_payment_tx(
                tx.value.transaction, ata, allow_any_reference=True
            )
            record["amount"] = human_amount(validated.amount) if validated else None
            record["is_sale"] = bool(validated)
            if validated:
                record["reference"] = str(validated.reference)
        except Exception as exc:  # noqa: BLE001
            record["decode_error"] = str(exc)
        sales.append(record)
    return sales


# --------------------------------------------------------------------------
# Vistas
# --------------------------------------------------------------------------


def print_sales(sales: list[dict]) -> None:
    if not sales:
        print("  (sin transacciones todavía)")
        return

    sales_only = [s for s in sales if s.get("is_sale")]
    if not sales_only:
        sales_only = [s for s in sales if s.get("amount") is not None]

    if sales_only:
        print(f"\n  🥤  VENTAS DETECTADAS: {len(sales_only)}")
        print("  " + "-" * 60)
        for s in sales_only:
            ref = s.get("reference", "")
            ref_short = f"ref:{ref[:12]}…" if ref else "ref:—"
            status = "✅" if not s.get("err") else f"❌ {s['err']}"
            print(
                f"  {status} {fmt_time(s.get('time'))}  "
                f"{s.get('amount') or 'USDC'}"
            )
            print(f"      {ref_short}  sig:{str(s['signature'])[:20]}…")
        print("  " + "-" * 60)

    other = [s for s in sales if s not in sales_only]
    if other:
        print(f"\n  📄 Otras transacciones recientes (no ventas): {len(other)}")
        for s in other[:5]:
            err = s.get("err") or ""
            print(
                f"  · {fmt_time(s.get('time'))}  "
                f"sig:{str(s['signature'])[:20]}…  {err}"
            )


async def run_show(client: AsyncClient, ata: Pubkey, last: int) -> None:
    bal, _ = await get_balance(client, ata)
    print(f"\n  💰 Balance de la ATA del comerciante:")
    print(f"      {bal}")
    print(f"  ATA: {ata}")
    print(f"\n  📜 Últimas {last} transacciones:")
    sales = await list_sales(client, ata, limit=last)
    print_sales(sales)


async def run_watch(client: AsyncClient, ata: Pubkey, interval: float) -> None:
    print(f"\n  👁  Modo vigilancia (actualiza cada {interval:.0f} s). Ctrl+C para salir.\n")
    last_count = 0
    while True:
        bal, _ = await get_balance(client, ata)
        sales = await list_sales(client, ata, limit=10)
        sales_only = [s for s in sales if s.get("is_sale")]
        print(f"[{time.strftime('%H:%M:%S')}] Balance: {bal}  |  "
              f"Ventas vistas: {len(sales_only)}")
        if len(sales_only) > last_count:
            print(f"  🎉 ¡Nueva venta detectada! Total ahora: {len(sales_only)}")
            print_sales(sales_only[:3])
        last_count = len(sales_only)
        await asyncio.sleep(interval)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Visor de ventas vending-solana")
    parser.add_argument("--watch", action="store_true", help="modo vigilancia")
    parser.add_argument("--interval", type=float, default=5.0, help="segundos entre refrescos")
    parser.add_argument("--last", type=int, default=15, help="nº de transacciones a mostrar")
    args = parser.parse_args()

    cfg = load_config()
    rpc = cfg.get("rpc_http", "https://api.devnet.solana.com")
    ata = merchant_ata()

    print(f"  🌐 Red: {cfg.get('network', '?')}  |  RPC: {rpc}")
    print(f"  📦 Visor SOLO-LECTURA (no abre puertos, no usa claves privadas)")

    async with AsyncClient(rpc) as client:
        if args.watch:
            await run_watch(client, ata, args.interval)
        else:
            await run_show(client, ata, args.last)
            print("\n  💡 Sugerencia: usa --watch para vigilancia en vivo.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  👋 Hasta luego.")