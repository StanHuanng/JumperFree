#ifndef JF2_BOARD_H
#define JF2_BOARD_H

/* Compile-time board profile of the JF2 core. Select one with a define:
 *   (none) or -DJF2_PROFILE_V01  board=v01, 2 chips, x 0..7, y 0..7, 16 LEDs.
 *   -DJF2_PROFILE_MINI            board=mini, 7 chips, x 0..15, y 0..7, 48 LEDs.
 *
 * Shadow layout (both profiles): per chip 128 switch bits (bit = x*8+y, the
 * full CH446Q 16x8 array, JF2_MAP_BYTES each), then per chip 128 tag nibbles
 * (JF2_TAG_BYTES each, even bit = low nibble). JF2_XN only limits which X
 * the protocol accepts.
 *
 * Conflict rule: a line that is shared by several chips is checked across
 * all chips; a private line is checked per chip.
 *   v01: Y0..7 are the 8 common channels (two CH446Q in parallel) = shared;
 *        X is FLIPk of one chip = private.
 *   mini: X0..15 = BUS0..15, wired to every chip = shared;
 *        Y is one clip (FLIP 8*chip+y) = private.
 *        Chip 6 = U307, the service chip: only y 6 (TEST_SRC, PB8 via 1.5k)
 *        and y 7 (SVC_GND) are legal. LED clip 0..47 = FLIP number. */

#if defined(JF2_PROFILE_V01) && defined(JF2_PROFILE_MINI)
#error "select one JF2 board profile"
#endif

#ifdef JF2_PROFILE_MINI
#define JF2_BOARD "mini"
#define JF2_CHIPS 7u
#define JF2_XN 16u
#define JF2_YN 8u
#define JF2_CLIPS 48u
#define JF2_X_SHARED 1
#define JF2_SVC_CHIP 6u
#define JF2_SVC_Y_TEST 6u
#define JF2_SVC_Y_GND 7u
#define JF2_TX_MAX 1024u /* SW TAGS for 7 chips is 939 bytes with LF */
#else
#ifndef JF2_PROFILE_V01
#define JF2_PROFILE_V01
#endif
#define JF2_BOARD "v01"
#define JF2_CHIPS 2u
#define JF2_XN 8u
#define JF2_YN 8u
#define JF2_CLIPS 16u
#define JF2_X_SHARED 0
#endif

#define JF2_MAP_BYTES 16u /* 128 switches / 8 */
#define JF2_TAG_BYTES 64u /* 128 tag nibbles */
#define JF2_TAG_BASE (JF2_CHIPS * JF2_MAP_BYTES)
#define JF2_SHADOW_BYTES (JF2_CHIPS * (JF2_MAP_BYTES + JF2_TAG_BYTES))

#endif
