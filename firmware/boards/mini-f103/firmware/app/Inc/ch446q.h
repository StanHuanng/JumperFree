/**
  * file  : ch446q.h
  * brief : 7 x CH446Q on JumperFree mini, serial address mode.
  *           shared : PB13 CH_CLK (CS/CK), PB15 CH_DAT, PA8 CH_RST (open-drain,
  *                    R300 10k to +5V: released = RST high = all switches open)
  *           STB    : U301 PB12, U302 PB14, U303 PB10, U304 PB11, U305 PB1,
  *                    U306 PB0, U307 PA7 (each with a 10k pull-down)
  *           X0-15  : BUS0-15 on all 7 chips
  *           Y0-7   : U301..U306 -> FLIP(8*(chip-1)+y); U307 -> service lines
  */
#ifndef CH446Q_H
#define CH446Q_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

#define CH_CHIPS   7U
#define CH_X       16U
#define CH_Y       8U
#define CH_SVC     7U   /* U307 */

/* U307 Y lines */
#define SVC_Y_MEAS_A  4U
#define SVC_Y_MEAS_B  5U
#define SVC_Y_TEST    6U
#define SVC_Y_GND     7U

void ch_init(void);                 /* pins only; matrix stays in reset */
bool ch_enabled(void);
void ch_enable(void);               /* PA8 low: switches may be set */
void ch_disable(void);              /* PA8 released: all open, held in reset */
void ch_clear(void);                /* RST pulse: every switch on every chip open */
void ch_write(uint32_t chip, uint32_t x, uint32_t y, bool on);  /* chip 1..7 */
bool ch_get(uint32_t chip, uint32_t x, uint32_t y);             /* shadow state */
uint32_t ch_count_on(void);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* CH446Q_H */
