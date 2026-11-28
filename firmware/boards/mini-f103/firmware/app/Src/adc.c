/**
  * file  : adc.c
  * brief : ADC1 single conversions, software start, 239.5-cycle sample time
  *         (the monitor dividers have 2.5-8 kOhm source impedance).
  *         VDDA = 3V3A (FB1 after the 3V3 LDO) is the ADC reference, so every
  *         result is scaled with the VREFINT-derived VDDA.
  */
#include "adc.h"
#include "board.h"

#define VREFINT_MV  1200

void adc_init(void)
{
  RCC->APB2ENR |= RCC_APB2ENR_ADC1EN;

  /* All used channels at 239.5 cycles: ch0-9 in SMPR2, ch17 in SMPR1 */
  ADC1->SMPR2 = 0x3FFFFFFFUL;
  ADC1->SMPR1 = 7UL << ADC_SMPR1_SMP17_Pos;
  ADC1->SQR1 = 0U;  /* one conversion */

  ADC1->CR2 = ADC_CR2_ADON | ADC_CR2_TSVREFE;
  board_delay_ms(2U);  /* tSTAB + VREFINT start-up */

  ADC1->CR2 |= ADC_CR2_RSTCAL;
  while ((ADC1->CR2 & ADC_CR2_RSTCAL) != 0U)
  {
  }
  ADC1->CR2 |= ADC_CR2_CAL;
  while ((ADC1->CR2 & ADC_CR2_CAL) != 0U)
  {
  }

  /* Software trigger: EXTSEL = 111 (SWSTART), EXTTRIG on */
  ADC1->CR2 |= ADC_CR2_EXTSEL | ADC_CR2_EXTTRIG;
}

uint16_t adc_read(uint32_t channel, uint32_t samples)
{
  uint32_t sum = 0U;
  ADC1->SQR3 = channel;
  for (uint32_t i = 0U; i < samples; i++)
  {
    ADC1->CR2 |= ADC_CR2_SWSTART;
    while ((ADC1->SR & ADC_SR_EOC) == 0U)
    {
    }
    sum += ADC1->DR & 0xFFFU;
  }
  return (uint16_t)(sum / samples);
}

static int32_t to_mv(uint16_t raw, int32_t vdda_mv)
{
  return ((int32_t)raw * vdda_mv + 2047) / 4095;
}

void adc_measure(adc_report_t *r)
{
  r->raw_vref = adc_read(17U, 32U);
  r->raw_v5   = adc_read(2U, 32U);
  r->raw_vee  = adc_read(3U, 32U);
  r->raw_ver  = adc_read(4U, 32U);
  r->raw_ma   = adc_read(0U, 32U);
  r->raw_mb   = adc_read(5U, 32U);

  r->vdda_mv = (r->raw_vref != 0U) ? (VREFINT_MV * 4095 / (int32_t)r->raw_vref) : 0;
  r->v5_mv   = 2 * to_mv(r->raw_v5, r->vdda_mv);
  r->ver_mv  = to_mv(r->raw_ver, r->vdda_mv);
  r->ma_mv   = to_mv(r->raw_ma, r->vdda_mv);
  r->mb_mv   = to_mv(r->raw_mb, r->vdda_mv);

  /* VEE_MON = 3V3 + (VEE - 3V3) * 10k / 54.2k  ->  VEE = 3V3 - (3V3 - VEE_MON) * 5.42
   * R8 goes to 3V3 (before FB1); 3V3 ~= 3V3A, so VDDA stands in for it. */
  int32_t mon = to_mv(r->raw_vee, r->vdda_mv);
  r->vee_mv = r->vdda_mv - ((r->vdda_mv - mon) * 542) / 100;
}
