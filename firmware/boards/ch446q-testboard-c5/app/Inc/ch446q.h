/**
  * file  : ch446q.h
  * brief : CH446Q 8x16 analog switch matrix, serial address mode (P/-S = GND,
  *         jumper H4 on the CH446Q test board). Pins follow the test board's
  *         U3 socket:
  *           PA8 STB, PA9 CS/CK, PA10 DAT                     (driven)
  *           PA11 AX0, PB9 AX1, PB8 AX2, PA12 AX3,
  *           PB7 AY0, PB6 AY1, PB5 AY2                        (held low)
  *         In serial mode the datasheet requires AX0-AX3 / AY0-AY2 at GND,
  *         so this driver keeps those MCU outputs low.
  *
  *         ch446q_diag is a software CH446Q fed only by reading the pins back
  *         (GPIO IDR) while the waveform is generated. It shows what a chip on
  *         these pins would latch, without a chip, and counts any mismatch.
  */
#ifndef CH446Q_H
#define CH446Q_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

#define CH446Q_X_LINES  16U
#define CH446Q_Y_LINES  8U
#define CH446Q_SNAPS    16U

typedef struct
{
  uint32_t magic;          /* CH446Q_DIAG_MAGIC once ch446q_init() ran */
  uint32_t strobes;        /* STB pulses seen on the pin */
  uint32_t errors;         /* pin readback differed from the intended frame */
  uint32_t addr_pin_high;  /* an AX/AY pin read high (must be GND in serial mode) */
  uint8_t  last_addr;      /* address decoded from DAT on the CS/CK rising edges */
  uint8_t  last_dat;       /* DAT sampled while STB was high */
  uint8_t  last_bits;      /* CS/CK rising edges since the previous strobe */
  uint8_t  reserved;
  uint16_t latch[CH446Q_Y_LINES];  /* bit x of latch[y]: Yy-Xx closed as decoded */
  uint32_t snaps;          /* snapshots taken; the latest is snap[(snaps - 1) % 16] */
  uint32_t model_errors;   /* snapshots where latch differed from the expected state */
  uint16_t snap[CH446Q_SNAPS][CH446Q_Y_LINES];
} ch446q_diag_t;

#define CH446Q_DIAG_MAGIC  0x34343651UL  /* "Q644" */

extern volatile ch446q_diag_t ch446q_diag;

/* Put all control pins in their idle state (low). Call once after GPIO init. */
void ch446q_init(void);

/* Close (on = true) or open the switch between X x (0-15) and Y y (0-7). */
void ch446q_write(uint8_t x, uint8_t y, bool on);

/* Open all 128 switches by writing 0 to every address (RST is not MCU-driven). */
void ch446q_all_open(void);

/* Record the decoded latch in the snapshot ring and compare it with expect
 * (same layout as latch). Lets a host read the whole history over SWD once,
 * after the run: a hot-plug SWD access while running stops UART interrupts. */
void ch446q_snapshot(const uint16_t expect[CH446Q_Y_LINES]);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* CH446Q_H */
