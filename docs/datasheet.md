# Datasheet — Vending Solana USDC · Arduino UNO Q

**Manual de usuario y operador.** Cubre instalación, operación, catálogo, códigos de error, mantenimiento y especificaciones de la máquina expendedora que cobra USDC por Solana Pay y dispensa con un relé gobernado por el MCU de una Arduino UNO Q.

- Versión documentada: octubre de 2026.
- Placa de referencia: Arduino UNO Q, Debian 13.2, Python 3.13.5.
- Red de producción: Solana **mainnet-beta**.
- Documentación técnica interna: [`developer.md`](developer.md) · [`BITACORA.md`](BITACORA.md) · [`../README.md`](../README.md).

> **Advertencia de dinero real.** La configuración de fábrica apunta a mainnet-beta. Cada pago confirmado mueve USDC real. Lea la sección de advertencias antes de energizar la máquina con carga conectada.

---

## 1. Qué es y qué alcance tiene

La máquina permite a un cliente:

1. Elegir una o varias bebidas en una pantalla web.
2. Pagar el total en USDC escaneando un QR de Solana Pay con su wallet.
3. Recibir la bebida cuando la transacción se confirma en la cadena.

El operador puede cambiar precios, consultar ventas, probar el relé, revisar la salud del sistema y reembolsar manualmente. **No** hay control de stock, gestión de usuarios, base de datos de órdenes ni facturación.

| Capacidad | Incluida | Observación |
|---|---|---|
| Cobro en USDC (SPL Token) | Sí | Mainnet-beta o devnet por configuración |
| Carrito multi-producto | Sí | Máximo 1 unidad por SKU |
| QR de Solana Pay | Sí | Sin `amount`; el cliente digita el monto |
| Detección por WebSocket | Sí | Con reconciliación periódica y *failover* de RPC |
| Dispensado por relé | Sí | Un pin (D7) para todos los SKU |
| LEDs de estado | Sí | `green:user`, `blue:user`, `red:user` |
| Persistencia de órdenes | No | En memoria; se pierden al reiniciar |
| Control de stock | No | Dispensa aunque no queden bebidas |
| Reembolso automático | No | Script manual con clave privada |
| Autenticación de operador | No | Kiosco para LAN de confianza |

---

## 2. Puesta en marcha

### 2.1 Antes de empezar

| Requisito | Detalle |
|---|---|
| Placa | Arduino UNO Q con Debian 13.2 y usuario `arduino` |
| PC | Linux/macOS con Python 3.11+, `adb` y cable USB-C de datos |
| Red | La placa debe alcanzar el RPC de Solana (WiFi o Ethernet) |
| Relé | Módulo optoacoplado 3,3 V, alimentación de carga independiente |
| Wallet | Pubkey del comercio; la clave privada **no** va en la placa |

### 2.2 Instalación resumida

En el PC, con internet:

```bash
cd vending-solana-unoq
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

pip download -r requirements.txt \
  --platform manylinux2014_aarch64 --python-version 3.13 \
  --implementation cp --abi cp313 --only-binary=:all: -d wheels
pip download pip --only-binary=:all: -d wheels
```

Flashear `firmware/unoq_gpio_control.ino` en el MCU (App Lab o `arduino-cli` con el core Zephyr). Después:

```bash
bash scripts/deploy_unoq.sh --wheels
adb -s 2480682956 shell
cd /home/arduino/vending-solana-unoq
bash scripts/setup_unoq.sh --systemd
```

### 2.3 Verificación inicial

```bash
adb shell "systemctl is-active vending-unoq"      # active
adb shell "curl -s http://127.0.0.1:8000/healthz"
```

Debe observarse:

| Campo | Valor esperado |
|---|---|
| `status` | `ok` |
| `rpc_ok` | `true` (con red y reloj correctos) |
| `wallet_mode` | `pubkey-only` |
| `gpio.backend` | `unoq` |
| `gpio.available` | `true` |
| `gpio.stub` | `false` |
| `monitor` | `running` |
| `pending_orders` | 0 en reposo |

Si `rpc_ok` es `false`, la máquina está sana localmente pero **no puede cobrar**. Ver la sección de diagnóstico.

---

## 3. Dependencias

Las dependencias Python están declaradas en [`../requirements.txt`](../requirements.txt). La versión instalada y verificada en la unidad de prueba fue:

| Paquete | Requisito | Versión verificada | Uso |
|---|---|---|---|
| `fastapi` | `>=0.110` | 0.159.1 | Servidor HTTP |
| `uvicorn` | `>=0.29` | 0.54.0 | Servidor ASGI |
| `jinja2` | `>=3.1` | 3.1.x | Plantillas del kiosco |
| `solders` | `>=0.21` | 0.29.0 | Tipos de Solana |
| `solana` | `>=0.40` | 0.41.0 | Cliente RPC (incluye `spl.token`) |
| `qrcode` | `>=7.4` | 8.2 | Generación del QR |
| `pillow` | `>=10.0` | 13.0.0 | Imagen del QR |
| `pydantic` | `>=2.0` | 2.14.1 | Validación de requests |
| `base58` | `>=2.1` | 2.1.x | Decodificación de datos |
| `python-dotenv` | `>=1.0` | 1.x | Carga de `.env` |
| `httpx` | `>=0.27` | 0.28.1 | Cliente HTTP |
| `websockets` | `>=10.4` | 17.3 | Suscripción WebSocket |
| `PyYAML` | `>=6.0` | 6.0.3 | Lectura de YAML |

`requirements-pi.txt` añade `gpiozero>=2.0` y `lgpio>=0.2.2.0` para el backend alternativo en Raspberry Pi. **No** se instalan en la UNO Q.

### 3.1 Instalación en un entorno virtual

```bash
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

Para Raspberry Pi:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-pi.txt
```

> **En la UNO Q no existe `pip` ni `ensurepip`.** El entorno se crea con `python3 -m venv --without-pip .venv`, se bootstrapea `pip` desde `wheels/pip-*.whl` y se instala con `pip install --no-index --find-links wheels -r requirements.txt`. Todo el proceso lo automatiza `scripts/setup_unoq.sh`.

### 3.2 Verificar dependencias

```bash
.venv/bin/python - <<'PY'
import fastapi, uvicorn, solana, solders, qrcode, yaml
print("fastapi", fastapi.__version__, "uvicorn", uvicorn.__version__)
print("solana", solana.__version__, "solders OK")
PY
```

---

## 4. Operación del cliente

### 4.1 Flujo normal

1. **Elegir.** Toca **Añadir +** en una o más bebidas. El botón cambia a **En carrito ✓**. No se puede añadir dos veces el mismo SKU.
2. **Revisar.** El carrito muestra nombre, precio y total en USDC.
3. **Pagar.** Pulsa **Pagar**. Se crea la orden y aparece la pantalla de pago.
4. **Escanear.** Abre Phantom, escanea el QR y **digita el monto exacto** que muestra la pantalla (por ejemplo `1.80`).
5. **Firmar.** Confirma la transacción en la wallet.
6. **Esperar.** La pantalla consulta el estado cada 1,5 s. Al confirmarse, pasa a la pantalla de confirmación y la máquina dispensa.
7. **Retirar.** Retira la bebida. Si pidió varias, la máquina dispensa una tras otra con 1 s de pausa.

### 4.2 Reglas que el cliente debe conocer

- **Una unidad por producto.** Para dos latas del mismo SKU hay que hacer dos órdenes.
- **El monto se digita.** El QR no lleva `amount`; si el cliente paga otra cifra, la orden queda en `monto_incorrecto` y **no dispensa**.
- **Tolerancia de ±0,01 USDC.** Diferencias mayores se rechazan.
- **60 segundos.** Pasado ese tiempo la orden expira y el QR deja de ser válido para esa orden.
- **No cerrar la pestaña** hasta ver la confirmación: el pago se detecta igual, pero el cliente no verá el resultado.

### 4.3 Si algo falla

| Situación | Qué hacer |
|---|---|
| El QR no escanea | Acercar el teléfono, subir el brillo de la pantalla, reintentar |
| La wallet muestra otro monto | Cancelar y digitar exactamente el total mostrado |
| Pagó y no dispensó | Anotar la firma y avisar al operador; puede ser monto incorrecto o falta de orden |
| La orden expiró | Volver al catálogo y crear una orden nueva |
| El navegador perdió la conexión | El pago puede haberse registrado; consultar con el operador |

---

## 5. Operación del operador

### 5.1 Acceso a la placa

```bash
adb devices                         # debe listar el serial
adb -s 2480682956 shell             # consola en la placa
```

Dentro: usuario `arduino`, `sudo` sin contraseña. El proyecto vive en `/home/arduino/vending-solana-unoq`.

### 5.2 Servicio

```bash
sudo systemctl status vending-unoq
sudo systemctl restart vending-unoq
journalctl -u vending-unoq -n 100 --no-pager
journalctl -u vending-unoq -f
```

El servicio arranca solo tras un reinicio (`Restart=always`, `WantedBy=multi-user.target`) y exige `arduino-router.service`.

### 5.3 Salud

```bash
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool
```

Interpretación:

| Campo | Significado |
|---|---|
| `status: ok` | Proceso vivo |
| `rpc_ok` | El RPC responde; si es `false`, no se pueden validar pagos |
| `wallet_mode` | `pubkey-only` (recomendado) o `keypair` |
| `merchant_ata` | Cuenta donde deben llegar los USDC |
| `gpio.backend` | `unoq`, `gpiozero`, `stub` o `none` |
| `gpio.available` / `gpio.stub` | Hardware listo / modo simulado |
| `gpio.mcu.last_error` | Último error del puente TCP |
| `monitor` | `running` o `not_started` |
| `pending_orders` | Órdenes sin pagar en este momento |

### 5.4 Cambiar precios o productos

1. Editar [`../catalog.yaml`](../catalog.yaml).
2. Actualizar **ambos** campos: `price_usdc` (humano, por ejemplo `1.25`) y `price_base` (unidades base: `1250000`, seis decimales).
3. Reiniciar: `sudo systemctl restart vending-unoq`.
4. Verificar que el catálogo muestra el precio nuevo.

Para un producto nuevo: copiar un bloque, definir `sku` único, `name`, precios, `pulse_seconds`, `dispense_pin` e `image`, y colocar el PNG en `static/`. El backend lo sirve automáticamente al reiniciar.

### 5.5 Consultar ventas y saldo

```bash
cd /home/arduino/vending-solana-unoq
.venv/bin/python scripts/sales.py              # balance + últimas 15
.venv/bin/python scripts/sales.py --watch      # refresco cada 5 s
.venv/bin/python scripts/sales.py --last 30    # últimas 30
```

Es **solo lectura**: consulta el RPC y la ATA, no abre puertos ni usa claves privadas.

> Deuda conocida: `sales.py` llama a `validate_payment_tx` con el argumento heredado `allow_any_reference`, que la firma actual no acepta. El error se captura y las transacciones se muestran **sin monto decodificado**. El saldo y las firmas sí se muestran. Para el detalle, usar un explorador como Solscan.

### 5.6 Reembolsos

Se ejecutan **fuera de la UNO Q**, en un equipo seguro que tenga `wallet.json`:

```bash
python scripts/refund.py --to <PUBKEY_DESTINO> --amount 1.50
python scripts/refund.py --to <PUBKEY_DESTINO> --amount 1.50 --yes
```

- Envía USDC desde la ATA del comercio a la wallet destino.
- Si la ATA destino no existe, se crea y el comercio paga el *rent* (~0,002 SOL).
- La wallet del comercio necesita SOL para la comisión (~0,000005 SOL).
- Nunca imprime la clave privada.

### 5.7 Probar el hardware

```bash
# Pulso real de 2,5 s en D7
.venv/bin/python scripts/dispense_test.py --pin 7 --seconds 2.5

# Sin tocar GPIO (modo stub)
.venv/bin/python scripts/dispense_test.py --force-stub

# Diagnóstico del puente Linux → MCU
.venv/bin/python scripts/mcu_probe.py
.venv/bin/python scripts/mcu_probe.py --sweep
.venv/bin/python scripts/mcu_probe.py --pulse 7 1.0
```

### 5.8 Pago simulado (solo con `TEST_TOKEN`)

```bash
TOKEN=<valor-de-TEST_TOKEN>
ORDER=<order_id>

curl -s -X POST "http://127.0.0.1:8000/api/test/simulate-payment/$ORDER" \
  -H "X-Test-Token: $TOKEN" | python3 -m json.tool
```

Si `TEST_TOKEN` está vacío, el endpoint responde `403`. No usar en producción con público.

---

## 6. Catálogo de fábrica

| SKU | Bebida | Precio | `price_base` |
|---|---|---:|---:|
| `coca-cola` | Coca-Cola | 1,00 USDC | 1.000.000 |
| `fanta` | Fanta | 0,95 USDC | 950.000 |
| `sprite` | Sprite | 1,05 USDC | 1.050.000 |
| `limon-soda` | Limón Soda | 0,90 USDC | 900.000 |
| `agua-sin-gas` | Agua sin gas | 0,80 USDC | 800.000 |
| `agua-con-gas` | Agua con gas | 0,85 USDC | 850.000 |
| `energetica` | Energética | 1,50 USDC | 1.500.000 |
| `te-helado` | Té helado | 1,20 USDC | 1.200.000 |

Todos comparten `dispense_pin: 7` y `pulse_seconds: 2.5`.

---

## 7. Configuración

Archivos: `config.yaml` (producción), `.env` (secretos y overrides) y `catalog.yaml` (productos). Prioridad: **entorno > `.env` > `config.yaml` > defaults**.

| Clave | Default | Descripción |
|---|---|---|
| `network` | `mainnet-beta` | Red lógica |
| `rpc_http` / `rpc_ws` | mainnet público | Endpoints RPC |
| `rpc_http_backup` | mainnet público | Respaldo para *failover* |
| `usdc_mint` | `EPjF…Dt1v` | Mint de USDC mainnet |
| `usdc_decimals` | 6 | Decimales de USDC |
| `wallet_path` | `wallet.json` | Keypair (opcional) |
| `merchant_pubkey` | `5FMS…56M` | Pubkey del comercio |
| `order_timeout_seconds` | 60 | Vida de la orden |
| `dispense_pulse_seconds` | 2.5 | Duración del pulso |
| `poll_interval_ms` | 1500 | Heredado; hoy la UI usa 1,5 s y el monitor 2 s |
| `dispense_pin` | 7 | Pin del relé |
| `dispenser_mode` | `auto` | `auto`, `unoq`, `gpiozero`, `stub` |
| `relay_active_high` | `true` | Polaridad |
| `mcu_host` / `mcu_port` | `127.0.0.1` / 7500 | Puente al MCU |
| `mcu_timeout_ms` | 2000 | Timeout de comando |
| `unoq_led_ready` | `green:user` | LED listo |
| `unoq_led_paid` | `blue:user` | LED pagado |
| `unoq_led_error` | `red:user` | LED error |
| `http_port` | 8000 | Puerto HTTP |

Variables de entorno equivalentes en mayúsculas (`MERCHANT_PUBKEY`, `RPC_HTTP`, `DISPENSE_PIN`, etc.). `TEST_TOKEN` vacío deshabilita los endpoints de prueba.

---

## 8. Códigos de error y estados

### 8.1 Estados de una orden

| Estado | Significado | Acción del operador |
|---|---|---|
| `pendiente` | Esperando pago | Ninguna |
| `pagada` | Pago validado, dispensado en curso o fallido | Si persiste, revisar GPIO y logs |
| `dispensada` | Pulso(s) completados | Ninguna |
| `expirada` | Pasaron 60 s sin pago | Ninguna; se puede crear otra |
| `cancelada` | Cancelada por el cliente | Ninguna |
| `monto_incorrecto` | El pago no coincide con el total | Reembolsar o dispensar a mano |

### 8.2 Respuestas HTTP

| Código | Causa | Solución |
|---|---|---|
| `400` | Carrito vacío, SKU duplicado, cancelar orden no pendiente | Corregir la solicitud |
| `403` | Endpoint de prueba sin token o token incorrecto | Configurar `TEST_TOKEN` o no usar el endpoint |
| `404` | Orden o SKU inexistente | Verificar el identificador |
| `429` | Más de 5 órdenes en 5 s desde la misma IP | Esperar unos segundos |
| `503` | Backend de pagos no inicializado | Revisar el arranque del servicio |

### 8.3 Errores de hardware y red

| Mensaje / síntoma | Significado | Solución |
|---|---|---|
| `gpio.backend: stub` | No se pudo abrir el MCU; se simula el GPIO | Verificar `arduino-router` y el sketch |
| `MCU no responde` | El puente TCP falla o el sketch es otro | `mcu_probe.py`; reflashear firmware |
| `rpc_ok: false` | Sin ruta, DNS o reloj | Revisar red; sincronizar hora |
| `certificate is not yet valid` | Reloj atrasado | `sudo date -u -s "..."` o NTP |
| `UNMATCHED_PAYMENTS` | Pago sin orden asociada | Registrar firma; reembolsar |
| `Transacción fallida ignorada` | `meta.err` no nulo | Ninguna: es el comportamiento correcto |
| `Failover a RPC de respaldo` | El RPC primario falló | Revisar el proveedor |

---

## 9. Mantenimiento

### 9.1 Antes de abrir al público

- [ ] `healthz` con `status: ok`, `rpc_ok: true`, `monitor: running`.
- [ ] `gpio.backend: unoq`, `available: true`, `stub: false`.
- [ ] LED verde encendido.
- [ ] Catálogo con los precios del día.
- [ ] Relé en OFF con la máquina en reposo.
- [ ] Una compra de prueba con monto mínimo.

### 9.2 Diario

- Revisar `journalctl -u vending-unoq` por errores repetidos.
- Confirmar que el saldo de la ATA coincide con las ventas esperadas (`sales.py`).
- Verificar que no haya pagos en `UNMATCHED_PAYMENTS`.

### 9.3 Semanal

- Respaldar `catalog.yaml`, `config.yaml` y `.env` (sin `wallet.json`).
- Limpiar polvo de la rejilla y del módulo relé.
- Revisar cables del header y alimentación de la carga.
- Probar el pulso con la carga desconectada.

### 9.4 Actualización de software

```bash
# En el PC
bash scripts/deploy_unoq.sh            # empuja y reinicia
bash scripts/deploy_unoq.sh --wheels    # si cambiaron dependencias
```

El script no copia `.env` ni `wallet.json`: la configuración de la placa se conserva.

### 9.5 Respaldar y restaurar

```bash
adb pull /home/arduino/vending-solana-unoq/config.yaml
adb pull /home/arduino/vending-solana-unoq/catalog.yaml
adb pull /home/arduino/vending-solana-unoq/.env
```

Nunca extraer `wallet.json` de un equipo seguro a la placa.

### 9.6 Parada y desinstalación

```bash
sudo systemctl stop vending-unoq
sudo systemctl disable vending-unoq
```

Para retirar el servicio: borrar `/etc/systemd/system/vending-unoq.service`, `systemctl daemon-reload` y eliminar el directorio del proyecto si se desea.

---

## 10. Diagnóstico rápido

| Síntoma | Comprobación | Acción |
|---|---|---|
| No abre la web | `systemctl status`; `curl localhost:8000/healthz` | Reiniciar el servicio; revisar IP |
| `502`/sin respuesta | `journalctl -u` | Ver excepción de arranque |
| No detecta pagos | `monitor`, `rpc_ok` | Revisar red y reloj |
| Paga y no dispensa | Estado de la orden; logs de `Dispenser` | Ver `monto_incorrecto`, GPIO o lock |
| Dispensa sin pagar | Logs de `_on_payment`; `UNMATCHED_PAYMENTS` | Revisar referencia y montos |
| El relé no actúa | `mcu_probe.py --pulse 7 2` | Verificar D7, GND y polaridad |
| El relé queda pegado | Medir D7 en reposo | Corregir `RELAY_ACTIVE_HIGH` o cableado |
| LED azul no enciende | `ls /sys/class/leds/` | Ajustar `UNOQ_LED_PAID` |
| La placa no aparece en ADB | `adb devices`, cable, reinicio | Probar otro cable/puerto |

---

## 11. Especificaciones

| Parámetro | Valor |
|---|---|
| Red | Solana mainnet-beta (configurable a devnet) |
| Activo | USDC SPL, 6 decimales |
| Mint mainnet | `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` |
| Mint devnet | `4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU` |
| Detección de pago | WebSocket + reconciliación cada 2 s |
| Objetivo de detección | < 2 s desde la confirmación |
| Vida de la orden | 60 s |
| Tolerancia de monto | ±0,01 USDC (10.000 unidades base) |
| Pulso de dispensado | 2,5 s (configurable) |
| Pausa entre productos | 1 s |
| Catálogo | 8 SKU, 0,80–1,50 USDC |
| Hardware | UNO Q (QCM2290 + STM32U585), relé en D7 |
| Sistema | Debian 13.2, Python 3.13.5, FastAPI + Uvicorn |
| Memoria del servicio | Límite 700 MB sobre 1,7 GiB |
| Puerto HTTP | 8000/tcp |
| Puente MCU | 127.0.0.1:7500 (loopback) |
| Acceso | ADB por USB-C; SSH opcional |
| Servicio | `vending-unoq.service` (systemd) |

---

## 12. Advertencias y seguridad

1. **Dinero real.** Verifique red, mint y pubkey antes de cada puesta en marcha.
2. **Sin clave privada en la placa.** El modo recomendado es `pubkey-only`. Si aparece `wallet.json`, la placa puede firmar y pierde esta protección.
3. **Relé y carga.** Use fuente externa para el motor o la electroválvula, diodo de rueda libre y sentido común: el header entrega 3,3 V de señal, no potencia.
4. **Polaridad.** Un módulo activo-bajo con `relay_active_high: true` mantiene el relé energizado en reposo.
5. **Sin TLS.** No exponga el puerto 8000 a internet. Úselo en LAN de confianza o detrás de un proxy TLS.
6. **Sin autenticación de kiosco.** Cualquiera en la LAN puede crear órdenes; el cobro sigue dependiendo de la cadena.
7. **Reembolsos.** Se hacen desde un equipo seguro, con confirmación manual (`--yes` desactiva el prompt).
8. **Órdenes en memoria.** Un reinicio pierde el historial; para auditoría use la cadena y `sales.py`.
9. **No publique** `.env`, `wallet.json` ni capturas con datos de su red.

---

## 13. Glosario

| Término | Definición |
|---|---|
| **USDC** | Stablecoin de Circle, 1:1 con el dólar, emitida como SPL Token en Solana |
| **SPL Token** | Estándar de tokens de Solana |
| **ATA** | *Associated Token Account*: cuenta determinista donde se guarda un token de una wallet |
| **Solana Pay** | Protocolo de solicitudes de pago mediante URL/QR (`solana:...`) |
| **Reference** | Pubkey única incluida en la transacción para emparejarla con una orden |
| **Lamport** | Unidad mínima de SOL (1 SOL = 1.000.000.000 lamports) |
| **Mainnet / devnet** | Red principal con dinero real / red de pruebas |
| **MPU / MCU** | Procesador Linux de la UNO Q / microcontrolador STM32U585 |
| **ADB** | Android Debug Bridge; aquí se usa para acceder por USB |
| **systemd** | Gestor de servicios de Linux que mantiene vivo el backend |
