/**
  * file  : scope.h
  * brief : Slow two-channel scope via U307 MEAS_A / MEAS_B (see scope.c).
  */
#ifndef SCOPE_H
#define SCOPE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

#define SCOPE_NONE  0xFFFFFFFFUL

/* Measurement path of one clip (also used by JF2 MEAS): the clip's BUS from the
 * CH446Q shadow; for a clip in no net a free BUS (BUS15 first) and one
 * temporary clip switch. Then U307 svc_y (Y4 MEAS_A, Y5 MEAS_B, Y6 PB8) on it. */
typedef struct
{
  uint32_t flip;
  uint32_t bus;
  bool     temp;  /* clip switch closed only for this capture */
} probe_ch_t;

bool scope_attach(probe_ch_t *p, uint32_t svc_y);  /* false: no free BUS */
void scope_detach(const probe_ch_t *p, uint32_t svc_y);

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
