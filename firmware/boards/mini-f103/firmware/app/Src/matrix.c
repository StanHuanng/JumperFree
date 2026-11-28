/**
  * file  : matrix.c
  * brief : Matrix self-test, clip connections and BUS measurement (JumperFree mini).
  *
  * Probe: PB8 low -> read PA0, PB8 high -> read, PB8 low -> read.
  *        d = hi - (lo1 + lo2) / 2 cancels slow drift of a floating BUS
  *        (MEAS_A input is 1 MOhm against up to a few nF of BUS capacitance).
  *        connected: d about +840 mV, open: about 0, grounded by Y7: ~50 mV.
  */
#include <stdlib.h>
#include <string.h>
#include "adc.h"
#include "board.h"
#include "ch446q.h"
#include "clipmap.h"
#include "console.h"
#include "matrix.h"
#include "ws2812.h"

#define CLIPS          48U
#define NONE           0xFFU
#define TH_CONN        500    /* mV: d at or above = connected */
#define TH_OPEN        150    /* mV: d at or below = open      */
#define SETTLE_MS      4U
#define SETTLE_BIG_MS  12U    /* many BUSes tied to MEAS_A     */
#define MAX_FAIL_LINES 40U

typedef struct
{
  int32_t lo;
  int32_t hi;
  int32_t d;
  int32_t lo_b;
  int32_t d_b;
} probe_t;

static int32_t s_vdda = 3300;
static uint32_t s_fail;
static uint32_t s_fail_lines;
static bool s_clip_bad[CLIPS];
static uint8_t s_net_bus[CLIPS];

/* ---------------------------------------------------------------- helpers */

static void drive(bool high)
{
  GPIOB->BSRR = high ? (1UL << 8) : (1UL << 24);  /* PB8 TEST_DRV */
}

static int32_t read_mv(uint32_t ch)
{
  return ((int32_t)adc_read(ch, 16U) * s_vdda + 2047) / 4095;
}

static probe_t probe_ms(uint32_t settle)
{
  probe_t p;
  drive(false);
  board_delay_ms(settle);
  int32_t lo1 = read_mv(0U), lo1b = read_mv(5U);
  drive(true);
  board_delay_ms(settle);
  p.hi = read_mv(0U);
  int32_t hib = read_mv(5U);
  drive(false);
  board_delay_ms(settle);
  int32_t lo2 = read_mv(0U), lo2b = read_mv(5U);
  p.lo = (lo1 + lo2) / 2;
  p.d = p.hi - p.lo;
  p.lo_b = (lo1b + lo2b) / 2;
  p.d_b = hib - p.lo_b;
  return p;
}

static probe_t probe(void)
{
  return probe_ms(SETTLE_MS);
}

static void svc(uint32_t y, uint32_t x, bool on)
{
  ch_write(CH_SVC, x, y, on);
}

static void clip_sw(uint32_t flip, uint32_t x, bool on)
{
  ch_write(flip / 8U + 1U, x, flip % 8U, on);
}

static const char *clip_name(uint32_t flip)
{
  static char b[4][24];
  static uint32_t k;
  char *s = b[k++ & 3U];
  const clip_pos_t *c = &CLIPMAP[flip];
  /* e.g. "U6(J19,U303.Y2)" */
  s[0] = c->row;
  itoa(c->col, &s[1], 10);
  strcat(s, "(J");
  itoa(c->j, s + strlen(s), 10);
  strcat(s, ",U30");
  itoa((int)(flip / 8U + 1U), s + strlen(s), 10);
  strcat(s, ".Y");
  itoa((int)(flip % 8U), s + strlen(s), 10);
  strcat(s, ")");
  return s;
}

static void fail(const char *what, const probe_t *p)
{
  s_fail++;
  if (s_fail_lines < MAX_FAIL_LINES)
  {
    s_fail_lines++;
    out("  FAIL %s  (lo %s V, d %ld mV)\r\n", what, mv(p->lo), (long)p->d);
  }
  else if (s_fail_lines == MAX_FAIL_LINES)
  {
    s_fail_lines++;
    out("  ... more failures not printed\r\n");
  }
}

/* Concatenate up to three strings; rotating buffers so calls can nest */
static const char *msg(const char *a, const char *b, const char *c)
{
  static char buf[4][96];
  static uint32_t k;
  char *s = buf[k++ & 3U];
  s[0] = '\0';
  strncat(s, a, 95U);
  if (b != NULL)
  {
    strncat(s, b, 95U - strlen(s));
  }
  if (c != NULL)
  {
    strncat(s, c, 95U - strlen(s));
  }
  return s;
}

static const char *num(uint32_t v)
{
  static char b[4][12];
  static uint32_t k;
  char *s = b[k++ & 3U];
  itoa((int)v, s, 10);
  return s;
}

/* ------------------------------------------------------------ enable gate */

bool matrix_ready(void)
{
  if (ch_enabled())
  {
    return true;
  }
  adc_report_t r;
  adc_measure(&r);
  if ((r.v5_mv < 4600) || (r.v5_mv > 5300) || (r.vee_mv < -7500) || (r.vee_mv > -6500))
  {
    out("matrix NOT enabled: +5V %s V, -7V %s V out of range\r\n", mv(r.v5_mv), mv(r.vee_mv));
    return false;
  }
  s_vdda = r.vdda_mv;
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    s_net_bus[i] = NONE;
  }
  ch_enable();
  out("matrix enabled (+5V %s V, -7V %s V, CH_RST low)\r\n", mv(r.v5_mv), mv(r.vee_mv));
  return true;
}

/* ------------------------------------------------------------- self-tests */

/* U307: Y6 (TEST_SRC), Y4 (MEAS_A), Y5 (MEAS_B) on every BUS; Y7 grounds it */
static void test_svc(void)
{
  out("[svc] U307 Y4/Y5/Y6/Y7 on BUS0-15\r\n");
  for (uint32_t b = 0U; b < CH_X; b++)
  {
    ch_clear();
    svc(SVC_Y_TEST, b, true);
    svc(SVC_Y_MEAS_A, b, true);
    svc(SVC_Y_MEAS_B, b, true);
    probe_t p = probe();
    if (p.d < TH_CONN)
    {
      fail(msg("U307 Y6->Y4 via BUS", num(b), " (MEAS_A path)"), &p);
    }
    if (p.d_b < TH_CONN)
    {
      probe_t q = p;
      q.lo = p.lo_b;
      q.d = p.d_b;
      fail(msg("U307 Y6->Y5 via BUS", num(b), " (MEAS_B path)"), &q);
    }
    svc(SVC_Y_GND, b, true);
    p = probe();
    if (p.d > TH_OPEN)
    {
      fail(msg("U307 Y7 (SVC_GND) does not ground BUS", num(b), NULL), &p);
    }
  }
  ch_clear();
}

/* Every BUS isolated from every other BUS */
static void test_bus(void)
{
  out("[bus] BUS-to-BUS isolation\r\n");
  for (uint32_t a = 0U; a < CH_X; a++)
  {
    ch_clear();
    svc(SVC_Y_TEST, a, true);
    for (uint32_t b = 0U; b < CH_X; b++)
    {
      if (b != a)
      {
        svc(SVC_Y_MEAS_A, b, true);
      }
    }
    probe_t p = probe_ms(SETTLE_BIG_MS);
    if (p.d <= TH_OPEN)
    {
      continue;
    }
    bool found = false;
    for (uint32_t b = 0U; b < CH_X; b++)
    {
      if (b == a)
      {
        continue;
      }
      ch_clear();
      svc(SVC_Y_TEST, a, true);
      svc(SVC_Y_MEAS_A, b, true);
      probe_t q = probe();
      if (q.d > TH_OPEN)
      {
        fail(msg("BUS", num(a), msg(" shorted to BUS", num(b), NULL)), &q);
        found = true;
      }
    }
    if (!found)
    {
      fail(msg("BUS", num(a), " leaks into the other BUSes (not isolated to one)"), &p);
    }
  }
  ch_clear();
}

/* A write to one chip must not land in another chip (STB crosstalk) */
static void test_stb(void)
{
  out("[stb] STB isolation between all chip pairs\r\n");
  for (uint32_t c = 1U; c <= CH_CHIPS; c++)
  {
    for (uint32_t d = c + 1U; d <= CH_CHIPS; d++)
    {
      ch_clear();
      svc(SVC_Y_TEST, 2U, true);
      svc(SVC_Y_MEAS_A, 3U, true);
      ch_write(c, 2U, 0U, true);
      ch_write(d, 3U, 0U, true);
      probe_t p = probe();
      if (p.d > TH_OPEN)
      {
        fail(msg("STB crosstalk between U30", num(c), msg(" and U30", num(d), NULL)), &p);
      }
    }
  }
  ch_clear();
}

/* RST pulse opens the switches of every chip */
static void test_rst(void)
{
  out("[rst] CH_RST clears every chip\r\n");
  for (uint32_t c = 1U; c <= CH_CHIPS; c++)
  {
    ch_clear();
    svc(SVC_Y_TEST, 0U, true);
    if (c == CH_SVC)
    {
      svc(SVC_Y_MEAS_A, 0U, true);
    }
    else
    {
      svc(SVC_Y_MEAS_A, 1U, true);
      ch_write(c, 0U, 0U, true);
      ch_write(c, 1U, 0U, true);
    }
    probe_t p = probe();
    if (p.d < TH_CONN)
    {
      fail(msg("rst pre-check: path through U30", num(c), " not connected"), &p);
      continue;
    }
    ch_clear();
    if (c != CH_SVC)
    {
      svc(SVC_Y_TEST, 0U, true);
      svc(SVC_Y_MEAS_A, 1U, true);
    }
    p = probe();
    if (p.d > TH_OPEN)
    {
      fail(msg("U30", num(c), " switches stay on after CH_RST pulse"), &p);
    }
  }
  ch_clear();
}

static probe_t pair_probe(uint32_t chip, uint32_t y, uint32_t xa, uint32_t xb)
{
  ch_clear();
  svc(SVC_Y_TEST, xa, true);
  svc(SVC_Y_MEAS_A, xb, true);
  ch_write(chip, xa, y, true);
  ch_write(chip, xb, y, true);
  return probe();
}

/* Every switch of U301-U306: Y joined to two BUSes, 8 pairs per Y */
static void test_switch(void)
{
  out("[switch] U301-U306, every Y to every X (768 switches)\r\n");
  for (uint32_t c = 1U; c <= 6U; c++)
  {
    uint32_t chip_ok = 0U;
    for (uint32_t y = 0U; y < CH_Y; y++)
    {
      uint32_t flip = (c - 1U) * 8U + y;
      uint8_t ok_mask = 0U;
      probe_t first_bad = { 0 };
      for (uint32_t pr = 0U; pr < 8U; pr++)
      {
        probe_t p = pair_probe(c, y, 2U * pr, 2U * pr + 1U);
        if (p.d >= TH_CONN)
        {
          ok_mask |= (uint8_t)(1U << pr);
          if (pr == 0U)
          {
            /* turning a switch off must break the path */
            ch_write(c, 1U, y, false);
            probe_t q = probe();
            if (q.d > TH_OPEN)
            {
              fail(msg(clip_name(flip), " switch X1 does not turn off", NULL), &q);
              s_clip_bad[flip] = true;
            }
          }
        }
        else if (first_bad.d == 0)
        {
          first_bad = p;
          first_bad.d = (p.d == 0) ? 1 : p.d;
        }
      }
      if (ok_mask == 0xFFU)
      {
        chip_ok += 16U;
        continue;
      }
      s_clip_bad[flip] = true;
      if (ok_mask == 0U)
      {
        fail(msg(clip_name(flip), ": no X works (Y line / FLIP net / clip short?)", NULL), &first_bad);
        continue;
      }
      uint32_t ref = 0U;
      while ((ok_mask & (1U << ref)) == 0U)
      {
        ref++;
      }
      uint32_t xref = 2U * ref;
      for (uint32_t pr = 0U; pr < 8U; pr++)
      {
        if ((ok_mask & (1U << pr)) != 0U)
        {
          chip_ok += 2U;
          continue;
        }
        for (uint32_t x = 2U * pr; x <= 2U * pr + 1U; x++)
        {
          probe_t p = pair_probe(c, y, x, xref);
          if (p.d < TH_CONN)
          {
            fail(msg(clip_name(flip), " switch to X", msg(num(x), " (BUS", msg(num(x), ") bad", NULL))), &p);
          }
          else
          {
            chip_ok++;
          }
        }
      }
    }
    out("  U30%lu: %lu/128 switches OK\r\n", (unsigned long)c, (unsigned long)chip_ok);
  }
  ch_clear();
}

/* Each clip isolated from all other clips and from all other BUSes */
static void test_short(void)
{
  out("[short] clip-to-clip and clip-to-BUS isolation (48 clips x 2)\r\n");
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    for (uint32_t pass = 0U; pass < 2U; pass++)
    {
      uint32_t a = (i + pass * 8U) % CH_X;
      uint32_t b = (a + 1U) % CH_X;
      ch_clear();
      svc(SVC_Y_TEST, a, true);
      clip_sw(i, a, true);
      for (uint32_t x = 0U; x < CH_X; x++)
      {
        if (x != a)
        {
          svc(SVC_Y_MEAS_A, x, true);
        }
      }
      for (uint32_t j = 0U; j < CLIPS; j++)
      {
        if (j != i)
        {
          clip_sw(j, b, true);
        }
      }
      probe_t p = probe_ms(SETTLE_BIG_MS);
      if (p.d <= TH_OPEN)
      {
        continue;
      }
      bool found = false;
      for (uint32_t j = 0U; j < CLIPS; j++)
      {
        if (j == i)
        {
          continue;
        }
        ch_clear();
        svc(SVC_Y_TEST, a, true);
        clip_sw(i, a, true);
        svc(SVC_Y_MEAS_A, b, true);
        clip_sw(j, b, true);
        probe_t q = probe();
        if (q.d > TH_OPEN)
        {
          fail(msg(clip_name(i), " shorted to ", clip_name(j)), &q);
          s_clip_bad[i] = true;
          s_clip_bad[j] = true;
          found = true;
        }
      }
      for (uint32_t x = 0U; x < CH_X; x++)
      {
        if (x == a)
        {
          continue;
        }
        ch_clear();
        svc(SVC_Y_TEST, a, true);
        clip_sw(i, a, true);
        svc(SVC_Y_MEAS_A, x, true);
        probe_t q = probe();
        if (q.d > TH_OPEN)
        {
          fail(msg(clip_name(i), " shorted to BUS", num(x)), &q);
          s_clip_bad[i] = true;
          found = true;
        }
      }
      if (!found)
      {
        fail(msg(clip_name(i), " leaks (not isolated to one clip or BUS)", NULL), &p);
        s_clip_bad[i] = true;
      }
      break;  /* one report per clip is enough */
    }
  }
  ch_clear();
}

uint32_t matrix_selftest(const char *only)
{
  if (!matrix_ready())
  {
    return 1U;
  }
  matrix_clear();
  adc_report_t r;
  adc_measure(&r);
  s_vdda = r.vdda_mv;
  s_fail = 0U;
  s_fail_lines = 0U;
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    s_clip_bad[i] = false;
  }

  uint32_t t0 = board_millis();
  bool all = (only == NULL);
  out("self-test: breadboard must be EMPTY. VDDA %s V\r\n", mv(s_vdda));
  if (all || strcmp(only, "svc") == 0)    { test_svc(); }
  if (all || strcmp(only, "bus") == 0)    { test_bus(); }
  if (all || strcmp(only, "stb") == 0)    { test_stb(); }
  if (all || strcmp(only, "rst") == 0)    { test_rst(); }
  if (all || strcmp(only, "switch") == 0) { test_switch(); }
  if (all || strcmp(only, "short") == 0)  { test_short(); }
  drive(false);
  ch_clear();

  /* Clip LEDs: green = all its tests passed, red = failed */
  ws2812_fill(0U, 0U, 0U);
  uint32_t bad = 0U;
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    if (s_clip_bad[i])
    {
      bad++;
      ws2812_set(CLIPMAP[i].led - 1U, 255U, 0U, 0U);
    }
    else
    {
      ws2812_set(CLIPMAP[i].led - 1U, 0U, 255U, 0U);
    }
  }
  ws2812_show();

  out("self-test %s: %lu failure(s), %lu clip(s) marked bad, %lu ms\r\n",
      (s_fail == 0U) ? "PASS" : "FAIL", (unsigned long)s_fail, (unsigned long)bad,
      (unsigned long)(board_millis() - t0));
  out("LEDs: green = clip OK, red = clip failed. 'led off' to clear.\r\n");
  return s_fail;
}

/* ------------------------------------------------------------ connections */

bool matrix_parse_clip(const char *s, uint32_t *flip)
{
  if ((s == NULL) || (s[0] == '\0'))
  {
    return false;
  }
  char row = s[0];
  if (row == 'u')
  {
    row = 'U';
  }
  if (row == 'd')
  {
    row = 'D';
  }
  char *end;
  long col = strtol(&s[1], &end, 10);
  if (((row != 'U') && (row != 'D')) || (*end != '\0') || (col < 1) || (col > 24))
  {
    return false;
  }
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    if ((CLIPMAP[i].row == row) && (CLIPMAP[i].col == (uint8_t)col))
    {
      *flip = i;
      return true;
    }
  }
  return false;
}

static const uint8_t PALETTE[CH_X][3] = {
  { 255, 0, 0 },   { 0, 255, 0 },   { 0, 0, 255 },   { 255, 160, 0 },
  { 0, 255, 255 }, { 255, 0, 255 }, { 255, 255, 0 }, { 255, 255, 255 },
  { 128, 0, 255 }, { 0, 128, 255 }, { 255, 64, 128 },{ 128, 255, 0 },
  { 255, 96, 0 },  { 0, 255, 128 }, { 96, 96, 255 }, { 255, 128, 128 },
};

static void show_nets(void)
{
  ws2812_fill(0U, 0U, 0U);
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    uint8_t b = s_net_bus[i];
    if (b != NONE)
    {
      ws2812_set(CLIPMAP[i].led - 1U, PALETTE[b][0], PALETTE[b][1], PALETTE[b][2]);
    }
  }
  ws2812_show();
}

static bool bus_free(uint32_t b)
{
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    if (s_net_bus[i] == b)
    {
      return false;
    }
  }
  return true;
}

static void attach(uint32_t flip, uint32_t bus)
{
  clip_sw(flip, bus, true);
  s_net_bus[flip] = (uint8_t)bus;
}

void matrix_conn(uint32_t a, uint32_t b)
{
  if (a == b)
  {
    out("same clip\r\n");
    return;
  }
  if (!matrix_ready())
  {
    return;
  }
  uint8_t ba = s_net_bus[a];
  uint8_t bb = s_net_bus[b];
  uint32_t bus;
  if ((ba != NONE) && (bb != NONE))
  {
    if (ba == bb)
    {
      out("already connected on BUS%u\r\n", ba);
      return;
    }
    for (uint32_t i = 0U; i < CLIPS; i++)  /* merge net bb into ba */
    {
      if (s_net_bus[i] == bb)
      {
        clip_sw(i, bb, false);
        attach(i, ba);
      }
    }
    bus = ba;
  }
  else if (ba != NONE)
  {
    bus = ba;
    attach(b, bus);
  }
  else if (bb != NONE)
  {
    bus = bb;
    attach(a, bus);
  }
  else
  {
    bus = 0U;
    while ((bus < CH_X) && !bus_free(bus))
    {
      bus++;
    }
    if (bus == CH_X)
    {
      out("no free BUS (16 nets in use)\r\n");
      return;
    }
    attach(a, bus);
    attach(b, bus);
  }
  show_nets();
  out("BUS%lu:", (unsigned long)bus);
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    if (s_net_bus[i] == bus)
    {
      out(" %s", clip_name(i));
    }
  }
  out("\r\n  two clips on one BUS: expect about 90-130 ohm between their holes\r\n");
}

void matrix_clear(void)
{
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    s_net_bus[i] = NONE;
  }
  ch_clear();
  drive(false);
  ws2812_fill(0U, 0U, 0U);
  ws2812_show();
}

void matrix_nets(void)
{
  uint32_t n = 0U;
  for (uint32_t b = 0U; b < CH_X; b++)
  {
    if (bus_free(b))
    {
      continue;
    }
    n++;
    out("BUS%lu:", (unsigned long)b);
    for (uint32_t i = 0U; i < CLIPS; i++)
    {
      if (s_net_bus[i] == b)
      {
        out(" %s", clip_name(i));
      }
    }
    out("\r\n");
  }
  out("%lu net(s), %lu switch(es) on, matrix %s\r\n", (unsigned long)n,
      (unsigned long)ch_count_on(), ch_enabled() ? "enabled" : "in reset");
}

/* Voltage on a clip through U307 Y4 -> MEAS_A (1 MOhm load) */
void matrix_meas(uint32_t flip)
{
  if (!matrix_ready())
  {
    return;
  }
  uint32_t bus = s_net_bus[flip];
  bool temp = false;
  if (bus == NONE)
  {
    bus = 0U;
    while ((bus < CH_X) && !bus_free(bus))
    {
      bus++;
    }
    if (bus == CH_X)
    {
      out("no free BUS\r\n");
      return;
    }
    clip_sw(flip, bus, true);
    temp = true;
  }
  svc(SVC_Y_MEAS_A, bus, true);
  board_delay_ms(30U);
  int32_t vadc = read_mv(0U);
  svc(SVC_Y_MEAS_A, bus, false);
  if (temp)
  {
    clip_sw(flip, bus, false);
  }
  /* Vadc = 0.2545 x Vbus + 0.5758 x VDDA */
  int32_t vbus = (vadc * 10000 - 5758 * s_vdda) / 2545;
  out("%s via BUS%lu: %s V (PA0 %s V). Nothing connected reads about 2.5 V.\r\n",
      clip_name(flip), (unsigned long)bus, mv(vbus), mv(vadc));
}

/* Find clips joined outside the matrix (wires, parts): drive one clip, listen
 * on all other unassigned clips at once, then narrow down one by one. Each
 * group found lights its clips in one colour; all other LEDs stay off. */
void matrix_find(void)
{
  if (!matrix_ready())
  {
    return;
  }
  matrix_clear();
  adc_report_t r;
  adc_measure(&r);
  s_vdda = r.vdda_mv;

  uint8_t group[CLIPS];
  uint32_t groups = 0U;
  uint32_t t0 = board_millis();
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    group[i] = NONE;
  }
  out("find: driving each clip on BUS0, listening on BUS1 (nothing powered on the breadboard)\r\n");

  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    if (group[i] != NONE)
    {
      continue;
    }
    ch_clear();
    svc(SVC_Y_TEST, 0U, true);
    clip_sw(i, 0U, true);
    svc(SVC_Y_MEAS_A, 1U, true);
    for (uint32_t j = 0U; j < CLIPS; j++)
    {
      if ((j != i) && (group[j] == NONE))
      {
        clip_sw(j, 1U, true);
      }
    }
    probe_t p = probe_ms(SETTLE_BIG_MS);
    if (p.d <= TH_OPEN)
    {
      continue;
    }

    uint32_t found = 0U;
    for (uint32_t j = 0U; j < CLIPS; j++)
    {
      if ((j == i) || (group[j] != NONE))
      {
        continue;
      }
      ch_clear();
      svc(SVC_Y_TEST, 0U, true);
      clip_sw(i, 0U, true);
      svc(SVC_Y_MEAS_A, 1U, true);
      clip_sw(j, 1U, true);
      probe_t q = probe();
      if (q.d > TH_OPEN)
      {
        if (found == 0U)
        {
          group[i] = (uint8_t)groups;
          out("group %lu: %s", (unsigned long)(groups + 1U), clip_name(i));
        }
        group[j] = (uint8_t)groups;
        out(" <-> %s (d %ld mV%s)", clip_name(j), (long)q.d, (q.d < TH_CONN) ? ", WEAK" : "");
        found++;
      }
    }
    if (found != 0U)
    {
      out("\r\n");
      groups++;
    }
    else
    {
      out("%s leaks (d %ld mV) but no single partner clip found\r\n", clip_name(i), (long)p.d);
    }
  }
  ch_clear();
  drive(false);

  ws2812_fill(0U, 0U, 0U);
  for (uint32_t i = 0U; i < CLIPS; i++)
  {
    if (group[i] != NONE)
    {
      const uint8_t *c = PALETTE[group[i] % CH_X];
      ws2812_set(CLIPMAP[i].led - 1U, c[0], c[1], c[2]);
    }
  }
  ws2812_show();
  out("find done: %lu group(s), %lu ms. LEDs of the joined clips are lit; 'led off' to clear.\r\n",
      (unsigned long)groups, (unsigned long)(board_millis() - t0));
}

void matrix_probe_raw(uint32_t settle_ms)
{
  /* a long settle lets module caps charge: static VCC/GND diode test */
  probe_t p = probe_ms((settle_ms == 0U) ? SETTLE_MS : ((settle_ms > 1000U) ? 1000U : settle_ms));
  out("PA0 lo %s hi %s d %ld mV | PA5 lo %s d %ld mV\r\n",
      mv(p.lo), mv(p.hi), (long)p.d, mv(p.lo_b), (long)p.d_b);
}
