#!/usr/bin/env python3
# scripts/setup_wallet.py — Genera el keypair del comercio y crea la ATA de USDC.
"""Uso:
    python scripts/setup_wallet.py

Carga o crea el keypair del comercio (config.wallet_path), intenta airdropear
SOL en devnet si el balance es bajo, y crea la ATA de USDC para el mint
configurado. Imprime pubkey del comercio y dirección ATA.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

from config import load_config
from wallet import get_or_create_ata, load_or_create_keypair

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("setup_wallet")

LAMPORTS_PER_SOL = 1_000_000_000


async def _airdrop_sol(client, pubkey, lamports) -> bool:
    for attempt in range(3):
        try:
            resp = await client.request_airdrop(pubkey, lamports)
            if resp.value:
                logger.info("Airdrop OK: %s (+%.1f SOL)", pubkey, lamports / LAMPORTS_PER_SOL)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Airdrop intento %d falló: %s", attempt + 1, exc)
        await asyncio.sleep(3)
    return False


async def main() -> None:
    cfg = load_config()
    async with AsyncClient(cfg.rpc_http) as client:
        kp = load_or_create_keypair(cfg.wallet_abs_path)
        logger.info("Wallet comercio: %s", kp.pubkey())

        bal = await client.get_balance(kp.pubkey())
        lamports = bal.value if bal.value else 0
        logger.info("Balance SOL: %.3f", lamports / LAMPORTS_PER_SOL)
        if lamports < 2 * LAMPORTS_PER_SOL:
            logger.info("Airdropeando SOL (devnet)…")
            await _airdrop_sol(client, kp.pubkey(), 2 * LAMPORTS_PER_SOL)

        mint = Pubkey.from_string(cfg.usdc_mint)
        ata, created = await get_or_create_ata(client, kp, kp.pubkey(), mint)
        logger.info("ATA %s (mint %s) %s", ata, mint, "CREADA" if created else "ya existía")

        bal_tok = await client.get_token_account_balance(ata)
        tok = int(bal_tok.value.amount) if bal_tok.value else 0
        print("=" * 60)
        print(f"MERCHANT_PUBKEY={kp.pubkey()}")
        print(f"MERCHANT_ATA={ata}")
        print(f"USDC_MINT={cfg.usdc_mint}")
        print(f"USDC_BALANCE_BASE={tok}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())