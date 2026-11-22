cmake_minimum_required(VERSION 3.22)

# JumperFree v0.4 bring-up firmware (STM32F103C8T6, register-level CMSIS)
# Configure:  cmake -S . -B build -G Ninja
# Build:      cmake --build build
# Flash over the CH340N (hold SW3 BOOT0, tap SW2 reset, release SW3):
#   STM32_Programmer_CLI -c port=COM4 br=115200 P=EVEN -w build/V04_BRINGUP.elf -v -s
set(CMAKE_TOOLCHAIN_FILE ${CMAKE_CURRENT_LIST_DIR}/cmake/arm-none-eabi.cmake CACHE FILEPATH "")
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE MinSizeRel)
endif()

project(V04_BRINGUP C ASM)
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

add_executable(V04_BRINGUP
  startup/startup_stm32f103xb.s
  app/Src/system_stm32f1xx.c
  app/Src/syscalls.c
  app/Src/board.c
  app/Src/adc.c
  app/Src/ws2812.c
  app/Src/ch446q.c
  app/Src/matrix.c
  app/Src/jf2_link.c
  app/Src/main.c
  app/jf2/jf2_protocol.c
  app/jf2/jf2_state.c
  app/jf2/jf2_crc.c
)

target_include_directories(V04_BRINGUP PRIVATE
  app/Inc
  app/jf2
  cmsis/core
  cmsis/device
)

target_compile_definitions(V04_BRINGUP PRIVATE STM32F103xB)

set(CPU_FLAGS -mcpu=cortex-m3 -mthumb)
target_compile_options(V04_BRINGUP PRIVATE
  ${CPU_FLAGS} -g3 -ffunction-sections -fdata-sections -Wall -Wextra
  $<$<COMPILE_LANGUAGE:C>:-std=gnu11>
  $<$<COMPILE_LANGUAGE:ASM>:-x assembler-with-cpp>
)

target_link_options(V04_BRINGUP PRIVATE
  # Full newlib: the JF2 core prints uint64 with PRIu64 (no %llu in nano)
  ${CPU_FLAGS} --specs=nosys.specs
  -T${CMAKE_CURRENT_LIST_DIR}/STM32F103C8_FLASH.ld
  -Wl,--gc-sections -Wl,-Map=${CMAKE_CURRENT_BINARY_DIR}/V04_BRINGUP.map
  -Wl,--print-memory-usage -Wl,--no-warn-rwx-segments
)
set_target_properties(V04_BRINGUP PROPERTIES
  LINK_DEPENDS ${CMAKE_CURRENT_LIST_DIR}/STM32F103C8_FLASH.ld)

add_custom_command(TARGET V04_BRINGUP POST_BUILD
  COMMAND ${CMAKE_SIZE} $<TARGET_FILE:V04_BRINGUP>
  COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:V04_BRINGUP> V04_BRINGUP.bin
)
