/**
  * file  : console.h
  * brief : USART1 console output shared by the bring-up modules.
  */
#ifndef CONSOLE_H
#define CONSOLE_H

#include <stdint.h>

void out(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
const char *mv(int32_t millivolts);  /* "-7.012" (rotating static buffers) */

#endif /* CONSOLE_H */
