#ifndef MEMORY_HW_H
#define MEMORY_HW_H
#include "jf2.h"

#define MEMORY_LOG_CAPACITY 8192u
#define MEMORY_CAPTURE_CAPACITY 8u
typedef enum { MEMORY_OFF, MEMORY_ON, MEMORY_ALL_OPEN, MEMORY_LED } MemoryEventKind;
typedef struct {
    MemoryEventKind kind;
    uint8_t chip, x, y, clip, r, g, b;
} MemoryEvent;
typedef struct {
    bool points[2][8][8]; /* independent of core shadow and tags */
    uint8_t leds[16][3];
    uint64_t now;
    MemoryEvent log[MEMORY_LOG_CAPACITY];
    size_t log_count;
    bool log_overflow;
    uint8_t responses[MEMORY_CAPTURE_CAPACITY][JF2_FRAME_MAX+1u];
    size_t response_len[MEMORY_CAPTURE_CAPACITY], response_count;
    bool response_overflow;
} MemoryHw;
void memory_hw_init(MemoryHw *);
Jf2Hw memory_hw_binding(MemoryHw *);
void memory_emit(void *, const uint8_t *, size_t);
void memory_clear_log(MemoryHw *);
void memory_clear_responses(MemoryHw *);
bool memory_advance(MemoryHw *, Jf2Device *, uint64_t);
void memory_reboot(MemoryHw *, Jf2Device *, uint32_t, Jf2ResetCause);
bool memory_matches(const MemoryHw *, const Jf2Device *);
int core_self_tests(void);
#endif
