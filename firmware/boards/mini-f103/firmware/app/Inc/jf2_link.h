/**
  * file  : jf2_link.h
  * brief : Shared JF2 protocol core (firmware/src, JF2_PROFILE_MINI) on the
  *         USART1 console. Lines that start with "JF" go to the core without
  *         echo; everything else stays a console command.
  */
#ifndef JF2_LINK_H
#define JF2_LINK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

void jf2_link_init(void);
void jf2_link_line(const char *line, size_t len);  /* one frame without LF */
void jf2_link_poll(void);                          /* lease, supplies, LEDs */
/* Console takes the matrix: if JF2 had a session or changed anything, reboot
 * the JF2 state (all open, new boot id). Returns true when it did. */
bool jf2_link_release(void);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* JF2_LINK_H */
