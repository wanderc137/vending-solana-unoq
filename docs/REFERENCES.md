# Referencias

Enlaces utilizados para diseñar, implementar y depurar este proyecto. Todos
apuntan a documentación oficial o a fuentes comunitarias verificables. Se
indica en una línea por qué resultan útiles.

> **Nota de vigencia.** Los enlaces se comprobaron durante el desarrollo
> (octubre de 2026). La documentación de Solana y de Arduino evoluciona rápido:
> si un enlace cambia, busca el título de la página en el sitio oficial
> correspondiente.

---

## 1. Solana Pay y pagos

| Recurso | Para qué sirve |
|---|---|
| [Especificación de Solana Pay](https://docs.solanapay.com/spec) | Referencia normativa del *Transfer Request*: parámetros `amount`, `spl-token`, `reference`, `label` y `message`, y semántica del esquema `solana:`. |
| [Repositorio solana-labs/solana-pay](https://github.com/solana-labs/solana-pay) | Implementaciones de referencia del protocolo y ejemplos de integración en comercios. |
| [Solana Docs — Transactions](https://solana.com/docs/core/transactions) | Estructura de una transacción, firmas, `recent_blockhash` y confirmación. Necesario para entender `validate_payment_tx`. |
| [Solana Docs — Versioned Transactions](https://solana.com/docs/core/transactions/versions) | Diferencias entre transacciones legacy y v0, y el papel de `loaded_addresses` al resolver índices de cuentas. |
| [Solana Docs — Tokens (SPL Token)](https://solana.com/docs/core/tokens) | Modelo de token accounts, mint, decimals y programas de token. Explica por qué se valida el destino contra la ATA. |
| [SPL Associated Token Account](https://spl.solana.com/associated-token-account) | Derivación determinista de la ATA (`get_associated_token_address`) y la instrucción de creación. |
| [Circle — USDC](https://www.circle.com/usdc) | Documentación del emisor de USDC; útil para confirmar dirección del mint y decimales (6). |
| [Solscan](https://solscan.io/) | Explorador para verificar firmas, ATA y movimientos durante las pruebas en mainnet. |
| [Phantom](https://phantom.app/) | Wallet usada como cliente de referencia para escanear el QR y firmar el pago. |

## 2. Librerías Python

| Recurso | Para qué sirve |
|---|---|
| [solana-py (documentación)](https://michaelhly.github.io/solana-py/) | Cliente RPC asíncrono: `AsyncClient`, `get_signatures_for_address`, `get_transaction`, `get_token_account_balance`. |
| [solana-py (PyPI)](https://pypi.org/project/solana/) | Versiones publicadas y notas de compatibilidad. En este proyecto se fijó `solana>=0.40`. |
| [solders (documentación)](https://kevinheavey.github.io/solders/) | Tipos base: `Pubkey`, `Keypair`, `Signature`, `MessageV0`, `VersionedTransaction`. |
| [solders (PyPI)](https://pypi.org/project/solders/) | Ruedas disponibles por plataforma/ABI; clave para preparar `wheels/` para aarch64 + cp313. |
| [FastAPI](https://fastapi.tiangolo.com/) | Framework HTTP: rutas, validación con Pydantic, `lifespan` y respuestas JSON/HTML. |
| [Uvicorn](https://www.uvicorn.org/) | Servidor ASGI usado por systemd (`--workers 1`). |
| [Jinja2](https://jinja.palletsprojects.com/) | Motor de plantillas de las páginas del kiosco. |
| [qrcode](https://pypi.org/project/qrcode/) | Generación del PNG del QR de Solana Pay embebido en base64. |
| [python-dotenv](https://pypi.org/project/python-dotenv/) | Carga de `.env` con prioridad sobre `config.yaml`. |
| [PyYAML](https://pyyaml.org/) | Lectura de `config.yaml` y `catalog.yaml`. |

## 3. Arduino UNO Q y puente Linux ↔ MCU

| Recurso | Para qué sirve |
|---|---|
| [Arduino UNO Q — documentación de hardware](https://docs.arduino.cc/hardware/uno-q) | Arquitectura de la placa (MPU Linux + MCU STM32U585), especificaciones y disposición del header. |
| [arduino/arduino-router](https://github.com/arduino/arduino-router) | Servicio que expone el MCU al Linux de la placa; en este proyecto se usa su monitor TCP en `127.0.0.1:7500`. |
| [Arduino App Lab](https://docs.arduino.cc/software/app-lab/) | Entorno oficial para compilar y flashear el sketch del MCU en la UNO Q. |
| [Arduino Forum — UNO Q Wi-Fi suddenly dead](https://forum.arduino.cc/t/uno-q-wi-fi-suddenly-dead/1432944) | Hilo comunitario sobre el fallo del módulo WCN3990 documentado en el informe interno de RMA (no publicado). |
| [Arduino Forum — UNO Q WiFi failed](https://forum.arduino.cc/t/uno-q-wifi-failed/1431889) | Segundo reporte del mismo patrón de fallo (IDs inválidos, crash en `qca61x0_rfactrl.c`). |
| [Zephyr Project — documentación](https://docs.zephyrproject.org/) | RTOS sobre el que corre el firmware del MCU; útil si se amplía el sketch. |

## 4. Sistema operativo y despliegue

| Recurso | Para qué sirve |
|---|---|
| [Debian 13 “trixie”](https://www.debian.org/releases/trixie/) | Base del Linux de la UNO Q; versión usada en la unidad de prueba (13.2). |
| [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) | Semántica de `Type=simple`, `Restart=always`, `MemoryMax`, `NoNewPrivileges`. |
| [systemd.exec — opciones de sandboxing](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html) | Detalle de `PrivateTmp`, `NoNewPrivileges` y límites de recursos. |
| [Linux LED class (sysfs)](https://www.kernel.org/doc/html/latest/leds/leds-class.html) | Interfaz `/sys/class/leds/*/brightness` y `trigger` usada por `unoq_leds.py`. |
| [Android ADB — documentación](https://developer.android.com/tools/adb) | `adb devices`, `adb shell`, `adb push`, `adb reverse`/`forward`, base del despliegue por USB. |
| [Python venv](https://docs.python.org/3/library/venv.html) | Creación de entornos virtuales; en la UNO Q se usa `--without-pip` porque la imagen no trae `ensurepip`. |
| [pip — descarga offline](https://pip.pypa.io/en/stable/cli/pip_download/) | Opciones `--platform`, `--python-version`, `--abi` y `--only-binary` para preparar `wheels/`. |

## 5. Seguridad web

| Recurso | Para qué sirve |
|---|---|
| [MDN — Content-Security-Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy) | Justifica la CSP aplicada en `app.py` y sus implicaciones en plantillas con JS inline. |
| [MDN — X-Frame-Options](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Frame-Options) | Motivo de `DENY` para impedir clickjacking sobre el kiosco. |
| [OWASP — Transaction Authorization](https://owasp.org/www-community/vulnerabilities/Transaction_Authorization_Checks) | Principio de validar el monto en el servidor y no confiar en el cliente. |

## 6. Documentación interna del proyecto

| Archivo | Contenido |
|---|---|
| [`../README.md`](../README.md) | Vista general, instalación, operación y advertencias. |
| [`datasheet.md`](datasheet.md) | Manual de usuario y operador, códigos de error y mantenimiento. |
| [`developer.md`](developer.md) | Arquitectura, contratos de módulos y guía de extensión. |
| [`BITACORA.md`](BITACORA.md) | Decisiones, problemas y lecciones aprendidas durante el desarrollo. |
