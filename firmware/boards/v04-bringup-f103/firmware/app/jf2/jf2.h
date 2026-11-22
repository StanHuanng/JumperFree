#ifndef JF2_H
#define JF2_H
#include "jf2_state.h"

/* Caller allocates the entire instance. Serial, non-reentrant calls only.
 * emit copies/consumes len bytes synchronously; its buffer expires on return.
 * now_ms is monotonic uint64 milliseconds. poll emits nothing. */
bool jf2_init(Jf2Device *, const Jf2Hw *, Jf2Emit, void *emit_ctx,
              uint32_t boot, Jf2ResetCause);
void jf2_feed(Jf2Device *, const uint8_t *, size_t);
/* Complete raw frame including LF. Invalid/missing LF still emits one error.
 * Do not submit the same frame to both entry points. */
void jf2_process_request(Jf2Device *, const uint8_t *, size_t);
void jf2_poll(Jf2Device *);
void jf2_fault_hw(Jf2Device *, bool active);
void jf2_interrupt_next_commit(Jf2Device *);
/* Caller supplies a fresh boot. Any external captured TX queue is owned and
 * cleared by the caller; core RX/TX storage is cleared here. */
void jf2_reboot(Jf2Device *, uint32_t new_boot, Jf2ResetCause);
#endif
