/**
  * file  : ws2812.c
  * brief : XL-1010RGBC-WS2812B chain driven by TIM4_CH4 PWM, one DMA write of
  *         CCR4 per bit (DMA1 channel 7 = TIM4_UP).
  *
  * XL-1010 timing (datasheet p.8): T0H >= 0.3 us, T1H >= 0.9 us,
  * T0L >= 0.9 us, T1L >= 0.3 us, bit >= 1.2 us, reset low >= 200 us,
  * GRB order, MSB first.
  * TIM4 clock 64 MHz (APB1 32 MHz x2), period 90 ticks = 1.406 us:
  *   0 bit: 26 ticks high (0.41 us) + 64 low (1.00 us)
  *   1 bit: 64 ticks high (1.00 us) + 26 low (0.41 us)
  * PB9 is open-drain with R501 1k to +5V, so the high level is 5 V
  * (VIH >= 2.8 V); the 1k rise costs a few tens of ns of high time.
  */
#include "ws2812.h"
#include "board.h"

#define TICKS_PERIOD  90U
#define TICKS_0       26U
#define TICKS_1       64U
#define LEAD_SLOTS    4U    /* low slots before the data           */
#define TAIL_SLOTS    180U  /* 180 x 1.406 us = 253 us reset low  */
#define BITS          (WS2812_COUNT * 24U)
#define SLOTS         (LEAD_SLOTS + BITS + TAIL_SLOTS)

static uint8_t s_rgb[WS2812_COUNT][3];
static uint16_t s_slots[SLOTS];
static uint32_t s_level = WS2812_LEVEL_DEFAULT;
static uint32_t s_current_ma;

/* TIM4 + PB9 for the LED chain; also used to give TIM4 back after the
 * PB8 input-capture probe (scope.c), the LEDs keep their latched colours */
void ws2812_hw_setup(void)
{
  RCC->APB1ENR |= RCC_APB1ENR_TIM4EN;
  RCC->AHBENR |= RCC_AHBENR_DMA1EN;

  TIM4->CR1 = 0U;
  TIM4->PSC = 0U;
  TIM4->ARR = TICKS_PERIOD - 1U;
  TIM4->CCR4 = 0U;
  /* PWM mode 1 (high while CNT < CCR4), CCR4 preload */
  TIM4->CCMR2 = (6UL << TIM_CCMR2_OC4M_Pos) | TIM_CCMR2_OC4PE;
  TIM4->CCER = TIM_CCER_CC4E;
  TIM4->CR1 = TIM_CR1_ARPE;
  TIM4->EGR = TIM_EGR_UG;

  /* PB9: alternate function open-drain, 50 MHz (MODE=11, CNF=11) */
  GPIOB->CRH = (GPIOB->CRH & ~(0xFUL << 4)) | (0xFUL << 4);
}

void ws2812_init(void)
{
  ws2812_hw_setup();
  ws2812_fill(0U, 0U, 0U);
  ws2812_show();
}

void ws2812_set_level(uint32_t level)
{
  s_level = (level > WS2812_LEVEL_MAX) ? WS2812_LEVEL_MAX : level;
}

uint32_t ws2812_level(void)
{
  return s_level;
}

void ws2812_set(uint32_t index, uint8_t r, uint8_t g, uint8_t b)
{
  if (index < WS2812_COUNT)
  {
    s_rgb[index][0] = r;
    s_rgb[index][1] = g;
    s_rgb[index][2] = b;
  }
}

void ws2812_fill(uint8_t r, uint8_t g, uint8_t b)
{
  for (uint32_t i = 0U; i < WS2812_COUNT; i++)
  {
    ws2812_set(i, r, g, b);
  }
}

static uint32_t scale(uint8_t c)
{
  return ((uint32_t)c * s_level + 127U) / 255U;
}

void ws2812_show(void)
{
  uint16_t *p = s_slots;
  uint32_t sum = 0U;

  for (uint32_t i = 0U; i < LEAD_SLOTS; i++)
  {
    *p++ = 0U;
  }
  for (uint32_t n = 0U; n < WS2812_COUNT; n++)
  {
    uint32_t r = scale(s_rgb[n][0]);
    uint32_t g = scale(s_rgb[n][1]);
    uint32_t b = scale(s_rgb[n][2]);
    sum += r + g + b;
    uint32_t grb = (g << 16) | (r << 8) | b;
    for (int32_t bit = 23; bit >= 0; bit--)
    {
      *p++ = ((grb >> bit) & 1U) ? TICKS_1 : TICKS_0;
    }
  }
  for (uint32_t i = 0U; i < TAIL_SLOTS; i++)
  {
    *p++ = 0U;
  }
  /* 5 mA per channel at 255, plus 0.35 mA quiescent per LED */
  s_current_ma = (sum * 5U + 127U) / 255U + (WS2812_COUNT * 35U) / 100U;

  /* Stop, rewind, load CCR4 = 0 into the shadow register */
  TIM4->CR1 &= ~TIM_CR1_CEN;
  TIM4->DIER = 0U;
  DMA1_Channel7->CCR = 0U;
  DMA1->IFCR = DMA_IFCR_CGIF7;
  TIM4->CCR4 = 0U;
  TIM4->CNT = 0U;
  TIM4->EGR = TIM_EGR_UG;

  /* Each update event writes the next slot into the CCR4 preload */
  DMA1_Channel7->CPAR = (uint32_t)&TIM4->CCR4;
  DMA1_Channel7->CMAR = (uint32_t)s_slots;
  DMA1_Channel7->CNDTR = SLOTS;
  DMA1_Channel7->CCR = DMA_CCR_DIR | DMA_CCR_MINC | DMA_CCR_PSIZE_0 |
                       DMA_CCR_MSIZE_0 | DMA_CCR_PL_1 | DMA_CCR_EN;
  TIM4->DIER = TIM_DIER_UDE;
  TIM4->CR1 |= TIM_CR1_CEN;

  while ((DMA1->ISR & DMA_ISR_TCIF7) == 0U)
  {
  }
  /* The last DMA write is a tail 0; let it take effect, then stop low */
  uint32_t t0 = board_millis();
  while ((board_millis() - t0) < 1U)
  {
  }
  TIM4->CR1 &= ~TIM_CR1_CEN;
  TIM4->DIER = 0U;
  DMA1_Channel7->CCR = 0U;
}

uint32_t ws2812_current_ma(void)
{
  return s_current_ma;
}
