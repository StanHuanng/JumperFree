/**
  * file  : jf2_link.h
  * brief : Run the portable JF2 protocol core (app/jf2) over USART2
  *         (115200 8N1) and drive the CH446Q on the CH446Q test board
  *         through ch446q.c. The 5x5 WS2812B module on PB10 shows the nets
  *         (display.c); PC13 is pulled low while any crosspoint is closed.
  */
#ifndef JF2_LINK_H
#define JF2_LINK_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

/* Bind the JF2 core to USART2 and open all switches. Call after board_init(). */
bool jf2_link_init(void);

/* Call repeatedly from the main loop: feeds received bytes and polls the core. */
void jf2_link_process(void);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* JF2_LINK_H */
