/**
  * file  : display.h
  * brief : 5x5 WS2812B module as a one-character ticker of the CH446Q state.
  *
  *   Every closed switch is spelled out glyph by glyph in its net's colour:
  *   X5 to Y7 shows "X", "5", "Y", "7" (X10..X15 as two digits). Switches of
  *   the same JF2 net (tag) share a colour, different nets differ; red and
  *   white are kept for events. The list repeats, ordered by net, then X, Y.
  *
  *   Events interrupt the loop and are shown first:
  *     - new switch    : one white full-panel flash, then its X/Y in its colour
  *     - opened switch : "-" then its X/Y in red
  *     - more than 4 at once (e.g. all-open): white flash or a red "-" only
  *   Idle (nothing closed): a breathing centre dot, green when the link is
  *   ARMED, red in SAFE (after ALLOPEN or a fault, until the next HELLO).
  *   Start-up shows "1", "2", "3" so the module orientation can be checked.
  */
#ifndef DISPLAY_H
#define DISPLAY_H

#include "jf2_state.h"

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

/* Init the LED chain and play the start-up "1 2 3". */
void display_init(void);

/* Call from the main loop; redraws at most every 20 ms and only on change. */
void display_update(const Jf2Device *dev, uint32_t now_ms);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* DISPLAY_H */
