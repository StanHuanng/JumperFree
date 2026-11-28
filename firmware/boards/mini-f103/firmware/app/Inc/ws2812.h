/**
  * file  : ws2812.h
  * brief : 48 x XL-1010RGBC-WS2812B on PB9 (TIM4_CH4 PWM + DMA1 ch7).
  *
  * Colours are given 0-255 and scaled by a global brightness level before
  * they are sent. The level is capped at WS2812_LEVEL_MAX, so the chain
  * cannot draw more than about 48 x 3 x 5 mA x 48/255 = 135 mA, however
  * the colours are set (each XL-1010 channel is a 5 mA constant current).
  */
#ifndef WS2812_H
#define WS2812_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

#define WS2812_COUNT        48U
#define WS2812_LEVEL_DEFAULT 8U   /* 8/255 of full scale */
#define WS2812_LEVEL_MAX    48U   /* hard cap, see above */

void ws2812_init(void);
void ws2812_hw_setup(void);  /* TIM4/PB9 registers only, no LED update */
void ws2812_set_level(uint32_t level);  /* clamped to 0..WS2812_LEVEL_MAX */
uint32_t ws2812_level(void);
void ws2812_set(uint32_t index, uint8_t r, uint8_t g, uint8_t b);  /* index 0 = RGB1 */
void ws2812_fill(uint8_t r, uint8_t g, uint8_t b);
void ws2812_show(void);            /* blocking, about 1.9 ms */
uint32_t ws2812_current_ma(void);  /* estimated LED current of the last frame */

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* WS2812_H */
