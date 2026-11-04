/**
  * file  : jf2_link.h
  * brief : Run the portable JF2 protocol core (app/jf2) over USART2
  *         (ST-Link virtual COM port, 115200 8N1) and drive the CH446Q on
  *         the CH446Q test board through ch446q.c. PE2 lights while any
  *         crosspoint is closed.
  */
#ifndef JF2_LINK_H
#define JF2_LINK_H

#include "stm32_hal.h"

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

/* Bind the JF2 core to USART2 and start reception. Returns HAL_OK on success. */
hal_status_t jf2_link_init(hal_uart_handle_t *uart);

/* Call repeatedly from the main loop: feeds received bytes and polls the core. */
void jf2_link_process(void);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* JF2_LINK_H */
