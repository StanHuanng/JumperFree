/**
  * file  : ws2812.c
  * brief : WS2812B on PB10 at 64 MHz (15.6 ns per cycle): bit period 1.25 us,
  *         T0H ~0.3 us, T1H ~0.75 us, reset low 300 us.
  *
  *         The high phase is a fixed run of NOPs in a function copied to RAM
  *         (no flash wait states), because polling DWT->CYCCNT from flash
  *         overshot by ~30 cycles: T0H came out 0.83 us and every 0 bit read
  *         as 1 (all LEDs full white). The low phase still waits on CYCCNT,
  *         since the chain tolerates a little extra low time. Interrupts are
  *         masked per byte; USART2 RX uses DMA (board.c) anyway.
  */
#include "ws2812.h"
#include "stm32f1xx.h"

#define PIN          (1UL << 10)  /* PB10 */
#define CYC_PERIOD   80U          /* 1.25 us */
#define CYC_RESET    (300U * 64U)

#define NOP2   __NOP(); __NOP();
#define NOP4   NOP2 NOP2
#define NOP8   NOP4 NOP4
#define NOP16  NOP8 NOP8

/* Measured cycles, max over all bits sent: [0] high time of 0 bits, [1] of
 * 1 bits (from just before the high write to just after the low write),
 * [2] bit period. Read over SWD to check the timing. */
volatile uint32_t ws2812_meas[3];

/* One byte, MSB first, with interrupts masked (~12 us). Masking per pixel or
 * per frame and resending every 100 ms were tried and looked worse on the
 * 5x5 module, so frames go out per byte and only when they change. */
__attribute__((section(".RamFunc"), noinline, long_call))
static void send_byte(uint8_t value)
{
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  for (uint32_t mask = 0x80U; mask != 0U; mask >>= 1)
  {
    uint32_t one = ((value & mask) != 0U) ? 1U : 0U;
    uint32_t start = DWT->CYCCNT;
    if (one != 0U)
    {
      GPIOB->BSRR = PIN;
      NOP16 NOP16 NOP8
      GPIOB->BSRR = PIN << 16;
    }
    else
    {
      GPIOB->BSRR = PIN;
      NOP8 NOP4
      GPIOB->BSRR = PIN << 16;
    }
    uint32_t t_high = DWT->CYCCNT - start;
    while ((DWT->CYCCNT - start) < CYC_PERIOD)
    {
    }
    uint32_t t_period = DWT->CYCCNT - start;
    if (t_high > ws2812_meas[one])
    {
      ws2812_meas[one] = t_high;
    }
    if (t_period > ws2812_meas[2])
    {
      ws2812_meas[2] = t_period;
    }
  }
  __set_PRIMASK(primask);
}

void ws2812_init(void)
{
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0U;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

  /* PB10: push-pull output, 50 MHz edges (MODE=11, CNF=00), idle low */
  GPIOB->BSRR = PIN << 16;
  GPIOB->CRH = (GPIOB->CRH & ~(0xFUL << 8)) | (0x3UL << 8);
}

void ws2812_show(const ws2812_rgb_t pixels[WS2812_COUNT])
{
  for (uint32_t i = 0U; i < WS2812_COUNT; i++)
  {
    /* WS2812B wire order is G, R, B */
    send_byte(pixels[i].g);
    send_byte(pixels[i].r);
    send_byte(pixels[i].b);
  }

  uint32_t start = DWT->CYCCNT;
  while ((DWT->CYCCNT - start) < CYC_RESET)
  {
  }
}
