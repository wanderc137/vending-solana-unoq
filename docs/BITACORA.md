# Bitácora de desarrollo — Vending Solana USDC · Arduino UNO Q

Registro de lo que costó, lo que se descartó y por qué. Está escrito para que un futuro proyecto (o yo mismo dentro de seis meses) no repita los mismos caminos.

- Periodo: **22 de octubre de 2026** (migración y pruebas).
- Origen: `vending-solana-v2`, funcionando en Raspberry Pi.
- Destino: Arduino UNO Q (MPU Qualcomm QCM2290 + MCU STM32U585), Debian 13.2.
- Resultado: servicio systemd activo, 3 pagos reales mainnet validados, auditoría aprobada sin incidencias, E2E devnet no ejecutado por falta de red.

---

## 1. Línea de tiempo

| Etapa | Qué pasó |
|---|---|
| Descubrimiento | Se verificó placa, OS, arquitectura, Python, RAM, disco, grupos y servicios activos |
| Primer intento de GPIO | `libgpiod` sobre `gpiochip1/2` no tocaba el header: eran pinctrl del MPU |
| Primer intento de serie | No existe `/dev/ttyACM0`; `/dev/ttyHS1` estaba ocupado por `arduino-router` |
| Solución | Monitor TCP `127.0.0.1:7500` con protocolo ASCII `READ`/`WRITE`/`ANALOG`/`PWM` |
| Entorno Python | La imagen no trae `pip`: venv sin pip + bootstrap desde rueda |
| Instalación | Sin internet: ruedas `cp313`/`aarch64` descargadas en el PC y empujadas por ADB |
| Backend | `dispenser.py` con backends `unoq`, `gpiozero`, `stub`; fail-safe OFF |
| Pagos | `solana-py` 0.41 rompió la ruta WebSocket heredada; se implementó la rama nueva |
| Pruebas | Pago simulado → pulso real; 3 pagos mainnet; tx fallida ignorada; LEDs |
| Red | WiFi de la placa muerto a nivel de hardware; se documentó para RMA |
| Cierre | Auditoría sin incidencias y documentación completa |

---

## 2. Problemas y cómo se resolvieron

### 2.1 El header no está expuesto al Linux

**Síntoma.** `gpiod` encontraba `gpiochip1` y `gpiochip2`, pero escribir en ellos no movía ningún pin del conector.

**Causa.** Esos chips son los pinctrl internos del MPU (LEDs de placa, botones), no el header de Arduino. El header pertenece al MCU STM32U585.

**Solución.** No usar GPIO del Linux. El sketch `unoq_gpio_control.ino` corre en el MCU y `arduino-router` publica un monitor TCP en `127.0.0.1:7500`. `mcu.py` habla ese protocolo.

**Descartado.** `libgpiod` para el relé, `sysfs` de GPIO, y cualquier idea de “bit-bang” desde Python.

### 2.2 No hay puerto serie accesible

**Síntoma.** Se esperaba `/dev/ttyACM0` como en un Arduino clásico. No existe dentro de la placa. `/dev/ttyHS1` está tomado por `arduino-router`.

**Solución.** El monitor TCP. Ventaja inesperada: no hay que pelear con permisos de `dialout` ni con el dueño del puerto; el router ya hizo ese trabajo.

**Descartado.** Abrir `/dev/ttyHS1` en paralelo (habría roto el router), flashear un firmware que exponga otro puerto (más fragilidad), y usar USB desde el PC para cada comando (solo sirve en laboratorio).

### 2.3 Python 3.13.5 sin pip

**Síntoma.** `python3 -m venv .venv` fallaba o creaba un entorno sin pip; `ensurepip` no está en la imagen.

**Solución.** `python3 -m venv --without-pip .venv`, luego bootstrapear pip desde `wheels/pip-*.whl` con el propio intérprete:

```bash
"$VENV_PY" "$PIP_WHL/pip" install --no-index --find-links wheels pip
```

Después, instalación normal con `--no-index --find-links wheels`.

**Lección.** En placas “Linux embebido” nunca asumir `ensurepip`, `apt` ni acceso a PyPI.

### 2.4 Instalación sin internet

**Síntoma.** La placa no tenía ruta por defecto; `api.devnet.solana.com` y `pypi.org` devolvían código 000.

**Solución.** Descargar ruedas en el PC con la matriz correcta:

```bash
pip download -r requirements.txt \
  --platform manylinux2014_aarch64 --python-version 3.13 \
  --implementation cp --abi cp313 --only-binary=:all: -d wheels
```

Y empujarlas por ADB. Para pruebas de red se montó un túnel TLS de laboratorio: `adb reverse tcp:443 tcp:8443` + proxy splice en el PC + entrada en `/etc/hosts` de la placa.

**Descartado.** Compilar en la placa (sin toolchain ni headers), copiar el `.venv` de otro equipo (arquitectura/ABI distinta), y usar `apt` (no hay red y además rompe la reproducibilidad).

### 2.5 `solana-py` 0.41 eliminó la API WebSocket heredada

**Síntoma.** `from solana.rpc.websocket_api import connect` ya no existía; el monitor no arrancaba.

**Solución.** Detección en runtime: si el módulo expone `SolanaWsClient`, usar `client.account_subscribe(...)`; si no, conservar la rama antigua con `connect`. Ambas ramas conviven y se prueban según la versión instalada.

**Lección.** No fijar una versión menor sin leer el changelog; y cuando se abstrae una API cambiante, dejar las dos ramas y detectar por capacidad, no por número de versión.

### 2.6 Transacciones v0 con lookup tables

**Síntoma.** Una transacción real no se validaba: el destino de la instrucción no coincidía con la ATA.

**Causa.** En v0 los índices de cuentas incluyen primero las claves estáticas y luego las cargadas desde lookup tables (`writable` y `readonly`). Mirar solo `message.account_keys` deja índices sin resolver.

**Solución.** `_account_keys` concatena las tres listas en el orden correcto. Se añadió una prueba con una tx v0 real.

### 2.7 Una transacción fallida parecía un pago

**Síntoma.** Una tx con `meta.err` contenía la instrucción de transferencia, pero no había movido fondos.

**Solución.** `validate_payment_tx` retorna `None` si `meta.err` no es nulo. Regla no negociable: **si la tx falló, no se dispensa**.

**Lección.** La presencia de una instrucción no implica ejecución exitosa. Validar siempre el resultado de la transacción, no solo su contenido.

### 2.8 Phantom y el monto del QR

**Síntoma.** Con `amount` en unidades base, algunas versiones de Phantom mostraban cifras absurdas (1.000.000 o 150.000 en lugar de 1,50).

**Solución.** Emitir el QR sin `amount` ni `label`/`message`. El monto se muestra en pantalla y el cliente lo digita; la `reference` sigue viajando en la transacción.

**Costo.** El usuario puede equivocarse al tipear; se mitiga con tolerancia ±0,01 y el estado `monto_incorrecto`.

**Descartado.** Confiar en que todas las wallets respetan la spec; recortar la URL de otra forma; y usar un deep link propio (más complejidad y menos compatibilidad).

### 2.9 Relé activo-bajo

**Riesgo detectado.** Muchos módulos chinos se activan con nivel bajo. Con `relay_active_high: true` el relé quedaría energizado en reposo.

**Solución.** `RELAY_ACTIVE_HIGH` configurable y escritura fail-safe del estado inactivo al inicializar y en `cleanup()`.

**Lección.** La polaridad es un dato de hardware, no una constante del código.

### 2.10 Reloj sin NTP y TLS

**Síntoma.** `certificate is not yet valid`: la placa arrancaba en enero de 2026 sin sincronizar hora, y el certificado del RPC “todavía no era válido”.

**Solución.** Ajuste manual de fecha para pruebas (`sudo date -u -s`) y recomendación de `timedatectl set-ntp true` en producción.

**Lección.** En una placa sin RTC, la hora es un requisito funcional del cliente TLS, no un detalle.

### 2.11 Faucet de devnet agotado

**Síntoma.** `HTTP 429` al pedir SOL de prueba.

**Consecuencia.** El E2E en devnet no se pudo ejecutar. Se documentó como limitación y se compensó con pagos reales pequeños en mainnet y con el endpoint de pago simulado.

**Lección.** Un plan de pruebas no debe depender de un faucet público. Para la próxima: wallet de devnet pre-fondeada y un RPC propio.

### 2.12 WiFi muerto (hardware)

**Síntoma.** El módulo WCN3990 funcionó unos segundos y crasheó: `WLAN RT:0x2147`, `qca61x0_rfactrl.c:halphy_rfa_ctrl_device_ErrorHandler:169`. Después, IDs de chip inválidos (`0xaddead`, `0xadad`) ausentes en `board-2.bin`.

**Probado sin éxito.** Ciclo de energía en frío (15 s y 2 min), `modprobe -r/-i`, reflasheo de la imagen oficial (Debian 13.5, kernel 7.0.0, firmware 2026.01-rc3), board file alternativo, fuente de 5 V/4 A.

**Conclusión.** Fallo de módulo/calibración RF, no de software ni de configuración. Se preparó informe de RMA con logs, versiones y pruebas. El servicio siguió `active` y el relé funcionó; lo único que se perdió fue el acceso a la red.

**Lección.** Separar “la app funciona” de “la red funciona”. El `healthz` con `status: ok` y `rpc_ok: false` fue clave para no confundir una cosa con la otra.

---

## 3. Enfoques descartados y por qué

| Enfoque | Por qué se descartó |
|---|---|
| Controlar el relé desde GPIO del Linux | El header pertenece al MCU; `gpiochip1/2` no lo exponen |
| Abrir `/dev/ttyHS1` desde la app | `arduino-router` es su dueño; conflicto y pérdida de comandos |
| Instalar con `apt`/`pip` online en la placa | Sin ruta a internet y sin `pip` de sistema |
| Copiar un venv ya construido | Dependencias nativas y ABI distintas |
| Poner `amount` y metadatos en el QR | Phantom los interpreta mal; QR más denso y menos legible |
| Firmar en la placa (keypair) | Superficie de ataque innecesaria; pubkey-only cubre el cobro |
| Base de datos desde el día uno | Complejidad prematura para una demo; se documentó como extensión |
| Varios workers de Uvicorn | El estado en memoria se fragmentaría |
| Validar solo por monto | Permite confundir pagos entre órdenes; la `reference` es más precisa |
| Ignorar transacciones fallidas por firma | Se perderían pagos legítimos tras un fallo transitorio de RPC |

---

## 4. Decisiones técnicas y su justificación

1. **Puente TCP en lugar de GPIO directo.** Es la única vía real que ofrece la placa y añade aislamiento: el MCU ejecuta el pulso aunque el Linux tenga carga.
2. **Pubkey-only por defecto.** La máquina cobra sin poder firmar. Un robo físico no compromete los fondos.
3. **Total recalculado en el servidor.** La defensa más simple contra la manipulación del carrito.
4. **Tolerancia ±0,01 USDC.** Absorbe redondeos y errores de tipeo sin abrir la puerta a pagos arbitrarios.
5. **Referencia única + fallback por monto.** Precisión cuando la wallet incluye la reference; resiliencia cuando no.
6. **Un solo worker y estado en memoria.** Simplicidad y coherencia para una demo; se documenta la ruta a SQLite.
7. **Fail-safe del relé.** Estado inactivo al arrancar y al limpiar; nunca dejar la carga energizada.
8. **`Requires=arduino-router.service`.** Sin router no hay MCU; mejor no arrancar que fallar al cobrar.
9. **Wheels offline versionadas fuera de git.** Instalación reproducible en una placa sin red.
10. **Endpoints de prueba con token vacío por defecto.** No se puede simular un pago en producción sin configurar un secreto.
11. **Túnel ADB solo para laboratorio.** Permite probar devnet sin red, pero no se documenta como solución de producción.

---

## 5. Lecciones aprendidas

- **Descubrir el hardware en runtime, no por suposición.** La mitad de los problemas iniciales venían de esperar un Arduino clásico donde había un Linux + MCU.
- **La documentación del vendor no siempre cubre el caso.** El monitor TCP no era la vía “oficial” para GPIO, pero era la única practicable.
- **Los cambios de API de las librerías cripto rompen silenciosamente.** Detectar capacidades (`hasattr`) y mantener dos ramas fue más robusto que fijar una versión.
- **El dinero exige validar el resultado, no la intención.** `meta.err`, destino exacto, monto y reference: cuatro controles, no uno.
- **Un kiosco debe arrancar aunque la red falle.** El `503` en `/order` y el `healthz` honesto valen más que un crash loop.
- **La evidencia se recoge mientras se prueba.** El informe de RMA se armó con logs y comandos ejecutados en el momento; reconstruirlo después habría sido imposible.
- **Documentar lo que no se probó es parte del trabajo.** El E2E devnet no se ejecutó y así quedó escrito; ocultarlo habría sido peor.
- **Los límites de recursos son parte del diseño.** `MemoryMax=700M` sobre 1,7 GiB evita que un pico de Python se lleve puesto el sistema.
- **La polaridad del relé es un requisito, no un detalle.** Un módulo activo-bajo mal configurado puede quemar la carga o vaciar la máquina.

---

## 6. Pendientes

- Corregir la llamada heredada de `sales.py` (`allow_any_reference`).
- Unificar la documentación del rango ADC (12 bits vs 0–1023) y exponer `MATRIX`/`TEMP` si se usan.
- Persistencia de órdenes y firmas vistas (SQLite).
- Suite de pruebas automatizada y CI.
- Multi-motor real usando `dispense_pin` por producto.
- E2E devnet con faucet propio o wallet pre-fondeada.
- Reemplazo de la unidad por RMA y reevaluación del WiFi.
- Proxy TLS y autenticación si el kiosco sale de una LAN de confianza.
