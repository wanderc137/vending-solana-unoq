/*
 * UNO Q GPIO Control — Servidor de comandos GPIO + Matriz LED
 * Placa: Arduino UNO Q (STM32U585, Zephyr RTOS)
 * FQBN: arduino:zephyr:unoq
 * 
 * Protocolo Serial a 115200 baud:
 *   READ <pin>          → lee estado digital del pin   → "OK 0" o "OK 1"
 *   WRITE <pin> <0|1>   → escribe estado digital        → "OK 0" o "OK 1"
 *   ANALOG <pin>        → lee ADC 12-bit (0-4095)       → "OK <valor>"
 *   PWM <pin> <0-255>   → salida PWM (analogWrite)      → "OK <valor>"
 *   MATRIX CLEAR        → limpia la matriz LED          → "OK CLEAR"
 *   MATRIX LINE1 <txt>  → guarda texto en línea 1       → "OK LINE1: <txt>"
 *   MATRIX LINE2 <txt>  → guarda texto en línea 2       → "OK LINE2: <txt>"
 *   MATRIX SHOW         → muestra línea1 + línea2 (scroll) → "OK SHOW"
 *   MATRIX <texto>      → atajo: scroll inmediato       → "OK SCROLL"
 *   Comando inválido    → "ERROR: comando no reconocido"
 *   Pin inválido        → "ERROR: pin invalido"
 * 
 * Pines válidos: 0–19 (D0–D13 + A0–A5 como 14–19)
 * También acepta notación: A0, A1, ..., A5
 * ADC: A0–A5 (pines 14–19)
 * PWM: 3, 5, 6, 9, 10, 11
 */

#include "ArduinoGraphics.h"
#include "Arduino_LED_Matrix.h"
#include <zephyr/device.h>
#include <zephyr/drivers/adc.h>

// ─── Constantes ───────────────────────────────────────────────────
const long BAUD_RATE = 115200;
const int  PIN_MIN   = 0;
const int  PIN_MAX   = 19;
const int  MAX_LINE  = 128;          // Longitud máxima del buffer de entrada

const char READY_MSG[] = "UNOQ_GPIO_READY";
const char ERR_CMD[]   = "ERROR: comando no reconocido";
const char ERR_PIN[]   = "ERROR: pin invalido";
const char ERR_PWM[]   = "ERROR: pin no soporta PWM";
const char ERR_ADC[]   = "ERROR: pin no soporta lectura analogica";

// Pines con capacidad PWM en UNO Q
const int  PWM_PINS[]  = {3, 5, 6, 9, 10, 11};
const int  PWM_COUNT   = 6;

// ─── Matriz LED ───────────────────────────────────────────────────
Arduino_LED_Matrix matrix;
char linea1[129] = "";   // Buffer para texto de línea 1
char linea2[129] = "";   // Buffer para texto de línea 2
int  tinySpeed      = 70;    // Velocidad de marquesina (ms, configurable)

// ─── Fuente 3×4 personalizada (para 2 líneas simultáneas) ────────
// Cada glifo: 4 bytes = 4 filas (top→bottom), 3 bits bajos = columnas (izq→der)
// Índice = carácter ASCII (32..95). Caracteres no definidos = espacio.
static const uint8_t FONT_3X4[96][4] = {
  {0,0,0,0}, // 32 ' '
  {2,2,0,2}, // 33 '!'
  {0,0,0,0}, // 34 '"' → no def
  {0,0,0,0}, // 35 '#'
  {0,0,0,0}, // 36 '$'
  {0,0,0,0}, // 37 '%'
  {0,0,0,0}, // 38 '&'
  {0,0,0,0}, // 39 '''
  {0,0,0,0}, // 40 '('
  {0,0,0,0}, // 41 ')'
  {0,0,0,0}, // 42 '*'
  {0,0,0,0}, // 43 '+'
  {0,0,0,0}, // 44 ','
  {0,0,7,0}, // 45 '-'
  {0,0,0,2}, // 46 '.'
  {0,0,0,0}, // 47 '/'
  {2,5,5,2}, // 48 '0'
  {2,6,2,7}, // 49 '1'
  {6,1,2,7}, // 50 '2'
  {6,1,2,6}, // 51 '3'
  {5,5,3,1}, // 52 '4'
  {7,4,6,1}, // 53 '5'
  {2,4,6,7}, // 54 '6'
  {7,1,2,2}, // 55 '7'
  {2,5,2,5}, // 56 '8'
  {2,5,3,1}, // 57 '9'
  {0,2,0,2}, // 58 ':'
  {0,0,0,0}, // 59 ';'
  {0,0,0,0}, // 60 '<'
  {0,0,0,0}, // 61 '='
  {0,0,0,0}, // 62 '>'
  {0,0,0,0}, // 63 '?'
  {0,0,0,0}, // 64 '@'
  {2,5,7,5}, // 65 'A'
  {6,5,6,7}, // 66 'B'
  {3,4,4,3}, // 67 'C'
  {6,5,5,6}, // 68 'D'
  {7,6,4,7}, // 69 'E'
  {7,6,4,4}, // 70 'F'
  {3,4,5,3}, // 71 'G'
  {5,7,5,5}, // 72 'H'
  {7,2,2,7}, // 73 'I'
  {3,1,5,2}, // 74 'J'
  {5,6,6,5}, // 75 'K'
  {4,4,4,7}, // 76 'L'
  {5,7,5,5}, // 77 'M'
  {5,7,7,5}, // 78 'N'
  {2,5,5,2}, // 79 'O'
  {6,5,6,4}, // 80 'P'
  {2,5,7,3}, // 81 'Q'
  {6,5,6,5}, // 82 'R'
  {3,4,2,6}, // 83 'S'
  {7,2,2,2}, // 84 'T'
  {5,5,5,2}, // 85 'U'
  {5,5,5,2}, // 86 'V'
  {5,5,7,5}, // 87 'W'
  {5,2,2,5}, // 88 'X'
  {5,2,2,2}, // 89 'Y'
  {7,2,4,7}, // 90 'Z'
  {0,0,0,0}, // 91 '['
  {0,0,0,0}, // 92 '\'
  {0,0,0,0}, // 93 ']'
  {0,0,0,0}, // 94 '^'
  {0,0,0,7}, // 95 '_'
};

// ─── Prototipos ───────────────────────────────────────────────────
String leerLinea();
int    parsearPin(const String &token);
bool   esPinPwm(int pin);
bool   esPinAnalog(int pin);
void   ejecutarRead(int pin);
void   ejecutarWrite(int pin, int valor);
void   ejecutarAnalog(int pin);
void   ejecutarPwm(int pin, int valor);
void   ejecutarTemp();
void   responder(const String &msg);

// Funciones de matriz LED
void   ejecutarMatrixClear();
void   ejecutarMatrixLine1(const String &texto);
void   ejecutarMatrixLine2(const String &texto);
void   ejecutarMatrixShow();
void   ejecutarMatrixScroll(const String &texto);
void   ejecutarMatrixTiny();
void   ejecutarMatrixTinyScroll(const String &texto);
void   renderTinyText(const char *texto, uint8_t y_offset, uint8_t *fb);

// ─── setup() ──────────────────────────────────────────────────────
void setup() {
  Serial.begin(BAUD_RATE);

  // Inicializar matriz LED
  matrix.begin();
  matrix.textFont(Font_5x7);
  matrix.textScrollSpeed(100);

  // Configurar todos los pines digitales como INPUT (alta impedancia, seguro)
  for (int p = PIN_MIN; p <= PIN_MAX; p++) {
    pinMode(p, INPUT);
  }

  // Configurar pines PWM como OUTPUT
  for (int i = 0; i < PWM_COUNT; i++) {
    pinMode(PWM_PINS[i], OUTPUT);
    digitalWrite(PWM_PINS[i], LOW);
  }

  // Pequeña pausa para que el USB CDC ACM esté listo
  delay(500);

  // Señal de inicialización: la placa está lista
  Serial.println(READY_MSG);
}

// ─── loop() ───────────────────────────────────────────────────────
void loop() {
  // Esperar a que llegue al menos una línea completa
  if (Serial.available() > 0) {
    String linea = leerLinea();

    // Ignorar líneas vacías
    if (linea.length() == 0) {
      return;
    }

    // ── Rama MATRIX (se procesa ANTES que los comandos GPIO) ────
    // Extraer primer token para comparación case-insensitive
    String firstToken = linea.substring(0, linea.indexOf(' ') != -1 ? linea.indexOf(' ') : linea.length());
    firstToken.toUpperCase();
    if (firstToken == "MATRIX") {
      String payload = linea.substring(6);  // Quitar "MATRIX"
      payload.trim();

      if (payload.length() == 0) {
        responder("ERROR: MATRIX requiere subcomando (CLEAR, LINE1, LINE2, SHOW, o texto)");
        return;
      }

      // Subcomando SPEED
      if (payload.startsWith("SPEED")) {
        if (payload.length() > 6 && payload.charAt(5) == ' ') {
          int ms = payload.substring(6).toInt();
          if (ms >= 10 && ms <= 500) {
            tinySpeed = ms;
            responder("OK SPEED: " + String(tinySpeed) + "ms");
          } else {
            responder("ERROR: velocidad 10-500 ms");
          }
        } else {
          responder("OK SPEED: " + String(tinySpeed) + "ms");
        }
        return;
      }

      // Subcomando CLEAR
      if (payload == "CLEAR") {
        ejecutarMatrixClear();
        return;
      }

      // Subcomando LINE1
      if (payload.startsWith("LINE1")) {
        if (payload.length() == 5) {
          // LINE1 sin argumentos → limpiar buffer
          ejecutarMatrixLine1("");
        } else if (payload.charAt(5) == ' ') {
          String texto = payload.substring(6);
          texto.trim();
          ejecutarMatrixLine1(texto);
        } else {
          responder(ERR_CMD);
        }
        return;
      }

      // Subcomando LINE2
      if (payload.startsWith("LINE2")) {
        if (payload.length() == 5) {
          // LINE2 sin argumentos → limpiar buffer
          ejecutarMatrixLine2("");
        } else if (payload.charAt(5) == ' ') {
          String texto = payload.substring(6);
          texto.trim();
          ejecutarMatrixLine2(texto);
        } else {
          responder(ERR_CMD);
        }
        return;
      }

      // Subcomando SHOW
      if (payload == "SHOW") {
        ejecutarMatrixShow();
        return;
      }

      // Subcomando TINY (2 líneas simultáneas con fuente 3x4)
      if (payload == "TINY") {
        ejecutarMatrixTiny();
        return;
      }

      // Subcomando TINYSCROLL (marquesina con fuente 3x4 centrada)
      if (payload.startsWith("TINYSCROLL")) {
        if (payload.length() == 10) {
          ejecutarMatrixTinyScroll(String(linea1));  // Usa linea1 si no hay texto
        } else if (payload.charAt(10) == ' ') {
          String texto = payload.substring(11);
          texto.trim();
          ejecutarMatrixTinyScroll(texto);
        } else {
          responder(ERR_CMD);
        }
        return;
      }

      // Atajo: MATRIX <cualquier texto> → scroll inmediato
      ejecutarMatrixScroll(payload);
      return;
    }

    // ── Comando TEMP (temperatura interna del chip) ────────────
    if (firstToken == "TEMP") {
      ejecutarTemp();
      return;
    }

    // ── Parsear comando GPIO ────────────────────────────────────
    // Formato: "CMD PIN [VALOR]"
    int firstSpace = linea.indexOf(' ');
    if (firstSpace == -1) {
      responder(ERR_CMD);
      return;
    }

    String cmd = linea.substring(0, firstSpace);
    cmd.toUpperCase();

    String resto = linea.substring(firstSpace + 1);
    resto.trim();

    // Extraer primer token (pin)
    int secondSpace = resto.indexOf(' ');
    String tokenPin;
    String tokenValor;

    if (secondSpace == -1) {
      tokenPin = resto;
    } else {
      tokenPin   = resto.substring(0, secondSpace);
      tokenValor = resto.substring(secondSpace + 1);
      tokenValor.trim();
    }

    tokenPin.trim();

    // ── Validar pin ─────────────────────────────────────────────
    int pin = parsearPin(tokenPin);
    if (pin < PIN_MIN || pin > PIN_MAX) {
      responder(ERR_PIN);
      return;
    }

    // ── Dispatch de comandos ────────────────────────────────────
    if (cmd == "READ") {
      ejecutarRead(pin);
    }
    else if (cmd == "WRITE") {
      // Validar que haya valor
      if (tokenValor.length() == 0) {
        responder(ERR_CMD);
        return;
      }
      int valor;
      if (tokenValor == "1" || tokenValor == "HIGH") {
        valor = HIGH;
      } else if (tokenValor == "0" || tokenValor == "LOW") {
        valor = LOW;
      } else {
        responder(ERR_CMD);
        return;
      }
      ejecutarWrite(pin, valor);
    }
    else if (cmd == "ANALOG") {
      if (!esPinAnalog(pin)) {
        responder(ERR_ADC);
        return;
      }
      ejecutarAnalog(pin);
    }
    else if (cmd == "PWM") {
      if (!esPinPwm(pin)) {
        responder(ERR_PWM);
        return;
      }
      if (tokenValor.length() == 0) {
        responder(ERR_CMD);
        return;
      }
      int valor = tokenValor.toInt();
      if (valor < 0 || valor > 255) {
        responder("ERROR: valor PWM debe ser 0-255");
        return;
      }
      ejecutarPwm(pin, valor);
    }
    else {
      responder(ERR_CMD);
    }
  }
}

// ─── leerLinea() ──────────────────────────────────────────────────
// Lee bytes del Serial hasta encontrar '\n' o timeout (1 segundo)
// Descarta '\r' y retorna la línea sin delimitadores
String leerLinea() {
  char          buffer[MAX_LINE + 1];
  int           idx     = 0;
  unsigned long start   = millis();
  const unsigned long TIMEOUT = 1000;  // 1 segundo de timeout

  while (idx < MAX_LINE) {
    if (Serial.available() > 0) {
      char c = Serial.read();

      if (c == '\n') {
        break;                     // Fin de línea
      }
      if (c == '\r') {
        continue;                  // Ignorar carriage return
      }

      buffer[idx++] = c;
      start = millis();            // Resetear timeout al recibir datos
    } else if (millis() - start > TIMEOUT) {
      break;                       // Timeout: devolver lo que se haya leído
    }
  }

  buffer[idx] = '\0';
  return String(buffer);
}

// ─── parsearPin() ─────────────────────────────────────────────────
// Soporta notación numérica (0-19) y con prefijo A (A0-A5 → 14-19)
int parsearPin(const String &token) {
  String t = token;
  t.trim();

  if (t.length() == 0) {
    return -1;
  }

  // ¿Comienza con 'A' o 'a'?
  if (t.charAt(0) == 'A' || t.charAt(0) == 'a') {
    String numStr = t.substring(1);

    // Verificar que hay un dígito después de 'A' (ej. "A" solo → inválido)
    if (numStr.length() == 0) {
      return -1;
    }

    int analogIndex = numStr.toInt();

    // Solo A0–A5 están disponibles
    if (analogIndex >= 0 && analogIndex <= 5) {
      return 14 + analogIndex;     // A0=14, A1=15, ..., A5=19
    }
    // A6, A7, etc. → inválido
    return -1;
  }

  // Notación numérica pura
  return t.toInt();
}

// ─── ejecutarRead() ───────────────────────────────────────────────
void ejecutarRead(int pin) {
  int state = digitalRead(pin);
  responder("OK " + String(state));
}

// ─── ejecutarWrite() ──────────────────────────────────────────────
void ejecutarWrite(int pin, int valor) {
  // Configurar el pin como OUTPUT antes de escribir
  // (si ya era OUTPUT, pinMode no hace daño repetirlo)
  pinMode(pin, OUTPUT);
  digitalWrite(pin, valor);

  int state = digitalRead(pin);    // Leer de vuelta para confirmar
  responder("OK " + String(state));
}

// ─── esPinPwm() ──────────────────────────────────────────────────
bool esPinPwm(int pin) {
  for (int i = 0; i < PWM_COUNT; i++) {
    if (PWM_PINS[i] == pin) return true;
  }
  return false;
}

// ─── esPinAnalog() ───────────────────────────────────────────────
// Pines analógicos: A0–A5 (pines lógicos 14–19)
bool esPinAnalog(int pin) {
  return (pin >= 14 && pin <= 19);
}

// ─── ejecutarAnalog() ────────────────────────────────────────────
void ejecutarAnalog(int pin) {
  int val = analogRead(pin);
  responder("OK " + String(val));
}

// ─── ejecutarPwm() ───────────────────────────────────────────────
void ejecutarPwm(int pin, int valor) {
  analogWrite(pin, valor);
  responder("OK " + String(valor));
}

// ─── responder() ──────────────────────────────────────────────────
void responder(const String &msg) {
  Serial.println(msg);
}

// ═══════════════════════════════════════════════════════════════════
//  FUNCIONES DE MATRIZ LED
// ═══════════════════════════════════════════════════════════════════

void ejecutarMatrixClear() {
  matrix.clear();
  linea1[0] = '\0';
  linea2[0] = '\0';
  responder("OK CLEAR");
}

void ejecutarMatrixLine1(const String &texto) {
  texto.toCharArray(linea1, 128);
  responder("OK LINE1: " + String(linea1));
}

void ejecutarMatrixLine2(const String &texto) {
  texto.toCharArray(linea2, 128);
  responder("OK LINE2: " + String(linea2));
}

void ejecutarMatrixShow() {
  // Modo TINY: 2 líneas simultáneas con fuente 3×4
  ejecutarMatrixTiny();
}

// ═══════════════════════════════════════════════════════════════════
//  FUNCIONES DE MATRIZ LED — FUENTE 3×4 (DOS LÍNEAS SIMULTÁNEAS)
// ═══════════════════════════════════════════════════════════════════

// Convierte minúscula a mayúscula y obtiene el glifo
static void getGlyph(char c, uint8_t glyph[4]) {
  if (c >= 'a' && c <= 'z') c -= 32;             // minúscula → mayúscula
  if (c < 32 || c > 95) c = 32;                   // fuera de rango → espacio
  uint8_t idx = c - 32;
  glyph[0] = FONT_3X4[idx][0];
  glyph[1] = FONT_3X4[idx][1];
  glyph[2] = FONT_3X4[idx][2];
  glyph[3] = FONT_3X4[idx][3];
}

// Renderiza texto en una región de 13×4 píxeles del framebuffer
// fb: framebuffer 13×8 (104 bytes, row-major)
void renderTinyText(const char *texto, uint8_t y_offset, uint8_t *fb) {
  memset(fb + y_offset * 13, 0, 52);  // Limpiar banda de 4 filas
  int x = 0;
  while (*texto && x < 13) {
    uint8_t glyph[4];
    getGlyph(*texto, glyph);
    for (int col = 0; col < 3 && (x + col) < 13; col++) {
      uint8_t colBits = 0;
      for (int row = 0; row < 4; row++) {
        colBits |= ((glyph[row] >> (2 - col)) & 1) << row;
      }
      // Escribir la columna en el framebuffer
      for (int row = 0; row < 4; row++) {
        fb[(y_offset + row) * 13 + (x + col)] = (colBits >> row) & 1;
      }
    }
    x += 4;  // 3px carácter + 1px espacio
    texto++;
  }
}

void ejecutarMatrixTiny() {
  uint8_t fb[104] = {0};

  if (strlen(linea1) > 0) {
    renderTinyText(linea1, 0, fb);   // Línea 1: filas 0-3
  }
  if (strlen(linea2) > 0) {
    renderTinyText(linea2, 4, fb);   // Línea 2: filas 4-7 (gap de 1px entre fila 3 y 4)
  }

  matrix.loadPixels(fb, sizeof(fb));
  responder("OK TINY");
}

// ═══════════════════════════════════════════════════════════════════
//  MARQUESINA CON FUENTE 3×4 (una línea, centrada verticalmente)
// ═══════════════════════════════════════════════════════════════════
#define TINYSCROLL_BUF_COLS 256

void ejecutarMatrixTinyScroll(const String &texto) {
  if (texto.length() == 0) {
    responder("ERROR: texto vacio para TINYSCROLL");
    return;
  }

  // Pre-renderizar todo el texto en una tira horizontal (4 filas × N columnas)
  int totalCols = texto.length() * 4;  // 3px char + 1px gap
  if (totalCols > TINYSCROLL_BUF_COLS) totalCols = TINYSCROLL_BUF_COLS;

  static uint8_t strip[TINYSCROLL_BUF_COLS * 4];  // 4 filas × 256 cols = 1KB
  memset(strip, 0, totalCols * 4);

  int x = 0;
  for (unsigned int i = 0; i < texto.length() && x < totalCols; i++) {
    uint8_t glyph[4];
    getGlyph(texto.charAt(i), glyph);
    for (int col = 0; col < 3 && (x + col) < totalCols; col++) {
      for (int row = 0; row < 4; row++) {
        if (glyph[row] & (1 << (2 - col))) {
          strip[row * totalCols + (x + col)] = 1;
        }
      }
    }
    x += 4;
  }

  // Scroll horizontal: deslizar ventana de 13 columnas
  int scrollEnd = totalCols + 13;  // scroll-in + scroll-out

  for (int window = -13; window < scrollEnd; window++) {
    uint8_t fb[104] = {0};
    for (int r = 0; r < 4; r++) {
      for (int c = 0; c < 13; c++) {
        int srcCol = window + c;
        if (srcCol >= 0 && srcCol < totalCols) {
          fb[(2 + r) * 13 + c] = strip[r * totalCols + srcCol];  // centrado: y=2
        }
      }
    }
    matrix.loadPixels(fb, sizeof(fb));
    delay(tinySpeed);  // velocidad de marquesina (configurable)
  }

  matrix.clear();
  responder("OK TINYSCROLL");
}

void ejecutarMatrixScroll(const String &texto) {
  matrix.clear();
  matrix.beginText(0, 0, 127, 127, 127);
  matrix.print(texto);
  matrix.endText(SCROLL_LEFT);
  responder("OK SCROLL");
}

// ═══════════════════════════════════════════════════════════════════
//  TEMPERATURA INTERNA DEL CHIP (STM32U585)
// ═══════════════════════════════════════════════════════════════════
// ADC1 canal 19: sensor de temperatura interno
// Calibración de fábrica: 30°C @ cal1, 130°C @ cal2, VREF=3.0V
#define TEMP_ADC_CHANNEL  19
#define TEMP_CAL1_ADDR    ((uint16_t*)0x0BFA0710UL)  // TS_CAL1 (30°C)
#define TEMP_CAL2_ADDR    ((uint16_t*)0x0BFA0742UL)  // TS_CAL2 (130°C)
#define TEMP_CAL1_TEMP    30.0f
#define TEMP_CAL2_TEMP    130.0f
#define TEMP_VREF         3.0f
#define TEMP_AVG_SLOPE    2.5f   // mV/°C típico del STM32U5

void ejecutarTemp() {
  // Leer ADC interno para el sensor de temperatura
  // En Zephyr/Arduino UNO Q, analogRead solo funciona con pines externos.
  // Usamos acceso directo al HAL de STM32 vía Zephyr ADC API.
  
  // Intentar vía Zephyr ADC
  const struct device *adc_dev = device_get_binding("ADC1");
  if (adc_dev == NULL) {
    responder("ERROR: ADC no disponible");
    return;
  }

  struct adc_channel_cfg ch_cfg = {
    .gain = ADC_GAIN_1,
    .reference = ADC_REF_INTERNAL,
    .acquisition_time = ADC_ACQ_TIME_MAX,
    .channel_id = TEMP_ADC_CHANNEL,
    .differential = 0,
  };

  int ret = adc_channel_setup(adc_dev, &ch_cfg);
  if (ret != 0) {
    responder("ERROR: config ADC");
    return;
  }

  struct adc_sequence seq = {
    .channels = BIT(TEMP_ADC_CHANNEL),
    .buffer = NULL,
    .buffer_size = 0,
    .resolution = 12,
    .oversampling = 0,
    .calibrate = 1,
  };

  // Necesitamos un buffer; asignamos
  uint16_t buf = 0;
  seq.buffer = &buf;
  seq.buffer_size = sizeof(buf);

  ret = adc_read(adc_dev, &seq);
  if (ret != 0) {
    // Fallback: intentar sin calibrate
    seq.calibrate = 0;
    ret = adc_read(adc_dev, &seq);
  }
  
  if (ret != 0) {
    responder("ERROR: lectura ADC");
    return;
  }

  // Conversión a temperatura usando calibración de fábrica
  uint16_t cal1 = *TEMP_CAL1_ADDR;
  uint16_t cal2 = *TEMP_CAL2_ADDR;

  float temp = ((float)(buf - cal1) * (TEMP_CAL2_TEMP - TEMP_CAL1_TEMP)) 
             / (float)(cal2 - cal1) + TEMP_CAL1_TEMP;

  char resp[32];
  snprintf(resp, sizeof(resp), "OK %.1f C (%d raw)", (double)temp, buf);
  responder(resp);
}
