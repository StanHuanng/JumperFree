/**
  * file  : board.h
  * brief : STM32F103C8 core board on the CH446Q test board (U3 socket),
  *         register-level CMSIS, no HAL.
  *           clock  : HSI 8 MHz -> PLL 64 MHz (no crystal needed), APB1 32 MHz
  *           USART2 : PA2 TX / PA3 RX, 115200 8N1, RX by DMA1 ch6 (circular)
  *                    (external 3.3 V USB-TTL; PA9/PA10 belong to the CH446Q)
  *           CH446Q : PA8-PA12, PB5-PB9 push-pull outputs, start low
  *           LED    : PC13 (not wired on the test board), low = on
  */
#ifndef BOARD_H
#define BOARD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "stm32f1xx.h"

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

void board_init(void);
uint32_t board_millis(void);
void board_led(bool on);

/* Copy up to max received bytes out of the USART2 DMA ring; returns count.
 * Reception runs from board_init() on, also while interrupts are masked. */
size_t board_uart_read(uint8_t *buf, size_t max);
void board_uart_write(const uint8_t *data, size_t len);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* BOARD_H */
