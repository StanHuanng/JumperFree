/**
  * file  : jf2_link.c
  * brief : JF2 core over USART2, crosspoints on the CH446Q test board.
  *         DMA stores received bytes in a ring buffer (board.c); jf2_feed /
  *         jf2_poll, every reply and every CH446Q write run in the main loop,
  *         as firmware/ARCH.md requires.
  */
#include <string.h>
#include "jf2_link.h"
#include "board.h"
#include "jf2.h"
#include "ch446q.h"
#include "display.h"

static Jf2Device s_dev;

/* Mirror of the JF2 crosspoints (two 8x8 chips), drives the PC13 LED */
static bool s_points[2][8][8];
static uint8_t s_leds[16][3];
static uint32_t s_tick_last;
static uint64_t s_tick_high;

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
  board_led(any);
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

/* board_millis wraps after ~49 days; the core needs a monotonic 64-bit clock */
static uint64_t hw_now_ms(void *ctx)
{
  (void)ctx;
  uint32_t now = board_millis();
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
  board_uart_write(line, len);
}

/* Not a true per-boot nonce: device UID folded with the SysTick counter */
static uint32_t boot_id(void)
{
  const volatile uint32_t *uid = (const volatile uint32_t *)UID_BASE;
  return uid[0] ^ uid[1] ^ uid[2] ^ SysTick->VAL ^ board_millis();
}

bool jf2_link_init(void)
{
  static const Jf2Hw hw = {NULL, hw_set_crosspoint, hw_all_open, hw_set_led, hw_now_ms};
  /* DMA queues bytes from board_init() on, so a HELLO sent during the ~1 s
   * start-up glyphs waits in the ring */
  display_init();
  ch446q_init();
  hw_all_open(NULL);
  return jf2_init(&s_dev, &hw, emit, NULL, boot_id(), JF2_RESET_POR);
}

void jf2_link_process(void)
{
  uint8_t chunk[64];
  size_t n = board_uart_read(chunk, sizeof chunk);
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

  display_update(&s_dev, board_millis());
}
