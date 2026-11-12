#!/usr/bin/env python3
# scripts/refund.py — Devuelve USDC desde la wallet del comercio (Raspberry Pi)
# hacia una dirección de destino (p.ej. la Phantom del cliente).
"""Reembolso de USDC desde la ATA del comercio hacia una wallet destino.

Uso:
    .venv/bin/python scripts/refund.py --to <PUBKEY_DESTINO> --amount 1.50
    .venv/bin/python scripts/refund.py --to <PUBKEY_DESTINO> --amount 1.50 --yes

Notas:
- Es una transferencia SPL `transfer_checked` normal (SIN `reference`: no es una
  venta, es una devolución).
- Si la ATA de USDC del destino no existe, se crea automáticamente (el rent lo
  paga la wallet del comercio, ~0.002 SOL).
- La wallet del comercio necesita SOL para la comisión de red (~0.000005 SOL).
- Nunca imprime la clave privada.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Permitir ejecutar como script (añade PROYECTO_DIR al path)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solders.instruction import Instruction  # noqa: E402
from solders.message import MessageV0  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.transaction import VersionedTransaction  # noqa: E402
from spl.token.constants import TOKEN_PROGRAM_ID  # noqa: E402
from spl.token.instructions import (  # noqa: E402
    create_associated_token_account,
    transfer_checked,
)
from spl.token.models import TransferCheckedParams  # noqa: E402
from solders.token.associated import get_associated_token_address  # noqa: E402

from config import load_config  # noqa: E402
from wallet import load_or_create_keypair  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("refund")


async def _send(client, keypair, instructions) -> str:
    blockhash = (await client.get_latest_blockhash()).value.blockhash
    msg = MessageV0.try_compile(
        payer=keypair.pubkey(),
        instructions=instructions,
        address_lookup_table_accounts=[],
        recent_blockhash=blockhash,
    )
    tx = VersionedTransaction(msg, [keypair])
    resp = await client.send_raw_transaction(bytes(tx))
    return str(resp.value)


async def refund(to_pubkey: str, amount_usdc: float) -> int:
    from solana.rpc.async_api import AsyncClient

    cfg = load_config()
    mint = Pubkey.from_string(cfg.usdc_mint)
    merchant = load_or_create_keypair(cfg.wallet_abs_path)
    merchant_ata = get_associated_token_address(merchant.pubkey(), mint)
    dest_owner = Pubkey.from_string(to_pubkey)
    dest_ata = get_associated_token_address(dest_owner, mint)
    amount_base = int(round(amount_usdc * (10 ** cfg.usdc_decimals)))

    print(f"  🌐 Red: {cfg.network}  |  RPC: {cfg.rpc_http}")
    print(f"  🏦 Origen (comercio):  {merchant_ata}")
    print(f"  🎯 Destino:            {dest_ata}  (owner {dest_owner})")
    print(f"  💸 Monto:              {amount_usdc} USDC  ({amount_base} base)")

    async with AsyncClient(cfg.rpc_http) as client:
        # Verificar balance de la ATA del comercio
        try:
            bal = (await client.get_token_account_balance(merchant_ata)).value
            print(f"  💰 Saldo origen:       {bal.ui_amount} USDC")
            if bal.amount and int(bal.amount) < amount_base:
                print(f"  ❌ Saldo insuficiente: la ATA del comercio tiene {bal.ui_amount} USDC")
                return 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  No se pudo leer el saldo de la ATA origen: {exc}")
            return 1

        # Crear la ATA destino si no existe
        info = await client.get_account_info_json_parsed(dest_ata)
        if info.value is None:
            print("  🏗️  La ATA destino no existe: creándola (paga el comercio)…")
            ix_create = create_associated_token_account(
                payer=merchant.pubkey(),
                owner=dest_owner,
                mint=mint,
                token_program_id=TOKEN_PROGRAM_ID,
            )
            sig = await _send(client, merchant, [ix_create])
            print(f"  ✅ ATA destino creada: {sig}")

        ix = transfer_checked(
            TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=merchant_ata,
                mint=mint,
                dest=dest_ata,
                owner=merchant.pubkey(),
                amount=amount_base,
                decimals=cfg.usdc_decimals,
                signers=[],
            )
        )
        sig = await _send(client, merchant, [ix])
        print(f"  ✅ Reembolso enviado. Firma: {sig}")
        print(f"     https://solscan.io/tx/{sig}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reembolso de USDC desde la wallet del comercio")
    parser.add_argument("--to", required=True, help="Pubkey (wallet) destino, p.ej. la Phantom del cliente")
    parser.add_argument("--amount", type=float, default=1.50, help="Monto en USDC (default: 1.50)")
    parser.add_argument("--yes", action="store_true", help="No pedir confirmación")
    args = parser.parse_args()

    if not args.yes:
        resp = input(f"¿Enviar {args.amount} USDC a {args.to}? [s/N] ").strip().lower()
        if resp not in ("s", "si", "sí", "y", "yes"):
            print("Cancelado.")
            return 1

    return asyncio.run(refund(args.to, args.amount))


if __name__ == "__main__":
    raise SystemExit(main())
