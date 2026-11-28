CC = gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS += -Iinclude -Ihost_test
CORE = src/jf2_protocol.c src/jf2_state.c src/jf2_crc.c host_test/memory_hw.c host_test/vector_runner.c
HEADERS = include/jf2.h include/jf2_board.h include/jf2_state.h include/jf2_hw.h host_test/memory_hw.h
# Default board profile v01: core self-tests plus the shared vectors.
OBJECTS = $(CORE:%.c=build/%.o) build/host_test/core_tests.o
# board profile mini (-DJF2_PROFILE_MINI): own objects, own self-tests.
OBJECTS_MINI = $(CORE:%.c=build/mini/%.o) build/mini/host_test/core_tests_mini.o
.PHONY: host host-mini test test-v01 test-mini clean
host: build/jf2_host_test
host-mini: build/mini/jf2_host_test
build/jf2_host_test: $(OBJECTS)
	$(CC) $(CFLAGS) $(OBJECTS) $(LDFLAGS) -o $@
build/mini/jf2_host_test: $(OBJECTS_MINI)
	$(CC) $(CFLAGS) $(OBJECTS_MINI) $(LDFLAGS) -o $@
build/mini/%.o: %.c $(HEADERS)
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) -DJF2_PROFILE_MINI $(CFLAGS) -c $< -o $@
build/%.o: %.c $(HEADERS)
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
test: test-v01 test-mini
test-v01: host
	./build/jf2_host_test --self-test
	./build/jf2_host_test ../protocol/vectors/*.vec
test-mini: host-mini
	./build/mini/jf2_host_test --self-test
clean:
	rm -rf build
