CC = gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS += -Iinclude -Ihost_test
SOURCES = src/jf2_protocol.c src/jf2_state.c src/jf2_crc.c host_test/memory_hw.c host_test/vector_runner.c host_test/core_tests.c
OBJECTS = $(SOURCES:%.c=build/%.o)
.PHONY: host test clean
host: build/jf2_host_test
build/jf2_host_test: $(OBJECTS)
	$(CC) $(CFLAGS) $(OBJECTS) $(LDFLAGS) -o $@
build/%.o: %.c include/jf2.h include/jf2_state.h include/jf2_hw.h host_test/memory_hw.h
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
test: host
	./build/jf2_host_test --self-test
	./build/jf2_host_test ../protocol/vectors/*.vec
clean:
	rm -rf build
