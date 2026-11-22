CC = gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS += -Iinclude -Ihost_test
CORE = src/jf2_protocol.c src/jf2_state.c src/jf2_crc.c host_test/memory_hw.c host_test/vector_runner.c
HEADERS = include/jf2.h include/jf2_board.h include/jf2_state.h include/jf2_hw.h host_test/memory_hw.h
# Default board profile v01: core self-tests plus the shared vectors.
OBJECTS = $(CORE:%.c=build/%.o) build/host_test/core_tests.o
# Board profile v04 (-DJF2_PROFILE_V04): own objects, own self-tests.
OBJECTS_V04 = $(CORE:%.c=build/v04/%.o) build/v04/host_test/core_tests_v04.o
.PHONY: host host-v04 test test-v01 test-v04 clean
host: build/jf2_host_test
host-v04: build/v04/jf2_host_test
build/jf2_host_test: $(OBJECTS)
	$(CC) $(CFLAGS) $(OBJECTS) $(LDFLAGS) -o $@
build/v04/jf2_host_test: $(OBJECTS_V04)
	$(CC) $(CFLAGS) $(OBJECTS_V04) $(LDFLAGS) -o $@
build/v04/%.o: %.c $(HEADERS)
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) -DJF2_PROFILE_V04 $(CFLAGS) -c $< -o $@
build/%.o: %.c $(HEADERS)
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
test: test-v01 test-v04
test-v01: host
	./build/jf2_host_test --self-test
	./build/jf2_host_test ../protocol/vectors/*.vec
test-v04: host-v04
	./build/v04/jf2_host_test --self-test
clean:
	rm -rf build
