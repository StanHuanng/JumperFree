/**
  * file  : matrix.h
  * brief : Matrix self-test and clip connections for JumperFree v0.4.
  *
  * Self-test signal path (no probes, breadboard must be empty):
  *   PB8 TEST_DRV -> R409 1.5k -> U307 Y6 -> BUS a -> ... -> BUS b
  *   -> U307 Y4 -> MEAS_A divider/follower -> PA0
  * PB8 is driven 0 V and 3.3 V; the PA0 difference tells the state:
  *   connected about +840 mV, open about 0 mV, BUS grounded (U307 Y7) ~50 mV.
  */
#ifndef MATRIX_H
#define MATRIX_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

bool matrix_ready(void);                /* enables the matrix if supplies are OK */
uint32_t matrix_selftest(const char *only);  /* returns number of failures */
bool matrix_parse_clip(const char *s, uint32_t *flip);  /* "U6" / "D20" -> FLIP n */
void matrix_conn(uint32_t a, uint32_t b);
void matrix_clear(void);
void matrix_nets(void);
void matrix_meas(uint32_t flip);
void matrix_probe_raw(uint32_t settle_ms);  /* 0 = SETTLE_MS */
void matrix_find(void);  /* clips joined outside the matrix, lights their LEDs */

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* MATRIX_H */
