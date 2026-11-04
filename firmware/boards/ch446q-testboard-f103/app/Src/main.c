/**
  * file  : main.c
  * brief : PC -> USART2 -> JF2 core -> CH446Q on the CH446Q test board (F103C8).
  */
#include "board.h"
#include "jf2_link.h"

int main(void)
{
  board_init();
  if (!jf2_link_init())
  {
    return -1;
  }

  while (1)
  {
    jf2_link_process();
  }
}
