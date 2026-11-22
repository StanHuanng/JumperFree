/**
  * file  : oled_demo.h
  * brief : JumperFree v0.4 demo on the 律动小板 (STM32C532CCT6).
  *         0.96" SSD1306 OLED on software I2C (PB6 = SCL, PB7 = SDA, open-drain,
  *         ~100 kHz because the lines run through the CH446Q matrix).
  *         PA0 (pull-up) is a "button" that the JumperFree matrix presses by
  *         joining it to GND.
  *         Console on USART2 (ST-Link VCP, 115200 8N1), one command per line:
  *           id none|pb6|pb7|pa0|off   wiring check: pull one pin low, release
  *                                     the others ("off" = back to the demo)
  *           scan                      I2C scan, prints "SCAN <addr hex>..."
  *           stat                      demo state
  */
#ifndef OLED_DEMO_H
#define OLED_DEMO_H

#include "stm32_hal.h"

hal_status_t oled_demo_init(hal_uart_handle_t *uart);
void oled_demo_process(void);

#endif /* OLED_DEMO_H */
