cmake_minimum_required(VERSION 3.22)

# Configure:  cmake -S . -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake
# Build:      cmake --build build
set(CMAKE_TOOLCHAIN_FILE ${CMAKE_CURRENT_LIST_DIR}/cmake/arm-none-eabi.cmake CACHE FILEPATH "")
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE MinSizeRel)
endif()

project(CH446Q_LINK_F103 C ASM)
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

add_executable(CH446Q_LINK_F103
  startup/startup_stm32f103xb.s
  app/Src/system_stm32f1xx.c
  app/Src/board.c
  app/Src/main.c
  app/Src/syscalls.c
  app/Src/jf2_link.c
  app/Src/ch446q.c
  app/Src/ws2812.c
  app/Src/display.c
  ${CMAKE_CURRENT_LIST_DIR}/../../src/jf2_protocol.c
  ${CMAKE_CURRENT_LIST_DIR}/../../src/jf2_state.c
  ${CMAKE_CURRENT_LIST_DIR}/../../src/jf2_crc.c
)

target_include_directories(CH446Q_LINK_F103 PRIVATE
  app/Inc
  ${CMAKE_CURRENT_LIST_DIR}/../../include
  cmsis/core
  cmsis/device
)

target_compile_definitions(CH446Q_LINK_F103 PRIVATE STM32F103xB)

set(CPU_FLAGS -mcpu=cortex-m3 -mthumb)
target_compile_options(CH446Q_LINK_F103 PRIVATE
  ${CPU_FLAGS} -g3 -ffunction-sections -fdata-sections -Wall
  $<$<COMPILE_LANGUAGE:C>:-std=gnu11>
  $<$<COMPILE_LANGUAGE:ASM>:-x assembler-with-cpp>
)

# Full newlib (no --specs=nano.specs): the JF2 core prints uint64 values with
# PRIu64 and newlib-nano's printf has no %llu.
target_link_options(CH446Q_LINK_F103 PRIVATE
  ${CPU_FLAGS} --specs=nosys.specs
  -T${CMAKE_CURRENT_LIST_DIR}/STM32F103C8_FLASH.ld
  -Wl,--gc-sections -Wl,-Map=${CMAKE_CURRENT_BINARY_DIR}/CH446Q_LINK_F103.map
  -Wl,--print-memory-usage -Wl,--no-warn-rwx-segments
)
set_target_properties(CH446Q_LINK_F103 PROPERTIES
  LINK_DEPENDS ${CMAKE_CURRENT_LIST_DIR}/STM32F103C8_FLASH.ld)

add_custom_command(TARGET CH446Q_LINK_F103 POST_BUILD
  COMMAND ${CMAKE_SIZE} $<TARGET_FILE:CH446Q_LINK_F103>
  COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:CH446Q_LINK_F103> CH446Q_LINK_F103.bin
)
