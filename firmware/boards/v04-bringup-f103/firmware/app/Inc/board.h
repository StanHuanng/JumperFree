/**
  * file  : board.h
  * brief : JumperFree v0.4 board support, register-level CMSIS, no HAL.
  *           MCU    : STM32F103C8T6 (U1), 8 MHz crystal X1
  *           clock  : HSE 8 MHz x8 = 64 MHz; falls back to HSI/2 x16 = 64 MHz
  *                    when the crystal does not start. APB1 32 MHz, APB2 64 MHz
  *           USART1 : PA9 TX -> CH340N RXD, PA10 RX <- CH340N TXD, 115200 8N1,
  *                    RX by DMA1 ch5 (circular)
  *           matrix : held safe for the whole bring-up phase
  *                    PA8 CH_RST open-drain released (R300 pulls RST high =
  *                    all CH446Q switches open), 7 x STB, CLK, DAT low,
  *                    PB8 TEST_DRV low
  *           LED    : PB9 LED_DATA, TIM4_CH4 alternate open-drain (ws2812.c)
  *           ADC    : PA0 MEAS_A, PA2 V5_MON, PA3 VEE_MON, PA4 VER_ID,
  *                    PA5 MEAS_B (analog inputs, adc.c)
  *           ESTOP  : PA6 ESTOP_MEM input (R3 100k up + C10 10 uF)
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

typedef struct
{
  bool     hse_ok;       /* crystal started, PLL runs from HSE */
  uint32_t reset_flags;  /* RCC->CSR bits 31:24 captured at boot */
  bool     estop_low;    /* PA6 level read at the start of board_init() */
} board_boot_t;

void board_init(void);
const board_boot_t *board_boot(void);
uint32_t board_millis(void);
void board_delay_ms(uint32_t ms);
bool board_estop_pin(void);
void board_enter_bootloader(void);  /* reset into the ROM USART bootloader */
void board_check_bootloader(void);  /* call first in main() */

size_t board_uart_read(uint8_t *buf, size_t max);
void board_uart_write(const uint8_t *data, size_t len);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* BOARD_H */
