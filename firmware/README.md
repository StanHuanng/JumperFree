# Portable JF1/JF2 core

ISO C11 protocol/state implementation and a host-only memory backend, following
[`protocol/jf2.md`](../protocol/jf2.md). No HAL, RTOS, heap allocation, VLA,
recursion, Python runtime, zlib library, serial port or board SDK is required.
The host tests establish protocol behavior and memory simulation behavior.
STM32 execution and CH446Q physical switching are **unverified**. This directory
does not contain a flashable board project or real electrical drivers.

Run from the repository root with GNU make and GCC (or `CC=clang`):

```sh
make -C firmware clean
make -C firmware CC=gcc CFLAGS='-std=c11 -O2 -Wall -Wextra -Wpedantic -Werror' host
./firmware/build/jf2_host_test --self-test
./firmware/build/jf2_host_test protocol/vectors/*.vec
# Equivalent combined check, also works from firmware/: make test
make -C firmware test
```

`--self-test` runs core acceptance assertions and vector parser/matcher checks.
Explicit vector paths are accepted; every file starts a fresh device at virtual
time zero. Any syntax, response, callback-model or resource failure exits nonzero.
Diagnostic payloads use explicit byte lengths and escaped output. Host fixtures
are created beside the executable and removed; all generated files belong in
`build/`, which the repository already ignores.

This validation container has GCC 14.2.0, but lacks make and clang. The direct
GCC build used here is reproducible without make:

```sh
mkdir -p firmware/build
gcc -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror \
  -Ifirmware/include -Ifirmware/host_test \
  firmware/src/jf2_protocol.c firmware/src/jf2_state.c firmware/src/jf2_crc.c \
  firmware/host_test/memory_hw.c firmware/host_test/vector_runner.c \
  firmware/host_test/core_tests.c -o firmware/build/jf2_host_test
./firmware/build/jf2_host_test --self-test
./firmware/build/jf2_host_test protocol/vectors/*.vec
```

For memory checks add `-O1 -g -fsanitize=address,undefined
-fno-omit-frame-pointer` instead of `-O2`, and link with
`-fsanitize=address,undefined`. In this ptraced container, run with
`ASAN_OPTIONS=detect_leaks=0`; the default LeakSanitizer cannot run here.
See [TEST_RESULTS.md](TEST_RESULTS.md) for the exact results and limitations.

Allocate `Jf2Device` in caller-owned static storage. Bind all required `Jf2Hw`
callbacks and a synchronous `Jf2Emit` consumer with `jf2_init`. Feed arbitrary
byte chunks through `jf2_feed`, and call `jf2_poll` periodically even without
traffic. A direct complete-frame test entry is `jf2_process_request`; it applies
the same routing and validation, including missing LF. Calls are serial and
non-reentrant. The emit buffer includes one LF and is valid only during the
callback; copy it immediately. Callers own and clear any external captured TX
queue at reboot, as `memory_reboot` demonstrates. Supply a fresh boot identifier
and a monotonic `uint64_t` millisecond clock.

The supplied callbacks synchronously update independent memory switches/LEDs
and log actual OFF, ON, all-open and LED events. They cannot fail. No disconnected
or no-op board backend is supplied. A future physical driver needs a separately
defined completion/failure contract and board validation before it can report
physical execution. Shadow/CRC is bookkeeping, not a continuity measurement;
SELFTEST 0 is logical only, and levels 1/2 report no sensing hardware. Raw RGB
is retained; brightness_cap is a configuration value, not an applied limiter.
