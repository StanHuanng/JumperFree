/**
  * file  : main.c
  * brief : JumperFree v0.4 bring-up console on USART1 (CH340N, 115200 8N1).
  *         Phase 1: clock, supply monitors, LED chain.
  *         Phase 2: CH446Q matrix self-test, clip connections, BUS measurement.
  *         The matrix stays in reset (all switches open) until a matrix
  *         command enables it, and only when +5V and -7V are in range.
  */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "adc.h"
#include "board.h"
#include "ch446q.h"
#include "console.h"
#include "jf2_link.h"
#include "jf2_state.h"
#include "matrix.h"
#include "ws2812.h"

#define FW_NAME     "JumperFree v0.4 bring-up"
#define FW_VERSION  "0.3 (phase 3: JF2 real-time)"
#define LINE_MAX    (JF2_FRAME_MAX + 8U)

void out(const char *fmt, ...)
{
  char buf[160];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (n > 0)
  {
    board_uart_write((const uint8_t *)buf, (n < (int)sizeof(buf)) ? (size_t)n : sizeof(buf) - 1U);
  }
}

/* Signed millivolts as "-7.012" */
const char *mv(int32_t v)
{
  static char b[4][16];
  static uint32_t k;
  char *s = b[k++ & 3U];
  int32_t a = (v < 0) ? -v : v;
  snprintf(s, 16, "%s%ld.%03ld", (v < 0) ? "-" : "", (long)(a / 1000), (long)(a % 1000));
  return s;
}

static const char *judge(int32_t v, int32_t lo, int32_t hi)
{
  return ((v >= lo) && (v <= hi)) ? "OK" : "FAIL";
}

/* Any byte received aborts a long test */
static bool key_pressed(void)
{
  uint8_t c;
  return board_uart_read(&c, 1U) != 0U;
}

static void cmd_info(void)
{
  const board_boot_t *b = board_boot();
  const uint32_t *uid = (const uint32_t *)0x1FFFF7E8UL;
  uint32_t f = b->reset_flags;

  out("firmware : %s %s\r\n", FW_NAME, FW_VERSION);
  out("clock    : %s -> PLL 64 MHz  [%s]\r\n",
      b->hse_ok ? "HSE 8 MHz crystal" : "HSI (crystal did NOT start)",
      b->hse_ok ? "OK" : "FAIL");
  out("chip     : flash %u KB, UID %08lX %08lX %08lX\r\n",
      (unsigned)*(const uint16_t *)0x1FFFF7E0UL,
      (unsigned long)uid[0], (unsigned long)uid[1], (unsigned long)uid[2]);
  out("reset    :%s%s%s%s%s%s\r\n",
      (f & RCC_CSR_PORRSTF) ? " POWER-ON" : "",
      (f & RCC_CSR_PINRSTF) ? " NRST-PIN" : "",
      (f & RCC_CSR_SFTRSTF) ? " SOFTWARE" : "",
      (f & RCC_CSR_IWDGRSTF) ? " IWDG" : "",
      (f & RCC_CSR_WWDGRSTF) ? " WWDG" : "",
      (f & RCC_CSR_LPWRRSTF) ? " LOW-POWER" : "");
  out("ESTOP_MEM: at boot %s, now %s", b->estop_low ? "LOW" : "high",
      board_estop_pin() ? "LOW" : "high");
  if (b->estop_low && ((f & RCC_CSR_PORRSTF) == 0U))
  {
    out("  -> E-stop (SW1) was pressed before this boot");
  }
  else if (b->estop_low)
  {
    out("  (normal after power-on: C10 charges in ~1 s)");
  }
  out("\r\nmatrix   : %s, %lu switch(es) on\r\n",
      ch_enabled() ? "enabled (CH_RST low)" : "in reset (CH_RST released, all switches open)",
      (unsigned long)ch_count_on());
  out("uptime   : %lu ms\r\n", (unsigned long)board_millis());
}

static void cmd_adc(void)
{
  adc_report_t r;
  adc_measure(&r);
  out("VDDA (3V3A) %7s V  raw VREFINT %4u   expect 3.20..3.40  [%s]\r\n",
      mv(r.vdda_mv), r.raw_vref, judge(r.vdda_mv, 3200, 3400));
  out("+5V         %7s V  raw %4u           expect 4.60..5.30  [%s]\r\n",
      mv(r.v5_mv), r.raw_v5, judge(r.v5_mv, 4600, 5300));
  out("-7V         %7s V  raw %4u           expect -7.50..-6.50 [%s]\r\n",
      mv(r.vee_mv), r.raw_vee, judge(r.vee_mv, -7500, -6500));
  out("VER_ID      %7s V  raw %4u           expect 0.75..0.89  [%s]\r\n",
      mv(r.ver_mv), r.raw_ver, judge(r.ver_mv, 750, 890));
  out("MEAS_A pin  %7s V  raw %4u           (BUS open: about 2.55)\r\n", mv(r.ma_mv), r.raw_ma);
  out("MEAS_B pin  %7s V  raw %4u           (BUS open: about 2.55)\r\n", mv(r.mb_mv), r.raw_mb);
}

static void led_report(void)
{
  out("LED level %lu/255, estimated chain current %lu mA\r\n",
      (unsigned long)ws2812_level(), (unsigned long)ws2812_current_ma());
}

/* Each LED in turn: red, green, blue. Abort with any key. */
static void led_walk(uint32_t step_ms)
{
  static const uint8_t col[3][3] = { { 255U, 0U, 0U }, { 0U, 255U, 0U }, { 0U, 0U, 255U } };
  static const char *name[3] = { "R", "G", "B" };
  out("walk: RGB1..RGB48, R/G/B, %lu ms per step, any key stops\r\n", (unsigned long)step_ms);
  for (uint32_t i = 0U; i < WS2812_COUNT; i++)
  {
    out("RGB%lu", (unsigned long)(i + 1U));
    for (uint32_t c = 0U; c < 3U; c++)
    {
      ws2812_fill(0U, 0U, 0U);
      ws2812_set(i, col[c][0], col[c][1], col[c][2]);
      ws2812_show();
      out(" %s", name[c]);
      board_delay_ms(step_ms);
      if (key_pressed())
      {
        ws2812_fill(0U, 0U, 0U);
        ws2812_show();
        out("\r\nstopped at RGB%lu\r\n", (unsigned long)(i + 1U));
        return;
      }
    }
    out("\r\n");
  }
  ws2812_fill(0U, 0U, 0U);
  ws2812_show();
  out("walk done\r\n");
}

/* Whole chain red, green, blue, white, then off */
static void led_rgb(uint32_t step_ms)
{
  static const uint8_t col[4][3] = { { 255U, 0U, 0U }, { 0U, 255U, 0U }, { 0U, 0U, 255U }, { 255U, 255U, 255U } };
  static const char *name[4] = { "red", "green", "blue", "white" };
  for (uint32_t c = 0U; c < 4U; c++)
  {
    ws2812_fill(col[c][0], col[c][1], col[c][2]);
    ws2812_show();
    out("all %s, ", name[c]);
    led_report();
    board_delay_ms(step_ms);
    if (key_pressed())
    {
      break;
    }
  }
  ws2812_fill(0U, 0U, 0U);
  ws2812_show();
  out("all off\r\n");
}

static void cmd_help(void)
{
  out("commands:\r\n"
      "  info                 clock, chip, reset cause, E-stop memory\r\n"
      "  adc                  supply monitors (+5V, -7V, 3V3A, VER_ID)\r\n"
      "  led off              all LEDs off\r\n"
      "  led level <0-%u>     global brightness (default %u, hard cap %u)\r\n"
      "  led all <r> <g> <b>  whole chain one colour (0-255, scaled by level)\r\n"
      "  led one <1-48> <r> <g> <b>  one LED, others off\r\n"
      "  led rgb [ms]         whole chain R, G, B, white (default 1000 ms each)\r\n"
      "  led walk [ms]        RGB1..48 one by one R/G/B (default 150 ms)\r\n"
      "  selftest [svc|bus|stb|rst|switch|short]  matrix self-test, breadboard empty\r\n"
      "  conn <clip> <clip>   connect two clips, e.g. conn U6 D20 (U = back row, USB side)\r\n"
      "  nets                 list connections\r\n"
      "  find                 find clips joined by wires/parts, light their LEDs\r\n"
      "  clr                  open every switch, LEDs off\r\n"
      "  meas <clip>          voltage on a clip (1 Mohm load)\r\n"
      "  sw <chip1-7> <x> <y> <0|1>  raw switch write\r\n"
      "  probe                TEST_SRC low/high, show PA0/PA5\r\n"
      "  mx off               put the matrix back in reset\r\n"
      "  reset                software reset\r\n"
      "  boot                 reset into the ROM USART bootloader (flash without SW3)\r\n",
      WS2812_LEVEL_MAX, WS2812_LEVEL_DEFAULT, WS2812_LEVEL_MAX);
}

static uint32_t arg_u(char *s, uint32_t def)
{
  return (s != NULL) ? (uint32_t)strtoul(s, NULL, 0) : def;
}

static void cmd_led(char *sub)
{
  char *a1 = strtok(NULL, " ");
  char *a2 = strtok(NULL, " ");
  char *a3 = strtok(NULL, " ");
  char *a4 = strtok(NULL, " ");

  if ((sub == NULL) || (strcmp(sub, "off") == 0))
  {
    ws2812_fill(0U, 0U, 0U);
    ws2812_show();
    led_report();
  }
  else if (strcmp(sub, "level") == 0)
  {
    ws2812_set_level(arg_u(a1, WS2812_LEVEL_DEFAULT));
    ws2812_show();
    led_report();
  }
  else if ((strcmp(sub, "all") == 0) && (a3 != NULL))
  {
    ws2812_fill((uint8_t)arg_u(a1, 0U), (uint8_t)arg_u(a2, 0U), (uint8_t)arg_u(a3, 0U));
    ws2812_show();
    led_report();
  }
  else if ((strcmp(sub, "one") == 0) && (a4 != NULL))
  {
    uint32_t n = arg_u(a1, 0U);
    if ((n < 1U) || (n > WS2812_COUNT))
    {
      out("index 1..%u\r\n", WS2812_COUNT);
      return;
    }
    ws2812_fill(0U, 0U, 0U);
    ws2812_set(n - 1U, (uint8_t)arg_u(a2, 0U), (uint8_t)arg_u(a3, 0U), (uint8_t)arg_u(a4, 0U));
    ws2812_show();
    led_report();
  }
  else if (strcmp(sub, "rgb") == 0)
  {
    led_rgb(arg_u(a1, 1000U));
  }
  else if (strcmp(sub, "walk") == 0)
  {
    led_walk(arg_u(a1, 150U));
  }
  else
  {
    out("? see help\r\n");
  }
}

/* Console matrix commands and JF2 must not share the switches: a console
 * matrix command ends the JF2 session (JF2 reboot, all open), and the first
 * JF2 frame after console use clears the console connections. */
static bool s_console_matrix;

static bool touches_matrix(const char *cmd)
{
  static const char *const names[] = { "selftest", "conn", "meas", "clr", "sw", "probe", "mx", "find" };
  for (uint32_t i = 0U; i < sizeof(names) / sizeof(names[0]); i++)
  {
    if (strcmp(cmd, names[i]) == 0)
    {
      return true;
    }
  }
  return false;
}

static void execute(char *line)
{
  char *cmd = strtok(line, " ");
  if (cmd == NULL)
  {
    return;
  }
  if (touches_matrix(cmd))
  {
    if (jf2_link_release())
    {
      out("(JF2 session ended: all switches open, host must HELLO again)\r\n");
    }
    s_console_matrix = true;
  }
  if (strcmp(cmd, "help") == 0 || strcmp(cmd, "?") == 0)
  {
    cmd_help();
  }
  else if (strcmp(cmd, "info") == 0)
  {
    cmd_info();
  }
  else if (strcmp(cmd, "adc") == 0)
  {
    cmd_adc();
  }
  else if (strcmp(cmd, "led") == 0)
  {
    cmd_led(strtok(NULL, " "));
  }
  else if (strcmp(cmd, "selftest") == 0)
  {
    (void)matrix_selftest(strtok(NULL, " "));
  }
  else if ((strcmp(cmd, "conn") == 0) || (strcmp(cmd, "meas") == 0))
  {
    uint32_t a;
    uint32_t b;
    char *s1 = strtok(NULL, " ");
    char *s2 = strtok(NULL, " ");
    if (!matrix_parse_clip(s1, &a))
    {
      out("clip like U6 or D20 (U = back row, D = front row, column 1-24)\r\n");
    }
    else if (cmd[0] == 'm')
    {
      matrix_meas(a);
    }
    else if (!matrix_parse_clip(s2, &b))
    {
      out("second clip like U6 or D20\r\n");
    }
    else
    {
      matrix_conn(a, b);
    }
  }
  else if (strcmp(cmd, "find") == 0)
  {
    matrix_find();
  }
  else if (strcmp(cmd, "nets") == 0)
  {
    matrix_nets();
  }
  else if (strcmp(cmd, "clr") == 0)
  {
    matrix_clear();
    out("all switches open\r\n");
  }
  else if (strcmp(cmd, "sw") == 0)
  {
    uint32_t c = arg_u(strtok(NULL, " "), 0U);
    uint32_t x = arg_u(strtok(NULL, " "), 99U);
    uint32_t y = arg_u(strtok(NULL, " "), 99U);
    uint32_t on = arg_u(strtok(NULL, " "), 0U);
    if ((c < 1U) || (c > CH_CHIPS) || (x >= CH_X) || (y >= CH_Y))
    {
      out("sw <chip 1-7> <x 0-15> <y 0-7> <0|1>\r\n");
    }
    else if (matrix_ready())
    {
      ch_write(c, x, y, on != 0U);
      out("U30%lu Y%lu-X%lu %s\r\n", (unsigned long)c, (unsigned long)y, (unsigned long)x,
          (on != 0U) ? "on" : "off");
    }
  }
  else if (strcmp(cmd, "probe") == 0)
  {
    if (matrix_ready())
    {
      matrix_probe_raw();
    }
  }
  else if (strcmp(cmd, "mx") == 0)
  {
    matrix_clear();
    ch_disable();
    out("matrix in reset\r\n");
  }
  else if (strcmp(cmd, "reset") == 0)
  {
    out("resetting\r\n");
    board_delay_ms(20U);
    NVIC_SystemReset();
  }
  else if (strcmp(cmd, "boot") == 0)
  {
    ws2812_fill(0U, 0U, 0U);
    ws2812_show();
    out("entering ROM bootloader (115200 8E1)\r\n");
    board_delay_ms(20U);
    board_enter_bootloader();
  }
  else
  {
    out("unknown command '%s', type help\r\n", cmd);
  }
}

int main(void)
{
  board_check_bootloader();  /* does not return after a 'boot' command */
  board_init();
  ws2812_init();  /* chain cleared before anything else is reported */
  adc_init();
  ch_init();
  jf2_link_init();  /* matrix enabled (all open) when +5V and -7V are in range */

  out("\r\n\r\n=== %s %s ===\r\n", FW_NAME, FW_VERSION);
  cmd_info();
  cmd_adc();
  out("JF2 board=v04 ready on this port (lines starting with JF, LF-terminated)\r\n");
  out("type help\r\n> ");

  static char line[LINE_MAX];
  uint32_t len = 0U;
  bool jf_line = false;
  while (1)
  {
    jf2_link_poll();
    uint8_t c;
    if (board_uart_read(&c, 1U) == 0U)
    {
      continue;
    }
    if ((len == 0U) && (c == 'J'))
    {
      jf_line = true;  /* JF1/JF2 frame: no echo, no prompt */
    }
    if ((c == '\r') || (c == '\n'))
    {
      if (len == 0U)
      {
        continue;  /* CR LF pairs and empty lines */
      }
      if (jf_line)
      {
        if (s_console_matrix)
        {
          matrix_clear();
          s_console_matrix = false;
        }
        jf2_link_line(line, len);
      }
      else
      {
        out("\r\n");
        line[len] = '\0';
        execute(line);
        out("> ");
      }
      len = 0U;
      jf_line = false;
    }
    else if (!jf_line && ((c == 0x08U) || (c == 0x7FU)))
    {
      if (len > 0U)
      {
        len--;
        out("\b \b");
      }
    }
    else if (len < (LINE_MAX - 1U))
    {
      line[len++] = (char)c;  /* JF frames keep every byte; the core validates */
      if (!jf_line && (c >= 0x20U))
      {
        board_uart_write(&c, 1U);
      }
    }
  }
}
