/**
  * file  : ws2812.h
  * brief : WS2812B chain on PB10 (5x5 module on the CH446Q test board),
  *         bit-banged at 64 MHz from RAM, interrupts masked per byte (~12 us).
  */
#ifndef WS2812_H
#define WS2812_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

#define WS2812_COUNT  25U

typedef struct
{
  uint8_t r, g, b;
} ws2812_rgb_t;

/* Configure PB10 and the DWT cycle counter. Call after board_init(). */
void ws2812_init(void);

/* Send all WS2812_COUNT pixels (chain order) and the reset gap (~1.1 ms). */
void ws2812_show(const ws2812_rgb_t pixels[WS2812_COUNT]);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* WS2812_H */
