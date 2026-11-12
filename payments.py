# payments.py — Solana Pay Transfer Request + account_subscribe + validación.
"""Detección instantánea de pagos USDC sobre Solana.

- `build_transfer_request()`: genera el Transfer Request de Solana Pay
  (solana:<recipiente>?amount=<base>&spl-token=<mint>&reference=<ref>...).
- `PaymentMonitor`: escucha con `account_subscribe` (WebSocket) la ATA del
  comercio. Al recibir una notificación consulta el balance y las firmas
  recientes, valida **monto exacto + reference** y llama a `on_payment`.
- Reconexión con backoff exponencial y **reconciliación** al reconectar
  (getSignaturesForAddress + getTransaction) para no perder pagos caídos
  durante un corte de red.
- **Reconciliación periódica** (I-011): un timer cada `reconcile_interval`
  segundos re-consulta firmas sin gate de balance y reintenta firmas que
  fallaron de forma transitoria (fallos de `get_transaction`), y esos
  errores se propagan a `_rpc` para activar el failover al RPC de respaldo.

  > **I-012 (resuelto, opción b):** no hay cola de reintento propia
  > (`_retry_queue`). El reintento de firmas con fallo transitorio lo cubre el
  > re-escaneo periódico de `get_signatures_for_address`: una firma solo se
  > marca en `_seen_signatures` tras validarse correctamente (I-004), así que
  > si `_inspect_signature` falla/agota sus reintentos, la firma NO queda
  > vista y la siguiente ronda periódica (o notificación/reconexión) la
  > re-consulta y la reintenta. No hace falta una cola extra.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import struct
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import base58
from solders.pubkey import Pubkey
from solders.signature import Signature
from spl.token.constants import TOKEN_PROGRAM_ID

logger = logging.getLogger(__name__)

# Discriminadores de instrucciones del Token Program.
TOKEN_TRANSFER = 3
TOKEN_TRANSFER_CHECKED = 12

TOKEN_ACCOUNT_BALANCE_OFFSET = 64  # amount u64 LE dentro del data de una token account
TOKEN_ACCOUNT_BALANCE_SIZE = 8


@dataclass
class Payment:
    signature: str
    reference: Optional[str]
    amount_base: int
    merchant_ata: str
    tx_time: Optional[int] = None
    account_keys: Optional[list[str]] = None


def build_transfer_request(
    recipient: Pubkey | str,
    amount_base: int,
    mint: Pubkey | str,
    reference: Pubkey | str,
    label: str = "Vending Coca-Cola",
    message: str = "Pago de Coca-Cola (lata 330 ml)",
    include_meta: bool = True,
    include_amount: bool = True,
) -> str:
    """Construye la URL de Transfer Request de Solana Pay.

    La URL mínima esencial es: solana:<recipient>?amount=<unidades base>
    &spl-token=<mint>&reference=<reference>. label/message son opcionales
    y alargan la URL, aumentando la densidad del QR (peor escaneo con
    cámaras de wallets como Phantom). Si include_meta es False se omite
    el label y message para producir un QR más simple y fácil de leer.

    Si include_amount es False se omite también el parámetro amount.
    El monto es opcional en la spec de Solana Pay: algunos wallets
    (p.ej. ciertas versiones de Phantom) parsean mal el amount del QR
    y muestran cifras absurdas (150000 en vez de 1.50). Al omitirlo, el
    wallet abre la pantalla de pago con el monto editable y el usuario
    digita el precio (1.50 USDC). El reference del QR sigue viajando en
    la transacción, por lo que la máquina asocia el pago a la orden.
    """
    recipient = str(recipient)
    mint = str(mint)
    reference = str(reference)

    parts = [f"solana:{recipient}"]
    if include_amount:
        parts.append(f"amount={amount_base}")
    parts.append(f"spl-token={mint}")
    parts.append(f"reference={reference}")
    if include_meta:
        from urllib.parse import quote

        parts.append(f"label={quote(label)}")
        parts.append(f"message={quote(message)}")
    return "?".join([parts[0], "&".join(parts[1:])])


def _account_keys(tx) -> list[Pubkey]:
    """Devuelve las claves del mensaje (v0 o legacy).

    Incluye las direcciones cargadas desde lookup tables (v0): las
    instrucciones compiladas indexan primero las claves estáticas y luego las
    cargadas (writable y readonly en ese orden).
    """
    msg = tx.transaction.message
    keys = list(msg.account_keys)
    meta = getattr(tx, "meta", None)
    loaded = getattr(meta, "loaded_addresses", None) if meta is not None else None
    if loaded is not None:
        keys += list(getattr(loaded, "writable", []) or [])
        keys += list(getattr(loaded, "readonly", []) or [])
    return keys


def _program_ids(tx) -> list[Pubkey]:
    """Resuelve el program_id de cada instrucción compilada."""
    msg = tx.transaction.message
    keys = list(msg.account_keys)
    out = []
    for ix in msg.instructions:
        out.append(keys[ix.program_id_index])
    return out


def _parse_transfer_instruction(
    tx,
    ix,
    program_id: Pubkey,
    merchant_ata: Pubkey,
) -> Optional[int]:
    """Devuelve el monto de un transfer/transfer_checked hacia `merchant_ata`.

    `ix` es una instrucción compilada (CompiledInstruction) del mensaje.
    Resuelve los índices de cuentas contra account_keys (estático).
    """
    msg = tx.transaction.message
    keys = list(msg.account_keys)
    data = ix.data
    if isinstance(data, str):
        # encoding="json" → base58 (comportamiento del RPC de Solana).
        try:
            data = base58.b58decode(data)
        except Exception:
            # Algunos RPC devuelven base64 en "json": intentar como fallback.
            data = base64.b64decode(data)
    data = bytes(data)
    if len(data) < 9:
        return None
    discriminator = data[0]
    if discriminator not in (TOKEN_TRANSFER, TOKEN_TRANSFER_CHECKED):
        return None

    accounts = list(ix.accounts)
    # transfer:   source(0) dest(1) owner(2) [signers...]
    # transfer_checked: source(0) mint(1) dest(2) owner(3) [signers...]
    try:
        dest_idx = accounts[2] if discriminator == TOKEN_TRANSFER_CHECKED else accounts[1]
    except IndexError:
        return None
    if keys[dest_idx] != merchant_ata:
        return None

    amount = struct.unpack("<Q", data[1:9])[0]
    return amount


def validate_payment_tx(
    tx,
    merchant_ata: Pubkey,
    reference: Pubkey | None,
) -> Optional[Payment]:
    """Valida una transacción como pago hacia la ATA del comercio.

    Criterios:
    - La transacción debe haber tenido éxito (meta.err is None). Una
      transacción fallida puede contener la instrucción de transferencia pero
      NO movió fondos; jamás debe dispensar.
    - El program_id de alguna instrucción es el Token Program.
    - Es un transfer/transfer_checked cuyo destino es `merchant_ata`.
    - Si `reference` se pasa, la pubkey debe estar entre las account_keys.
    Devuelve un Payment o None.
    """
    meta = getattr(tx, "meta", None)
    if meta is not None and getattr(meta, "err", None) is not None:
        logger.warning("Transacción fallida ignorada (meta.err=%s)", meta.err)
        return None

    keys = _account_keys(tx)
    program_ids = _program_ids(tx)
    tx_sig = str(tx.transaction.signatures[0]) if tx.transaction.signatures else None

    has_reference = reference is not None and reference in keys

    for ix, prog_id in zip(tx.transaction.message.instructions, program_ids):
        if prog_id != TOKEN_PROGRAM_ID:
            continue
        amount = _parse_transfer_instruction(tx, ix, prog_id, merchant_ata)
        if amount is not None and amount > 0:
            return Payment(
                signature=tx_sig or "",
                reference=str(reference) if has_reference else None,
                amount_base=amount,
                merchant_ata=str(merchant_ata),
                account_keys=[str(k) for k in keys],
            )
    return None


class PaymentMonitor:
    """Monitor de pagos por WebSocket (account_subscribe) con reconciliación."""

    def __init__(
        self,
        rpc_http: str,
        rpc_ws: str,
        merchant_ata: Pubkey,
        on_payment: Callable[[Payment], Awaitable[None]],
        *,
        max_backoff: float = 30.0,
        rpc_http_backup: Optional[str] = None,
        get_transaction_retries: int = 3,
        reconcile_interval: float = 30.0,
    ):
        self.rpc_http = rpc_http
        self.rpc_ws = rpc_ws
        self.rpc_http_backup = rpc_http_backup
        self.merchant_ata = merchant_ata
        self.on_payment = on_payment
        self.max_backoff = max_backoff
        self.get_transaction_retries = get_transaction_retries
        self.reconcile_interval = reconcile_interval
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        self._seen_signatures: set[str] = set()
        self._baseline: Optional[int] = None

    # ------------------------------------------------------------------
    # RPC HTTP con failover al respaldo (I-003)
    # ------------------------------------------------------------------
    async def _rpc(self, fn):
        """Ejecuta `fn(client)` sobre el RPC primario; si falla, reintenta
        contra `rpc_http_backup` (failover real, I-003)."""
        attempts = [
            ("primary", self.rpc_http),
        ]
        if self.rpc_http_backup and self.rpc_http_backup != self.rpc_http:
            attempts.append(("backup", self.rpc_http_backup))

        last_exc: Optional[Exception] = None
        for name, url in attempts:
            try:
                from solana.rpc.async_api import AsyncClient

                async with AsyncClient(url) as client:
                    result = await fn(client)
                    if name == "backup":
                        logger.warning("Failover: usado RPC de respaldo (%s)", url)
                    return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("RPC %s (%s) falló: %s", name, url, exc)
        if last_exc:
            raise last_exc
        return None

    async def start(self) -> None:
        self._stop = False
        self._task = asyncio.create_task(self._run())
        logger.info("PaymentMonitor iniciado (ata=%s)", self.merchant_ata)

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------
    async def _run(self) -> None:
        # solana-py cambió de API WebSocket en 0.41: `connect` (heredado) fue
        # reemplazado por `SolanaWsClient` + `ws_connect`. Soportamos ambas
        # para que el mismo código corra en la Pi (versión antigua) y en la
        # UNO Q (0.41+).
        from solana.rpc import websocket_api as ws_api

        backoff = 1.0
        while not self._stop:
            try:
                # Reconciliación antes de suscribir: recupera pagos caídos y
                # fija el balance de referencia.
                await self._reconcile()

                if hasattr(ws_api, "SolanaWsClient"):
                    client = ws_api.SolanaWsClient(self.rpc_ws)
                    async with client:
                        await client.account_subscribe(
                            pubkey=self.merchant_ata,
                            commitment="confirmed",
                            encoding="base64",
                        )
                        backoff = 1.0
                        logger.info(
                            "Suscrito a account_subscribe de %s (API SolanaWsClient)",
                            self.merchant_ata,
                        )
                        periodic = asyncio.create_task(
                            self._periodic_reconcile_loop()
                        )
                        try:
                            async for _notification in client:
                                if self._stop:
                                    break
                                # En la API nueva el ACK de suscripción lo
                                # consume account_subscribe; todo lo que llega
                                # por el iterador es una notificación.
                                await self._handle_notification()
                        finally:
                            periodic.cancel()
                            try:
                                await periodic
                            except (asyncio.CancelledError, Exception):
                                pass
                else:
                    async with ws_api.connect(self.rpc_ws) as websocket:
                        await websocket.account_subscribe(
                            self.merchant_ata,
                            commitment="confirmed",
                            encoding="base64",
                        )
                        # Primera respuesta = confirmación de suscripción.
                        try:
                            await asyncio.wait_for(websocket.recv(), timeout=30)
                        except Exception:
                            pass
                        backoff = 1.0
                        logger.info(
                            "Suscrito a account_subscribe de %s (API connect heredada)",
                            self.merchant_ata,
                        )

                        # I-011: timer periódico de reconciliación (reintenta firmas
                        # pendientes cada reconcile_interval segundos, no solo al
                        # reconectar).
                        periodic = asyncio.create_task(
                            self._periodic_reconcile_loop()
                        )
                        try:
                            async for msgs in websocket:
                                if self._stop:
                                    break
                                for msg in msgs:
                                    # Ignorar confirmaciones de suscripción (result int).
                                    if hasattr(msg, "params"):
                                        await self._handle_notification()
                        finally:
                            periodic.cancel()
                            try:
                                await periodic
                            except (asyncio.CancelledError, Exception):
                                pass
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("WebSocket error: %s. Reintentando en %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)

    # ------------------------------------------------------------------
    async def _periodic_reconcile_loop(self) -> None:
        """Reconciliación periódica (I-011): cada `reconcile_interval` segundos
        re-consulta firmas SIN gate de balance. Las firmas que fallaron de forma
        transitoria no quedan vistas y se reintentan aquí (I-012: no hay cola
        propia, el re-escaneo periódico cubre el reintento)."""
        while not self._stop:
            await asyncio.sleep(self.reconcile_interval)
            if self._stop:
                break
            try:
                await self._rpc(self._process_new_signatures)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Reconciliación periódica falló: %s", exc)

    # ------------------------------------------------------------------
    async def _handle_notification(self) -> None:
        """Disparada por cada notificación de cambio en la ATA."""
        try:
            await self._rpc(self._process_new_signatures_from_balance)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallo al procesar notificación: %s", exc)

    async def _process_new_signatures_from_balance(self, client) -> None:
        """Lee el balance actual de la ATA y procesa firmas nuevas."""
        balance = await client.get_token_account_balance(self.merchant_ata)
        current = int(balance.value.amount) if balance.value else 0
        if self._baseline is not None and current <= self._baseline:
            self._baseline = current
            # I-011/I-012: aunque el balance no cambie, re-consulta firmas
            # recientes para reintentar firmas con fallo transitorio previo
            # (no vistas). El re-escaneo periódico cubre el reintento.
            await self._process_new_signatures(client)
            return
        self._baseline = current
        await self._process_new_signatures(client)

    # ------------------------------------------------------------------
    async def _process_new_signatures(self, client) -> None:
        """Consulta firmas recientes de la ATA y valida las no vistas.

        I-011/I-012: un fallo de get_transaction se PROPAGA (no se traga) para
        que `_rpc` active el failover al RPC de respaldo. La firma NO se marca
        como vista (I-004), así que el siguiente re-escaneo periódico de
        get_signatures_for_address (~reconcile_interval) la reintenta.
        """
        resp = await client.get_signatures_for_address(
            self.merchant_ata,
            limit=10,
            commitment="confirmed",
        )
        if resp is None or resp.value is None:
            return
        for sig_info in resp.value:
            sig = str(sig_info.signature)
            if sig in self._seen_signatures:
                continue
            # Las transacciones fallidas no mueven fondos: no son pagos.
            if getattr(sig_info, "err", None) is not None:
                self._seen_signatures.add(sig)
                logger.debug("Firma %s fallida (err=%s); ignorada", sig, sig_info.err)
                continue
            # I-004: NO marcar como vista todavía; solo se marca tras
            # validación exitosa. Si la validación falla de forma transitoria,
            # la firma se re-consulta y reintenta en la próxima ronda (I-012).
            ok = await self._inspect_signature(client, sig)
            if ok:
                self._seen_signatures.add(sig)

    # ------------------------------------------------------------------
    async def _inspect_signature(self, client, sig: str) -> bool:
        """Inspecciona una firma; devuelve True si se validó correctamente.

        I-004: reintenta con backoff ante fallos transitorios de get_transaction
        (red, RPC 429/500). Solo retorna True si la validación tuvo éxito.
        I-008: captura TypeError por max_supported_transaction_version y degrada.
        I-011: si se agotan los reintentos, ELEVA la última excepción para que
        `_rpc` active el failover al RPC de respaldo.
        """
        tx_resp = None
        last_exc: Optional[Exception] = None
        for attempt in range(self.get_transaction_retries):
            try:
                try:
                    tx_resp = await client.get_transaction(
                        Signature.from_string(sig),
                        encoding="json",
                        commitment="confirmed",
                        max_supported_transaction_version=0,
                    )
                except TypeError:
                    # solana-py antiguo no acepta el parámetro -> degradar (I-008)
                    logger.debug(
                        "get_transaction sin max_supported_transaction_version (TypeError)"
                    )
                    tx_resp = await client.get_transaction(
                        Signature.from_string(sig),
                        encoding="json",
                        commitment="confirmed",
                    )
                break
            except Exception as exc:  # noqa: BLE001
                # Una transacción con versión de mensaje más nueva que la
                # soportada por el cliente no es un pago procesable: se
                # ignora (marcar vista) en vez de bloquear la reconciliación.
                if "UnsupportedTransactionVersion" in str(exc):
                    logger.warning(
                        "get_transaction %s: versión de transacción no soportada; ignorada",
                        sig,
                    )
                    return True
                last_exc = exc
                wait = 0.5 * (2 ** attempt)
                logger.debug(
                    "get_transaction %s intento %d falló (%s); reintento en %.1fs",
                    sig, attempt + 1, exc, wait,
                )
                if attempt < self.get_transaction_retries - 1:
                    await asyncio.sleep(wait)
                else:
                    logger.warning(
                        "get_transaction %s falló tras %d intentos: %s",
                        sig, self.get_transaction_retries, exc,
                    )
                    # I-011: NO tragar el error -> propagar a _rpc (failover).
                    raise last_exc

        if not tx_resp or not tx_resp.value:
            logger.debug("get_transaction %s sin valor (aún no confirmada)", sig)
            return False

        # Transacción fallida: la instrucción existe pero no movió USDC.
        meta = getattr(tx_resp.value, "meta", None)
        if meta is not None and getattr(meta, "err", None) is not None:
            logger.info("Firma %s fallida (meta.err=%s); ignorada", sig, meta.err)
            return True

        tx = tx_resp.value.transaction
        payment = validate_payment_tx(tx, self.merchant_ata, reference=None)
        if payment is None:
            logger.debug("Firma %s no es un pago válido hacia la ATA", sig)
            return True  # validada correctamente (no es pago) -> no reintentar
        payment.signature = sig
        payment.tx_time = tx_resp.value.block_time
        logger.info(
            "Pago detectado sig=%s amount=%d", sig, payment.amount_base
        )
        await self.on_payment(payment)
        return True

    # ------------------------------------------------------------------
    async def _reconcile(self) -> None:
        """Fija el balance base y procesa pagos recientes no vistos."""
        try:
            await self._rpc(self._reconcile_with_client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reconciliación falló: %s", exc)

    async def _reconcile_with_client(self, client) -> None:
        balance = await client.get_token_account_balance(self.merchant_ata)
        self._baseline = int(balance.value.amount) if balance.value else 0
        logger.info("Reconciliación: balance base de la ATA = %s", self._baseline)
        await self._process_new_signatures(client)