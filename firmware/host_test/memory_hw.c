#include "memory_hw.h"
#include <assert.h>
#include <string.h>

static void record(MemoryHw *m, MemoryEvent e)
{
    if (m->log_count==MEMORY_LOG_CAPACITY) { m->log_overflow=true; return; }
    m->log[m->log_count++]=e;
}
static void crosspoint(void *ctx, uint8_t chip, uint8_t x, uint8_t y, bool on)
{
    MemoryHw *m=ctx;
    assert(chip<2 && x<8 && y<8);
    m->points[chip][x][y]=on;
    MemoryEvent e={0}; e.kind=on ? MEMORY_ON : MEMORY_OFF; e.chip=chip; e.x=x; e.y=y;
    record(m,e);
}
static void all_open(void *ctx)
{
    MemoryHw *m=ctx;
    memset(m->points,0,sizeof m->points);
    MemoryEvent e={0}; e.kind=MEMORY_ALL_OPEN; record(m,e);
}
static void led(void *ctx, uint8_t clip, uint8_t r, uint8_t g, uint8_t b)
{
    MemoryHw *m=ctx;
    assert(clip<16);
    m->leds[clip][0]=r; m->leds[clip][1]=g; m->leds[clip][2]=b;
    MemoryEvent e={0}; e.kind=MEMORY_LED; e.clip=clip; e.r=r; e.g=g; e.b=b;
    record(m,e);
}
static uint64_t now_ms(void *ctx) { return ((MemoryHw *)ctx)->now; }
void memory_hw_init(MemoryHw *m) { memset(m,0,sizeof *m); }
Jf2Hw memory_hw_binding(MemoryHw *m)
{ return (Jf2Hw){m,crosspoint,all_open,led,now_ms}; }
void memory_emit(void *ctx, const uint8_t *bytes, size_t len)
{
    MemoryHw *m=ctx;
    if (m->response_count==MEMORY_CAPTURE_CAPACITY || len>JF2_FRAME_MAX) {
        m->response_overflow=true; return;
    }
    size_t i=m->response_count++;
    memcpy(m->responses[i],bytes,len);
    m->responses[i][len]=0;
    m->response_len[i]=len;
}
void memory_clear_log(MemoryHw *m) { m->log_count=0; m->log_overflow=false; }
void memory_clear_responses(MemoryHw *m)
{
    memset(m->responses,0,sizeof m->responses);
    memset(m->response_len,0,sizeof m->response_len);
    m->response_count=0; m->response_overflow=false;
}
bool memory_advance(MemoryHw *m, Jf2Device *d, uint64_t amount)
{
    if (UINT64_MAX-m->now<amount) return false;
    m->now+=amount; jf2_poll(d); return true;
}
void memory_reboot(MemoryHw *m, Jf2Device *d, uint32_t boot, Jf2ResetCause cause)
{
    memory_clear_responses(m);
    jf2_reboot(d,boot,cause);
}
bool memory_matches(const MemoryHw *m, const Jf2Device *d)
{
    for (unsigned c=0; c<2; ++c) for (unsigned x=0; x<8; ++x) for (unsigned y=0; y<8; ++y) {
        unsigned bit=x*8+y;
        bool on=(d->live.bytes[c*16+bit/8] & (1u << (bit%8)))!=0;
        if (m->points[c][x][y]!=on) return false;
    }
    return !memcmp(m->leds,d->leds,sizeof m->leds);
}
