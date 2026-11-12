# Guía del desarrollador — Vending Solana USDC · Arduino UNO Q

Documento técnico para quien va a **leer, modificar o extender** el proyecto. Para la operación diaria, ver [`datasheet.md`](datasheet.md); para el relato de decisiones y tropiezos, [`BITACORA.md`](BITACORA.md).

- Runtime verificado: Debian 13.2 (aarch64), Python 3.13.5, FastAPI 0.159.1, Uvicorn 0.54.0, solana-py 0.41.0, solders 0.29.0.
- Arquitectura: **MPU Linux** (HTTP, lógica, RPC) + **MCU STM32U585** (pines y tiempo real), unidos por `arduino-router` en `127.0.0.1:7500`.
- Estado: auditado sin incidencias; 3 pagos reales mainnet validados.

---

## 1. Mapa del repositorio

```text
vending-solana-unoq/
├── app.py                  # FastAPI: órdenes, QR, UI, healthz, endpoints de prueba
├── payments.py             # Solana Pay, validación de tx y PaymentMonitor
├── wallet.py               # keypair o pubkey-only, derivación/creación de ATA
├── config.py               # Config + carga jerárquica (env > .env > YAML)
├── dispenser.py            # Backends de hardware y fail-safe
├── mcu.py                  # Cliente TCP del monitor de arduino-router
├── unoq_leds.py            # LEDs de placa por sysfs
├── catalog.yaml            # 8 SKU: precio, pulso, pin, imagen
├── config.yaml             # Configuración de producción (mainnet)
├── requirements.txt        # Dependencias UNO Q
├── requirements-pi.txt     # Dependencias Raspberry Pi (+gpiozero, +lgpio)
├── .env.example            # Plantilla de variables
├── firmware/
│   └── unoq_gpio_control.ino   # Sketch del MCU (protocolo ASCII + matriz LED)
├── deploy/
│   └── vending-unoq.service    # Unidad systemd
├── scripts/
│   ├── deploy_unoq.sh      # PC → placa por ADB (tar)
│   ├── setup_unoq.sh       # venv offline + dependencias + systemd
│   ├── devnet_tunnel.sh    # Túnel TLS de laboratorio por ADB
│   ├── tcp_splice_proxy.py # Proxy TCP usado por el túnel
│   ├── devnet_e2e_test.py  # Prueba E2E en devnet (requiere red)
│   ├── dispense_test.py    # Pulso manual del relé
│   ├── mcu_probe.py        # Diagnóstico del puente al MCU
│   ├── sales.py            # Visor de ventas (solo lectura)
│   ├── refund.py           # Reembolso USDC (requiere keypair)
│   ├── setup_wallet.py     # Genera keypair y crea ATA (equipo seguro)
│   └── setup_pi.sh         # Variante para Raspberry Pi
├── templates/              # index, payment, confirmed, cancelled, error
├── static/                 # 8 PNG de producto + style.css
└── wheels/                 # Ruedas cp313/aarch64 (no se versiona)
```

---

## 2. Modelo de ejecución

- **Un solo proceso y un solo worker** (`uvicorn app:app --workers 1`). El estado de las órdenes vive en memoria; con varios workers cada uno tendría su propio diccionario y el polling vería respuestas inconsistentes. **No aumentar workers sin mover el estado a un almacén compartido.**
- **asyncio** para HTTP, RPC y monitoreo. El acceso a hardware se ejecuta en hilos (`asyncio.to_thread`) para no bloquear el event loop.
- **`lifespan`** de FastAPI: construye `Dispenser`, inicializa `AsyncClient`, `MerchantWallet`, `PaymentMonitor`, lanza `_expiry_sweeper` y, al cerrar, detiene el monitor, cierra el cliente y limpia GPIO/LEDs.
- **Errores de arranque controlados:** si el backend de pagos falla, la app arranca igual y `/order` responde `503`. Es preferible un kiosco que muestra el catálogo y falla al cobrar que un proceso que no arranca.

---

## 3. Flujo de una orden (detalle de implementación)

```text
POST /order
  ├─ throttle por IP (5 s, máx. 5)
  ├─ 503 si merchant/ATA/monitor no están listos
  ├─ valida items no vacíos y SKU conocidos (404)
  ├─ rechaza SKU duplicados (400)
  ├─ recalcula total con price_base de catalog.yaml
  ├─ order_id = uuid4().hex[:12]
  ├─ reference = str(Pubkey.new_unique())
  ├─ transfer_request = build_transfer_request(include_meta=False, include_amount=False)
  ├─ QR PNG (qrcode, ERROR_CORRECT_M, box_size=12, border=4) → base64
  └─ registra Order en ORDERS y ORDERS_BY_REFERENCE

PaymentMonitor (WSS)
  └─ _on_payment(Payment)
       ├─ busca reference en payment.account_keys entre órdenes pendientes
       ├─ si no hay: fallback por monto a la orden pendiente más antigua (±0,01)
       ├─ si no hay ninguna: UNMATCHED_PAYMENTS.append({...})
       ├─ si |paid_amount − total| > 10.000 → status = monto_incorrecto
       ├─ si OK → status = pagada, guarda signature y paid_amount
       └─ fuera del lock → _dispatch_dispense(order)

_dispatch_dispense
  ├─ LED paid ON
  ├─ por cada item: dispenser.dispense(pin, pulse_seconds); item.dispensed = True
  ├─ sleep 1 s entre items
  ├─ status = dispensada
  ├─ excepción → status = pagada + LED error
  └─ finally LED paid OFF

_expiry_sweeper (cada 5 s)
  └─ pendiente con expires_at vencido → expirada + libera reference
```

Detalle importante: `_dispatch_dispense` se ejecuta **después** de soltar `ORDERS_LOCK`. El lock protege el estado, no el pulso de 2,5 s; mantenerlo tomado durante el dispensado bloquearía el polling y el webhook.

---

## 4. Módulo por módulo

### 4.1 `config.py`

```python
@dataclass
class Config:
    network: str
    rpc_http: str
    rpc_ws: str
    rpc_http_backup: str
    usdc_mint: str
    usdc_decimals: int
    wallet_path: str
    merchant_pubkey: str
    order_timeout_seconds: int
    dispense_pulse_seconds: float
    poll_interval_ms: int
    dispense_pin: int
    dispenser_mode: str
    relay_active_high: bool
    led_ready_pin: int
    led_paid_pin: int
    led_error_pin: int
    mcu_host: str
    mcu_port: int
    mcu_timeout_ms: int
    unoq_led_ready: str
    unoq_led_paid: str
    unoq_led_error: str
    test_token: str
    http_port: int
    base_dir: Path
```

- `load_config()` carga `.env` con `python-dotenv`, crea `Config()` (defaults **devnet**), aplica `config.yaml` y después `ENV_MAP` (25 variables). Los valores vacíos se ignoran.
- Coerción por conjuntos: `_FLOAT_FIELDS` (`dispense_pulse_seconds`), `_INT_FIELDS`, `_BOOL_FIELDS` (`relay_active_high`, acepta `1/true/yes/y/on/si/sí`).
- `wallet_abs_path` resuelve `wallet_path` contra `base_dir`.
- **Nombres que suelen confundirse:** los LEDs de la UNO Q son `unoq_led_ready`, `unoq_led_paid`, `unoq_led_error` (no `led_ready`/`led_paid`/`led_error`). Los pines `led_*_pin` existen para el backend `gpiozero`, no para sysfs.

### 4.2 `wallet.py`

```python
@dataclass
class MerchantWallet:
    pubkey: Pubkey
    keypair: Optional[Keypair]
    source: str          # "keypair" | "pubkey"
    # can_sign: bool ; mode_label: "keypair" | "pubkey-only"

def load_merchant(cfg) -> MerchantWallet
def derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey
async def get_or_create_ata(client, payer, owner, mint) -> tuple[Pubkey, bool]
```

- `load_merchant` prioriza `wallet.json` (JSON con lista de bytes o string base58). Si no existe, usa `MERCHANT_PUBKEY`. Si tampoco hay, genera un keypair nuevo (útil en desarrollo, **peligroso en producción si no se revisa la pubkey**).
- `get_or_create_ata` consulta la cuenta; si no existe, compila una `MessageV0` con `create_associated_token_account`, firma una `VersionedTransaction` y la envía. Solo se llama cuando hay keypair.
- `MINT_ACCOUNT_SPACE = 82` documenta el tamaño de una token account.

### 4.3 `payments.py`

Constantes:

```python
TOKEN_TRANSFER = 3
TOKEN_TRANSFER_CHECKED = 12
TOKEN_ACCOUNT_BALANCE_OFFSET = 64
TOKEN_ACCOUNT_BALANCE_SIZE = 8
```

```python
@dataclass
class Payment:
    signature: str
    reference: Optional[str]
    amount_base: int
    merchant_ata: str
    tx_time: Optional[int] = None
    account_keys: Optional[list[str]] = None
```

**`build_transfer_request(recipient, amount_base, mint, reference, label=..., message=..., include_meta=True, include_amount=True)`** — orden de parámetros: `amount` (si aplica), `spl-token`, `reference`, `label`, `message`. En producción se llama con `include_meta=False` e `include_amount=False`, produciendo `solana:<recipient>?spl-token=<mint>&reference=<ref>`.

**`validate_payment_tx(tx, merchant_ata, reference)`** → `Payment | None`:

1. Si `meta.err is not None`, retorna `None` (tx fallida).
2. `_account_keys` concatena claves estáticas + `meta.loaded_addresses.writable` + `readonly` (v0).
3. `_program_ids` resuelve el programa de cada instrucción compilada.
4. Para cada instrucción del Token Program, `_parse_transfer_instruction` decodifica `data` (str → base58, fallback base64), exige ≥9 bytes, discriminante 3 o 12, resuelve el destino (`accounts[2]` en `transfer_checked`, `accounts[1]` en `transfer`) y compara con la ATA.
5. El monto son 8 bytes little-endian desde `data[1:9]`.
6. `reference` solo se adjunta si está entre las `account_keys`.

**`PaymentMonitor`**:

```python
PaymentMonitor(rpc_http, rpc_ws, merchant_ata, on_payment,
               max_backoff=30.0, rpc_http_backup=None,
               get_transaction_retries=3, reconcile_interval=30.0)
```

- `start()` / `stop()` gestionan la tarea; `stop()` es idempotente.
- `_rpc(fn)` intenta el RPC primario y, si falla y hay backup distinto, el secundario (log `Failover`).
- `_run()` reconecta con backoff exponencial 1→30 s. Antes de suscribir hace `_reconcile()` para fijar la línea base.
- **Dos ramas WebSocket:** si `solana.rpc.websocket_api` expone `SolanaWsClient` (solana-py ≥0.41), usa `client.account_subscribe(pubkey=ATA, commitment="confirmed", encoding="base64")`; si no, `websocket_api.connect` + `account_subscribe` + ACK + `recv`.
- `_periodic_reconcile_loop` corre cada `reconcile_interval` (la app pasa **2,0 s**) y llama `_process_new_signatures`.
- `_process_new_signatures`: `get_signatures_for_address(ATA, limit=10, commitment="confirmed")`; salta firmas ya vistas; si `sig_info.err` no es nulo, la marca vista y sigue.
- `_inspect_signature`: hasta 3 intentos con backoff 0,5·2ⁿ; ante `TypeError` reintenta sin `max_supported_transaction_version`; ante `UnsupportedTransactionVersion` la marca vista y la ignora; si se agotan los intentos **propaga** la excepción para activar el failover. Solo marca la firma como vista cuando el procesamiento tuvo éxito (I-004).

### 4.4 `dispenser.py`

```python
Dispenser(config, force_stub=False)
  .backend        # "unoq" | "gpiozero" | "stub" | "none"
  .is_available   # bool
  .probe()        # solo unoq: McuLink.probe()
  .dispense(pin, seconds) -> await
  .dispense_sync(pin, seconds)
  .set_led(name, on) / .set_led_async(name, on)   # name ∈ ready|paid|error
  .status() -> dict
  .cleanup()
```

- **`auto`** intenta `unoq` y, si falla, cae a `stub` con `stub_reason`. `unoq` explícito también cae a stub si el MCU no responde; `gpiozero` es explícito.
- `_init_unoq`: `McuLink`, `probe()`, escritura fail-safe del pin inactivo, `BoardLeds` y LED verde.
- `_dispense_blocking`: lock de hilo, descarte si ya hay un pulso en curso, LED azul, `mcu.pulse(pin, seconds, active_high)`, LED azul OFF en `finally`.
- `status()` devuelve backend, disponibilidad, motivo del stub, pin, pulso, polaridad, estado del MCU (`host`, `port`, `timeout_s`, `last_error`) y de cada LED (`name`, `path`, `available`, `last_error`).

### 4.5 `mcu.py`

Protocolo ASCII, una línea por comando, respuesta `OK ...`:

| Comando | Respuesta | Notas |
|---|---|---|
| `READ <pin>` | `OK 0` / `OK 1` | `digitalRead` |
| `WRITE <pin> <0\|1>` | `OK <estado>` | Configura el pin como OUTPUT y relee |
| `ANALOG <pin>` | `OK <valor>` | Solo pines 14–19 (A0–A5) |
| `PWM <pin> <0–255>` | `OK <valor>` | Solo 3, 5, 6, 9, 10, 11 |

- `McuLink(host="127.0.0.1", port=7500, timeout=2.0, retries=1)`; un socket nuevo por comando (medido 65–75 ms); toma la última línea no vacía; reintenta una vez si la respuesta llega vacía.
- `pulse(pin, seconds, active_high)` escribe el estado activo, espera y **siempre** escribe el inactivo en `finally`.
- `probe()` hace `read_pin(0)`; `status()` expone el último error.
- Rango de pines 0–19. No expone `MATRIX` ni `TEMP`, aunque el firmware los implementa.

### 4.6 `unoq_leds.py`

- `BoardLed(name, root="/sys/class/leds")`: `available` si existe `brightness`; `_prepare()` escribe `none` en `trigger` si no está ya; `set(on)` escribe `1`/`0` y **nunca lanza** (solo registra `last_error`).
- `BoardLeds(ready, paid, error)`: agrupa los tres, `set(name, on)`, `all_off()` y `status()`.

### 4.7 `app.py`

Globals: `ORDERS`, `ORDERS_BY_REFERENCE`, `ORDERS_LOCK`, `UNMATCHED_PAYMENTS`, `merchant`, `keypair`, `merchant_pubkey`, `merchant_ata`, `dispenser`, `payment_monitor`, `rpc_client`.

Constantes: `AMOUNT_TOLERANCE = 10_000` (±0,01 USDC), `DISPENSE_BETWEEN_SECONDS = 1.0`.

`_on_payment`:

1. Busca la `reference` en `payment.account_keys` entre órdenes `pendiente`.
2. Si no hay, *fallback* por monto: orden pendiente más antigua cuyo total esté dentro de la tolerancia (deja log).
3. Si no hay ninguna, agrega a `UNMATCHED_PAYMENTS` con firma, monto, hora de tx y de registro.
4. Si el monto difiere, `monto_incorrecto` y guarda `paid_amount`.
5. Si todo cuadra, `pagada` + `signature` + `paid_amount`.
6. Fuera del lock, `_dispatch_dispense`.

Middlewares y límites: cabeceras de seguridad (CSP, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`) y throttle de 5 órdenes/5 s por IP.

---

## 5. Contratos

### 5.1 HTTP

| Método | Ruta | Request | Response |
|---|---|---|---|
| `GET` | `/` | — | HTML del catálogo |
| `POST` | `/order` | `{"items":[{"sku":"..."}]}` | `order_id`, `items[]`, `total_amount_base`, `total_usdc`, `reference`, `transfer_request`, `qr_base64`, `timeout_s` |
| `GET` | `/order/{id}` | — | `order_id`, `status`, `items[].dispensed`, `total_*`, `reference`, `signature`, `paid_amount`, `remaining_s` |
| `GET` | `/order/{id}/qr` | — | `image/png` |
| `GET` | `/order/{id}/payment` | — | HTML |
| `POST` | `/order/{id}/cancel` | — | `{"order_id","status"}`; 400 si no pendiente |
| `GET` | `/order/{id}/confirmed` | — | HTML; 400 si no pagada/dispensada |
| `GET` | `/order/{id}/expired` \| `/cancelled` | — | HTML |
| `GET` | `/healthz` | — | JSON de salud |
| `POST` | `/api/test/simulate-payment/{id}` | header `X-Test-Token` | `{"ok","order_id","status","items","simulated":true}` |
| `POST` | `/api/test/dispense` | token + `{"pin","seconds"}` | `{"ok","pin","seconds"}` |

Códigos: `400` validación, `403` token, `404` orden/SKU, `429` throttle, `503` backend no inicializado.

### 5.2 Protocolo del MCU

El contrato es **texto plano terminado en `\n`**, con respuesta de una línea. Cualquier cambio en el sketch debe conservar `OK` como prefijo y el formato `OK <valor>`, porque `McuLink.command` valida `startswith("OK")`. El firmware también acepta `MATRIX ...`, `MATRIX TINY`, `MATRIX TINYSCROLL`, `MATRIX SPEED` y `TEMP`; no están expuestos por `mcu.py`.

### 5.3 Configuración

Prioridad `entorno > .env > config.yaml > defaults`. El `.env` se carga desde `BASE_DIR`, no desde el directorio de trabajo. `config.yaml` se ignora silenciosamente si no existe.

---

## 6. Concurrencia y estado

| Recurso | Protección | Riesgo si se cambia |
|---|---|---|
| `ORDERS` / `ORDERS_BY_REFERENCE` | `asyncio.Lock` | Lecturas inconsistentes si se accede sin lock desde tareas nuevas |
| Dispensado | `threading.Lock` + `_dispensing` | Doble pulso si se elimina |
| Órdenes expiradas | Sweeper cada 5 s | Referencias huérfanas y *fallback* por monto |
| Firmas vistas | `set` en el monitor | Doble dispensado de la misma tx |
| `wallet.json` | Solo lectura al cargar | Fuga de clave si se sirve por HTTP |

No hay persistencia. Si se reinicia el proceso, las órdenes desaparecen pero la cadena conserva los pagos; un pago posterior al reinicio quedará en `UNMATCHED_PAYMENTS`.

---

## 7. Cómo extender

### 7.1 Añadir una bebida

1. Copiar un bloque en `catalog.yaml` con `sku` único, `name`, `price_usdc`, `price_base`, `pulse_seconds`, `dispense_pin`, `image`.
2. Colocar el PNG en `static/`.
3. `sudo systemctl restart vending-unoq`.
4. Verificar `GET /` y `POST /order` con el nuevo SKU.

No hay recarga en caliente ni validación de esquema al arrancar: un YAML mal formado rompe el catálogo.

### 7.2 Soportar varios motores

Hoy `app.py` llama `dispenser.dispense(cfg.dispense_pin, cfg.dispense_pulse_seconds)` para todos los ítems. Para multi-motor:

1. Leer el `dispense_pin` y `pulse_seconds` de cada producto del catálogo.
2. Cambiar `_dispatch_dispense` para pasar los valores del ítem.
3. Añadir una prueba de que dos productos con pines distintos activan pines distintos.
4. Revisar el fail-safe: `cleanup()` solo conoce el pin global actual.

### 7.3 Persistir órdenes

Sustituir `ORDERS` por SQLite (o similar) manteniendo la interfaz usada por `_on_payment`, `_expiry_sweeper` y los endpoints. Cuidado con el lock: si la persistencia es asíncrona, no mantenerlo durante I/O lento. Migrar también `UNMATCHED_PAYMENTS` y las firmas vistas para sobrevivir reinicios.

### 7.4 Otro backend de GPIO

Implementar una clase con `probe`, `dispense`, `set_led`, `cleanup` y `status`, y engancharla en `Dispenser._init_backend`. Para Raspberry Pi ya existe `gpiozero`; para un relé USB, un adaptador serial o un PLC, el contrato es el mismo.

### 7.5 Cambiar de red

Ajustar `network`, `rpc_http`, `rpc_ws`, `usdc_mint` y `merchant_pubkey`. Nunca reutilizar una wallet de mainnet en devnet ni al revés. Para pruebas sin internet, `scripts/devnet_tunnel.sh` levanta un túnel TLS por ADB; es **solo de laboratorio**.

### 7.6 Cambiar la UI

Las plantillas usan Jinja2 y `static/style.css`. El contrato mínimo es:

- `POST /order` con `{items:[{sku}]}`.
- Polling a `GET /order/{id}` y redirección según `status`.
- No confiar en precios del cliente.

Si se reemplaza el frontend, conservar el nombre `vending_cart` en `localStorage` solo si se mantiene la lógica actual de la pantalla de pago.

---

## 8. Despliegue interno

1. `deploy_unoq.sh` empuja el proyecto con `tar` por ADB, excluyendo `.venv`, `wheels`, `__pycache__`, `.git`, `.env` y `wallet.json`.
2. `setup_unoq.sh` crea `.venv --without-pip`, bootstrapea pip desde `wheels/pip-*.whl`, instala con `--no-index --find-links wheels`, verifica imports y copia `.env.example` a `.env` si falta.
3. `vending-unoq.service` corre como `arduino`, con `Restart=always`, `MemoryMax=700M`, `NoNewPrivileges=true` y `Requires=arduino-router.service`.
4. Los wheels deben ser `cp313` + `manylinux2014_aarch64`; una rueda `x86_64` falla en la placa.

---

## 9. Pruebas

No hay suite automatizada. El conjunto mínimo verificable:

```bash
python3 -m compileall -q app.py payments.py wallet.py config.py dispenser.py mcu.py unoq_leds.py
.venv/bin/python scripts/mcu_probe.py            # READ D7 + ANALOG A0
.venv/bin/python scripts/dispense_test.py --force-stub
.venv/bin/python scripts/dispense_test.py --pin 7 --seconds 1.0
curl -s localhost:8000/healthz | python3 -m json.tool
curl -s -X POST localhost:8000/order -H 'Content-Type: application/json' \
  -d '{"items":[{"sku":"coca-cola"}]}' | python3 -m json.tool
```

Casos que ya se probaron en la unidad: `compileall`, imports sin `gpiozero`, `READ`/`ANALOG`, pulso de 2,5 s con retorno a 0, LEDs, catálogo de 8 productos, total recalculado, QR con `spl-token` y `reference`, duplicados 400, SKU 404, `healthz` con `unoq`, pago simulado → pulso real → `dispensada`, pago real mainnet (3), tx fallida ignorada, servicio activo tras reinicio.

**Pendiente:** E2E devnet (sin red y faucet agotado), prueba de carga, reconexión de WebSocket a largo plazo y *soak test* de 24 h.

---

## 10. Deuda técnica y trampas conocidas

| Punto | Detalle | Impacto |
|---|---|---|
| `sales.py` | Pasa `allow_any_reference=True` a `validate_payment_tx`, que ya no acepta ese kwarg | Las ventas se muestran sin monto decodificado; no afecta al cobro |
| Docstring de `app.py` | Dice “grilla de 9 bebidas”; el catálogo tiene 8 | Cosmético |
| Rango analógico | El firmware declara ADC de 12 bits (0–4095); el docstring de `mcu.py` menciona 0–1023 | Tratar el valor como crudo, sin escala |
| `poll_interval_ms` | Se define y se mapea desde entorno, pero el backend no lo consume | Cosmético |
| `config.py` | Los LEDs son `unoq_led_*`; documentación antigua decía `led_*` | Configuración ignorada si se usa el nombre viejo |
| Estado | Todo en memoria | Pérdida de órdenes al reiniciar |
| HTTP | Sin TLS ni autenticación | Solo LAN de confianza |
| Tests | Sin suite ni CI | Regresiones dependen de prueba manual |

---

## 11. Convenciones

- Mantener la separación `app.py` (HTTP) / `payments.py` (cadena) / `dispenser.py` (hardware) / `mcu.py` (transporte).
- Todo acceso a hardware, síncrono y lento, va por `asyncio.to_thread`.
- Los errores de hardware no deben tumbar el proceso: se registran, se enciende el LED rojo y la orden queda en un estado recuperable.
- Nunca loggear claves privadas, `wallet.json` ni el contenido de `.env`.
- Cualquier cambio en `validate_payment_tx` debe mantener la regla: **tx fallida = no dispensa**.
- Cualquier cambio en el protocolo del MCU debe actualizar `mcu.py`, el sketch y esta guía a la vez.
