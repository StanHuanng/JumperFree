/**
  * file  : display.c
  * brief : See display.h for what is shown. The chain is a row-major snake
  *         (one row left to right, the next right to left, ...) whose first
  *         LED sits bottom-left on the module as mounted. If the start-up
  *         "1 2 3" is mirrored or rotated, change FLIP_ROWS / FLIP_COLS /
  *         TRANSPOSE.
  *
  *         A script of steps (glyph + colour + duration) is played back; it is
  *         rebuilt from the JF2 live shadow whenever it ends or the switch set
  *         changes. Switch coordinates are the test board's CH446Q lines:
  *         JF2 chip c, x -> X(8c + x), y -> Y y.
  */
#include <string.h>
#include "display.h"
#include "board.h"
#include "ws2812.h"

#define FRAME_MS      20U
#define FLIP_ROWS     1U   /* chain starts on the bottom row */
#define FLIP_COLS     0U   /* chain starts in the left column */
#define TRANSPOSE     0U   /* chain runs along rows, not columns */

#define GLYPH_MS      450U  /* one character */
#define GAP_MS        120U  /* blank between characters ("1" "1" stay apart) */
#define ITEM_GAP_MS   600U  /* blank between two switches */
#define FLASH_MS      150U
#define MAX_ANNOUNCE  4U
#define MAX_STEPS     160U

enum
{
  G_0 = 0, /* 0..9 are the digits */
  G_X = 10,
  G_Y,
  G_MINUS,
  G_FULL,
  G_BLANK,
  G_IDLE,  /* breathing centre dot */
};

/* 5x5 glyphs, one byte per row (top first), bit 4 = left column */
static const uint8_t FONT[G_FULL + 1][5] = {
  {0x0E, 0x11, 0x11, 0x11, 0x0E}, /* 0 */
  {0x04, 0x0C, 0x04, 0x04, 0x0E}, /* 1 */
  {0x1E, 0x01, 0x0E, 0x10, 0x1F}, /* 2 */
  {0x1E, 0x01, 0x06, 0x01, 0x1E}, /* 3 */
  {0x12, 0x12, 0x1F, 0x02, 0x02}, /* 4 */
  {0x1F, 0x10, 0x1E, 0x01, 0x1E}, /* 5 */
  {0x0E, 0x10, 0x1E, 0x11, 0x0E}, /* 6 */
  {0x1F, 0x01, 0x02, 0x04, 0x04}, /* 7 */
  {0x0E, 0x11, 0x0E, 0x11, 0x0E}, /* 8 */
  {0x0E, 0x11, 0x0F, 0x01, 0x0E}, /* 9 */
  {0x11, 0x0A, 0x04, 0x0A, 0x11}, /* X */
  {0x11, 0x0A, 0x04, 0x04, 0x04}, /* Y */
  {0x00, 0x00, 0x1F, 0x00, 0x00}, /* - */
  {0x1F, 0x1F, 0x1F, 0x1F, 0x1F}, /* full panel */
};

/* Net colours by JF2 tag 1..15: no red or white (kept for events), dim
 * (max 40) so a full glyph stays well inside USB power */
static const ws2812_rgb_t TAG_COLOR[16] = {
  {0, 0, 0},
  {0, 40, 0},   {0, 10, 40},  {36, 26, 0},  {0, 30, 30},  {30, 0, 36},
  {40, 14, 0},  {16, 40, 0},  {40, 0, 18},  {10, 0, 40},  {0, 40, 16},
  {24, 40, 10}, {40, 8, 30},  {0, 24, 40},  {30, 30, 0},  {20, 10, 40},
};
static const ws2812_rgb_t COLOR_WHITE = {40, 40, 40};
static const ws2812_rgb_t COLOR_RED = {40, 0, 0};
static const ws2812_rgb_t COLOR_BOOT = {0, 12, 40};

typedef struct
{
  uint8_t glyph;
  ws2812_rgb_t color;
  uint16_t ms;
} step_t;

static step_t s_steps[MAX_STEPS];
static uint32_t s_nsteps;
static uint32_t s_index;
static uint32_t s_step_start;

static uint16_t s_prev[8];      /* bit x of s_prev[y]: X x - Y y was closed */
static bool s_prev_valid;
static ws2812_rgb_t s_shown[WS2812_COUNT];
static uint32_t s_last_frame;

/* (col, row) with row 0 at the top, as seen by the user -> chain position */
static uint32_t chain_index(uint32_t col, uint32_t row)
{
  uint32_t r = (FLIP_ROWS != 0U) ? (4U - row) : row;
  uint32_t c = (FLIP_COLS != 0U) ? (4U - col) : col;
  if (TRANSPOSE != 0U)
  {
    uint32_t t = r;
    r = c;
    c = t;
  }
  return r * 5U + (((r & 1U) == 0U) ? c : (4U - c));
}

static void draw_glyph(ws2812_rgb_t *frame, uint8_t glyph, ws2812_rgb_t color)
{
  for (uint32_t row = 0U; row < 5U; row++)
  {
    for (uint32_t col = 0U; col < 5U; col++)
    {
      if ((FONT[glyph][row] & (0x10U >> col)) != 0U)
      {
        frame[chain_index(col, row)] = color;
      }
    }
  }
}

/* Send only when the frame changes */
static void show_frame(const ws2812_rgb_t *frame)
{
  if (memcmp(frame, s_shown, sizeof s_shown) != 0)
  {
    ws2812_show(frame);
    memcpy(s_shown, frame, sizeof s_shown);
  }
}

static void add(uint8_t glyph, ws2812_rgb_t color, uint16_t ms)
{
  if (s_nsteps < MAX_STEPS)
  {
    s_steps[s_nsteps].glyph = glyph;
    s_steps[s_nsteps].color = color;
    s_steps[s_nsteps].ms = ms;
    s_nsteps++;
  }
}

static void add_number(uint32_t n, ws2812_rgb_t color)
{
  if (n >= 10U)
  {
    add((uint8_t)(n / 10U), color, GLYPH_MS);
    add(G_BLANK, color, GAP_MS);
  }
  add((uint8_t)(n % 10U), color, GLYPH_MS);
  add(G_BLANK, color, GAP_MS);
}

/* "X", x, "Y", y and a pause */
static void add_switch(uint32_t x, uint32_t y, ws2812_rgb_t color)
{
  add(G_X, color, GLYPH_MS);
  add(G_BLANK, color, GAP_MS);
  add_number(x, color);
  add(G_Y, color, GLYPH_MS);
  add(G_BLANK, color, GAP_MS);
  add_number(y, color);
  add(G_BLANK, color, ITEM_GAP_MS);
}

/* Closed switches of the live shadow, as latch rows and per-switch tags */
static void read_switches(const Jf2Device *dev, uint16_t now[8], uint8_t tags[16][8])
{
  memset(now, 0, 8U * sizeof now[0]);
  for (uint32_t chip = 0U; chip < 2U; chip++)
  {
    for (uint32_t x = 0U; x < 8U; x++)
    {
      for (uint32_t y = 0U; y < 8U; y++)
      {
        uint32_t bit = x * 8U + y;
        uint32_t big_x = chip * 8U + x;
        tags[big_x][y] = 0U;
        if ((dev->live.bytes[chip * 16U + bit / 8U] & (1U << (bit % 8U))) != 0U)
        {
          now[y] |= (uint16_t)(1U << big_x);
          tags[big_x][y] = jf2_shadow_tag(&dev->live, chip, bit);
        }
      }
    }
  }
}

static uint32_t count_bits(const uint16_t rows[8])
{
  uint32_t n = 0U;
  for (uint32_t y = 0U; y < 8U; y++)
  {
    for (uint16_t v = rows[y]; v != 0U; v &= (uint16_t)(v - 1U))
    {
      n++;
    }
  }
  return n;
}

/* The repeating list, ordered by tag, then X, then Y; or the idle dot */
static void add_loop(const uint16_t now[8], uint8_t tags[16][8])
{
  bool any = false;
  for (uint32_t tag = 1U; tag < 16U; tag++)
  {
    for (uint32_t x = 0U; x < 16U; x++)
    {
      for (uint32_t y = 0U; y < 8U; y++)
      {
        if (((now[y] >> x) & 1U) != 0U && tags[x][y] == tag)
        {
          add_switch(x, y, TAG_COLOR[tag]);
          any = true;
        }
      }
    }
  }
  if (!any)
  {
    add(G_IDLE, COLOR_WHITE, 2000U);
  }
}

static void restart(uint32_t now_ms)
{
  s_index = 0U;
  s_step_start = now_ms;
}

void display_init(void)
{
  ws2812_rgb_t frame[WS2812_COUNT];

  ws2812_init();
  for (uint8_t digit = 1U; digit <= 3U; digit++)
  {
    memset(frame, 0, sizeof frame);
    draw_glyph(frame, digit, COLOR_BOOT);
    ws2812_show(frame);
    for (uint32_t t = board_millis(); (board_millis() - t) < 350U;)
    {
    }
  }
  memset(frame, 0, sizeof frame);
  ws2812_show(frame);
  memcpy(s_shown, frame, sizeof frame);
}

void display_update(const Jf2Device *dev, uint32_t now_ms)
{
  if ((now_ms - s_last_frame) < FRAME_MS)
  {
    return;
  }
  s_last_frame = now_ms;

  uint16_t now[8];
  uint8_t tags[16][8];
  read_switches(dev, now, tags);

  bool changed = !s_prev_valid || (memcmp(now, s_prev, sizeof s_prev) != 0);
  if (changed)
  {
    uint16_t added[8];
    uint16_t removed[8];
    for (uint32_t y = 0U; y < 8U; y++)
    {
      added[y] = s_prev_valid ? (uint16_t)(now[y] & ~s_prev[y]) : 0U;
      removed[y] = s_prev_valid ? (uint16_t)(s_prev[y] & ~now[y]) : 0U;
    }
    uint32_t n_added = count_bits(added);
    uint32_t n_removed = count_bits(removed);

    s_nsteps = 0U;
    if (n_removed > MAX_ANNOUNCE)
    {
      add(G_MINUS, COLOR_RED, 800U);
      add(G_BLANK, COLOR_RED, ITEM_GAP_MS);
    }
    else
    {
      for (uint32_t y = 0U; y < 8U; y++)
      {
        for (uint32_t x = 0U; x < 16U; x++)
        {
          if (((removed[y] >> x) & 1U) != 0U)
          {
            add(G_MINUS, COLOR_RED, GLYPH_MS);
            add(G_BLANK, COLOR_RED, GAP_MS);
            add_switch(x, y, COLOR_RED);
          }
        }
      }
    }
    if (n_added > 0U)
    {
      add(G_FULL, COLOR_WHITE, FLASH_MS);
      add(G_BLANK, COLOR_WHITE, GAP_MS);
    }
    if (n_added <= MAX_ANNOUNCE)
    {
      for (uint32_t y = 0U; y < 8U; y++)
      {
        for (uint32_t x = 0U; x < 16U; x++)
        {
          if (((added[y] >> x) & 1U) != 0U)
          {
            add_switch(x, y, TAG_COLOR[tags[x][y]]);
          }
        }
      }
    }
    add_loop(now, tags);
    memcpy(s_prev, now, sizeof s_prev);
    s_prev_valid = true;
    restart(now_ms);
  }
  else if ((now_ms - s_step_start) >= s_steps[s_index].ms)
  {
    s_index++;
    s_step_start = now_ms;
    if (s_index >= s_nsteps)
    {
      s_nsteps = 0U;
      add_loop(now, tags);
      restart(now_ms);
    }
  }

  ws2812_rgb_t frame[WS2812_COUNT];
  memset(frame, 0, sizeof frame);
  const step_t *step = &s_steps[s_index];
  if (step->glyph == G_IDLE)
  {
    /* triangle wave 2..20 over two seconds, centre dot only */
    uint32_t phase = now_ms % 2000U;
    uint32_t level = 2U + ((phase < 1000U) ? phase : (2000U - phase)) * 18U / 1000U;
    ws2812_rgb_t c = {0, 0, 0};
    if (dev->mode == JF2_SAFE)
    {
      c.r = (uint8_t)level;
    }
    else
    {
      c.g = (uint8_t)level;
    }
    frame[chain_index(2U, 2U)] = c;
  }
  else if (step->glyph != G_BLANK)
  {
    draw_glyph(frame, step->glyph, step->color);
  }
  show_frame(frame);
}
