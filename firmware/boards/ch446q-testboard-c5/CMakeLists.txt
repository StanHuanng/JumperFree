# file-format: 1.0.0
cmake_minimum_required(VERSION 3.30)

set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)
set(CMAKE_C_COMPILER_FORCED TRUE)
set(CMAKE_CXX_COMPILER_FORCED TRUE)

include(cmake/target.cmake)
include(cmake/${TOOLCHAIN_FILE})

project(CH446Q_LINK)
add_executable(CH446Q_LINK)
enable_language(C CXX ASM)
include(cmake/files.cmake)
include(cmake/flags.cmake)
include(cmake/components.cmake)

# Stubs to be able to add compiler flag overrides, e.g. -Os
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
  # Add additional source files here
  app/Src/jf2_link.c
  app/Src/ch446q.c
  ${CMAKE_CURRENT_LIST_DIR}/../../src/jf2_protocol.c
  ${CMAKE_CURRENT_LIST_DIR}/../../src/jf2_state.c
  ${CMAKE_CURRENT_LIST_DIR}/../../src/jf2_crc.c
)

target_include_directories(${CMAKE_PROJECT_NAME} PUBLIC
  # Add additional include directories here
  app/Inc
  ${CMAKE_CURRENT_LIST_DIR}/../../include
)

target_compile_definitions(${CMAKE_PROJECT_NAME} PUBLIC
  # Add additional defined symbols here
)

target_compile_options(${CMAKE_PROJECT_NAME} PUBLIC
  # Add additional compiler flags here
)

target_link_options(${CMAKE_PROJECT_NAME} PUBLIC
  # Add additional linker flags here
)

target_link_directories(${CMAKE_PROJECT_NAME} PUBLIC
  # Add additional linker search directories here
)

target_link_libraries(${CMAKE_PROJECT_NAME} PUBLIC
  # Add additional linker libraries here
)

# The JF2 core prints uint64 values with PRIu64; newlib-nano's printf has no
# %llu and misreads the following arguments, so use full newlib instead.
foreach(prop COMPILE_OPTIONS INTERFACE_COMPILE_OPTIONS LINK_OPTIONS INTERFACE_LINK_OPTIONS)
  get_target_property(opts ${CMAKE_PROJECT_NAME} ${prop})
  if(opts)
    list(TRANSFORM opts REPLACE "--specs=nano.specs" "")
    list(REMOVE_ITEM opts "")
    set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES ${prop} "${opts}")
  endif()
endforeach()
