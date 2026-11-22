/* Generated from JumperFree_Claude/fable/review/v04_cliptable.csv (EDA pins of PCB v04).
 * Index = FLIP number n: chip U30(n/8+1), Y = n%8.
 * row U = back row (USB side), D = front row; col 1..24 from the left; led 1..48 = RGBn. */
#ifndef CLIPMAP_H
#define CLIPMAP_H
#include <stdint.h>
typedef struct { char row; uint8_t col; uint8_t j; uint8_t led; } clip_pos_t;
static const clip_pos_t CLIPMAP[48] = {
  { 'U', 24,  1,  1 },  /* FLIP0 */
  { 'U', 23,  2,  2 },  /* FLIP1 */
  { 'U', 22,  3,  3 },  /* FLIP2 */
  { 'U', 21,  4,  4 },  /* FLIP3 */
  { 'U', 17,  5,  8 },  /* FLIP4 */
  { 'U', 18,  6,  7 },  /* FLIP5 */
  { 'U', 19,  7,  6 },  /* FLIP6 */
  { 'U', 20,  8,  5 },  /* FLIP7 */
  { 'U', 16,  9,  9 },  /* FLIP8 */
  { 'U', 15, 10, 10 },  /* FLIP9 */
  { 'U', 14, 11, 11 },  /* FLIP10 */
  { 'U', 13, 12, 12 },  /* FLIP11 */
  { 'U',  9, 13, 16 },  /* FLIP12 */
  { 'U', 10, 14, 15 },  /* FLIP13 */
  { 'U', 11, 15, 14 },  /* FLIP14 */
  { 'U', 12, 16, 13 },  /* FLIP15 */
  { 'U',  8, 17, 17 },  /* FLIP16 */
  { 'U',  7, 18, 18 },  /* FLIP17 */
  { 'U',  6, 19, 19 },  /* FLIP18 */
  { 'U',  5, 20, 20 },  /* FLIP19 */
  { 'U',  1, 21, 24 },  /* FLIP20 */
  { 'U',  2, 22, 23 },  /* FLIP21 */
  { 'U',  3, 23, 22 },  /* FLIP22 */
  { 'U',  4, 24, 21 },  /* FLIP23 */
  { 'D', 17, 25, 41 },  /* FLIP24 */
  { 'D', 18, 26, 42 },  /* FLIP25 */
  { 'D', 19, 27, 43 },  /* FLIP26 */
  { 'D', 20, 28, 44 },  /* FLIP27 */
  { 'D', 24, 29, 48 },  /* FLIP28 */
  { 'D', 23, 30, 47 },  /* FLIP29 */
  { 'D', 22, 31, 46 },  /* FLIP30 */
  { 'D', 21, 32, 45 },  /* FLIP31 */
  { 'D',  9, 33, 33 },  /* FLIP32 */
  { 'D', 10, 34, 34 },  /* FLIP33 */
  { 'D', 11, 35, 35 },  /* FLIP34 */
  { 'D', 12, 36, 36 },  /* FLIP35 */
  { 'D', 16, 37, 40 },  /* FLIP36 */
  { 'D', 15, 38, 39 },  /* FLIP37 */
  { 'D', 14, 39, 38 },  /* FLIP38 */
  { 'D', 13, 40, 37 },  /* FLIP39 */
  { 'D',  1, 41, 25 },  /* FLIP40 */
  { 'D',  2, 42, 26 },  /* FLIP41 */
  { 'D',  3, 43, 27 },  /* FLIP42 */
  { 'D',  4, 44, 28 },  /* FLIP43 */
  { 'D',  8, 45, 32 },  /* FLIP44 */
  { 'D',  7, 46, 31 },  /* FLIP45 */
  { 'D',  6, 47, 30 },  /* FLIP46 */
  { 'D',  5, 48, 29 },  /* FLIP47 */
};
#endif /* CLIPMAP_H */
