/**
  * file  : oled_demo.c
  * brief : JumperFree mini demo, see oled_demo.h.
  *         Robot eyes on a 128x64 SSD1306. When JumperFree joins PA0 to GND,
  *         the eyes turn happy. All I2C is bit-banged so it can run slowly
  *         and tolerate the ~100 ohm / ~200 pF of a matrix path.
  */
#include <math.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "oled_demo.h"

#define FW_VERSION      "1.0"
#define LED_PORT        HAL_GPIOE
#define LED_PIN         HAL_GPIO_PIN_2
#define SCL_BIT         6U            /* PB6 */
#define SDA_BIT         7U            /* PB7 */
#define BTN_BIT         0U            /* PA0 */
#define I2C_HALF_US     5U            /* 5 us half period = 100 kHz */
#define RING_SIZE       256U
#define W               128
#define H               64

/* ------------------------------------------------------------------ timing */

static uint32_t s_cyc_per_us;
static bool s_dwt_ok;

static void timing_init(void)
{
  s_cyc_per_us = SystemCoreClock / 1000000U;
  if (s_cyc_per_us == 0U)
  {
    s_cyc_per_us = 144U;
  }
#if defined(DCB)
  DCB->DEMCR |= DCB_DEMCR_TRCENA_Msk;
#else
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
#endif
  DWT->CYCCNT = 0U;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  uint32_t a = DWT->CYCCNT;
  for (volatile uint32_t i = 0U; i < 100U; i++)
  {
  }
  s_dwt_ok = (DWT->CYCCNT != a);
}

static void delay_us(uint32_t us)
{
  if (s_dwt_ok)
  {
    uint32_t t0 = DWT->CYCCNT;
    uint32_t n = us * s_cyc_per_us;
    while ((DWT->CYCCNT - t0) < n)
    {
    }
  }
  else
  {
    /* Fallback: ~4 cycles per loop, errs on the slow side */
    for (volatile uint32_t i = us * s_cyc_per_us / 4U; i != 0U; i--)
    {
    }
  }
}

/* ------------------------------------------------------------- I2C (soft) */

static inline void scl_hi(void) { GPIOB->BSRR = 1UL << SCL_BIT; }
static inline void scl_lo(void) { GPIOB->BSRR = 1UL << (SCL_BIT + 16U); }
static inline void sda_hi(void) { GPIOB->BSRR = 1UL << SDA_BIT; }
static inline void sda_lo(void) { GPIOB->BSRR = 1UL << (SDA_BIT + 16U); }
static inline bool scl_in(void) { return ((GPIOB->IDR >> SCL_BIT) & 1UL) != 0UL; }
static inline bool sda_in(void) { return ((GPIOB->IDR >> SDA_BIT) & 1UL) != 0UL; }

/* Release SCL and wait for it to rise (slow RC through the matrix) */
static bool scl_release(void)
{
  scl_hi();
  for (uint32_t i = 0U; i < 200U; i++)
  {
    if (scl_in())
    {
      return true;
    }
    delay_us(1U);
  }
  return false;
}

static bool i2c_start(void)
{
  sda_hi();
  if (!scl_release())
  {
    return false;
  }
  delay_us(I2C_HALF_US);
  if (!sda_in())
  {
    return false;  /* SDA stuck low */
  }
  sda_lo();
  delay_us(I2C_HALF_US);
  scl_lo();
  delay_us(I2C_HALF_US);
  return true;
}

static void i2c_stop(void)
{
  sda_lo();
  delay_us(I2C_HALF_US);
  (void)scl_release();
  delay_us(I2C_HALF_US);
  sda_hi();
  delay_us(I2C_HALF_US);
}

static bool i2c_write(uint8_t b)
{
  for (uint32_t i = 0U; i < 8U; i++)
  {
    if ((b & 0x80U) != 0U)
    {
      sda_hi();
    }
    else
    {
      sda_lo();
    }
    b <<= 1;
    delay_us(I2C_HALF_US);
    if (!scl_release())
    {
      return false;
    }
    delay_us(I2C_HALF_US);
    scl_lo();
  }
  sda_hi();
  delay_us(I2C_HALF_US);
  if (!scl_release())
  {
    return false;
  }
  delay_us(I2C_HALF_US);
  bool ack = !sda_in();
  scl_lo();
  delay_us(I2C_HALF_US);
  return ack;
}

static bool i2c_probe(uint8_t addr)
{
  bool ok = i2c_start() && i2c_write((uint8_t)(addr << 1));
  i2c_stop();
  return ok;
}

/* ----------------------------------------------------------------- SSD1306 */

static uint8_t s_addr;
static uint8_t s_fb[W * H / 8];

static bool oled_cmds(const uint8_t *c, uint32_t n)
{
  bool ok = i2c_start() && i2c_write((uint8_t)(s_addr << 1)) && i2c_write(0x00U);
  for (uint32_t i = 0U; ok && (i < n); i++)
  {
    ok = i2c_write(c[i]);
  }
  i2c_stop();
  return ok;
}

static bool oled_init(void)
{
  static const uint8_t init[] = {
    0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0x20, 0x00,
    0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6,
    0x2E, 0xAF
  };
  return oled_cmds(init, sizeof init);
}

static bool oled_flush(void)
{
  static const uint8_t win[] = { 0x21, 0x00, 0x7F, 0x22, 0x00, 0x07 };
  if (!oled_cmds(win, sizeof win))
  {
    return false;
  }
  bool ok = i2c_start() && i2c_write((uint8_t)(s_addr << 1)) && i2c_write(0x40U);
  for (uint32_t i = 0U; ok && (i < sizeof s_fb); i++)
  {
    ok = i2c_write(s_fb[i]);
  }
  i2c_stop();
  return ok;
}

/* ----------------------------------------------------------------- drawing */

static void px(int x, int y, bool on)
{
  if ((x < 0) || (x >= W) || (y < 0) || (y >= H))
  {
    return;
  }
  uint8_t m = (uint8_t)(1U << (y & 7));
  if (on)
  {
    s_fb[(y >> 3) * W + x] |= m;
  }
  else
  {
    s_fb[(y >> 3) * W + x] &= (uint8_t)~m;
  }
}

static void hline(int x0, int x1, int y, bool on)
{
  for (int x = x0; x <= x1; x++)
  {
    px(x, y, on);
  }
}

/* Filled rounded rectangle, top-left (x, y) */
static void rrect(int x, int y, int w, int h, int r, bool on)
{
  if ((w <= 0) || (h <= 0))
  {
    return;
  }
  if (r > w / 2)
  {
    r = w / 2;
  }
  if (r > h / 2)
  {
    r = h / 2;
  }
  for (int j = 0; j < h; j++)
  {
    int inset = 0;
    float d = -1.0f;
    if (j < r)
    {
      d = (float)r - (float)j - 0.5f;
    }
    else if (j >= h - r)
    {
      d = (float)(j - (h - r)) + 0.5f;
    }
    if (d >= 0.0f)
    {
      inset = (int)((float)r - sqrtf((float)(r * r) - d * d) + 0.5f);
    }
    hline(x + inset, x + w - 1 - inset, y + j, on);
  }
}

/* 5x7 font, ASCII 0x20..0x5A, column-major, LSB = top row */
static const uint8_t FONT[][5] = {
  {0x00,0x00,0x00,0x00,0x00},{0x00,0x00,0x5F,0x00,0x00},{0x00,0x07,0x00,0x07,0x00},
  {0x14,0x7F,0x14,0x7F,0x14},{0x24,0x2A,0x7F,0x2A,0x12},{0x23,0x13,0x08,0x64,0x62},
  {0x36,0x49,0x56,0x20,0x50},{0x00,0x08,0x07,0x03,0x00},{0x00,0x1C,0x22,0x41,0x00},
  {0x00,0x41,0x22,0x1C,0x00},{0x2A,0x1C,0x7F,0x1C,0x2A},{0x08,0x08,0x3E,0x08,0x08},
  {0x00,0x80,0x70,0x30,0x00},{0x08,0x08,0x08,0x08,0x08},{0x00,0x00,0x60,0x60,0x00},
  {0x20,0x10,0x08,0x04,0x02},{0x3E,0x51,0x49,0x45,0x3E},{0x00,0x42,0x7F,0x40,0x00},
  {0x72,0x49,0x49,0x49,0x46},{0x21,0x41,0x49,0x4D,0x33},{0x18,0x14,0x12,0x7F,0x10},
  {0x27,0x45,0x45,0x45,0x39},{0x3C,0x4A,0x49,0x49,0x31},{0x41,0x21,0x11,0x09,0x07},
  {0x36,0x49,0x49,0x49,0x36},{0x46,0x49,0x49,0x29,0x1E},{0x00,0x00,0x14,0x00,0x00},
  {0x00,0x40,0x34,0x00,0x00},{0x00,0x08,0x14,0x22,0x41},{0x14,0x14,0x14,0x14,0x14},
  {0x00,0x41,0x22,0x14,0x08},{0x02,0x01,0x59,0x09,0x06},{0x3E,0x41,0x5D,0x59,0x4E},
  {0x7C,0x12,0x11,0x12,0x7C},{0x7F,0x49,0x49,0x49,0x36},{0x3E,0x41,0x41,0x41,0x22},
  {0x7F,0x41,0x41,0x41,0x3E},{0x7F,0x49,0x49,0x49,0x41},{0x7F,0x09,0x09,0x09,0x01},
  {0x3E,0x41,0x41,0x51,0x73},{0x7F,0x08,0x08,0x08,0x7F},{0x00,0x41,0x7F,0x41,0x00},
  {0x20,0x40,0x41,0x3F,0x01},{0x7F,0x08,0x14,0x22,0x41},{0x7F,0x40,0x40,0x40,0x40},
  {0x7F,0x02,0x1C,0x02,0x7F},{0x7F,0x04,0x08,0x10,0x7F},{0x3E,0x41,0x41,0x41,0x3E},
  {0x7F,0x09,0x09,0x09,0x06},{0x3E,0x41,0x51,0x21,0x5E},{0x7F,0x09,0x19,0x29,0x46},
  {0x26,0x49,0x49,0x49,0x32},{0x03,0x01,0x7F,0x01,0x03},{0x3F,0x40,0x40,0x40,0x3F},
  {0x1F,0x20,0x40,0x20,0x1F},{0x3F,0x40,0x38,0x40,0x3F},{0x63,0x14,0x08,0x14,0x63},
  {0x03,0x04,0x78,0x04,0x03},{0x61,0x59,0x49,0x4D,0x43},
};

static void text(int x, int y, const char *s, int scale)
{
  for (; *s != '\0'; s++)
  {
    char c = *s;
    if ((c >= 'a') && (c <= 'z'))
    {
      c = (char)(c - 32);
    }
    if ((c < 0x20) || (c > 0x5A))
    {
      c = '?';
    }
    const uint8_t *g = FONT[c - 0x20];
    for (int col = 0; col < 5; col++)
    {
      for (int row = 0; row < 7; row++)
      {
        if (((g[col] >> row) & 1U) != 0U)
        {
          for (int a = 0; a < scale; a++)
          {
            for (int b = 0; b < scale; b++)
            {
              px(x + col * scale + a, y + row * scale + b, true);
            }
          }
        }
      }
    }
    x += 6 * scale;
  }
}

static void text_c(int y, const char *s, int scale)
{
  int w = (int)strlen(s) * 6 * scale - scale;
  text((W - w) / 2, y, s, scale);
}

/* -------------------------------------------------------------- the eyes */

#define EYE_W   34
#define EYE_H   34
#define EYE_R   9
#define EYE_Y   26
#define EYE_LX  38
#define EYE_RX  90

static uint32_t s_rng = 0x1234567U;
static float s_lx, s_ly, s_tx, s_ty;
static uint32_t s_next_look, s_next_blink, s_blink_t0;
static uint32_t s_taps;

static uint32_t rnd(uint32_t n)
{
  s_rng ^= s_rng << 13;
  s_rng ^= s_rng >> 17;
  s_rng ^= s_rng << 5;
  return s_rng % n;
}

static void eye(int cx, int cy, float open, bool happy)
{
  int x = cx - EYE_W / 2;
  if (happy)
  {
    /* ^ shape: dome, hollowed from below */
    rrect(x, cy - EYE_H / 2 + 4, EYE_W, EYE_H - 8, EYE_W / 2, true);
    rrect(x + 2, cy - EYE_H / 2 + 14, EYE_W - 4, EYE_H - 8, EYE_W / 2 - 2, false);
    return;
  }
  int h = (int)((float)EYE_H * open);
  if (h < 3)
  {
    h = 3;
  }
  rrect(x, cy - h / 2, EYE_W, h, EYE_R, true);
}

static void render_eyes(uint32_t now, bool happy)
{
  memset(s_fb, 0, sizeof s_fb);

  if (!happy && ((int32_t)(now - s_next_look) >= 0))
  {
    s_tx = (float)((int)rnd(21) - 10);
    s_ty = (float)((int)rnd(11) - 5);
    if (rnd(4) == 0U)
    {
      s_tx = 0.0f;
      s_ty = 0.0f;
    }
    s_next_look = now + 900U + rnd(2200);
  }
  if (happy)
  {
    s_tx = 0.0f;
    s_ty = -2.0f;
  }
  s_lx += (s_tx - s_lx) * 0.35f;
  s_ly += (s_ty - s_ly) * 0.35f;

  float open = 1.0f;
  if ((int32_t)(now - s_next_blink) >= 0)
  {
    s_blink_t0 = now;
    s_next_blink = now + 1800U + rnd(3000);
  }
  uint32_t bt = now - s_blink_t0;
  if (bt < 240U)
  {
    float k = (float)bt / 120.0f;  /* 0..2 */
    open = (k < 1.0f) ? (1.0f - k) : (k - 1.0f);
  }

  int dx = (int)s_lx;
  int dy = (int)s_ly;
  eye(EYE_LX + dx, EYE_Y + dy, open, happy);
  eye(EYE_RX + dx, EYE_Y + dy, open, happy);

  if (happy)
  {
    char line[24];
    (void)snprintf(line, sizeof line, "JF TAPPED ME! X%lu", (unsigned long)s_taps);
    text_c(55, line, 1);
  }
  else
  {
    text_c(55, "JUMPERFREE MINI", 1);
  }
}

static void render_splash(void)
{
  memset(s_fb, 0, sizeof s_fb);
  text_c(6, "JUMPERFREE", 2);
  text_c(30, "OLED VIA CH446Q", 1);
  text_c(42, "SCL/SDA ON THE MATRIX", 1);
  text_c(54, "READY TO PLAY", 1);
}

/* ----------------------------------------------------------------- pins */

typedef enum { ID_OFF, ID_NONE, ID_PB6, ID_PB7, ID_PA0 } id_mode_t;
static id_mode_t s_id = ID_OFF;

static void pa0_config(bool drive_low)
{
  hal_gpio_config_t g = {0};
  if (drive_low)
  {
    g.mode = HAL_GPIO_MODE_OUTPUT;
    g.output_type = HAL_GPIO_OUTPUT_OPENDRAIN;
    g.speed = HAL_GPIO_SPEED_FREQ_LOW;
    g.pull = HAL_GPIO_PULL_UP;
    g.init_state = HAL_GPIO_PIN_RESET;
  }
  else
  {
    g.mode = HAL_GPIO_MODE_INPUT;
    g.pull = HAL_GPIO_PULL_UP;
  }
  (void)HAL_GPIO_Init(HAL_GPIOA, HAL_GPIO_PIN_0, &g);
}

static void pins_init(void)
{
  HAL_RCC_GPIOA_EnableClock();
  HAL_RCC_GPIOB_EnableClock();
  HAL_RCC_GPIOE_EnableClock();

  hal_gpio_config_t g = {0};
  g.mode = HAL_GPIO_MODE_OUTPUT;
  g.output_type = HAL_GPIO_OUTPUT_OPENDRAIN;
  g.speed = HAL_GPIO_SPEED_FREQ_MEDIUM;
  g.pull = HAL_GPIO_PULL_UP;
  g.init_state = HAL_GPIO_PIN_SET;
  (void)HAL_GPIO_Init(HAL_GPIOB, HAL_GPIO_PIN_6 | HAL_GPIO_PIN_7, &g);
  pa0_config(false);

  hal_gpio_config_t led = {0};
  led.mode = HAL_GPIO_MODE_OUTPUT;
  led.output_type = HAL_GPIO_OUTPUT_PUSHPULL;
  led.speed = HAL_GPIO_SPEED_FREQ_LOW;
  led.pull = HAL_GPIO_PULL_NO;
  led.init_state = HAL_GPIO_PIN_RESET;
  (void)HAL_GPIO_Init(LED_PORT, LED_PIN, &led);
}

static void pins_release(void)
{
  scl_hi();
  sda_hi();
  pa0_config(false);
}

static bool btn_pressed(void)
{
  return ((GPIOA->IDR >> BTN_BIT) & 1UL) == 0UL;
}

/* ----------------------------------------------------------------- console */

static hal_uart_handle_t *s_uart;
static uint8_t s_rx_byte;
static volatile uint8_t s_rx_restart;
static uint8_t s_ring[RING_SIZE];
static volatile uint32_t s_head;
static volatile uint32_t s_tail;
static char s_line[64];
static uint32_t s_line_len;

static void out(const char *fmt, ...)
{
  char buf[160];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  if (n > 0)
  {
    if ((size_t)n >= sizeof buf)
    {
      n = (int)sizeof buf - 1;
    }
    (void)HAL_UART_Transmit(s_uart, (const uint8_t *)buf, (uint32_t)n, 100U);
  }
}

static void rx_start(void)
{
  s_rx_restart = (HAL_UART_Receive_IT(s_uart, &s_rx_byte, 1U) == HAL_OK) ? 0U : 1U;
}

static void rx_cplt_cb(hal_uart_handle_t *uart, uint32_t size_byte, hal_uart_rx_event_types_t event)
{
  (void)uart;
  (void)event;
  if (size_byte == 1U)
  {
    uint32_t next = (s_head + 1U) & (RING_SIZE - 1U);
    if (next != s_tail)
    {
      s_ring[s_head] = s_rx_byte;
      s_head = next;
    }
  }
  rx_start();
}

static void rx_error_cb(hal_uart_handle_t *uart)
{
  (void)uart;
  s_rx_restart = 1U;
}

/* ----------------------------------------------------------------- demo */

static bool s_oled_ok;
static bool s_oled_reported;
static uint32_t s_next_try;
static uint32_t s_frames;
static uint32_t s_splash_until;
static bool s_btn_last;

static const char *id_name(id_mode_t m)
{
  static const char *const n[] = { "off", "none", "pb6", "pb7", "pa0" };
  return n[m];
}

static void set_id(id_mode_t m)
{
  pins_release();
  s_id = m;
  if (m == ID_PB6)
  {
    scl_lo();
  }
  else if (m == ID_PB7)
  {
    sda_lo();
  }
  else if (m == ID_PA0)
  {
    pa0_config(true);
  }
  else if (m == ID_OFF)
  {
    s_oled_ok = false;
    s_oled_reported = false;
    s_next_try = 0U;
  }
  out("OK id %s\r\n", id_name(m));
}

static void cmd_scan(void)
{
  if (s_id == ID_OFF)
  {
    s_id = ID_NONE;  /* the demo must not talk on the bus in between */
  }
  pins_release();
  delay_us(50U);
  out("SCAN");
  uint32_t n = 0U;
  for (uint8_t a = 0x08U; a < 0x78U; a++)
  {
    if (i2c_probe(a))
    {
      out(" %02X", a);
      n++;
    }
  }
  out("%s\r\n", (n == 0U) ? " none" : "");
  out("SCL=%u SDA=%u (idle should be 1 1)\r\n", scl_in() ? 1U : 0U, sda_in() ? 1U : 0U);
}

static void execute(char *line)
{
  char *cmd = strtok(line, " \t");
  char *arg = strtok(NULL, " \t");
  if (cmd == NULL)
  {
    return;
  }
  if (strcmp(cmd, "id") == 0)
  {
    static const char *const n[] = { "off", "none", "pb6", "pb7", "pa0" };
    for (uint32_t i = 0U; i < 5U; i++)
    {
      if ((arg != NULL) && (strcmp(arg, n[i]) == 0))
      {
        set_id((id_mode_t)i);
        return;
      }
    }
    out("ERR id none|pb6|pb7|pa0|off\r\n");
  }
  else if (strcmp(cmd, "scan") == 0)
  {
    cmd_scan();
  }
  else if (strcmp(cmd, "stat") == 0)
  {
    out("STAT fw=%s mode=%s oled=%u addr=%02X pa0=%u taps=%lu frames=%lu dwt=%u\r\n",
        FW_VERSION, (s_id == ID_OFF) ? "demo" : id_name(s_id), s_oled_ok ? 1U : 0U, s_addr,
        btn_pressed() ? 0U : 1U, (unsigned long)s_taps, (unsigned long)s_frames, s_dwt_ok ? 1U : 0U);
  }
  else
  {
    out("ERR unknown (id/scan/stat)\r\n");
  }
}

static void console_poll(void)
{
  if (s_rx_restart != 0U)
  {
    rx_start();
  }
  while (s_tail != s_head)
  {
    char c = (char)s_ring[s_tail];
    s_tail = (s_tail + 1U) & (RING_SIZE - 1U);
    if ((c == '\r') || (c == '\n'))
    {
      if (s_line_len > 0U)
      {
        s_line[s_line_len] = '\0';
        s_line_len = 0U;
        execute(s_line);
      }
    }
    else if (s_line_len < sizeof s_line - 1U)
    {
      s_line[s_line_len++] = c;
    }
  }
}

hal_status_t oled_demo_init(hal_uart_handle_t *uart)
{
  if (uart == NULL)
  {
    return HAL_INVALID_PARAM;
  }
  s_uart = uart;
  timing_init();
  pins_init();
  if ((HAL_UART_RegisterRxCpltCallback(s_uart, rx_cplt_cb) != HAL_OK) ||
      (HAL_UART_RegisterErrorCallback(s_uart, rx_error_cb) != HAL_OK))
  {
    return HAL_ERROR;
  }
  const volatile uint32_t *uid = (const volatile uint32_t *)UID_BASE;
  s_rng ^= uid[0] ^ uid[1] ^ uid[2];
  rx_start();
  out("\r\nOLED_DEMO %s ready (PB6 SCL, PB7 SDA, PA0 button)\r\n", FW_VERSION);
  return HAL_OK;
}

void oled_demo_process(void)
{
  console_poll();
  if (s_id != ID_OFF)
  {
    /* steady: PE2 also sits in a clip, a blinking pin would confuse the check */
    HAL_GPIO_WritePin(LED_PORT, LED_PIN, HAL_GPIO_PIN_SET);
    return;
  }

  uint32_t now = HAL_GetTick();
  bool btn = btn_pressed();
  if (btn != s_btn_last)
  {
    s_btn_last = btn;
    if (btn)
    {
      s_taps++;
    }
    out("EVT pa0 %s\r\n", btn ? "low (pressed by matrix)" : "high");
  }

  if (!s_oled_ok)
  {
    if ((int32_t)(now - s_next_try) < 0)
    {
      return;
    }
    s_next_try = now + 1000U;
    s_addr = i2c_probe(0x3CU) ? 0x3CU : (i2c_probe(0x3DU) ? 0x3DU : 0U);
    if ((s_addr != 0U) && oled_init())
    {
      s_oled_ok = true;
      s_oled_reported = false;
      out("EVT oled ok addr=%02X\r\n", s_addr);
      render_splash();
      (void)oled_flush();
      s_splash_until = now + 2500U;
      s_next_blink = now + 3000U;
    }
    else if (!s_oled_reported)
    {
      s_oled_reported = true;
      out("EVT oled no ACK (check SCL/SDA route, VCC, GND)\r\n");
    }
    HAL_GPIO_WritePin(LED_PORT, LED_PIN, HAL_GPIO_PIN_RESET);
    return;
  }

  if ((int32_t)(now - s_splash_until) < 0)
  {
    return;
  }
  render_eyes(now, btn);
  if (oled_flush())
  {
    s_frames++;
    HAL_GPIO_WritePin(LED_PORT, LED_PIN, (btn && ((now / 120U) & 1U)) ? HAL_GPIO_PIN_RESET : HAL_GPIO_PIN_SET);
  }
  else
  {
    s_oled_ok = false;
    s_oled_reported = false;
    out("EVT oled lost\r\n");
  }
}
