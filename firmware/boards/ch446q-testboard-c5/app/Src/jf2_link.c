/**
  * file  : jf2_link.c
  * brief : JF2 core over USART2, crosspoints on the CH446Q test board.
  *         The ISR only stores bytes in a ring buffer; jf2_feed / jf2_poll,
  *         every reply and every CH446Q write run in the main loop, as
  *         firmware/ARCH.md requires.
  */
#include <string.h>
#include "jf2_link.h"
#include "jf2.h"
#include "ch446q.h"

#define LED_PORT          HAL_GPIOE
#define LED_PIN           HAL_GPIO_PIN_2
#define RING_SIZE         256U   /* power of two */
#define TX_TIMEOUT_MS     100U

static hal_uart_handle_t *s_uart;
static uint8_t s_rx_byte;
static volatile uint8_t s_rx_restart;

/* Written by the ISR (head), read by the main loop (tail) */
static uint8_t s_ring[RING_SIZE];
static volatile uint32_t s_head;
static volatile uint32_t s_tail;
static volatile uint32_t s_dropped;

static Jf2Device s_dev;

/* Mirror of the JF2 crosspoints (two 8x8 chips), drives the PE2 LED */
static bool s_points[2][8][8];
static uint8_t s_leds[16][3];
static uint32_t s_tick_last;
static uint64_t s_tick_high;

static void rx_start(void)
{
  if (HAL_UART_Receive_IT(s_uart, &s_rx_byte, 1U) == HAL_OK)
  {
    s_rx_restart = 0U;
  }
  else
  {
    s_rx_restart = 1U;
  }
}

static void rx_cplt_cb(hal_uart_handle_t *uart, uint32_t size_byte,
                       hal_uart_rx_event_types_t event)
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
    else
    {
      /* Ring full: the core sees a broken frame and replies with an error */
      s_dropped++;
    }
  }

  rx_start();
}

static void rx_error_cb(hal_uart_handle_t *uart)
{
  (void)uart;
  /* A blocking error (e.g. overrun) stops reception: restart from main loop */
  s_rx_restart = 1U;
}

static void show_points(void)
{
  bool any = false;
  for (uint32_t c = 0U; c < 2U; c++)
  {
    for (uint32_t x = 0U; x < 8U; x++)
    {
      for (uint32_t y = 0U; y < 8U; y++)
      {
        any = any || s_points[c][x][y];
      }
    }
  }
  HAL_GPIO_WritePin(LED_PORT, LED_PIN, any ? HAL_GPIO_PIN_SET : HAL_GPIO_PIN_RESET);
}

/* JF2 v01 topology: chip C1 X0-7 = Clip0-7, C2 X0-7 = Clip8-15, Y0-7 shared.
 * The test board has one CH446Q, so C1 Xn -> Xn and C2 Xn -> X(8+n), same Y:
 * ClipN sits on header H2 pin N+1 (X N). */
static void hw_set_crosspoint(void *ctx, uint8_t chip, uint8_t x, uint8_t y, bool on)
{
  (void)ctx;
  if ((chip < 2U) && (x < 8U) && (y < 8U))
  {
    ch446q_write((uint8_t)(chip * 8U + x), y, on);
    s_points[chip][x][y] = on;
  }
  show_points();
}

static void hw_all_open(void *ctx)
{
  (void)ctx;
  ch446q_all_open();
  memset(s_points, 0, sizeof s_points);
  show_points();
}

static void hw_set_led(void *ctx, uint8_t clip, uint8_t r, uint8_t g, uint8_t b)
{
  (void)ctx;
  if (clip < 16U)
  {
    s_leds[clip][0] = r;
    s_leds[clip][1] = g;
    s_leds[clip][2] = b;
  }
}

/* HAL_GetTick wraps after ~49 days; the core needs a monotonic 64-bit clock */
static uint64_t hw_now_ms(void *ctx)
{
  (void)ctx;
  uint32_t now = HAL_GetTick();
  if (now < s_tick_last)
  {
    s_tick_high += (uint64_t)1U << 32;
  }
  s_tick_last = now;
  return s_tick_high | now;
}

/* The core's buffer is only valid during this call: send it right away */
static void emit(void *ctx, const uint8_t *line, size_t len)
{
  (void)ctx;
  (void)HAL_UART_Transmit(s_uart, line, (uint32_t)len, TX_TIMEOUT_MS);
}

/* Not a true per-boot nonce: device UID folded with the SysTick counter */
static uint32_t boot_id(void)
{
  const volatile uint32_t *uid = (const volatile uint32_t *)UID_BASE;
  return uid[0] ^ uid[1] ^ uid[2] ^ SysTick->VAL ^ HAL_GetTick();
}

hal_status_t jf2_link_init(hal_uart_handle_t *uart)
{
  if (uart == NULL)
  {
    return HAL_INVALID_PARAM;
  }
  s_uart = uart;

  hal_status_t status = HAL_UART_RegisterRxCpltCallback(s_uart, rx_cplt_cb);
  if (status == HAL_OK)
  {
    status = HAL_UART_RegisterErrorCallback(s_uart, rx_error_cb);
  }
  if (status != HAL_OK)
  {
    return status;
  }

  static const Jf2Hw hw = {NULL, hw_set_crosspoint, hw_all_open, hw_set_led, hw_now_ms};
  ch446q_init();
  hw_all_open(NULL);
  if (!jf2_init(&s_dev, &hw, emit, NULL, boot_id(), JF2_RESET_POR))
  {
    return HAL_ERROR;
  }

  rx_start();
  return HAL_OK;
}

void jf2_link_process(void)
{
  if (s_rx_restart != 0U)
  {
    rx_start();
  }

  uint8_t chunk[64];
  size_t n = 0U;
  while ((s_tail != s_head) && (n < sizeof chunk))
  {
    chunk[n++] = s_ring[s_tail];
    s_tail = (s_tail + 1U) & (RING_SIZE - 1U);
  }
  if (n > 0U)
  {
    jf2_feed(&s_dev, chunk, n);
  }

  jf2_poll(&s_dev);

  /* After any CH446Q write, snapshot what the pins delivered vs s_points */
  static uint32_t seen_strobes;
  if (ch446q_diag.strobes != seen_strobes)
  {
    uint16_t expect[CH446Q_Y_LINES] = {0U};
    for (uint32_t c = 0U; c < 2U; c++)
    {
      for (uint32_t x = 0U; x < 8U; x++)
      {
        for (uint32_t y = 0U; y < 8U; y++)
        {
          if (s_points[c][x][y])
          {
            expect[y] |= (uint16_t)(1U << (c * 8U + x));
          }
        }
      }
    }
    ch446q_snapshot(expect);
    seen_strobes = ch446q_diag.strobes;
  }
}
