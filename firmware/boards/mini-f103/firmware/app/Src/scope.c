/**
  * file  : scope.c
  * brief : Slow two-channel "scope" through the matrix.
  *         CH A: clip -> its BUS -> U307 Y4 -> MEAS_A divider/follower -> PA0
  *         CH B: clip -> its BUS -> U307 Y5 -> MEAS_B divider/follower -> PA5
  *         TIM3 update (TRGO) starts one ADC1 regular sequence (1 or 2
  *         channels); DMA1 ch1 moves the results to a RAM buffer.
  *
  *         Coexists with JF2 routing: the clip's BUS comes from the CH446Q
  *         shadow state; only the U307 Y4/Y5 switch (and, for a clip that is
  *         in no net, one temporary clip switch on a free BUS) is closed for
  *         the capture and opened again afterwards.
  *
  *         Front end: Vadc = 0.2545 x Vbus + 0.5758 x VDDA, input range about
  *         -7.4..+5.5 V. Analog bandwidth is set by R401/R403/R402 with C401
  *         100 pF (about 6 kHz) and R404/C402 1k/10n (about 16 kHz).
  */
#include <stdio.h>
#include "adc.h"
#include "board.h"
#include "ch446q.h"
#include "console.h"
#include "scope.h"
#include "ws2812.h"

#define SCOPE_BUF   1024U
#define ADC_CLK_HZ  10666667UL   /* 64 MHz / 6 */
#define TIM_CLK_HZ  64000000UL   /* APB1 32 MHz x2 */
#define CONV_CYC    20UL         /* 7.5 sample + 12.5 conversion */
#define NONE        0xFFU

static uint16_t s_buf[SCOPE_BUF];

/* BUS that already carries this clip (from the shadow state), or NONE */
static uint32_t clip_bus(uint32_t flip)
{
  for (uint32_t x = 0U; x < CH_X; x++)
  {
    if (ch_get(flip / 8U + 1U, x, flip % 8U))
    {
      return x;
    }
  }
  return NONE;
}

static bool bus_free(uint32_t x)
{
  for (uint32_t c = 1U; c <= CH_CHIPS; c++)
  {
    for (uint32_t y = 0U; y < CH_Y; y++)
    {
      if (ch_get(c, x, y))
      {
        return false;
      }
    }
  }
  return true;
}

typedef struct
{
  uint32_t flip;
  uint32_t bus;
  bool     temp;  /* clip switch closed only for this capture */
} probe_ch_t;

static bool attach(probe_ch_t *p, uint32_t svc_y)
{
  p->temp = false;
  p->bus = clip_bus(p->flip);
  if (p->bus == NONE)
  {
    for (uint32_t x = CH_X; x-- > 0U;)  /* high BUSes first: JF2 fills from 0 */
    {
      if (bus_free(x))
      {
        p->bus = x;
        break;
      }
    }
    if (p->bus == NONE)
    {
      return false;
    }
    ch_write(p->flip / 8U + 1U, p->bus, p->flip % 8U, true);
    p->temp = true;
  }
  ch_write(CH_SVC, p->bus, svc_y, true);
  return true;
}

static void detach(const probe_ch_t *p, uint32_t svc_y)
{
  ch_write(CH_SVC, p->bus, svc_y, false);
  if (p->temp)
  {
    ch_write(p->flip / 8U + 1U, p->bus, p->flip % 8U, false);
  }
}

/* Returns false on timeout or key press */
static bool capture(uint32_t nch, uint32_t rate, uint32_t total)
{
  RCC->APB1ENR |= RCC_APB1ENR_TIM3EN;
  RCC->AHBENR |= RCC_AHBENR_DMA1EN;

  /* TIM3: update event every 1/rate s, TRGO = update */
  uint32_t ticks = TIM_CLK_HZ / rate;
  uint32_t psc = (ticks + 65535U) / 65536U;
  TIM3->CR1 = 0U;
  TIM3->PSC = psc - 1U;
  TIM3->ARR = (ticks / psc) - 1U;
  TIM3->CR2 = 2UL << TIM_CR2_MMS_Pos;
  TIM3->EGR = TIM_EGR_UG;
  TIM3->SR = 0U;

  /* ADC1: ch0 (MEAS_A) [+ ch5 (MEAS_B)], 7.5 cycles, scan, DMA, TIM3_TRGO */
  uint32_t smpr2 = ADC1->SMPR2;
  ADC1->SMPR2 = (smpr2 & ~((7UL << 0) | (7UL << 15))) | (1UL << 0) | (1UL << 15);
  ADC1->SQR3 = 0UL | (5UL << 5);
  ADC1->SQR1 = (nch - 1U) << ADC_SQR1_L_Pos;
  ADC1->CR1 = (nch > 1U) ? ADC_CR1_SCAN : 0U;
  (void)ADC1->DR;
  ADC1->SR = 0U;

  DMA1_Channel1->CCR = 0U;
  DMA1->IFCR = DMA_IFCR_CGIF1;
  DMA1_Channel1->CPAR = (uint32_t)&ADC1->DR;
  DMA1_Channel1->CMAR = (uint32_t)s_buf;
  DMA1_Channel1->CNDTR = total;
  DMA1_Channel1->CCR = DMA_CCR_MINC | DMA_CCR_PSIZE_0 | DMA_CCR_MSIZE_0 | DMA_CCR_PL_1 | DMA_CCR_EN;

  /* EXTSEL 100 = TIM3_TRGO; changing EXTSEL in the same write does not start a conversion */
  ADC1->CR2 = ADC_CR2_ADON | ADC_CR2_TSVREFE | ADC_CR2_DMA | ADC_CR2_EXTTRIG | (4UL << ADC_CR2_EXTSEL_Pos);
  TIM3->CR1 = TIM_CR1_CEN;

  uint32_t limit = (uint32_t)(((uint64_t)total * 1000U) / ((uint64_t)rate * nch)) + 200U;
  uint32_t t0 = board_millis();
  bool ok = true;
  while ((DMA1->ISR & DMA_ISR_TCIF1) == 0U)
  {
    uint8_t c;
    if (((board_millis() - t0) > limit) || (board_uart_read(&c, 1U) != 0U))
    {
      ok = false;
      break;
    }
  }

  TIM3->CR1 = 0U;
  DMA1_Channel1->CCR = 0U;
  DMA1->IFCR = DMA_IFCR_CGIF1;
  /* Back to single software-started conversions (adc.c) */
  ADC1->CR2 = ADC_CR2_ADON | ADC_CR2_TSVREFE | ADC_CR2_EXTTRIG | ADC_CR2_EXTSEL;
  ADC1->CR1 = 0U;
  ADC1->SQR1 = 0U;
  ADC1->SMPR2 = smpr2;
  (void)ADC1->DR;
  return ok;
}

/* ------------------------------------------------------------------------
 * Logic edge capture on PB8 (one digital channel).
 * clip -> BUS -> U307 Y6 (TEST_SRC) -> R409 1.5k -> PB8 (FT input, floating).
 * PB8 is normally the TEST_DRV output: it is switched to input BEFORE Y6 is
 * closed so the probe never drives the bus. A tight loop with IRQs masked
 * reads GPIOB->IDR and stores the DWT cycle count of every level change.
 * --------------------------------------------------------------------- */
#define LA_PIN      (1UL << 8)
#define LA_EDGES    (SCOPE_BUF / 2U)   /* uint32 timestamps in the shared buffer */

/* Tight sampling loop, run from RAM (no flash wait states on branches).
 * Returns the number of timestamps in ts[] (ts[0] = trigger, already set). */
__attribute__((section(".RamFunc"), noinline, optimize("O2")))
static uint32_t la_loop(uint32_t *ts, uint32_t last, uint32_t ts0, uint32_t win, uint32_t *loops)
{
  volatile uint32_t *idr = &GPIOB->IDR;
  volatile uint32_t *cyc = &DWT->CYCCNT;
  uint32_t n = 1U;
  uint32_t k = 0U;
  for (;;)
  {
    uint32_t v = *idr & LA_PIN;
    k++;
    if (v != last)
    {
      ts[n++] = *cyc - ts0;
      last = v;
      if (n >= LA_EDGES)
      {
        break;
      }
    }
    else if ((*cyc - ts0) >= win)
    {
      break;
    }
  }
  *loops = k;
  return n;
}

void la_run(uint32_t flip, char trig, uint32_t wait_ms, uint32_t win_us)
{
  uint32_t *ts = (uint32_t *)(void *)s_buf;
  if (!ch_enabled())
  {
    out("matrix in reset (supplies?), no capture\r\n");
    return;
  }
  for (uint32_t x = 0U; x < CH_X; x++)
  {
    if (ch_get(CH_SVC, x, SVC_Y_TEST))
    {
      out("TEST_SRC (U307 Y6) in use on BUS%lu (JF 3V3 net?), no capture\r\n", (unsigned long)x);
      return;
    }
  }

  /* PB8 -> input floating (CNF 01, MODE 00) before anything is connected */
  uint32_t crh = GPIOB->CRH;
  GPIOB->CRH = (crh & ~(0xFUL << 0)) | (0x4UL << 0);

  probe_ch_t p = { flip, NONE, false };
  if (!attach(&p, SVC_Y_TEST))
  {
    GPIOB->CRH = crh;
    out("no free BUS for the clip\r\n");
    return;
  }
  board_delay_ms(2U);

  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  uint32_t f_cpu = 64000000UL;  /* HSE x8 or HSI/2 x16, both 64 MHz */
  uint32_t wait_cyc = (wait_ms > 60000U ? 60000U : wait_ms) * (f_cpu / 1000U);
  uint32_t win_cyc = (win_us > 60000000U / 64U ? 60000000U / 64U : win_us) * (f_cpu / 1000000U);
  uint32_t n = 0U;
  uint32_t level0;
  bool trig_ok = false;
  uint32_t loops = 0U;

  __disable_irq();
  uint32_t t0 = DWT->CYCCNT;
  uint32_t last = GPIOB->IDR & LA_PIN;
  /* trigger: f = falling, r = rising, a = any edge,
   * i = falling edge after the line was high (idle) for at least 50 us */
  uint32_t t_chg = t0;
  bool idle_ok = false;
  while ((DWT->CYCCNT - t0) < wait_cyc)
  {
    uint32_t v = GPIOB->IDR & LA_PIN;
    uint32_t now = DWT->CYCCNT;
    if (v != last)
    {
      idle_ok = (last != 0U) && ((now - t_chg) >= 50U * 64U);
      t_chg = now;
      if ((trig == 'a') || ((trig == 'f') && (v == 0U)) || ((trig == 'r') && (v != 0U)) ||
          ((trig == 'i') && (v == 0U) && idle_ok))
      {
        trig_ok = true;
        last = v;
        break;
      }
      last = v;
    }
  }
  level0 = (last != 0U) ? 0U : 1U;  /* level before the trigger edge */
  uint32_t ts0 = DWT->CYCCNT;
  if (trig_ok)
  {
    ts[0] = 0U;
    n = la_loop(ts, last, ts0, win_cyc, &loops);
  }
  uint32_t span = DWT->CYCCNT - ts0;
  __enable_irq();

  detach(&p, SVC_Y_TEST);
  GPIOB->BSRR = LA_PIN << 16;  /* TEST_DRV low */
  GPIOB->CRH = crh;

  if (!trig_ok)
  {
    out("LA NOTRIG level=%lu bus=%lu\r\n", (unsigned long)((last != 0U) ? 1U : 0U), (unsigned long)p.bus);
    return;
  }
  /* loop period = span / loops: the time resolution */
  out("LA n=%lu f=%lu level0=%lu bus=%lu temp=%u loop_ns=%lu span_us=%lu full=%u\r\n",
      (unsigned long)n, (unsigned long)f_cpu, (unsigned long)level0, (unsigned long)p.bus,
      p.temp ? 1U : 0U, (unsigned long)((loops != 0U) ? (uint32_t)(((uint64_t)span * 1000000000ULL / f_cpu) / loops) : 0U),
      (unsigned long)(span / (f_cpu / 1000000U)), (n >= LA_EDGES) ? 1U : 0U);
  char line[9U * 12U + 4U];
  for (uint32_t i = 0U; i < n; i += 12U)
  {
    uint32_t k = 0U;
    for (uint32_t j = i; (j < n) && (j < i + 12U); j++)
    {
      k += (uint32_t)snprintf(&line[k], sizeof(line) - k, "%08lX ", (unsigned long)ts[j]);
    }
    line[k++] = '\r';
    line[k++] = '\n';
    board_uart_write((const uint8_t *)line, k);
  }
  out("LA END\r\n");
}

/* ------------------------------------------------------------------------
 * Hardware edge capture on PB8 = TIM4_CH3 (TI3).
 *   IC3 <- TI3 rising, IC4 <- TI3 falling (CC4S = 10), so both edges are
 *   latched by hardware at 64 MHz (15.6 ns). TIM4 normally drives the LED
 *   chain on CH4/PB9: PB9 is held as a GPIO low (LED idle) meanwhile, and
 *   ws2812_hw_setup() gives TIM4 back afterwards (LEDs keep their colours).
 *   Each entry: (time in ticks << 1) | rising. Overflows extend the 16-bit
 *   counter; one rising and one falling edge can wait in hardware, so pulses
 *   down to a few ticks are timed exactly while the edge rate stays below
 *   the poll loop (about 0.3 us per edge pair). CCxOF = edges lost.
 * --------------------------------------------------------------------- */
/* Runs from flash: edge times are latched by TIM4, the loop only collects them */
__attribute__((noinline, optimize("O2")))
static uint32_t lac_loop(uint32_t *ts, char trig, uint32_t wait_tk, uint32_t win_tk, uint32_t *lost,
                         uint32_t *trig_ok)
{
  uint32_t ovf = 0U;
  uint32_t n = 0U;
  uint32_t t_trig = 0U;
  uint32_t prev_t = 0U;        /* last edge time (for the idle trigger) */
  uint32_t prev_r = 2U;        /* last edge type, 2 = none yet           */
  bool armed = false;
  *lost = 0U;
  for (;;)
  {
    uint32_t sr = TIM4->SR;
    uint32_t hi_now = ovf;
    uint32_t got = 0U;
    uint32_t t_e[2];
    uint32_t r_e[2];
    if ((sr & TIM_SR_CC3IF) != 0U)
    {
      uint32_t c = TIM4->CCR3;
      t_e[got] = ((((sr & TIM_SR_UIF) != 0U) && (c < 0x8000U)) ? (hi_now + 1U) : hi_now) << 16 | c;
      r_e[got++] = 1U;
    }
    if ((sr & TIM_SR_CC4IF) != 0U)
    {
      uint32_t c = TIM4->CCR4;
      t_e[got] = ((((sr & TIM_SR_UIF) != 0U) && (c < 0x8000U)) ? (hi_now + 1U) : hi_now) << 16 | c;
      r_e[got++] = 0U;
    }
    if ((sr & (TIM_SR_CC3OF | TIM_SR_CC4OF)) != 0U)
    {
      (*lost)++;
      TIM4->SR = ~(sr & (TIM_SR_CC3OF | TIM_SR_CC4OF));
    }
    if ((sr & TIM_SR_UIF) != 0U)
    {
      TIM4->SR = ~TIM_SR_UIF;
      ovf++;
    }
    if ((got == 2U) && ((int32_t)(t_e[1] - t_e[0]) < 0))
    {
      uint32_t a = t_e[0]; t_e[0] = t_e[1]; t_e[1] = a;
      a = r_e[0]; r_e[0] = r_e[1]; r_e[1] = a;
    }
    for (uint32_t i = 0U; i < got; i++)
    {
      uint32_t t = t_e[i];
      uint32_t r = r_e[i];
      if (!armed)
      {
        bool fire = (trig == 'a') || ((trig == 'f') && (r == 0U)) || ((trig == 'r') && (r == 1U)) ||
                    ((trig == 'i') && (r == 0U) && (prev_r == 1U) && ((t - prev_t) >= 50U * 64U));
        prev_t = t;
        prev_r = r;
        if (fire)
        {
          armed = true;
          t_trig = t;
          ts[n++] = r;
        }
      }
      else
      {
        ts[n++] = ((t - t_trig) << 1) | r;
        if (n >= LA_EDGES)
        {
          *trig_ok = 1U;
          return n;
        }
      }
    }
    /* CNT may have wrapped after SR was read: count that overflow here too */
    uint32_t cnt = TIM4->CNT;
    uint32_t now = ((((TIM4->SR & TIM_SR_UIF) != 0U) && (cnt < 0x8000U)) ? (ovf + 1U) : ovf) << 16 | cnt;
    if (armed)
    {
      if ((now - t_trig) >= win_tk)
      {
        break;
      }
    }
    else if (now >= wait_tk)
    {
      break;
    }
  }
  *trig_ok = armed ? 1U : 0U;
  return n;
}

void lac_run(uint32_t flip, char trig, uint32_t wait_ms, uint32_t win_us)
{
  uint32_t *ts = (uint32_t *)(void *)s_buf;
  if (!ch_enabled())
  {
    out("matrix in reset (supplies?), no capture\r\n");
    return;
  }
  for (uint32_t x = 0U; x < CH_X; x++)
  {
    if (ch_get(CH_SVC, x, SVC_Y_TEST))
    {
      out("TEST_SRC (U307 Y6) in use on BUS%lu (JF 3V3 net?), no capture\r\n", (unsigned long)x);
      return;
    }
  }
  if (wait_ms > 30000U)
  {
    wait_ms = 30000U;
  }
  if (win_us > 30000000U)
  {
    win_us = 30000000U;
  }

  uint32_t crh = GPIOB->CRH;
  /* PB8 input floating before anything is connected; PB9 GPIO open-drain low */
  GPIOB->BSRR = (1UL << 9) << 16;
  GPIOB->CRH = (crh & ~0xFFUL) | (0x4UL << 0) | (0x6UL << 4);

  probe_ch_t p = { flip, NONE, false };
  if (!attach(&p, SVC_Y_TEST))
  {
    GPIOB->CRH = crh;
    ws2812_hw_setup();
    out("no free BUS for the clip\r\n");
    return;
  }
  board_delay_ms(2U);

  /* TIM4: free-running 64 MHz, IC3 = TI3 rising, IC4 = TI3 falling, no filter */
  TIM4->CR1 = 0U;
  TIM4->DIER = 0U;
  TIM4->CCER = 0U;
  TIM4->PSC = 0U;
  TIM4->ARR = 0xFFFFU;
  TIM4->CCMR2 = (1UL << TIM_CCMR2_CC3S_Pos) | (2UL << TIM_CCMR2_CC4S_Pos);
  TIM4->CCER = TIM_CCER_CC3E | TIM_CCER_CC4E | TIM_CCER_CC4P;
  TIM4->EGR = TIM_EGR_UG;
  TIM4->SR = 0U;
  (void)TIM4->CCR3;
  (void)TIM4->CCR4;

  uint32_t lost = 0U;
  uint32_t ok = 0U;
  __disable_irq();
  TIM4->CR1 = TIM_CR1_CEN;
  uint32_t n = lac_loop(ts, trig, wait_ms * 64000U, win_us * 64U, &lost, &ok);
  TIM4->CR1 = 0U;
  __enable_irq();
  uint32_t level_now = ((GPIOB->IDR & LA_PIN) != 0U) ? 1U : 0U;

  detach(&p, SVC_Y_TEST);
  GPIOB->BSRR = LA_PIN << 16;  /* TEST_DRV low */
  GPIOB->CRH = crh;
  ws2812_hw_setup();

  if ((ok == 0U) || (n == 0U))
  {
    out("LA NOTRIG level=%lu bus=%lu\r\n", (unsigned long)level_now, (unsigned long)p.bus);
    return;
  }
  out("LAC n=%lu f=64000000 bus=%lu temp=%u lost=%lu full=%u\r\n", (unsigned long)n, (unsigned long)p.bus,
      p.temp ? 1U : 0U, (unsigned long)lost, (n >= LA_EDGES) ? 1U : 0U);
  char line[9U * 12U + 4U];
  for (uint32_t i = 0U; i < n; i += 12U)
  {
    uint32_t k = 0U;
    for (uint32_t j = i; (j < n) && (j < i + 12U); j++)
    {
      k += (uint32_t)snprintf(&line[k], sizeof(line) - k, "%08lX ", (unsigned long)ts[j]);
    }
    line[k++] = '\r';
    line[k++] = '\n';
    board_uart_write((const uint8_t *)line, k);
  }
  out("LA END\r\n");
}

void scope_run(uint32_t flip_a, uint32_t flip_b, uint32_t rate, uint32_t n)
{
  uint32_t nch = (flip_b != SCOPE_NONE) ? 2U : 1U;
  if (!ch_enabled())
  {
    out("matrix in reset (supplies?), no scope\r\n");
    return;
  }
  if ((flip_b != SCOPE_NONE) && (flip_a == flip_b))
  {
    nch = 1U;
    flip_b = SCOPE_NONE;
  }
  uint32_t rate_max = ADC_CLK_HZ / (CONV_CYC * nch);
  if (rate < 10U)
  {
    rate = 10U;
  }
  if (rate > rate_max)
  {
    rate = rate_max;
  }
  uint32_t n_max = SCOPE_BUF / nch;
  if ((n == 0U) || (n > n_max))
  {
    n = n_max;
  }

  probe_ch_t a = { flip_a, NONE, false };
  probe_ch_t b = { flip_b, NONE, false };
  if (!attach(&a, SVC_Y_MEAS_A))
  {
    out("no free BUS for clip A\r\n");
    return;
  }
  if ((nch > 1U) && !attach(&b, SVC_Y_MEAS_B))
  {
    detach(&a, SVC_Y_MEAS_A);
    out("no free BUS for clip B\r\n");
    return;
  }
  if ((nch > 1U) && (a.bus == b.bus))
  {
    out("note: both clips on BUS%lu (same net)\r\n", (unsigned long)a.bus);
  }

  int32_t raw_vref = (int32_t)adc_read(17U, 16U);
  int32_t vdda = (raw_vref != 0) ? (1200 * 4095 / raw_vref) : 3300;
  board_delay_ms(5U);  /* divider and RC settle */

  uint32_t psc = ((TIM_CLK_HZ / rate) + 65535U) / 65536U;
  uint32_t t_real = TIM_CLK_HZ / (psc * ((TIM_CLK_HZ / rate) / psc));
  bool ok = capture(nch, rate, n * nch);

  detach(&a, SVC_Y_MEAS_A);
  if (nch > 1U)
  {
    detach(&b, SVC_Y_MEAS_B);
  }
  if (!ok)
  {
    out("SCOPE ABORT (timeout or key)\r\n");
    return;
  }

  out("SCOPE n=%lu ch=%lu rate=%lu vdda=%ld busA=%lu tempA=%u busB=%ld tempB=%u\r\n",
      (unsigned long)n, (unsigned long)nch, (unsigned long)t_real, (long)vdda,
      (unsigned long)a.bus, a.temp ? 1U : 0U, (nch > 1U) ? (long)b.bus : -1L, b.temp ? 1U : 0U);
  char line[3U * 32U + 4U];
  uint32_t total = n * nch;
  for (uint32_t i = 0U; i < total; i += 32U)
  {
    uint32_t k = 0U;
    for (uint32_t j = i; (j < total) && (j < i + 32U); j++)
    {
      k += (uint32_t)snprintf(&line[k], sizeof(line) - k, "%03X", s_buf[j] & 0xFFFU);
    }
    line[k++] = '\r';
    line[k++] = '\n';
    board_uart_write((const uint8_t *)line, k);
  }
  out("SCOPE END\r\n");
}
