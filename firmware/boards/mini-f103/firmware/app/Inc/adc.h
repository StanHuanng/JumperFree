/**
  * file  : adc.h
  * brief : ADC1 readings of the JumperFree mini supply monitors and measurement inputs.
  */
#ifndef ADC_H
#define ADC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

typedef struct
{
  uint16_t raw_vref;   /* ch17 VREFINT        */
  uint16_t raw_v5;     /* ch2  PA2 V5_MON     */
  uint16_t raw_vee;    /* ch3  PA3 VEE_MON    */
  uint16_t raw_ver;    /* ch4  PA4 VER_ID     */
  uint16_t raw_ma;     /* ch0  PA0 MEAS_A     */
  uint16_t raw_mb;     /* ch5  PA5 MEAS_B     */
  int32_t  vdda_mv;    /* from VREFINT (1.20 V typical, 1.16-1.24 V) */
  int32_t  v5_mv;      /* +5V  = 2 x V5_MON (R6/R7 10k/10k)          */
  int32_t  vee_mv;     /* -7V  from VEE_MON (R8 10k to 3V3, R9 44.2k) */
  int32_t  ver_mv;     /* VER_ID pin voltage (R4 10k / R5 3.3k)       */
  int32_t  ma_mv;      /* MEAS_A pin voltage                          */
  int32_t  mb_mv;      /* MEAS_B pin voltage                          */
} adc_report_t;

void adc_init(void);
uint16_t adc_read(uint32_t channel, uint32_t samples);
void adc_measure(adc_report_t *r);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* ADC_H */
