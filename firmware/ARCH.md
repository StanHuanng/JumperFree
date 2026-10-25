# Architecture and integration boundary

`bytes → routing/framing/uint validation → state/target validation → BBM callbacks
→ bounded response → emit`. `jf2_protocol.c` owns LF assembly, legacy JF1 routing,
strict raw-byte checks, JF2 prevalidation order and exact wire encoding.
`jf2_state.c` owns sessions, transactions, results, LEDs, leases/faults and
complete-target safety checks. `jf2_crc.c` implements incremental reflected
CRC32 compatible with zlib, without linking zlib.

The caller statically owns each complete `Jf2Device`; callbacks and contexts
outlive it. There are no core mutable globals, background tasks or allocations.
One serialized caller feeds bytes and polls a monotonic millisecond clock;
callbacks must not reenter the device. Request processing and memory callbacks
finish synchronously, so COMMITTING is never externally observable. `poll`
emits nothing. Each complete input produces exactly one length-bearing LF
response. The raw-frame entry also rejects missing LF, embedded LF and oversize
input. Streaming oversize input keeps at most 512 bytes, retains three routing
bytes, and discards through LF before replying once; a valid-looking suffix
cannot execute. RX/TX each have 512 bytes plus a local NUL slot. Tokens use
NUL-separated validated RX storage and bounded scans, without a pointer array.

Live and target shadows each use the normative 160-byte packed representation.
Target operations apply every break, then every make, preserving order within
each phase. Complete target validation checks both chips' X ownership and
shared Y ownership before the first hardware callback. Canonical operations
use four bytes each; 257 uint16 boundaries preserve up to 256 accepted ADD
frames. Duplicate comparison checks all operation fields and boundaries, not
CRC alone. Regenerating a canonical token is lossless. One OPEN transaction
is separate from the four oldest-to-newest COMMITTED/ABORTED results. COMMIT
replay uses stored result metadata, with no execution or epoch increment.

ALLOPEN, faults and expiry call the backend's two-chip all-open and abort OPEN;
LEDs survive these events. Initialization/reboot additionally clear all 16 LEDs.
Reboot preserves bindings and the HW source; it clears core RX/TX and history.
An external response queue belongs to its consumer, so the harness clears it
with `memory_reboot`. `memory_hw` maintains its own switch/LED arrays and bounded
callback/capture logs. Host fixture and log limits fail explicitly; they do not
reduce the protocol's transaction capacities.

Future USART1 candidates are PA9 TX / PA10 RX, subject to final KiCad schematic.
Use a bounded ISR RX ring and main-loop `feed` + periodic `poll`; queue complete
TX responses. Do not parse or commit in an ISR. Overflow/recovery of that future
ring must be specified and tested. A future CH446Q adapter maps chip/x/y,
OFF/ON and two-chip all-open, with safe startup/fault behavior. Its completion,
failure and measured BBM timing need new validation; this synchronous memory
success contract does not establish real switching completion or a 1 ms gap.
CH340C is a planned bridge, not an implemented transport. WS2812, HAL, USB-UART,
electrical sensing, protection and board deployment remain outside this core.
