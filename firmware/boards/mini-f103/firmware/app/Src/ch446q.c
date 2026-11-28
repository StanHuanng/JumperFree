/**
  * file  : ch446q.c
  * brief : CH446Q serial address mode (datasheet section 5 / 7.2), same bit
  *         order as the driver verified on the CH446Q test board:
  *         ADDR6..ADDR0 = AY2 AY1 AY0 AX3 AX2 AX1 AX0, MSB first, one CS/CK
  *         rising edge per bit; then DAT = switch state (1 = on) and a high
  *         pulse on the chip's STB. DAT stays stable until STB falls.
  *         Every phase waits ~0.5 us (datasheet minimums are tens of ns).
  */
#include "ch446q.h"
#include "board.h"

#define PIN_CLK   (1UL << 13)  /* PB13 */
#define PIN_DAT   (1UL << 15)  /* PB15 */
#define PIN_RST   (1UL << 8)   /* PA8, open-drain */

typedef struct
{
  GPIO_TypeDef *port;
  uint32_t      pin;
} stb_t;

static const stb_t s_stb[CH_CHIPS] = {
  { GPIOB, 1UL << 12 },  /* U301 CH1_STB */
  { GPIOB, 1UL << 14 },  /* U302 CH2_STB */
  { GPIOB, 1UL << 10 },  /* U303 CH3_STB */
  { GPIOB, 1UL << 11 },  /* U304 CH4_STB */
  { GPIOB, 1UL << 1  },  /* U305 CH5_STB */
  { GPIOB, 1UL << 0  },  /* U306 CH6_STB */
  { GPIOA, 1UL << 7  },  /* U307 CH7_STB */
};

static uint16_t s_state[CH_CHIPS][CH_Y];  /* bit x set = switch Y-X on */
static bool s_enabled;

static void delay(void)
{
  for (volatile uint32_t i = 8U; i != 0U; i--)
  {
  }
}

static void delay_us(uint32_t us)
{
  for (volatile uint32_t i = us * 10U; i != 0U; i--)
  {
  }
}

static void forget_all(void)
{
  for (uint32_t c = 0U; c < CH_CHIPS; c++)
  {
    for (uint32_t y = 0U; y < CH_Y; y++)
    {
      s_state[c][y] = 0U;
    }
  }
}

void ch_init(void)
{
  /* board.c already set CLK/DAT/STB low and RST released */
  s_enabled = false;
  forget_all();
}

bool ch_enabled(void)
{
  return s_enabled;
}

void ch_enable(void)
{
  GPIOA->BSRR = PIN_RST;        /* make sure everything starts open */
  delay_us(20U);
  GPIOA->BSRR = PIN_RST << 16;  /* RST low: chips accept writes */
  delay_us(5U);
  forget_all();
  s_enabled = true;
}

void ch_disable(void)
{
  GPIOA->BSRR = PIN_RST;
  forget_all();
  s_enabled = false;
}

void ch_clear(void)
{
  GPIOA->BSRR = PIN_RST;
  delay_us(20U);
  if (s_enabled)
  {
    GPIOA->BSRR = PIN_RST << 16;
    delay_us(5U);
  }
  forget_all();
}

void ch_write(uint32_t chip, uint32_t x, uint32_t y, bool on)
{
  if (!s_enabled || (chip < 1U) || (chip > CH_CHIPS) || (x >= CH_X) || (y >= CH_Y))
  {
    return;
  }
  const stb_t *stb = &s_stb[chip - 1U];
  uint32_t addr = (y << 4) | x;

  for (int32_t bit = 6; bit >= 0; bit--)
  {
    GPIOB->BSRR = ((addr >> bit) & 1U) ? PIN_DAT : (PIN_DAT << 16);
    delay();
    GPIOB->BSRR = PIN_CLK;
    delay();
    GPIOB->BSRR = PIN_CLK << 16;
    delay();
  }
  GPIOB->BSRR = on ? PIN_DAT : (PIN_DAT << 16);
  delay();
  stb->port->BSRR = stb->pin;
  delay();
  stb->port->BSRR = stb->pin << 16;
  delay();
  GPIOB->BSRR = PIN_DAT << 16;

  if (on)
  {
    s_state[chip - 1U][y] |= (uint16_t)(1U << x);
  }
  else
  {
    s_state[chip - 1U][y] &= (uint16_t)~(1U << x);
  }
}

bool ch_get(uint32_t chip, uint32_t x, uint32_t y)
{
  if ((chip < 1U) || (chip > CH_CHIPS) || (x >= CH_X) || (y >= CH_Y))
  {
    return false;
  }
  return (s_state[chip - 1U][y] & (1U << x)) != 0U;
}

uint32_t ch_count_on(void)
{
  uint32_t n = 0U;
  for (uint32_t c = 0U; c < CH_CHIPS; c++)
  {
    for (uint32_t y = 0U; y < CH_Y; y++)
    {
      for (uint16_t v = s_state[c][y]; v != 0U; v &= (uint16_t)(v - 1U))
      {
        n++;
      }
    }
  }
  return n;
}
