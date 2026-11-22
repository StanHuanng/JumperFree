/**
  * file  : board.c
  * brief : Clock, SysTick, safe GPIO states and USART1 for JumperFree v0.4.
  */
#include "board.h"

#define USART1_BRR_115200  0x22BU  /* 64 MHz / 115200 = 555.6 -> 34 + 11/16 */
#define RX_BUF_SIZE        256U
#define HSE_TIMEOUT        200000U

static volatile uint32_t s_ms;
static uint8_t s_rx_buf[RX_BUF_SIZE];
static uint32_t s_rx_tail;
static board_boot_t s_boot;

/* 4-bit CRL/CRH field for one pin */
static void pin_mode(GPIO_TypeDef *port, uint32_t pin, uint32_t mode)
{
  volatile uint32_t *cr = (pin < 8U) ? &port->CRL : &port->CRH;
  uint32_t shift = (pin & 7U) * 4U;
  *cr = (*cr & ~(0xFUL << shift)) | (mode << shift);
}

#define ANALOG       0x0U  /* MODE=00, CNF=00 */
#define IN_FLOAT     0x4U  /* MODE=00, CNF=01 */
#define IN_PULL      0x8U  /* MODE=00, CNF=10, ODR selects up/down */
#define OUT_PP_2MHZ  0x2U  /* MODE=10, CNF=00 */
#define OUT_OD_2MHZ  0x6U  /* MODE=10, CNF=01 */
#define AF_PP_2MHZ   0xAU  /* MODE=10, CNF=10 */

static bool clock_init(void)
{
  bool hse_ok = false;

  /* 64 MHz needs two flash wait states; enable the prefetch buffer */
  FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY_2;

  RCC->CR |= RCC_CR_HSEON;
  for (uint32_t i = 0U; i < HSE_TIMEOUT; i++)
  {
    if ((RCC->CR & RCC_CR_HSERDY) != 0U)
    {
      hse_ok = true;
      break;
    }
  }

  /* APB1 = 32 MHz (max 36), APB2 = 64 MHz, ADC = 64 / 6 = 10.7 MHz (max 14) */
  uint32_t cfgr;
  if (hse_ok)
  {
    cfgr = RCC_CFGR_PLLSRC | RCC_CFGR_PLLMULL8;  /* HSE 8 MHz x8 */
  }
  else
  {
    RCC->CR &= ~RCC_CR_HSEON;
    cfgr = RCC_CFGR_PLLMULL16;                   /* HSI/2 4 MHz x16 */
  }
  RCC->CFGR = cfgr | RCC_CFGR_PPRE1_DIV2 | RCC_CFGR_ADCPRE_DIV6;

  RCC->CR |= RCC_CR_PLLON;
  while ((RCC->CR & RCC_CR_PLLRDY) == 0U)
  {
  }
  RCC->CFGR |= RCC_CFGR_SW_PLL;
  while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL)
  {
  }
  SystemCoreClock = 64000000U;
  return hse_ok;
}

/* Every matrix control line goes to its safe level before it becomes an
 * output: CH_RST released (open-drain high = all switches open), STB / CLK /
 * DAT / TEST_DRV low. */
static void gpio_init(void)
{
  RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_IOPBEN | RCC_APB2ENR_AFIOEN;

  /* PA8 CH_RST: write 1 first, then open-drain -> pin floats, R300 holds RST high */
  GPIOA->BSRR = 1UL << 8;
  pin_mode(GPIOA, 8U, OUT_OD_2MHZ);

  /* PA7 CH7_STB low */
  GPIOA->BSRR = (1UL << 7) << 16;
  pin_mode(GPIOA, 7U, OUT_PP_2MHZ);

  /* PB0 CH6_STB, PB1 CH5_STB, PB8 TEST_DRV, PB10 CH3_STB, PB11 CH4_STB,
   * PB12 CH1_STB, PB13 CH_CLK, PB14 CH2_STB, PB15 CH_DAT: all low */
  static const uint8_t pb_out[] = { 0U, 1U, 8U, 10U, 11U, 12U, 13U, 14U, 15U };
  for (uint32_t i = 0U; i < sizeof(pb_out); i++)
  {
    GPIOB->BSRR = (1UL << pb_out[i]) << 16;
    pin_mode(GPIOB, pb_out[i], OUT_PP_2MHZ);
  }

  /* ADC inputs */
  pin_mode(GPIOA, 0U, ANALOG);  /* MEAS_A  */
  pin_mode(GPIOA, 2U, ANALOG);  /* V5_MON  */
  pin_mode(GPIOA, 3U, ANALOG);  /* VEE_MON */
  pin_mode(GPIOA, 4U, ANALOG);  /* VER_ID  */
  pin_mode(GPIOA, 5U, ANALOG);  /* MEAS_B  */

  /* PA6 ESTOP_MEM has its own 100k pull-up */
  pin_mode(GPIOA, 6U, IN_FLOAT);

  /* USART1: PA9 TX alternate push-pull, PA10 RX input pull-up */
  pin_mode(GPIOA, 9U, AF_PP_2MHZ);
  GPIOA->BSRR = 1UL << 10;
  pin_mode(GPIOA, 10U, IN_PULL);

  /* PB9 LED_DATA is configured by ws2812_init() */
}

static void usart1_init(void)
{
  RCC->APB2ENR |= RCC_APB2ENR_USART1EN;
  RCC->AHBENR |= RCC_AHBENR_DMA1EN;

  /* USART1_RX = DMA1 channel 5 */
  DMA1_Channel5->CCR = 0U;
  DMA1_Channel5->CPAR = (uint32_t)&USART1->DR;
  DMA1_Channel5->CMAR = (uint32_t)s_rx_buf;
  DMA1_Channel5->CNDTR = RX_BUF_SIZE;
  DMA1_Channel5->CCR = DMA_CCR_MINC | DMA_CCR_CIRC | DMA_CCR_PL_0 | DMA_CCR_EN;

  USART1->BRR = USART1_BRR_115200;
  USART1->CR3 = USART_CR3_DMAR;
  USART1->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
}

void board_init(void)
{
  /* Capture boot facts before anything changes them. The GPIOA clock needs
   * a few cycles after it is enabled before IDR reads are valid. */
  RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
  (void)RCC->APB2ENR;
  for (volatile uint32_t i = 0U; i < 16U; i++)
  {
  }
  s_boot.estop_low = (GPIOA->IDR & (1UL << 6)) == 0U;
  s_boot.reset_flags = RCC->CSR & 0xFF000000UL;
  RCC->CSR |= RCC_CSR_RMVF;

  gpio_init();
  s_boot.hse_ok = clock_init();
  SysTick_Config(SystemCoreClock / 1000U);
  usart1_init();
}

const board_boot_t *board_boot(void)
{
  return &s_boot;
}

uint32_t board_millis(void)
{
  return s_ms;
}

void board_delay_ms(uint32_t ms)
{
  uint32_t start = s_ms;
  while ((s_ms - start) < ms)
  {
  }
}

/* Software entry into the ROM USART bootloader: a magic value in backup
 * register DR1 survives the reset; board_check_bootloader() at the very start
 * of main() sees it and jumps to system memory before any clock or pin is
 * touched, as if BOOT0 were high. */
#define BOOT_MAGIC   0xB007U
#define SYSMEM_BASE  0x1FFFF000UL

void board_enter_bootloader(void)
{
  RCC->APB1ENR |= RCC_APB1ENR_PWREN | RCC_APB1ENR_BKPEN;
  PWR->CR |= PWR_CR_DBP;
  BKP->DR1 = BOOT_MAGIC;
  NVIC_SystemReset();
}

void board_check_bootloader(void)
{
  RCC->APB1ENR |= RCC_APB1ENR_PWREN | RCC_APB1ENR_BKPEN;
  (void)RCC->APB1ENR;
  if ((BKP->DR1 & 0xFFFFU) != BOOT_MAGIC)
  {
    return;
  }
  PWR->CR |= PWR_CR_DBP;
  BKP->DR1 = 0U;
  PWR->CR &= ~PWR_CR_DBP;
  RCC->APB1ENR &= ~(RCC_APB1ENR_PWREN | RCC_APB1ENR_BKPEN);

  uint32_t sp = *(const uint32_t *)SYSMEM_BASE;
  void (*entry)(void) = (void (*)(void))(*(const uint32_t *)(SYSMEM_BASE + 4U));
  __disable_irq();
  SysTick->CTRL = 0U;
  __set_MSP(sp);
  __enable_irq();
  entry();
}

bool board_estop_pin(void)
{
  return (GPIOA->IDR & (1UL << 6)) == 0U;
}

size_t board_uart_read(uint8_t *buf, size_t max)
{
  uint32_t head = (RX_BUF_SIZE - DMA1_Channel5->CNDTR) % RX_BUF_SIZE;
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
    while ((USART1->SR & USART_SR_TXE) == 0U)
    {
    }
    USART1->DR = data[i];
  }
}

void SysTick_Handler(void)
{
  s_ms++;
}
