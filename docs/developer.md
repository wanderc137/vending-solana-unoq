# Developer — Vending Solana en Arduino UNO Q (código fuente)

## 1. Arquitectura

```
        Cliente (navegador)                  Arduino UNO Q
 ┌──────────────────────────┐      ┌──────────────────────────────────────┐
 │  index.html (catálogo +  │      │  Linux (Debian 13, aarch64)          │
 │  carrito)                │ HTTP │  uvicorn :8000                       │
 │  payment.html (QR +      │─────▶│  app.py (FastAPI)                    │
 │  cronómetro)             │      │    │ POST /order                     │
 └──────────────────────────┘      │    │ GET  /order/{id}/payment        │
                                   │    ▼                                 │
                                   │  payments.py  PaymentMonitor         │
                                   │   ├─ WebSocket account_subscribe     │
                                   │   └─ reconciliación cada 2 s         │
                                   │    ▼                                 │
                                   │  _on_payment → valida monto/ref      │
                                   │    ▼                                 │
                                   │  dispenser.py (fachada)              │
                                   │   └─ backend unoq → mcu.py           │
                                   │        TCP 127.0.0.1:7500            │
                                   │        │                             │
                                   │  ┌─────▼──────────────────────────┐  │
                                   │  │ MCU STM32U585                  │  │
                                   │  │  D7 ──▶ relé/motor (2,5 s)     │  │
                                   │  │  LEDs user (sysfs)             │  │
                                   │  └────────────────────────────────┘  │
                                   └──────────────────────────────────────┘
```

La diferencia clave con la Raspberry Pi: el Linux de la UNO Q **no expone el
header Arduino**. El acceso a pines se hace por el servicio `arduino-router`, que
publica un bridge TCP en `127.0.0.1:7500` y ejecuta el sketch del MCU
(`firmware/unoq_gpio_control.ino`).

## 2. Módulos

### `app.py` — web app FastAPI

- **`POST /order`** con `{"items":[{"sku":"coca-cola"},{"sku":"sprite"}]}`:
  1. Throttle por IP (5 órdenes / 5 s).
  2. Valida SKU contra `catalog.yaml`; rechaza carrito vacío y duplicados.
  3. **Recalcula el total** desde el catálogo; nunca confía en el cliente.
  4. Genera `reference = str(Pubkey.new_unique())`.
  5. Crea la orden con `items`, `total_amount_base`, `total_usdc` y
     `expires_at = now + order_timeout_seconds`.
  6. Construye el Transfer Request **sin `amount`** y **sin metadatos**
     (`build_transfer_request(..., include_meta=False, include_amount=False)`).
  7. Devuelve `order_id`, `items`, `total`, `reference`, `transfer_request`,
     `qr_base64` y `timeout_s`.
- **`_on_payment(payment)`**:
  1. Busca una orden pendiente cuyo `reference` esté en `payment.account_keys`.
  2. Si no hay referencia, empareja por monto (`AMOUNT_TOLERANCE = 10_000`,
     ±0,01 USDC) con la orden pendiente más antigua.
  3. Valida el monto, marca `pagada` y guarda `signature`/`paid_amount`.
  4. Fuera del lock, llama `_dispatch_dispense(order_id)`.
- **`_dispatch_dispense`**: recorre los ítems, ejecuta un pulso por ítem y espera
  1 s entre bebidas; al terminar marca `dispensada`. Si falla, deja `pagada` y
  enciende el LED de error.
- **`_expiry_sweeper`**: cada 5 s marca `expirada` las órdenes vencidas y libera el
  `reference`.
- **Endpoints de prueba** (protegidos por `X-Test-Token`):
  - `POST /api/test/dispense`: pulso manual.
  - `POST /api/test/simulate-payment/{order_id}`: crea un `Payment` sintético y
    ejecuta el mismo camino que un pago real. Con `TEST_TOKEN` vacío → 403.
- **`/healthz`**: estado, red, RPC, wallet, ATA, mint, backend GPIO (pin, pulso,
  polaridad, estado del bridge), LEDs y monitor.

### `payments.py` — Solana Pay y detección

- `build_transfer_request(recipient, amount_base, mint, reference,
  include_meta, include_amount)`:
  - `include_amount=False` omite `amount` (evita bugs de parseo en algunas wallets).
  - `include_meta=False` acorta el QR.
- `PaymentMonitor(rpc_http, rpc_ws, merchant_ata, on_payment, rpc_http_backup=None,
  reconcile_interval=2.0)`:
  - **WebSocket**: en solana-py ≥ 0.37 usa `SolanaWsClient` +
    `account_subscribe(pubkey=..., commitment=..., encoding=...)`; conserva la rama
    antigua `websocket_api.connect` para la Raspberry Pi.
  - **Reconciliación** periódica de firmas como respaldo del WebSocket.
  - **Failover** al RPC de respaldo.
  - `validate_payment_tx` decodifica la tx con `get_transaction`, resuelve claves
    estáticas **y** `meta.loaded_addresses` (transacciones v0 con lookup tables), y
    extrae `amount_base`, `reference` y `account_keys` de la instrucción
    `transfer_checked` (discriminante 12) del Token Program.
  - **Transacciones fallidas**: si `meta.err` no es nulo, se ignoran (no se marcan
    como pago). Las firmas con `UnsupportedTransactionVersion` también se ignoran y
    se marcan vistas para no bloquear la reconciliación.

### `dispenser.py` — fachada GPIO

Selección de backend (`auto`):

1. **`unoq`**: si existe el bridge en `MCU_HOST:MCU_PORT`, usa `mcu.py`; los LEDs
   se manejan por `unoq_leds.py`.
2. **`gpiozero`**: en Raspberry Pi u otros hosts con GPIO; usa `gpiozero` +
   `LGPIOFactory` y LEDs en pines BCM.
3. **`stub`**: si no hay hardware, registra el motivo y permite desarrollar la web.

API:

- `probe()` — comprueba el backend.
- `dispense(pin=None, seconds=None)` — pulso asíncrono (hilo de trabajo).
- `set_led("ready"|"paid"|"error", on)` — LEDs.
- `cleanup()` — libera recursos.

**Fail-safe**: al inicializar el backend `unoq`, tras el `probe()` se escribe el pin
de dispensado a OFF; en `gpiozero` se hace `self._relay.off()` tras crear el
`OutputDevice`. Así un reinicio o crash no deja el relé energizado.

### `mcu.py` — cliente del bridge

- `McuLink(host, port, timeout_s)` con lock de exclusión y reintento del primer
  `READ` vacío (particularidad del bridge).
- Comandos: `READ <pin>`, `WRITE <pin> 0|1`, `ANALOG <pin>`, `PWM <pin> <val>`.
- `pulse(pin, seconds, active_high)` escribe el nivel activo, espera y vuelve al
  nivel inactivo; `probe()` valida el enlace.
- Pines 0–19: D0–D13 = 0–13, A0–A5 = 14–19.

### `unoq_leds.py` — LEDs de placa

- Controla `/sys/class/leds/{green,blue,red}:user`.
- Al inicializar cambia el `trigger` a `none` y escribe `brightness` 0/1.
- `status()` expone disponibilidad y último error para `/healthz`.

### `wallet.py`

- `load_or_create_keypair(wallet_path)`: carga/crea keypair (0600).
- **Modo solo-pubkey**: si no hay `wallet.json` pero sí `MERCHANT_PUBKEY`, deriva la
  ATA USDC sin necesitar la clave privada. Es el modo usado en la UNO Q.
- `derive_ata(owner, mint)`.

### `config.py`

- Carga `config.yaml`, `.env` y variables de entorno; prioridad
  **env > .env > config.yaml**.
- Campos específicos: `dispense_pin`, `dispenser_mode`, `mcu_host`, `mcu_port`,
  `mcu_timeout_ms`, `led_ready`, `led_paid`, `led_error`.

## 3. Modelo de datos

```python
@dataclass
class OrderItem:
    sku: str
    product_name: str
    price_usdc: float
    amount_base: int
    dispensed: bool = False

@dataclass
class Order:
    order_id: str
    items: list[OrderItem]
    total_amount_base: int   # suma de amount_base
    total_usdc: float
    reference: str
    status: str              # pendiente|pagada|dispensada|expirada|cancelada|monto_incorrecto
    created_at / expires_at / signature / paid_amount
```

Estados: `pendiente` → `pagada` → `dispensada`; también `expirada`, `cancelada` y
`monto_incorrecto`.

## 4. Endpoints

| Método | Ruta | Uso |
|---|---|---|
| GET | `/` | Catálogo + carrito |
| POST | `/order` | Crear orden multi-ítem |
| GET | `/order/{id}` | Estado (polling) |
| GET | `/order/{id}/qr` | PNG del QR |
| GET | `/order/{id}/payment` | Página de pago |
| GET | `/order/{id}/confirmed` | Celebración |
| GET | `/order/{id}/expired` | Expirada |
| POST | `/order/{id}/cancel` | Cancelar (solo pendiente) |
| GET | `/order/{id}/cancelled` | Cancelada |
| GET | `/healthz` | Salud |
| POST | `/api/test/dispense` | Pulso manual (`X-Test-Token`) |
| POST | `/api/test/simulate-payment/{id}` | Pago simulado (`X-Test-Token`) |

## 5. Firmware del MCU

`firmware/unoq_gpio_control.ino` implementa el protocolo de texto del bridge:

| Comando | Respuesta | Efecto |
|---|---|---|
| `READ <pin>` | `0` / `1` | `digitalRead` |
| `WRITE <pin> 0\|1` | `OK` | `pinMode(OUTPUT)` + `digitalWrite` + lectura de confirmación |
| `ANALOG <pin>` | `0..1023` | `analogRead` |
| `PWM <pin> <val>` | `OK` | `analogWrite` |

En `setup()` los pines quedan en `INPUT` y los PWM en LOW. El sketch se flashea con
Arduino IDE/CLI desde el PC; el Linux de la UNO Q solo habla con el bridge.

## 6. Despliegue y operación

- `scripts/deploy_unoq.sh` (PC): empaqueta con `tar` sobre ADB, excluyendo `.venv`,
  `wheels`, `__pycache__`, `.git`, `.env` y `wallet.json`; `--wheels` envía las
  ruedas; reinicia el servicio salvo `--no-restart`.
- `scripts/setup_unoq.sh` (placa): crea `.venv --without-pip` (la imagen no trae
  `ensurepip`), bootstrapea pip desde `wheels/pip-*.whl`, instala offline y crea
  `.env`; con `--systemd` instala la unidad.
- `deploy/vending-unoq.service`: `User=arduino`, `Restart=always`,
  `NoNewPrivileges=yes`, `MemoryMax=700M`, `Requires=arduino-router.service`,
  `WorkingDirectory=/home/arduino/vending-solana-unoq`,
  `ExecStart=.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1`.

## 7. Pruebas

```bash
# Sintaxis
.venv/bin/python -m compileall -q .

# Bridge del MCU
.venv/bin/python scripts/mcu_probe.py

# Relé (D7)
.venv/bin/python scripts/dispense_test.py --pin 7 --seconds 1.0

# Salud
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool

# Flujo sin fondos (requiere TEST_TOKEN en .env)
curl -s -X POST http://127.0.0.1:8000/order \
  -H 'Content-Type: application/json' -d '{"items":[{"sku":"coca-cola"}]}'
curl -s -X POST http://127.0.0.1:8000/api/test/simulate-payment/<ORDER_ID> \
  -H "X-Test-Token: $TEST_TOKEN"
```

Resultados de la migración:

- `compileall`: OK.
- Servicio activo; `/healthz` con backend `unoq`, `available: true`, `stub: false`.
- Orden → pago simulado → pulso real D7 (2,5 s) → `dispensada`: OK.
- LEDs `green:user`, `blue:user`, `red:user`: OK.
- `mcu_probe.py`: `READ D7 = 0`, `ANALOG A0` con variación: OK.
- Parser de pagos reales mainnet: 1,50019 / 1,000145 / 0,900125 USDC: OK.
- Tx fallida y tx v1 no soportada: ignoradas correctamente.
- E2E on-chain devnet: no ejecutado (sin internet en la placa y faucet agotado).

## 8. Cómo extender

- **Nueva bebida**: añade la entrada en `catalog.yaml` (sku, nombre, `price_usdc`,
  `price_base`, `pulse_seconds`, `dispense_pin`, `image`) y coloca la imagen en
  `static/`.
- **Precio**: edita `catalog.yaml` y reinicia `vending-unoq`.
- **Múltiples motores**: el catálogo ya admite `dispense_pin` por producto; adapta
  `_dispatch_dispense` para usar el pin del ítem en lugar del global.
- **Otro hardware GPIO**: implementa un backend nuevo en `dispenser.py` siguiendo la
  interfaz `probe`/`dispense`/`set_led`/`cleanup`.
- **Persistencia**: `ORDERS` y el throttle están en memoria; para producción a gran
  escala, sustituir por SQLite/Redis y podar periódicamente.

## 9. Notas de seguridad

- La clave privada **nunca** está en la placa ni en git; modo `pubkey-only`.
- `.gitignore` excluye `.env`, `wallet.json`, `*.key`, `*.whl`, `wheels/`, `PLAN.md`
  y `AUDIT_REPORT.md`.
- Los endpoints de prueba requieren `X-Test-Token`; deshabilitados si está vacío.
- Cabeceras CSP, `X-Frame-Options: DENY` y `X-Content-Type-Options: nosniff`.
- En producción, restringe el puerto 8000 a la LAN/ZeroTier.
