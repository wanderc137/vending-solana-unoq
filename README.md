# 🥤 Vending Solana — Arduino UNO Q

**Máquina expendedora cripto** que acepta **USDC sobre Solana** y dispensa bebidas
mediante un relé/motor. Esta versión es la **migración del proyecto
[`vending-solana-v2`](https://github.com/wanderc137/vending-solana-v2)** (que corre
en Raspberry Pi) al **Linux embebido de la Arduino UNO Q**, conservando el mismo
resultado funcional: catálogo, carrito, QR de Solana Pay, detección de pago en
menos de 2 s, dispensado por GPIO y web app de autoatención.

> ⚠️ **Dinero real**: la configuración de producción usa **Solana mainnet-beta**.
> Los pagos son USDC reales. Prueba primero en devnet.

---

## 1. Qué cambia respecto a la Raspberry Pi

| Aspecto | Raspberry Pi 5 (v2) | Arduino UNO Q (esta versión) |
|---|---|---|
| Computadora | BCM2712, aarch64 | MPU aarch64 con **Debian 13.2**, Linux embebido |
| GPIO | `gpiozero` + `lgpio` sobre `/dev/gpiochip*` | **MCU STM32U585** vía `arduino-router` TCP `127.0.0.1:7500` |
| Pin del relé | GPIO BCM 17 | **D7** (pin lógico 7 del MCU) |
| LEDs | GPIO 27 / 22 / 23 | LEDs de placa `green:user` / `blue:user` / `red:user` (sysfs) |
| Python | 3.11 | **3.13.5** (sin `ensurepip`; venv `--without-pip`) |
| Acceso | SSH | **USB + ADB** (`adb -s <serial> shell`) |
| Internet | Módem LTE Teltonika | WiFi/Ethernet/ZeroTier de la placa (no provisto en el banco de pruebas) |
| Despliegue | `scp` + systemd | `scripts/deploy_unoq.sh` (tar por ADB) + systemd |

El código de negocio (`app.py`, `payments.py`, `wallet.py`, `config.py`) se conserva.
Las capas que cambiaron son `dispenser.py` (fachada con backends), `mcu.py` (cliente
del bridge del MCU) y `unoq_leds.py` (LEDs por sysfs).

## 2. Principio de funcionamiento

1. El cliente **añade bebidas** al carrito (1 unidad por producto, hasta 8 distintas).
2. Pulsa **«Pagar»**: el backend valida los SKU, **recalcula el total** desde
   `catalog.yaml` y crea una orden con un `reference` único.
3. La pantalla muestra un **QR Solana Pay**
   `solana:<wallet>?spl-token=<USDC>&reference=<ref>` **sin `amount`** (el cliente
   digita el total), el monto en grande y un cronómetro de 60 s.
4. El cliente escanea, digita el total exacto y confirma en su wallet.
5. El monitor detecta la transferencia SPL al ATA USDC del comercio (WebSocket +
   reconciliación cada 2 s), valida monto y referencia, y **dispensa** cada bebida
   con un pulso de 2,5 s en D7.
6. La orden queda `dispensada`, el carrito se limpia y la pantalla celebra.

## 3. Catálogo

| # | Producto | Precio USDC | Unidades base (6 dec.) |
|---|---|---|---|
| 1 | Coca-Cola (330 ml) | 1.00 | 1_000_000 |
| 2 | Fanta (330 ml) | 0.95 | 950_000 |
| 3 | Sprite (330 ml) | 1.05 | 1_050_000 |
| 4 | Limón Soda (330 ml) | 0.90 | 900_000 |
| 5 | Agua sin gas (500 ml) | 0.80 | 800_000 |
| 6 | Agua con gas (500 ml) | 0.85 | 850_000 |
| 7 | Bebida energética (250 ml) | 1.50 | 1_500_000 |
| 8 | Té helado (330 ml) | 1.20 | 1_200_000 |

Los precios y las imágenes se definen en `catalog.yaml` y `static/`.

## 4. Tabla de pines y conexiones

| Pin UNO Q | Modo | Componente | Notas |
|---|---|---|---|
| **D7** (lógico 7) | OUTPUT | **Relé / motor** | Pulso activo-alto de 2,5 s por bebida |
| LED `green:user` | sysfs | LED «listo» | `/sys/class/leds/green:user` |
| LED `blue:user` | sysfs | LED «pagado» | `/sys/class/leds/blue:user` |
| LED `red:user` | sysfs | LED «error» | `/sys/class/leds/red:user` |
| GND | — | GND común con la etapa de potencia | Imprescindible |

```
   ┌──────────────────────────────┐
   │  Arduino UNO Q               │
   │  ┌────────────────────────┐  │
   │  │ Linux (Debian)         │  │
   │  │  FastAPI + uvicorn     │  │
   │  │  payments (Solana)     │  │
   │  └──────────┬─────────────┘  │
   │             │ TCP 7500       │
   │  ┌──────────▼─────────────┐  │
   │  │ MCU STM32U585          │  │
   │  │  D7 ──▶ Relé ──▶ Motor │  │  2,5 s por bebida
   │  │  LEDs user (sysfs)     │  │
   │  └────────────────────────┘  │
   └──────────────────────────────┘
```

> **Nivel lógico 3,3 V.** La UNO Q **no es tolerante a 5 V** en sus pines. Usa un
> módulo relé con optoacoplador alimentado aparte (o un transistor/MOSFET con
> resistencia de base) y un **diodo de rueda libre** en la bobina del relé o del
> motor. Nunca alimentes el motor desde el pin D7.

## 5. Acceso a la placa

La unidad de prueba se conecta por **USB** y se controla con **ADB**:

```bash
adb devices
# List of devices attached
# 2480682956   device

adb -s 2480682956 shell
# usuario: arduino (sudo sin contraseña)
```

- Sistema: **Debian 13.2**, arquitectura **aarch64**, hostname `x11`.
- Python del sistema: **3.13.5**, **sin pip/ensurepip** (por eso el venv se crea
  con `--without-pip` y se bootstrapea pip desde una rueda local).
- El header Arduino **no está expuesto** al Linux: el acceso a pines es por el
  bridge del MCU en `127.0.0.1:7500` (servicio `arduino-router`).
- También se puede usar SSH si se configura la red de la placa; el flujo probado y
  documentado es ADB por USB.

## 6. Despliegue

### 6.1 Desde el PC (código + ruedas)

```bash
# Dependencias offline para aarch64 / Python 3.13 (solo la primera vez)
pip download -r requirements.txt \
  --platform manylinux2014_aarch64 --python-version 3.13 \
  --implementation cp --abi cp313 --only-binary=:all: -d wheels
pip download pip --only-binary=:all: -d wheels

# Copiar el proyecto y las ruedas a la placa, y reiniciar el servicio
bash scripts/deploy_unoq.sh --wheels
```

`deploy_unoq.sh` usa `tar` sobre ADB y **excluye** `.env`, `wallet.json`, `.venv`,
`.git` y `wheels/` del árbol de código; las ruedas se envían aparte con `--wheels`.

### 6.2 En la placa (entorno + servicio)

```bash
adb -s 2480682956 shell
cd /home/arduino/vending-solana-unoq
bash scripts/setup_unoq.sh --systemd
```

El script:

1. Comprueba Python y arquitectura.
2. Crea `.venv` con `--without-pip` (la imagen no trae `ensurepip`).
3. Bootstrapea pip desde `wheels/pip-*.whl`.
4. Instala todas las dependencias **offline** desde `wheels/`.
5. Crea `.env` desde `.env.example` si no existe.
6. Con `--systemd`, instala y habilita `vending-unoq.service`.

### 6.3 Servicio

```bash
sudo systemctl status vending-unoq
sudo systemctl restart vending-unoq
journalctl -u vending-unoq -f
```

El servicio corre como usuario `arduino`, con `Restart=always`,
`NoNewPrivileges=yes`, `MemoryMax=700M` y `Requires=arduino-router.service`.
Uvicorn escucha en `0.0.0.0:8000`.

La web queda en `http://<IP_DE_LA_UNO_Q>:8000`.

## 7. Configuración

Prioridad: **variables de entorno > `.env` > `config.yaml`**.

```bash
cp .env.example .env
```

Variables relevantes:

| Variable | Default | Uso |
|---|---|---|
| `MERCHANT_PUBKEY` | pubkey del comercio | Modo **solo-pubkey** (sin clave privada en la placa) |
| `RPC_HTTP` / `RPC_WS` | mainnet público | RPC de Solana |
| `NETWORK` | `mainnet-beta` | `devnet` para pruebas |
| `DISPENSE_PIN` | `7` | Pin lógico del MCU (D7) |
| `DISPENSER_MODE` | `auto` | `auto \| unoq \| gpiozero \| stub` |
| `DISPENSE_PULSE_SECONDS` | `2.5` | Duración del pulso |
| `RELAY_ACTIVE_HIGH` | `true` | Polaridad del relé |
| `TEST_TOKEN` | vacío | Habilita endpoints de prueba; **vacío = deshabilitados** |

**Wallet**: en la UNO Q se usa el modo **solo-pubkey**; no hay `wallet.json` ni
clave privada. Para reembolsos o creación de ATA, ejecuta `scripts/refund.py` o
`scripts/setup_wallet.py` desde un equipo seguro que sí tenga la clave.

## 8. Red y reloj (importante)

La unidad de prueba **no tiene salida a internet** por defecto:

- `wlan0` está down.
- ZeroTier tiene dirección `192.168.196.62/24` pero **sin ruta por defecto**.

Consecuencias:

- `/healthz` muestra `rpc_ok: false` y el monitor reintenta la conexión.
- Sin NTP, el reloj arranca en **enero de 2026** y TLS falla con
  `certificate is not yet valid`. Sincroniza la fecha antes de operar:

```bash
sudo date -u -s "2026-10-22 15:00:00"
```

Para producción, configura WiFi/Ethernet o una ruta por ZeroTier y habilita NTP
(`timedatectl set-ntp true`). El túnel ADB (`scripts/devnet_tunnel.sh`) es **solo
para laboratorio**: crea un proxy en el PC, un `adb reverse` y una entrada temporal
en `/etc/hosts` de la placa; al salir restaura todo.

## 9. Pruebas

```bash
# Salud (en la placa)
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool

# Crear una orden
curl -s -X POST http://127.0.0.1:8000/order \
  -H 'Content-Type: application/json' \
  -d '{"items":[{"sku":"coca-cola"}]}'

# Estado
curl -s http://127.0.0.1:8000/order/<ORDER_ID>

# GPIO: pulso directo en D7
cd /home/arduino/vending-solana-unoq
.venv/bin/python scripts/dispense_test.py --pin 7 --seconds 1.0

# Bridge del MCU
.venv/bin/python scripts/mcu_probe.py
```

### Pago simulado (solo laboratorio)

Si `TEST_TOKEN` está definido en `.env`, se puede validar el flujo completo sin
fondos on-chain:

```bash
curl -s -X POST http://127.0.0.1:8000/api/test/simulate-payment/<ORDER_ID> \
  -H "X-Test-Token: $TEST_TOKEN"
```

Con `TEST_TOKEN` vacío (configuración de producción) el endpoint responde **403**.

### Resultado de las pruebas de esta migración

- `compileall` de todos los módulos: OK.
- Servicio `vending-unoq`: `active`; `/healthz` con backend `unoq`, `available: true`,
  `stub: false`, `monitor: running`.
- Orden + QR sin `amount`: OK.
- Orden → pago simulado → **pulso real en D7** (2,5 s) → estado `dispensada`: OK.
- LEDs `green:user`, `blue:user`, `red:user`: encendido/apagado OK.
- `mcu_probe.py`: `READ D7 = 0`, `ANALOG A0` con variación OK.
- Pagos reales de mainnet: el parser validó correctamente 1,50019 / 1,000145 /
  0,900125 USDC; una transacción fallida fue ignorada.
- Pago on-chain E2E en devnet: **no ejecutado** por falta de internet en la placa y
  faucet devnet agotado (HTTP 429). Ver limitaciones.

## 10. Scripts

| Script | Dónde se ejecuta | Uso |
|---|---|---|
| `scripts/deploy_unoq.sh` | PC | Copia el proyecto por ADB; `--wheels` envía ruedas; `--no-restart` no reinicia |
| `scripts/setup_unoq.sh` | UNO Q | Crea venv, instala offline y opcionalmente el servicio (`--systemd`) |
| `scripts/dispense_test.py` | UNO Q | Pulso de prueba: `--pin 7 --seconds 1.0` |
| `scripts/mcu_probe.py` | UNO Q | Diagnóstico del bridge (`READ`, `ANALOG`) |
| `scripts/devnet_tunnel.sh` | PC | Túnel ADB temporal a devnet (solo laboratorio) |
| `scripts/devnet_e2e_test.py` | PC/placa | Test E2E en devnet (requiere fondos) |
| `scripts/sales.py` | PC seguro | Visor de ventas/saldo de la ATA |
| `scripts/refund.py` | PC seguro | Reembolso USDC |
| `scripts/setup_wallet.py` | PC seguro | Crear/fondear keypair y ATA |

## 11. Estructura

```
vending-solana-unoq/
├── app.py                    # FastAPI: catálogo, carrito, QR, detección, dispensado
├── payments.py               # Solana Pay: Transfer Request, validación, monitor WS
├── dispenser.py              # Fachada GPIO: backends unoq | gpiozero | stub
├── mcu.py                    # Cliente TCP del bridge arduino-router (7500)
├── unoq_leds.py              # LEDs de la placa vía sysfs
├── wallet.py                 # Keypair / modo solo-pubkey y ATA USDC
├── config.py                 # Configuración (env > .env > config.yaml)
├── config.yaml               # Red, RPC, pin, LEDs, tiempos
├── catalog.yaml              # 8 bebidas y precios
├── requirements.txt          # Dependencias de la UNO Q
├── requirements-pi.txt       # Dependencias extra para Raspberry Pi (gpiozero/lgpio)
├── deploy/vending-unoq.service
├── firmware/unoq_gpio_control.ino   # Sketch del MCU (READ/WRITE/ANALOG/PWM)
├── scripts/                  # Despliegue, setup, pruebas y utilidades
├── templates/                # index, payment, confirmed, cancelled, error
├── static/                   # style.css + imágenes de las latas
├── docs/                     # datasheet.md + developer.md
└── wheels/                   # Ruedas offline (ignoradas por git)
```

## 12. Seguridad

- **Sin clave privada en la placa**: modo `pubkey-only`; el secreto nunca se
  versiona ni se despliega.
- `.env`, `wallet.json`, `*.key`, `wheels/`, `PLAN.md` y `AUDIT_REPORT.md` están en
  `.gitignore`.
- El backend **recalcula el total** desde el catálogo; no confía en el cliente.
- Validación de pago por monto y `reference`; las transacciones fallidas se ignoran.
- Endpoints de prueba protegidos con `X-Test-Token`; deshabilitados si el token está
  vacío.
- Cabeceras CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`.
- En producción, limita el puerto 8000 a la LAN/ZeroTier; no lo expongas a Internet
  sin TLS y autenticación.

## 13. Limitaciones conocidas

1. **Sin internet en el banco de pruebas**: wlan0 down y ZeroTier sin ruta por
   defecto. El pago on-chain real requiere configurar la red.
2. **Reloj sin NTP**: sincronizar la fecha antes de operar o TLS fallará.
3. **Faucet devnet agotado**: no se pudo fondear una wallet de prueba para el E2E
   on-chain; se validó el parser contra pagos reales de mainnet y el flujo completo
   con pago simulado.
4. **Volumen bajo**: las órdenes y el throttle viven en memoria; un reinicio las
   borra. Para un kiosco es aceptable; para producción a gran escala, usar una base
   de datos.
5. **Un solo motor**: las 8 bebidas comparten el mismo compartimiento y se dispensan
   secuencialmente.

## 14. Advertencias

- **Mainnet = dinero real.** Prueba en devnet antes de aceptar pagos.
- El QR **no incluye el monto** a propósito (algunas wallets como Phantom lo
  parsean mal). El cliente debe digitar el total exacto.
- No alimentes el relé/motor desde el pin D7: usa una etapa de potencia con
  alimentación independiente y masa común.
- La UNO Q trabaja a **3,3 V**; verifica los niveles de tu módulo relé.

## 15. Documentación

- [`docs/datasheet.md`](docs/datasheet.md) — guía de usuario/operador.
- [`docs/developer.md`](docs/developer.md) — arquitectura y código fuente.
- `PLAN.md` y `AUDIT_REPORT.md` son documentos internos del pipeline y **no** se
  publican en GitHub.
