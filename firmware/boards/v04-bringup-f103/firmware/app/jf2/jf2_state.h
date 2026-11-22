#ifndef JF2_STATE_H
#define JF2_STATE_H
#include "jf2_hw.h"
#include <stddef.h>

/* v0.4 topology (core copied from JumperFree repo firmware/ @07a481c, which
 * is board=v01; protocol/jf2.md still describes v01):
 *   chip 0..5 = U301..U306, x 0..15 = BUS0..15 (shared by all chips),
 *   y 0..7 = FLIP(8*chip+y) (one clip each).
 *   chip 6 = U307 (service chip), only y 6 = TEST_SRC (PB8 via 1.5k: weak
 *   3.3 V while any of its switches is closed) and y 7 = SVC_GND (GND via
 *   R410). Shadow: per chip 128 bit (bit = x*8+y), then per chip 128 tag
 *   nibbles (even bit = low nibble). LED clip 0..47 = FLIP number. */
#define JF2_BOARD "v04"
#define JF2_CHIPS 7u
#define JF2_SVC_CHIP 6u
#define JF2_SVC_Y_TEST 6u
#define JF2_SVC_Y_GND 7u
#define JF2_XN 16u
#define JF2_YN 8u
#define JF2_CLIPS 48u
#define JF2_MAP_BYTES 16u              /* 128 points / 8 */
#define JF2_TAG_BYTES 64u              /* 128 nibbles */
#define JF2_TAG_BASE (JF2_CHIPS * JF2_MAP_BYTES)
#define JF2_SHADOW_BYTES (JF2_CHIPS * (JF2_MAP_BYTES + JF2_TAG_BYTES))
#define JF2_FRAME_MAX 512u
#define JF2_TX_MAX 1024u               /* SW TAGS for 6 chips is ~800 bytes */
#define JF2_MAX_OPS 256u
#define JF2_RESULT_CAPACITY 4u

typedef enum { JF2_RESET_POR, JF2_RESET_IWDG, JF2_RESET_SW } Jf2ResetCause;
typedef enum { JF2_SAFE, JF2_ARMED, JF2_STAGING } Jf2Mode;
typedef void (*Jf2Emit)(void *ctx, const uint8_t *line, size_t len);
typedef struct { uint8_t chip, x, y, tag; } Jf2Op; /* tag 0 = break */
typedef struct { uint8_t bytes[JF2_SHADOW_BYTES]; } Jf2Shadow;
typedef struct {
    uint64_t epoch;
    uint32_t id, crc;
    uint16_t opened, closed;
    bool committed;
} Jf2Result;
typedef struct {
    Jf2Op ops[JF2_MAX_OPS];
    uint16_t boundary[JF2_MAX_OPS + 1u];
    uint32_t id, base, declared;
    uint16_t count, frames;
    bool open;
} Jf2Transaction;
typedef struct {
    Jf2Hw hw;
    Jf2Emit emit;
    void *emit_ctx;
    uint64_t epoch, boot_at, lease_at;
    uint32_t boot, lease_ms, faults;
    Jf2ResetCause reset_cause;
    Jf2Mode mode;
    bool hello, hw_source, interrupt_commit;
    Jf2Shadow live, target;
    Jf2Transaction txn;
    Jf2Result results[JF2_RESULT_CAPACITY]; /* oldest first; OPEN is separate */
    uint8_t result_count, leds[JF2_CLIPS][3];
    uint8_t rx[JF2_FRAME_MAX + 1u];
    char tx[JF2_TX_MAX + 1u];
    size_t rx_len, tx_len;
    bool rx_overflow;
    uint8_t route_stage, route_mask, route_index;
} Jf2Device;

/* Core implementation helpers, also useful for inspecting host tests. Request
 * storage is NUL-separated only AFTER raw length/byte validation. No token
 * pointer array or per-frame raw copies are stored. */
typedef struct { char *body; size_t count; } Jf2Request;
const char *jf2_token(const Jf2Request *, size_t);
void jf2_append(Jf2Device *, const char *format, ...);
bool jf2_error(Jf2Device *, const char *code, const char *message);
bool jf2_uint(Jf2Device *, const char *, uint32_t *);
bool jf2_dispatch(Jf2Device *, const Jf2Request *);
uint32_t jf2_crc32(const void *, size_t, uint32_t previous);
uint32_t jf2_operations_crc(const Jf2Op *, size_t);
uint8_t jf2_shadow_tag(const Jf2Shadow *, unsigned chip, unsigned bit);
#endif
