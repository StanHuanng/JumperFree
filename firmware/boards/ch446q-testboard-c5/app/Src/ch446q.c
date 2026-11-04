/**
  * file  : ch446q.c
  * brief : CH446Q serial address mode (datasheet v1D, section 5 / 7.2):
  *         shift ADDR6..ADDR0 = AY2 AY1 AY0 AX3 AX2 AX1 AX0 into DAT on 7
  *         CS/CK rising edges, then set DAT to the switch state (1 = on) and
  *         give STB a high pulse. DAT must stay stable until STB falls.
  *         Minimum timings are 10 ns; the datasheet recommends slow edges, so
  *         every phase waits ~0.3 us (about 8 us per switch).
  */
#include "ch446q.h"
#include "stm32_hal.h"

#define PIN_STB   (1UL << 8)   /* PA8  */
#define PIN_CK    (1UL << 9)   /* PA9  */
#define PIN_DAT   (1UL << 10)  /* PA10 */
#define PIN_AX0   (1UL << 11)  /* PA11 */
#define PIN_AX3   (1UL << 12)  /* PA12 */
#define PIN_AY2   (1UL << 5)   /* PB5  */
#define PIN_AY1   (1UL << 6)   /* PB6  */
#define PIN_AY0   (1UL << 7)   /* PB7  */
#define PIN_AX2   (1UL << 8)   /* PB8  */
#define PIN_AX1   (1UL << 9)   /* PB9  */

#define ADDR_PINS_A  (PIN_AX0 | PIN_AX3)
#define ADDR_PINS_B  (PIN_AY2 | PIN_AY1 | PIN_AY0 | PIN_AX2 | PIN_AX1)

#define DELAY_LOOPS  16U  /* ~0.3 us at 144 MHz */

volatile ch446q_diag_t ch446q_diag;

static uint8_t s_shift;  /* monitor: address shift register */

static void delay(void)
{
  for (volatile uint32_t i = DELAY_LOOPS; i != 0U; i--)
  {
  }
}

static void pin_a(uint32_t pin, uint32_t high)
{
  GPIOA->BSRR = (high != 0U) ? pin : (pin << 16);
}

static bool read_a(uint32_t pin)
{
  return (GPIOA->IDR & pin) != 0U;
}

/* Monitor: called with CS/CK high, i.e. right after a rising edge */
static void monitor_clock(void)
{
  if (!read_a(PIN_CK))
  {
    ch446q_diag.errors++;
  }
  s_shift = (uint8_t)(((s_shift << 1) | (read_a(PIN_DAT) ? 1U : 0U)) & 0x7FU);
  if (ch446q_diag.last_bits < 0xFFU)
  {
    ch446q_diag.last_bits++;
  }
}

/* Monitor: called with STB high. Returns the address the pins delivered. */
static uint8_t monitor_strobe(void)
{
  uint8_t addr = s_shift;
  bool on = read_a(PIN_DAT);

  if (!read_a(PIN_STB) || read_a(PIN_CK))
  {
    ch446q_diag.errors++;
  }
  if (((GPIOA->IDR & ADDR_PINS_A) != 0U) || ((GPIOB->IDR & ADDR_PINS_B) != 0U))
  {
    ch446q_diag.addr_pin_high++;
  }

  uint16_t bit = (uint16_t)(1U << (addr & 0x0FU));
  uint8_t y = (uint8_t)(addr >> 4);
  if (on)
  {
    ch446q_diag.latch[y] |= bit;
  }
  else
  {
    ch446q_diag.latch[y] &= (uint16_t)~bit;
  }

  ch446q_diag.strobes++;
  ch446q_diag.last_addr = addr;
  ch446q_diag.last_dat = on ? 1U : 0U;
  return addr;
}

static void write_addr(uint8_t addr, bool on)
{
  ch446q_diag.last_bits = 0U;

  for (int32_t bit = 6; bit >= 0; bit--)
  {
    pin_a(PIN_DAT, (addr >> bit) & 1U);
    delay();
    pin_a(PIN_CK, 1U);
    delay();
    monitor_clock();
    pin_a(PIN_CK, 0U);
    delay();
  }

  pin_a(PIN_DAT, on ? 1U : 0U);
  delay();
  pin_a(PIN_STB, 1U);
  delay();
  uint8_t seen = monitor_strobe();
  pin_a(PIN_STB, 0U);
  delay();
  pin_a(PIN_DAT, 0U);

  if ((seen != addr) || (ch446q_diag.last_bits != 7U) || ((ch446q_diag.last_dat != 0U) != on))
  {
    ch446q_diag.errors++;
  }
}

void ch446q_init(void)
{
  GPIOA->BSRR = (PIN_STB | PIN_CK | PIN_DAT | ADDR_PINS_A) << 16;
  GPIOB->BSRR = ADDR_PINS_B << 16;
  delay();

  for (uint32_t y = 0U; y < CH446Q_Y_LINES; y++)
  {
    ch446q_diag.latch[y] = 0xFFFFU;  /* unknown until all_open runs */
  }
  ch446q_diag.strobes = 0U;
  ch446q_diag.errors = 0U;
  ch446q_diag.addr_pin_high = 0U;
  ch446q_diag.snaps = 0U;
  ch446q_diag.model_errors = 0U;
  ch446q_diag.magic = CH446Q_DIAG_MAGIC;
}

void ch446q_write(uint8_t x, uint8_t y, bool on)
{
  if ((x < CH446Q_X_LINES) && (y < CH446Q_Y_LINES))
  {
    write_addr((uint8_t)((y << 4) | x), on);
  }
}

void ch446q_all_open(void)
{
  for (uint8_t addr = 0U; addr < (CH446Q_X_LINES * CH446Q_Y_LINES); addr++)
  {
    write_addr(addr, false);
  }
}

void ch446q_snapshot(const uint16_t expect[CH446Q_Y_LINES])
{
  uint32_t slot = ch446q_diag.snaps % CH446Q_SNAPS;
  bool same = true;

  for (uint32_t y = 0U; y < CH446Q_Y_LINES; y++)
  {
    ch446q_diag.snap[slot][y] = ch446q_diag.latch[y];
    same = same && (ch446q_diag.latch[y] == expect[y]);
  }
  if (!same)
  {
    ch446q_diag.model_errors++;
  }
  ch446q_diag.snaps++;
}
