/**
  * file  : jf2_link.c
  * brief : JF2 core bound to the JumperFree mini hardware.
  *           crosspoint (chip 0..6, x, y) -> ch_write(chip + 1, x, y)
  *           chip 6 y 6 (TEST_SRC)        -> PB8 high while any is closed
  *           all_open                     -> CH_RST pulse, PB8 low
  *           LED clip n (FLIP n)          -> RGB at the clip's panel position
  *           +5V / -7V out of range       -> HW_FAULT (all open, SAFE),
  *                                           matrix held in reset
  */
#include <string.h>
#include "adc.h"
#include "board.h"
#include "ch446q.h"
#include "clipmap.h"
#include "jf2.h"
#include "jf2_link.h"
#include "ws2812.h"

#define SUPPLY_CHECK_MS  500U

static Jf2Device s_dev;
static bool s_led_dirty;
static bool s_supply_bad;
static uint32_t s_last_check;
static uint32_t s_tick_last;
static uint64_t s_tick_high;
static uint8_t s_frame[JF2_FRAME_MAX + 1U];

/* PB8 TEST_DRV feeds U307 Y6 (TEST_SRC) through R409 1.5k: high while any
 * U307 Y6 switch is closed, so a net on TEST_SRC sees a weak 3.3 V */
static void test_drive(bool high)
{
  GPIOB->BSRR = high ? (1UL << 8) : (1UL << 24);
}

static bool test_src_used(void)
{
  for (uint32_t x = 0U; x < CH_X; x++)
  {
    if (ch_get(CH_SVC, x, SVC_Y_TEST))
    {
      return true;
    }
  }
  return false;
}

static void hw_set_crosspoint(void *ctx, uint8_t chip, uint8_t x, uint8_t y, bool on)
{
  (void)ctx;
  if (chip < JF2_CHIPS)
  {
    if (!ch_enabled() && !s_supply_bad)
    {
      ch_enable();  /* e.g. after console 'mx off'; the core only commits when ARMED */
    }
    ch_write(chip + 1U, x, y, on);
    if ((chip == JF2_SVC_CHIP) && (y == JF2_SVC_Y_TEST))
    {
      test_drive(test_src_used());
    }
  }
}

static void hw_all_open(void *ctx)
{
  (void)ctx;
  ch_clear();
  test_drive(false);
}

static void hw_set_led(void *ctx, uint8_t clip, uint8_t r, uint8_t g, uint8_t b)
{
  (void)ctx;
  if (clip < JF2_CLIPS)
  {
    ws2812_set(CLIPMAP[clip].led - 1U, r, g, b);
    s_led_dirty = true;
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

/* The core's buffer is valid only during this call */
static void emit(void *ctx, const uint8_t *line, size_t len)
{
  (void)ctx;
  board_uart_write(line, len);
}

/* Not a true per-boot nonce: device UID folded with SysTick and uptime */
static uint32_t boot_id(void)
{
  const volatile uint32_t *uid = (const volatile uint32_t *)UID_BASE;
  return uid[0] ^ uid[1] ^ uid[2] ^ SysTick->VAL ^ (board_millis() * 2654435761U);
}

static bool supplies_ok(void)
{
  adc_report_t r;
  adc_measure(&r);
  return (r.v5_mv >= 4600) && (r.v5_mv <= 5300) && (r.vee_mv >= -7500) && (r.vee_mv <= -6500);
}

static void check_supplies(void)
{
  bool bad = !supplies_ok();
  if (bad && !s_supply_bad)
  {
    jf2_fault_hw(&s_dev, true);  /* all open, SAFE, f=4 latched */
    ch_disable();
  }
  else if (!bad && s_supply_bad)
  {
    jf2_fault_hw(&s_dev, false);  /* source gone; FAULT CLEAR + HELLO re-arm */
    ch_enable();
  }
  s_supply_bad = bad;
}

void jf2_link_init(void)
{
  static const Jf2Hw hw = { NULL, hw_set_crosspoint, hw_all_open, hw_set_led, hw_now_ms };
  (void)jf2_init(&s_dev, &hw, emit, NULL, boot_id(),
                 ((board_boot()->reset_flags & RCC_CSR_SFTRSTF) != 0U) ? JF2_RESET_SW : JF2_RESET_POR);
  s_supply_bad = false;
  check_supplies();
  if (!s_supply_bad)
  {
    ch_enable();
  }
  s_last_check = board_millis();
}

void jf2_link_line(const char *line, size_t len)
{
  if (len > JF2_FRAME_MAX - 1U)
  {
    len = JF2_FRAME_MAX - 1U;  /* the core answers BAD_FRAME without LF */
    memcpy(s_frame, line, len);
    jf2_process_request(&s_dev, s_frame, len);
    return;
  }
  memcpy(s_frame, line, len);
  s_frame[len] = '\n';
  jf2_process_request(&s_dev, s_frame, len + 1U);
}

void jf2_link_poll(void)
{
  jf2_poll(&s_dev);
  if ((board_millis() - s_last_check) >= SUPPLY_CHECK_MS)
  {
    s_last_check = board_millis();
    check_supplies();
  }
  if (s_led_dirty)
  {
    s_led_dirty = false;
    ws2812_show();
  }
}

bool jf2_link_release(void)
{
  if (!s_dev.hello && (s_dev.epoch == 0U))
  {
    return false;  /* JF2 never touched the matrix since the last release */
  }
  jf2_reboot(&s_dev, boot_id(), JF2_RESET_SW);
  return true;
}
