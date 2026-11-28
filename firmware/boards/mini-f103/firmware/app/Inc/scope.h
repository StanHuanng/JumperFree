/**
  * file  : scope.h
  * brief : Slow two-channel scope via U307 MEAS_A / MEAS_B (see scope.c).
  */
#ifndef SCOPE_H
#define SCOPE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

#define SCOPE_NONE  0xFFFFFFFFUL

/* flip_b = SCOPE_NONE for one channel; n = samples per channel (0 = max) */
void scope_run(uint32_t flip_a, uint32_t flip_b, uint32_t rate, uint32_t n);

/* PB8 edge capture: trig 'f' / 'r' / 'a', wait up to wait_ms for it, then
 * record edges for win_us (max 512 edges) */
void la_run(uint32_t flip, char trig, uint32_t wait_ms, uint32_t win_us);

/* Same, timed by TIM4 input capture (15.6 ns); 'i' = fall after >= 50 us high */
void lac_run(uint32_t flip, char trig, uint32_t wait_ms, uint32_t win_us);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* SCOPE_H */
