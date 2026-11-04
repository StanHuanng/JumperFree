/**
  * file  : board.c
  * brief : Clock, SysTick, GPIO and USART2 for the F103 on the CH446Q test board.
  */
#include "board.h"

#define USART2_BRR_115200  278U  /* 32 MHz / 115200 = 277.8 -> 17 + 6/16 */
#define RX_BUF_SIZE        512U  /* > one 512-byte JF2 frame in flight */

static volatile uint32_t s_ms;
static uint8_t s_rx_buf[RX_BUF_SIZE];
static uint32_t s_rx_tail;

static void clock_init(void)
{
  /* 64 MHz needs two flash wait states; enable the prefetch buffer */
  FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY_2;

  /* PLL source HSI/2 = 4 MHz, x16 = 64 MHz; APB1 = 32 MHz, APB2 = 64 MHz */
  RCC->CFGR = RCC_CFGR_PLLMULL16 | RCC_CFGR_PPRE1_DIV2;
  RCC->CR |= RCC_CR_PLLON;
  while ((RCC->CR & RCC_CR_PLLRDY) == 0U)
  {
  }
  RCC->CFGR |= RCC_CFGR_SW_PLL;
  while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL)
  {
  }
  SystemCoreClock = 64000000U;
}

/* 4-bit CRL/CRH field for one pin */
static void pin_mode(GPIO_TypeDef *port, uint32_t pin, uint32_t mode)
{
  volatile uint32_t *cr = (pin < 8U) ? &port->CRL : &port->CRH;
  uint32_t shift = (pin & 7U) * 4U;
  *cr = (*cr & ~(0xFUL << shift)) | (mode << shift);
}

#define OUT_PP_2MHZ  0x2U  /* MODE=10, CNF=00 */
#define AF_PP_2MHZ   0xAU  /* MODE=10, CNF=10 */
#define IN_PULL      0x8U  /* MODE=00, CNF=10, ODR selects up/down */

static void gpio_init(void)
{
  RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_IOPBEN | RCC_APB2ENR_IOPCEN;

  /* CH446Q pins low before they become outputs */
  GPIOA->BSRR = (0x1FUL << 8) << 16;  /* PA8-PA12 */
  GPIOB->BSRR = (0x1FUL << 5) << 16;  /* PB5-PB9  */
  for (uint32_t pin = 8U; pin <= 12U; pin++)
  {
    pin_mode(GPIOA, pin, OUT_PP_2MHZ);
  }
  for (uint32_t pin = 5U; pin <= 9U; pin++)
  {
    pin_mode(GPIOB, pin, OUT_PP_2MHZ);
  }

  GPIOC->BSRR = 1UL << 13;  /* LED off */
  pin_mode(GPIOC, 13U, OUT_PP_2MHZ);

  /* USART2: PA2 TX alternate push-pull, PA3 RX input with pull-up (idle high
   * when no adapter is connected) */
  pin_mode(GPIOA, 2U, AF_PP_2MHZ);
  GPIOA->BSRR = 1UL << 3;
  pin_mode(GPIOA, 3U, IN_PULL);
}

/* USART2 RX lands in a circular buffer through DMA1 channel 6, so reception
 * keeps going while interrupts are masked (WS2812 frames mask them ~1 ms) */
static void usart2_init(void)
{
  RCC->APB1ENR |= RCC_APB1ENR_USART2EN;
  RCC->AHBENR |= RCC_AHBENR_DMA1EN;

  DMA1_Channel6->CCR = 0U;
  DMA1_Channel6->CPAR = (uint32_t)&USART2->DR;
  DMA1_Channel6->CMAR = (uint32_t)s_rx_buf;
  DMA1_Channel6->CNDTR = RX_BUF_SIZE;
  DMA1_Channel6->CCR = DMA_CCR_MINC | DMA_CCR_CIRC | DMA_CCR_PL_1 | DMA_CCR_EN;

  USART2->BRR = USART2_BRR_115200;
  USART2->CR3 = USART_CR3_DMAR;
  USART2->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
}

void board_init(void)
{
  clock_init();
  SysTick_Config(SystemCoreClock / 1000U);
  gpio_init();
  usart2_init();
}

uint32_t board_millis(void)
{
  return s_ms;
}

void board_led(bool on)
{
  GPIOC->BSRR = on ? (1UL << (13U + 16U)) : (1UL << 13U);
}

size_t board_uart_read(uint8_t *buf, size_t max)
{
  uint32_t head = (RX_BUF_SIZE - DMA1_Channel6->CNDTR) % RX_BUF_SIZE;
  size_t n = 0U;
  while ((s_rx_tail != head) && (n < max))
  {
    buf[n++] = s_rx_buf[s_rx_tail];
    s_rx_tail = (s_rx_tail + 1U) % RX_BUF_SIZE;
  }
  return n;
}

void board_uart_write(const uint8_t *data, size_t len)
{
  for (size_t i = 0U; i < len; i++)
  {
    while ((USART2->SR & USART_SR_TXE) == 0U)
    {
    }
    USART2->DR = data[i];
  }
}

void SysTick_Handler(void)
{
  s_ms++;
}
