#ifndef JF2_HW_H
#define JF2_HW_H
#include <stdbool.h>
#include <stdint.h>

/* Synchronous, infallible memory backend contract. Not a board driver API.
 * ctx objects must outlive the device. All callbacks are required; do not bind
 * disconnected/no-op electrical drivers and acknowledge physical execution. */
typedef struct {
    void *ctx;
    void (*set_crosspoint)(void *, uint8_t, uint8_t, uint8_t, bool);
    void (*all_open)(void *);
    void (*set_led)(void *, uint8_t, uint8_t, uint8_t, uint8_t);
    uint64_t (*now_ms)(void *);
} Jf2Hw;
#endif
