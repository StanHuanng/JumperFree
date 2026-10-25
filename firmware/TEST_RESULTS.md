# Host validation — 2026-10-25

Worktree: `/workspace/JumperFree-f103-jf2-core`.
Branch: `feat/f103-jf2-core`.
Base/current HEAD: `2c649c30353839297429a39084c34eacee0e59e1`.
Implementation is uncommitted. No commit, push, PR, merge or author configuration
was performed. PR: N/A — prohibited for this task; Sol handles publication.
All source/document changes are under `firmware/`. Shared vectors and Python
host/router/services were not modified. Generated artifacts are ignored under
`firmware/build/`.

## Toolchain and commands actually run

GCC: `gcc (Debian 14.2.0-19) 14.2.0`; target `x86_64-linux-gnu`, Linux host,
LP64 ABI (`sizeof(void *)=8`, `sizeof(size_t)=8`). GNU `size` is available.
Neither `make` nor `clang` is installed/on PATH; local tool searches found no
alternate copy. The mandated commands were attempted:

```text
make -C firmware clean
  /bin/bash: make: command not found                         exit 127
make -C firmware CC=gcc CFLAGS='-std=c11 -O2 -Wall -Wextra -Wpedantic -Werror' host
  /bin/bash: make: command not found                         exit 127
make -C firmware CC=clang CFLAGS='-std=c11 -O2 -Wall -Wextra -Wpedantic -Werror' test
  /bin/bash: make: command not found                         exit 127
clang --version
  /bin/bash: clang: command not found                        exit 127
```

The requested clang sanitizer `make ... test` command was also attempted and
returned 127 because make is missing. These are **unavailable checks, not PASS**.
The Makefile's host/test/clean recipes were reviewed, but execution through make
and compilation through clang remain unverified in this environment.

Instead, all six C translation units were compiled directly, with the requested
strict GCC options. This command completed with exit 0 and no diagnostics:

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

Final self-test output, exit 0:

```text
core self-tests: 6400 passed, 0 failed
sizeof: Jf2Device=3184 Jf2Transaction=1560 Jf2Op=4 Jf2Result=24 Jf2Shadow=160 MemoryHw=102688 (host only)
runner self-tests: 97 passed, 0 failed
```

The initial supplementary run found one incorrect test expectation: the test
tried replaying a committed result after its fourth newer completion had evicted
it. The test now establishes a recent completed result before checking SAFE
replay, while separately checking eviction. No vector or protocol expectation
was relaxed. The final runs below have no failing acceptance assertions.

## Fixed vectors

The existing `.vec` files were read directly by the C executable, without
Python, generating expected responses or changing the original files.

| File under `protocol/vectors/` | Passed | Failed | Directives |
| --- | ---: | ---: | ---: |
| jf1.vec | 174 | 0 | 0 |
| jf2-discovery.vec | 17 | 0 | 0 |
| jf2-lease-faults.vec | 25 | 0 | 9 |
| jf2-led-interleave.vec | 9 | 0 | 0 |
| jf2-txn-basic.vec | 15 | 0 | 0 |
| jf2-txn-errors.vec | 32 | 0 | 0 |
| **Total** | **272** | **0** | **9** |

Exit status: 0. The same self-tests and six files also passed when invoked from
`firmware/` as `./build/jf2_host_test --self-test` followed by
`./build/jf2_host_test ../protocol/vectors/*.vec`, verifying the working-directory
paths used by `make test` independently of the unavailable make executable.

Negative CLI acceptance: copied `jf1.vec` to ignored
`firmware/build/corrupted.vec` and changed exactly the first expected response's
`JF1 OK` to `JF1 WRONG`. Running the executable on that temporary copy produced
**173 passed, 1 failed**, exit **1**, with `:7: FAIL token=2`, escaped
request/expected/actual, response count, epoch and faults. The original stayed
unchanged. The negative fixture is deliberately failing and is not in the
passing totals.

## Supplementary acceptance coverage

`core_tests.c` uses fixed wire strings, known CRC answers, expected callbacks,
independent memory switches/LEDs and pre/post snapshots. Some larger scenarios
construct a valid COMMIT CRC with the separately tested core CRC helper; target
legality, physical model expectations and callback order are not computed by
copying the implementation algorithm. Fixed vectors independently freeze both
operation and shadow CRCs. Assertion counts include repeated per-frame and
capacity checks; they are not counts of distinct scenarios.

1. id=41/id=42 fixed CRCs, actual ordered OFF(0,3,2), OFF(1,1,2), ON(0,3,3);
   initial BEGIN/ADD and successful replay have zero switch callbacks. Replay
   ignores new CRC contents after generic numeric/shape validation. Core shadow
   agrees with independent memory hardware after every ordinary test request.
2. Wrong CRC/frame count/declared count/base epoch, illegal break/make, duplicate
   breaks/makes, same-chip X and shared cross-chip Y conflicts: no hardware
   callbacks, no live/LED/epoch mutation. Base mismatch after BEGIN is exercised
   by explicitly perturbing the test instance's epoch. Legitimate same-tag
   sharing and break/remake tag changes succeed. Reserved bits/tags stay zero.
3. Empty COMMIT increments epoch; exactly 256 accepted single-operation ADD
   frames retain exact duplicate comparison and reject operation 257. A valid
   128-break/128-make commit executes 256 callbacks in phase order. Four-result
   mixed cache, eviction, repeated ABORT reorder, ABORT of another id while OPEN,
   HELLO abort/preservation, SAFE history replay and repeated ALLOPEN are checked.
4. Commit interruption starts from existing closed points and calls all-open
   without a make. Invalid CRC and safety failure preserve the injection until
   a legal COMMIT consumes it. ABORTED result, epoch, SAFE and f=2 are checked.
5. Virtual lease boundary before/exact expiry, read/query/replay refresh,
   STAGING renewal, ERR/bad-frame/JF1 non-refresh, lease=0, idle poll with no
   emission, nonrepeating SAFE expiry and request-before-validation expiry.
   CLEAR does not arm; repeated active HW fault increments epoch; source
   disappearance only removes the source; source survives reboot.
6. Exact lowercase eight-digit boot, same-boot stability, supplied fresh reboot
   values, exact uptime 0/37/125 at known virtual times, iwdg/sw causes, cleared
   history/session/LEDs/partial RX/core TX and external captured-response queue.
7. Fragmented and NUL input, no reply before LF, multiple complete lines,
   exactly 512/513 bytes, 4096-byte discard and resynchronization, embedded valid
   suffix suppression, old-command routing after 3000 spaces, direct-entry
   missing/internal LF errors, JF1 CRLF acceptance/JF2 rejection. Another 512
   deterministic binary raw frames check exactly one error and atomic state.
8. SAFE permissions, STAGING reads/renewal versus LED/SELFTEST BUSY, unknown
   subcommands, uint32 limits, numeric-looking tokens at nonargument positions,
   nonce/CRC exemptions, arity-before-numeric precedence, capacity-before-BUSY,
   malformed-operation-before-sequence precedence. LED count/RGB/duplicate
   validation is atomic; raw values above brightness_cap are retained.
9. Runner checks mirror the format boundaries of Python `test_vectors.py` and
   `test_jf2_directives.py`: physical lines, CRLF/EOF, comments/blanks, exact
   prefixes/payload spaces, missing/orphan/extra responses, directive placement
   and whitelist, every escape category, nonrecursive decoding, decoded LF
   rejection, NUL/control token lengths, all wildcard positive/negative forms,
   escaped wildcard, empty/non-ASCII expected payload, long-line resource
   failure, advance beyond uint32 and explicit uint64 parse/add overflow.
   Incorrect expected output returns failure; the separate CLI check above
   verifies process exit 1. Capture queues reject extra responses and overflow.

## Sanitizers

This strict GCC sanitizer build completed without diagnostics:

```sh
gcc -std=c11 -O1 -g -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -Ifirmware/include -Ifirmware/host_test \
  firmware/src/jf2_protocol.c firmware/src/jf2_state.c firmware/src/jf2_crc.c \
  firmware/host_test/memory_hw.c firmware/host_test/vector_runner.c \
  firmware/host_test/core_tests.c -fsanitize=address,undefined \
  -o firmware/build/jf2_host_test_san
ASAN_OPTIONS=detect_leaks=0 ./firmware/build/jf2_host_test_san --self-test
ASAN_OPTIONS=detect_leaks=0 ./firmware/build/jf2_host_test_san protocol/vectors/*.vec
```

Both executions exited 0: **6400 core + 97 runner assertions; 272 vector pairs,
9 directives; zero failures and zero ASan/UBSan diagnostics**. The first run
without `ASAN_OPTIONS` exited 1 with the LeakSanitizer fatal error:
`LeakSanitizer does not work under ptrace`. Leak detection is therefore
**not verified**, and that failed initial invocation is not counted as PASS.
Address and undefined-behavior instrumentation remained enabled in the final
runs. No custom signal handling or sanitizer suppressions were installed.

## Storage and stack evidence

Measured on the host ABI above, not a Cortex-M3 ABI/link:

| Storage | Bytes |
| --- | ---: |
| Entire caller-owned Jf2Device | 3184 |
| Jf2Transaction (included) | 1560 |
| 256 four-byte operations (included) | 1024 |
| 257 uint16 frame boundaries (included) | 514 |
| Four Jf2Result entries (included) | 96 |
| Live + target shadow (included) | 320 |
| RX + TX including local NUL slots (included) | 1026 |
| Raw LED bytes (included) | 48 |
| Host-only MemoryHw, per fixture | 102688 |

One device is sufficient for integration. All core mutable storage is in the
device; the core has no additional writable static buffers and no dynamic
allocation, VLA, recursion, stack token-pointer arrays or stack device copies.
The host executable has two separate static device/backend test fixtures,
three 65537-byte vector buffers and a 4096-byte fixture path; these large host
test resources are **not MCU core RAM**. Host limits: 65536 bytes per physical
vector line, 8192 callback-log entries and 8 captured responses. Exceeding one
fails explicitly, never silently truncates a request or passes a vector.

GNU `size` output for the final builds:

```text
   text    data     bss     dec     hex
  68888    1624  412976  483488   760a0  firmware/build/jf2_host_test
 372038  208192  413376  993606   f2946  firmware/build/jf2_host_test_san
   7203      32       0    7235    1c43  firmware/build/jf2_protocol.o
   9948       0       0    9948    26dc  firmware/build/jf2_state.o
    480       0       0     480     1e0  firmware/build/jf2_crc.o
```

Core objects were independently compiled with the same strict `-O2` flags plus
`-fstack-usage` (no sanitizer), with `.su` files left under ignored `build/`.
Core-object totals are text=17631, data=32, bss=0. The 32 bytes are the host's
const routing-name pointer table (relocatable read-only data), not a writable
protocol-state array. These object totals exclude libc and are not an MCU
Flash link size.

The largest conservative nested core frame sum from those GCC `.su` files is
`feed(80) → process_request(160) → dispatch(176) → uint(48) → error(16)
→ append(240)` = **720 bytes**, excluding libc (`vsnprintf`/`snprintf`/string functions),
hardware/emit callbacks, callers, interrupt stack, ABI changes and link-time
optimization. The CRC operations helper is 96 bytes; reboot is 64 bytes.
`dynamic,bounded` entries reflect compiler-bounded outgoing arguments, not
source VLAs. This is a host compiler call-path estimate, **not a measured
maximum STM32 stack**. Target libc stack and Flash cost, especially formatting,
need a Cortex-M3 map/stack analysis and stack watermark exercise.

For planning against the nominal F103C8T6 **20 KiB RAM / 64 KiB Flash** budget,
using the measured host instance as an estimate leaves 17296 bytes of RAM.
Provisionally reserving another 1024 bytes for UART rings and 2048 for stack
leaves 14224 bytes for future drivers/application storage. Those reserves are
planning numbers, not verified target requirements. No measured RAM
incompatibility or reduced protocol capacity was found. Flash fit and actual
RAM/stack margin cannot be certified without the target compiler, libc,
startup/linker script and future drivers. Do not substitute host executable
size for a Cortex-M3 link result.

## Remaining boundaries

- No known failing fixed vector or supplementary assertion remains; these
  checks are not an exhaustive proof of all byte sequences or histories.
- Make execution, clang compilation, LeakSanitizer, Cortex-M3 build/link,
  STM32 board operation and CH446Q/WS2812/USART/USB-UART physical behavior are
  unverified. No drivers, electrical interval, sensing, protection or continuity
  measurements were implemented. `SELFTEST 0` only checks logical availability.
- `now_ms` must be monotonic within uint64; the harness reports clock resource
  exhaustion before parse/add overflow. Epoch/uptime use uint64; id/seq/base/
  counts retain uint32 request validation. Epoch exhaustion at UINT64_MAX has
  an explicit assertion (enabled in all tested builds), because no normative
  wire error exists for it; reboot before that lifetime limit. Builds disabling
  assertions must preserve this caller precondition. This finite-machine bound
  was not exercised through 2^64 real transactions.
- Caller supplies fresh boot values and owns external capture/TX queues.
  Reboot clears core buffers; the integration must also clear its external
  queue, as the host wrapper does. Emit consumers must copy within the callback.
- The only executable backend supplied is independent synchronous memory.
  Future real drivers need defined and tested failure/completion semantics;
  no-op stubs cannot establish physical success. Passive unplug detection is
  unavailable with lease=0. Tags do not detect dishonest labels or external
  wiring hazards.
