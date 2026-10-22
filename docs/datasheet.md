# Datasheet — Vending Solana en Arduino UNO Q (usuario/operador)

## Qué es

Máquina expendedora de **8 bebidas en lata** que acepta **USDC sobre Solana** de
forma instantánea (< 2 s). Es la migración de la máquina que corría en Raspberry Pi
al **Linux embebido de la Arduino UNO Q**. El cliente usa una web local para armar
un carrito y pagar con un único QR de Solana Pay; la máquina detecta el pago y
dispensa cada bebida con un pulso de relé.

## Operación (cliente)

1. En el catálogo, toca **«Añadir +»** en cada bebida. El carrito admite **1 unidad
   por producto** (máximo 8 bebidas distintas).
2. Revisa el carrito: subtotales y **TOTAL**. Puedes quitar con **−** o vaciar todo.
3. Toca **«Pagar»**. La pantalla muestra:
   - el **monto total** en grande,
   - el resumen con la imagen de cada bebida,
   - el **QR** y un **cronómetro circular de 60 s**.
4. Escanea el QR con Phantom u otra wallet compatible con Solana Pay y **digita el
   total exacto** (el QR no incluye el monto a propósito).
5. Confirma antes de que el cronómetro llegue a cero. La máquina detecta el pago y
   **dispensa las bebidas en secuencia** (2,5 s por bebida).
6. La pantalla celebra el pago, el carrito se limpia y vuelve al catálogo en 15 s.

> Si el pago se detecta pero el dispensado falla, la orden queda en estado
> `pagada`. El operador debe revisar el relé/motor y puede reintentar el dispensado
> con el endpoint de prueba (si está habilitado) o manualmente.

## Operación (operador)

### Acceso a la placa

La UNO Q se conecta por **USB** y se administra con **ADB**:

```bash
adb devices
# 2480682956   device

adb -s 2480682956 shell
# usuario: arduino  (sudo sin contraseña)
```

El proyecto está en `/home/arduino/vending-solana-unoq`.

### Salud de la máquina

```bash
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool
```

Valores esperados en operación normal:

| Campo | Valor esperado |
|---|---|
| `status` | `ok` |
| `rpc_ok` | `true` (requiere internet) |
| `gpio.backend` | `unoq` |
| `gpio.available` | `true` |
| `gpio.stub` | `false` |
| `monitor` | `running` |
| `wallet_mode` | `pubkey-only` |

### Servicio

```bash
sudo systemctl status vending-unoq
sudo systemctl restart vending-unoq
journalctl -u vending-unoq -f
```

La web queda en `http://<IP_DE_LA_UNO_Q>:8000`.

### Ver ventas / saldo

Desde un equipo seguro con la clave del comercio (no desde la placa en modo
solo-pubkey):

```bash
.venv/bin/python scripts/sales.py
.venv/bin/python scripts/sales.py --watch
.venv/bin/python scripts/sales.py --last 10
```

### Devolver fondos (reembolso)

Requiere el keypair del comercio (`wallet.json`), por lo que se ejecuta en un equipo
seguro, nunca en la UNO Q en modo solo-pubkey:

```bash
.venv/bin/python scripts/refund.py --to <WALLET_CLIENTE> --amount 2.05 --yes
```

### Cambiar precios

Edita `catalog.yaml` (`price_usdc` y `price_base`; 1 USDC = 1_000_000) y reinicia:

```bash
sudo systemctl restart vending-unoq
```

### Probar el relé (dispensado manual)

```bash
cd /home/arduino/vending-solana-unoq
.venv/bin/python scripts/dispense_test.py --pin 7 --seconds 2.5
```

### Probar el bridge del MCU

```bash
.venv/bin/python scripts/mcu_probe.py
```

## Solución de problemas

| Síntoma | Causa probable | Acción |
|---|---|---|
| La web no carga | Servicio caído | `sudo systemctl restart vending-unoq`; `systemctl status vending-unoq` |
| `rpc_ok: false` | Sin internet en la placa | Configura WiFi/Ethernet/ZeroTier y verifica `ping api.mainnet-beta.solana.com` |
| TLS falla con «certificate is not yet valid» | Reloj sin NTP (arranca en enero 2026) | `sudo date -u -s "AAAA-MM-DD HH:MM:SS"` o habilita NTP |
| `gpio.backend: stub` | El bridge del MCU no responde | `systemctl status arduino-router`; reinicia `vending-unoq` |
| El cliente paga y no dispensa | Pago no detectado, monto incorrecto o fallo del relé | Revisa el monto (±0,01 USDC), `journalctl -u vending-unoq -f` y la prueba de relé |
| Pago sin orden asociada | El cliente pagó sin escanear el QR | Revisa `UNMATCHED_PAYMENTS` en logs y reembolsa manualmente |
| Los LEDs no cambian | Permisos/trigger de sysfs | Verifica `/sys/class/leds/*:user` y el log del arranque |

## Especificaciones

| Parámetro | Valor |
|---|---|
| Red | Solana **mainnet-beta** (USDC real); devnet para pruebas |
| Token | USDC (`EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`) |
| RPC | `https://api.mainnet-beta.solana.com` (con respaldo) |
| Detección de pago | < 2 s (WebSocket + reconciliación cada 2 s) |
| Timeout de orden | 60 s |
| Tolerancia de monto | ±0,01 USDC |
| Dispensado | Pulso de 2,5 s en **D7**, pausa de 1 s entre bebidas |
| Catálogo | 8 bebidas, 0,80 – 1,50 USDC |
| Hardware | Arduino UNO Q (MPU Linux aarch64 + MCU STM32U585), relé/motor, etapa de potencia 3,3 V |
| Acceso | USB/ADB, usuario `arduino`, hostname `x11` |
| Servicio | `vending-unoq.service`, uvicorn en `0.0.0.0:8000` |

## Advertencias

- **Mainnet = dinero real**: cada venta mueve USDC reales a la ATA del comercio.
- La clave privada (`wallet.json`) y el `.env` **nunca** se suben a GitHub ni se
  comparten. En la UNO Q se opera en modo **solo-pubkey**.
- No alimentes el relé ni el motor desde el pin D7. Usa una fuente independiente,
  masa común y protección de rueda libre.
- Los pines de la UNO Q son de **3,3 V**; no son tolerantes a 5 V.
- El túnel ADB a devnet es solo para laboratorio; no es un mecanismo de producción.
