#!/usr/bin/env python3
# scripts/devnet_e2e_test.py — Prueba end-to-end del flujo de pago en devnet.
"""Prueba el flujo completo SIN depender del faucet de Circle:

1. Crea un token SPL propio (6 decimales) en devnet.
2. Airdroppea SOL a un wallet de prueba (comercio de test) y a un comprador.
3. Arranca la app (uvicorn) con USDC_MINT = token de prueba y WALLET_PATH propio.
4. Crea una orden vía HTTP, parsea el Transfer Request Solana Pay.
5. Paga con un transfer_checked que incluye el `reference` (como hace Phantom).
6. Comprueba que la app detecta el pago y marca la orden pagada/dispensada.
7. Opcional: prueba el endpoint de dispensado manual.

Uso:
    python scripts/devnet_e2e_test.py [--port 8000] [--keep-app]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from solana.rpc.async_api import AsyncClient  # noqa: E402
from solders.instruction import AccountMeta, Instruction  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.message import MessageV0  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.system_program import create_account, CreateAccountParams  # noqa: E402
from solders.sysvar import RENT  # noqa: E402
from solders.transaction import VersionedTransaction  # noqa: E402
from spl.token.constants import TOKEN_PROGRAM_ID  # noqa: E402
from spl.token.instructions import (  # noqa: E402
    create_associated_token_account,
    initialize_mint,
    mint_to,
    transfer_checked,
)
from spl.token.models import (  # noqa: E402
    InitializeMintParams,
    MintToParams,
    TransferCheckedParams,
)
from solders.token.associated import get_associated_token_address  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("e2e")

LAMPORTS_PER_SOL = 1_000_000_000
MINT_SPACE = 82
DECIMALS = 6
AMOUNT = 1_500_000  # 1.50 USDC en unidades base


# ----------------------------------------------------------------------
# Helpers de transacción (devnet)
# ----------------------------------------------------------------------
async def _airdrop(client, pubkey, lamports, tries=5) -> bool:
    for i in range(tries):
        try:
            r = await client.request_airdrop(pubkey, lamports)
            if r.value:
                logger.info("Airdrop OK %s (+%.1f SOL)", pubkey, lamports / LAMPORTS_PER_SOL)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Airdrop intento %d falló: %s", i + 1, exc)
        await asyncio.sleep(2)
    return False


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


async def create_mint(client, payer: Keypair) -> Pubkey:
    rent = await client.get_minimum_balance_for_rent_exemption(MINT_SPACE)
    mint = Keypair()
    ix_create = create_account(
        CreateAccountParams(
            from_pubkey=payer.pubkey(),
            to_pubkey=mint.pubkey(),
            lamports=rent.value,
            space=MINT_SPACE,
            owner=TOKEN_PROGRAM_ID,
        )
    )
    ix_init = initialize_mint(
        InitializeMintParams(
            program_id=TOKEN_PROGRAM_ID,
            mint=mint.pubkey(),
            decimals=DECIMALS,
            mint_authority=payer.pubkey(),
            freeze_authority=None,
        )
    )
    blockhash = (await client.get_latest_blockhash()).value.blockhash
    msg = MessageV0.try_compile(
        payer=payer.pubkey(),
        instructions=[ix_create, ix_init],
        address_lookup_table_accounts=[],
        recent_blockhash=blockhash,
    )
    tx = VersionedTransaction(msg, [payer, mint])
    await client.send_raw_transaction(bytes(tx))
    logger.info("Mint creado: %s", mint.pubkey())
    return mint.pubkey()


async def create_ata(client, payer: Keypair, owner: Pubkey, mint: Pubkey) -> Pubkey:
    ata = get_associated_token_address(owner, mint)
    info = await client.get_account_info_json_parsed(ata)
    if info.value is not None:
        return ata
    ix = create_associated_token_account(
        payer=payer.pubkey(), owner=owner, mint=mint, token_program_id=TOKEN_PROGRAM_ID
    )
    await _send(client, payer, [ix])
    return ata


async def mint_tokens(client, payer: Keypair, mint: Pubkey, dest_ata: Pubkey, amount: int) -> None:
    ix = mint_to(
        MintToParams(
            program_id=TOKEN_PROGRAM_ID,
            mint=mint,
            dest=dest_ata,
            mint_authority=payer.pubkey(),
            amount=amount,
            signers=[],
        )
    )
    await _send(client, payer, [ix])
    logger.info("Minteados %d tokens hacia %s", amount, dest_ata)


async def pay_transfer_checked(
    client,
    payer: Keypair,
    source_ata: Pubkey,
    dest_ata: Pubkey,
    mint: Pubkey,
    amount: int,
    reference: Pubkey,
) -> str:
    """Simula lo que Phantom hace: transfer_checked + reference en account metas."""
    ix = transfer_checked(
        TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=source_ata,
            mint=mint,
            dest=dest_ata,
            owner=payer.pubkey(),
            amount=amount,
            decimals=DECIMALS,
            signers=[],
        )
    )
    # Solana Pay: el reference se añade como cuenta readonly no firmante.
    # Nota: solders devuelve una COPIA de accounts; hay que reconstruir la instrucción.
    from solders.instruction import Instruction

    ix = Instruction(
        program_id=ix.program_id,
        data=ix.data,
        accounts=list(ix.accounts)
        + [AccountMeta(pubkey=reference, is_signer=False, is_writable=False)],
    )
    return await _send(client, payer, [ix])


# ----------------------------------------------------------------------
# Flujo principal
# ----------------------------------------------------------------------
async def run(args) -> int:
    rpc_http = os.getenv("RPC_HTTP", "https://api.devnet.solana.com")
    port = args.port

    logger.info("RPC: %s", rpc_http)
    async with AsyncClient(rpc_http) as client:
        # 1) Wallets de prueba
        buyer = Keypair()
        merchant_test = Keypair()
        logger.info("Comprador: %s", buyer.pubkey())
        logger.info("Comercio de test: %s", merchant_test.pubkey())

        await _airdrop(client, buyer.pubkey(), 2 * LAMPORTS_PER_SOL)
        await _airdrop(client, merchant_test.pubkey(), 2 * LAMPORTS_PER_SOL)

        # 2) Token SPL propio
        mint = await create_mint(client, merchant_test)
        buyer_ata = await create_ata(client, buyer, buyer.pubkey(), mint)
        await mint_tokens(client, merchant_test, mint, buyer_ata, 10_000_000)

        # 3) Preparar entorno para la app (wallet propio + mint de test)
        wallet_test = BASE_DIR / "wallet_test.json"
        wallet_test.write_text(json.dumps(list(bytes(merchant_test))))
        env = os.environ.copy()
        env.update(
            {
                "WALLET_PATH": str(wallet_test),
                "USDC_MINT": str(mint),
                "USDC_DECIMALS": str(DECIMALS),
                # "stub" para host sin hardware; "unoq"/"gpiozero" para probar
                # el pulso real del relé durante el E2E.
                "DISPENSER_MODE": args.dispenser_mode,
                "TEST_TOKEN": "e2etest",
                "RPC_HTTP": rpc_http,
                "RPC_WS": os.getenv("RPC_WS", "wss://api.devnet.solana.com"),
            }
        )

        # 4) Arrancar la app
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        app_url = f"http://127.0.0.1:{port}"
        try:
            await _wait_health(app_url, timeout=60)

            # 5) Crear orden (carrito v2: items[])
            async with httpx.AsyncClient(timeout=30) as h:
                r = await h.post(f"{app_url}/order", json={"items": [{"sku": "coca-cola"}]})
                r.raise_for_status()
                order = r.json()
                amount_base = int(order["total_amount_base"])
                logger.info("Orden creada: %s (ref=%s, total=%d)",
                            order["order_id"], order["reference"], amount_base)

                # Verificar Transfer Request Solana Pay
                tr = order["transfer_request"]
                assert tr.startswith("solana:"), f"Transfer Request inválido: {tr}"
                parsed = urllib.parse.urlparse(tr)
                q = urllib.parse.parse_qs(parsed.query)
                recipient = Pubkey.from_string(parsed.netloc)
                # Decisión de la referencia: el QR omite `amount` a propósito
                # (Phantom parsea mal el monto); debe seguir estando spl-token
                # y reference.
                assert "amount" not in q, f"El QR no debe incluir amount: {tr}"
                assert q["spl-token"][0] == str(mint)
                reference = Pubkey.from_string(q["reference"][0])
                logger.info("Transfer Request OK: %s", tr[:80] + "…")

                # 6) Pagar (como haría Phantom)
                merchant_ata = get_associated_token_address(recipient, mint)
                t0 = time.monotonic()
                sig = await pay_transfer_checked(
                    client, buyer, buyer_ata, merchant_ata, mint, amount_base, reference
                )
                logger.info("Pago enviado: %s", sig)

                # 7) Esperar detección
                elapsed = await _wait_order(app_url, order["order_id"], timeout=40)
                if elapsed is None:
                    logger.error("La app NO detectó el pago a tiempo")
                    return 1
                logger.info("Detección del pago en %.2f s", elapsed)

                # 8) Endpoint de dispensado manual (stub)
                rd = await h.post(
                    f"{app_url}/api/test/dispense",
                    json={"pin": args.pin, "seconds": 1.0},
                    headers={"X-Test-Token": "e2etest"},
                )
                rd.raise_for_status()
                logger.info("Dispensado manual OK: %s", rd.json())

                # 9) healthz
                hz = await h.get(f"{app_url}/healthz")
                hz.raise_for_status()
                logger.info("healthz: %s", hz.json()["status"])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    return 0


async def _wait_health(url: str, timeout: int = 60) -> None:
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=10) as h:
        while time.monotonic() - t0 < timeout:
            try:
                r = await h.get(f"{url}/healthz")
                if r.status_code == 200:
                    logger.info("App lista (healthz OK)")
                    return
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1)
    raise RuntimeError("La app no arrancó a tiempo")


async def _wait_order(url: str, order_id: str, timeout: int = 40):
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=10) as h:
        while time.monotonic() - t0 < timeout:
            try:
                r = await h.get(f"{url}/order/{order_id}")
                st = r.json()["status"]
                if st in ("pagada", "dispensada"):
                    logger.info("Estado final de la orden: %s", st)
                    return time.monotonic() - t0
                if st in ("expirada", "monto_incorrecto"):
                    logger.error("Orden en estado %s", st)
                    return None
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E devnet de vending-solana")
    parser.add_argument("--port", type=int, default=8199)
    parser.add_argument(
        "--dispenser-mode", default="stub", choices=["stub", "unoq", "gpiozero", "auto"],
        help="Backend del dispensador para la prueba (unoq = pulso real en la UNO Q)",
    )
    parser.add_argument("--pin", type=int, default=7, help="Pin del pulso de prueba")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())