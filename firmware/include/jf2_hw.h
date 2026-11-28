#ifndef JF2_HW_H
#define JF2_HW_H
#include <stdbool.h>
#include <stdint.h>

/* One voltage reading of a clip (JF2 MEAS). mv is the mean over the window,
 * min/max the extremes of single samples (all equal mv for a 0 ms window). */
typedef struct {
    int32_t mv, min_mv, max_mv;
    uint16_t samples;
    uint8_t bus;   /* BUS the measurement path used */
    bool temp;     /* the clip was in no net: one switch closed for the reading */
} Jf2Meas;

/* Synchronous, infallible memory backend contract. Not a board driver API.
 * ctx objects must outlive the device. The first four callbacks are required;
 * do not bind disconnected/no-op electrical drivers and acknowledge physical
 * execution.
 * measure is optional (NULL: MEAS answers UNSUPPORTED). It reads the voltage on
 * clip over window_ms (0..1000) and must leave every switch exactly as it found
 * it: the shadow is not changed. It returns false only when no BUS is free for a
 * clip that is in no net. */
typedef struct {
    void *ctx;
    void (*set_crosspoint)(void *, uint8_t, uint8_t, uint8_t, bool);
    void (*all_open)(void *);
    void (*set_led)(void *, uint8_t, uint8_t, uint8_t, uint8_t);
    uint64_t (*now_ms)(void *);
    bool (*measure)(void *, uint8_t clip, uint16_t window_ms, Jf2Meas *);
} Jf2Hw;
#endif
