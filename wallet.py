# wallet.py — Wallet del comercio: keypair, modo solo-pubkey y ATA de USDC.
"""Wallet del comercio con dos modos de operación.

Por qué existen dos modos: para **cobrar** no hace falta la clave privada. El
``pubkey`` del comercio se usa como ``recipient`` del Transfer Request Solana
Pay y para derivar la ATA de USDC (que es una función pura sobre el pubkey).
Firmar solo es necesario para crear la ATA on-chain (rent) o para refunds.

- Modo ``keypair``   : hay ``wallet.json`` (formato lista de enteros o base58).
  Comportamiento de la Raspberry Pi.
- Modo ``pubkey``    : no hay fichero, pero hay ``MERCHANT_PUBKEY`` en
  config/.env. El dispositivo **no contiene ninguna clave privada** (mejor
  seguridad). Es el modo recomendado para la UNO Q.

La clave privada nunca se registra en logs ni se devuelve fuera de este módulo.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from spl.token.constants import TOKEN_PROGRAM_ID
from solders.token.associated import get_associated_token_address

logger = logging.getLogger(__name__)

# Tamaño mínimo de cuenta de mint (82 bytes) para rent-exemption.
MINT_ACCOUNT_SPACE = 82


@dataclass
class MerchantWallet:
    """Wallet del comercio resuelta (pubkey siempre; keypair solo si hay fichero)."""

    pubkey: Pubkey
    keypair: Optional[Keypair] = None
    source: str = "keypair"  # "keypair" | "pubkey"

    @property
    def can_sign(self) -> bool:
        return self.keypair is not None

    def mode_label(self) -> str:
        return "keypair" if self.can_sign else "pubkey-only"


def load_or_create_keypair(path: Path | str) -> Keypair:
    """Carga el keypair existente o crea uno nuevo en `path` (permisos 0600)."""
    path = Path(path)
    if path.exists():
        raw = json.loads(path.read_text())
        if isinstance(raw, str):  # base58
            return Keypair.from_base58_string(raw)
        if isinstance(raw, list):
            return Keypair.from_bytes(bytes(raw))
        raise ValueError(f"Formato de keypair no reconocido en {path}")

    kp = Keypair()  # keypair aleatorio nuevo
    path.parent.mkdir(parents=True, exist_ok=True)
    # formato estándar solana CLI: lista de enteros (bytes de la keypair)
    path.write_text(json.dumps(list(bytes(kp))))
    os.chmod(path, 0o600)
    logger.info("Keypair nuevo generado en %s (pubkey=%s)", path, kp.pubkey())
    return kp


def load_merchant(cfg) -> MerchantWallet:
    """Resuelve la wallet del comercio según la config.

    Orden: ``wallet.json`` (keypair) > ``merchant_pubkey`` (solo pubkey) >
    generar keypair nuevo (comportamiento de la referencia).
    """
    path = Path(cfg.wallet_abs_path)
    if path.exists():
        kp = load_or_create_keypair(path)
        logger.info("Wallet del comercio: modo keypair (%s)", kp.pubkey())
        return MerchantWallet(pubkey=kp.pubkey(), keypair=kp, source="keypair")

    pubkey_str = (getattr(cfg, "merchant_pubkey", "") or "").strip()
    if pubkey_str:
        pubkey = Pubkey.from_string(pubkey_str)
        logger.info("Wallet del comercio: modo SOLO PUBKEY (%s); sin clave privada", pubkey)
        return MerchantWallet(pubkey=pubkey, keypair=None, source="pubkey")

    logger.warning(
        "Sin %s ni MERCHANT_PUBKEY: se genera un keypair nuevo (modo keypair).", path
    )
    kp = load_or_create_keypair(path)
    return MerchantWallet(pubkey=kp.pubkey(), keypair=kp, source="keypair")


def derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """Deriva (sin crear) la ATA de `owner` para `mint`."""
    return get_associated_token_address(owner, mint)


async def get_or_create_ata(client, payer: Keypair, owner: Pubkey, mint: Pubkey) -> tuple[Pubkey, bool]:
    """Devuelve (ata, created). Crea la ATA en devnet si no existe.

    `payer` firma y paga el rent (necesita SOL).
    """
    from solana.rpc.async_api import AsyncClient
    from solders.message import MessageV0
    from solders.transaction import VersionedTransaction
    from spl.token.instructions import create_associated_token_account

    ata = derive_ata(owner, mint)

    info = await client.get_account_info_json_parsed(ata)
    if info.value is not None:
        return ata, False

    logger.info("Creando ATA %s para mint %s", ata, mint)
    ix = create_associated_token_account(
        payer=payer.pubkey(),
        owner=owner,
        mint=mint,
        token_program_id=TOKEN_PROGRAM_ID,
    )
    blockhash = (await client.get_latest_blockhash()).value.blockhash
    msg = MessageV0.try_compile(
        payer=payer.pubkey(),
        instructions=[ix],
        address_lookup_table_accounts=[],
        recent_blockhash=blockhash,
    )
    tx = VersionedTransaction(msg, [payer])
    resp = await client.send_raw_transaction(bytes(tx))
    return ata, True
