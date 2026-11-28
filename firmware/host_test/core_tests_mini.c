#include "memory_hw.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

/* JumperFree mini profile acceptance tests (build with -DJF2_PROFILE_MINI).
 * Topology: chip 0..5 = U301..U306, x 0..15 = BUS0..15 shared by all chips,
 * y 0..7 = one clip per chip; chip 6 = U307 service chip, y 6/7 only.
 * Expected strings and shadow bytes are written out by hand or rebuilt from
 * an independent bitmap, never copied from the core shadow. */
#ifndef JF2_PROFILE_MINI
#error "core_tests_mini.c needs -DJF2_PROFILE_MINI"
#endif

static Jf2Device dev;
static MemoryHw mem;
static size_t passed,failed;
#define CHECK(x) do { if (x) ++passed; else { ++failed; fprintf(stderr,"mini self-test %s:%d: %s\n",__func__,__LINE__,#x); } } while (0)

static void setup(void)
{
    memory_hw_init(&mem);
    Jf2Hw hw=memory_hw_binding(&mem);
    CHECK(jf2_init(&dev,&hw,memory_emit,&mem,UINT32_C(0x00ab12ef),JF2_RESET_POR));
}
static const char *send(const char *frame)
{
    memory_clear_responses(&mem);
    jf2_feed(&dev,(const uint8_t *)frame,strlen(frame));
    jf2_poll(&dev);
    CHECK(mem.response_count==1 && !mem.response_overflow);
    CHECK(!mem.log_overflow && memory_matches(&mem,&dev));
    return (const char *)mem.responses[0];
}
static void exact(const char *frame, const char *expected)
{
    const char *actual=send(frame);
    if (strcmp(actual,expected)) fprintf(stderr,"  request: %s  expected: %s  actual: %s",frame,expected,actual);
    CHECK(!strcmp(actual,expected));
    CHECK(mem.response_len[0]==strlen(expected));
}
static void contains(const char *frame, const char *fragment)
{
    const char *actual=send(frame);
    if (!strstr(actual,fragment)) fprintf(stderr,"  request: %s  wanted fragment: %s\n  actual: %s",frame,fragment,actual);
    CHECK(strstr(actual,fragment)!=NULL);
}
static void hello(void) { contains("JF2 HELLO 2 aBc0\n","state=ARMED reset=por nonce=aBc0\n"); }
static void begin(uint32_t id, uint32_t count)
{
    char frame[96];
    (void)snprintf(frame,sizeof frame,"JF2 TXN BEGIN %" PRIu32 " %" PRIu64 " %" PRIu32 "\n",id,dev.epoch,count);
    contains(frame,"JF2 OK TXN BEGIN");
}
static const char *add(uint32_t id, uint32_t seq, const char *ops)
{
    char frame[512];
    (void)snprintf(frame,sizeof frame,"JF2 TXN ADD %" PRIu32 " %" PRIu32 " %s\n",id,seq,ops);
    return send(frame);
}
static const char *commit(uint32_t id)
{
    char frame[128];
    (void)snprintf(frame,sizeof frame,"JF2 TXN COMMIT %" PRIu32 " %u %08" PRIx32 "\n",id,dev.txn.frames,jf2_operations_crc(dev.txn.ops,dev.txn.count));
    return send(frame);
}
static void abort_txn(uint32_t id)
{
    char frame[64];
    (void)snprintf(frame,sizeof frame,"JF2 TXN ABORT %" PRIu32 "\n",id);
    contains(frame,"JF2 OK TXN ABORT");
}
/* One transaction with one ADD frame; returns the COMMIT response. */
static const char *apply(uint32_t id, uint32_t count, const char *ops)
{
    begin(id,count);
    const char *r=add(id,0,ops);
    CHECK(strstr(r,"JF2 OK TXN ADD")!=NULL);
    return commit(id);
}

static void profile_info(void)
{
    setup();
    CHECK(JF2_CHIPS==7 && JF2_XN==16 && JF2_YN==8 && JF2_CLIPS==48);
    CHECK(sizeof(Jf2Shadow)==7u*(16u+64u));
    CHECK(sizeof dev.leds==48u*3u);
    /* reboot: all open, then every one of the 48 LEDs off */
    CHECK(mem.log_count==49 && mem.log[0].kind==MEMORY_ALL_OPEN);
    for (unsigned i=1; i<49; ++i) CHECK(mem.log[i].kind==MEMORY_LED && mem.log[i].clip==i-1);
    exact("JF2 INFO\n","JF2 OK INFO fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=7 board=mini boot=00ab12ef epoch=0 state=SAFE reset=por brightness_cap=64 lease_default_ms=0 max_ops=256\n");
    exact("JF2 HELLO 2 aBc0\n","JF2 OK HELLO fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=7 board=mini boot=00ab12ef epoch=0 state=ARMED reset=por nonce=aBc0\n");
}

static void op_parsing(void)
{
    setup(); hello();
    /* full range: chip 0..6, x 0..15 (one or two digits), y 0..7, tag 1..15 */
    const char *good[]={"+0:0:0@1","+5:15:7@15","+0:10:3@9","+4:9:2@10","+6:0:6@1","+6:15:7@2"};
    for (unsigned i=0; i<sizeof good/sizeof good[0]; ++i) {
        uint32_t id=100+i;
        CHECK(strstr(apply(id,1,good[i]),"JF2 OK TXN COMMIT")!=NULL);
        contains("JF2 SW ALLOPEN\n","JF2 OK SW ALLOPEN");
        hello();
    }
    /* break with a two-digit X */
    CHECK(strstr(apply(110,1,"+3:12:4@7"),"OK TXN COMMIT")!=NULL);
    CHECK(mem.points[3][12][4] && jf2_shadow_tag(&dev.live,3,12*8+4)==7);
    CHECK(strstr(apply(111,1,"-3:12:4"),"OK TXN COMMIT")!=NULL);
    CHECK(!mem.points[3][12][4] && jf2_shadow_tag(&dev.live,3,12*8+4)==0);
    /* rejected at ADD: range, non-canonical decimal, malformed tag */
    const char *bad[]={
        "+7:0:0@1","+0:16:0@1","+0:0:8@1","+0:0:0@16","+0:0:0@0","+00:0:0@1","+0:01:0@1",
        "+0:0:01@1","+0:0:0@01","+0:0:0","+0:0:0@","+:0:0@1","+0::0@1","+0:0:0@1x",
        "-0:0:0@1","-0:0","*0:0:0@1","+99999999999:0:0@1","+0:0:0@99999999999","+-1:0:0@1"};
    begin(120,1);
    for (unsigned i=0; i<sizeof bad/sizeof bad[0]; ++i) {
        const char *r=add(120,0,bad[i]);
        if (!strstr(r,"ERR BAD_ARG invalid switch operation")) fprintf(stderr,"  op %s accepted: %s",bad[i],r);
        CHECK(strstr(r,"ERR BAD_ARG invalid switch operation")!=NULL);
    }
    abort_txn(120);
}

static void service_chip_limits(void)
{
    setup(); hello();
    /* U307: only y 6 (TEST_SRC) and y 7 (SVC_GND), on any BUS */
    begin(1,1);
    for (unsigned y=0; y<6; ++y) {
        char op[32];
        (void)snprintf(op,sizeof op,"+6:3:%u@1",y);
        CHECK(strstr(add(1,0,op),"ERR BAD_ARG invalid switch operation")!=NULL);
        (void)snprintf(op,sizeof op,"-6:3:%u",y);
        CHECK(strstr(add(1,0,op),"ERR BAD_ARG invalid switch operation")!=NULL);
    }
    abort_txn(1);
    CHECK(strstr(apply(2,2,"+6:3:6@1 +6:4:7@2"),"OK TXN COMMIT")!=NULL);
    CHECK(mem.points[6][3][6] && mem.points[6][4][7]);
    CHECK(strstr(apply(3,1,"-6:4:7"),"OK TXN COMMIT")!=NULL);
    CHECK(mem.points[6][3][6] && !mem.points[6][4][7]);
    /* chips 0..5 keep all eight Y */
    CHECK(strstr(apply(4,2,"+5:0:0@3 +5:1:5@4"),"OK TXN COMMIT")!=NULL);
}

static void conflict_rules(void)
{
    setup(); hello();
    /* X = BUS is shared by every chip: different tags on one BUS short */
    const char *shorts[]={
        "+0:3:0@1 +1:3:0@2",  /* BUS3 on U301 and U302 */
        "+0:5:2@3 +6:5:6@4",  /* BUS5 to a clip and to TEST_SRC */
        "+0:3:0@1 +0:4:0@2",  /* one clip (U301 y0) on two BUS lines */
        "+6:7:7@1 +6:8:7@2"}; /* SVC_GND on two BUS lines */
    for (unsigned i=0; i<sizeof shorts/sizeof shorts[0]; ++i) {
        uint32_t id=10+i;
        const char *r=apply(id,2,shorts[i]);
        if (!strstr(r,"ERR SAFETY_SHORT")) fprintf(stderr,"  ops %s: %s",shorts[i],r);
        CHECK(strstr(r,"ERR SAFETY_SHORT line has different tags")!=NULL);
        CHECK(dev.epoch==0 && mem.log_count==49);
        abort_txn(id);
    }
    /* Y is private to its chip: equal y on other chips with other tags is fine
     * (this pair is a short on v01, where Y is the common channel). */
    CHECK(strstr(apply(20,2,"+0:3:0@1 +1:4:0@2"),"OK TXN COMMIT 20 epoch=1")!=NULL);
    CHECK(mem.points[0][3][0] && mem.points[1][4][0]);
    /* equal tags may share a BUS across chips and the service chip */
    CHECK(strstr(apply(21,3,"+2:3:5@1 +5:3:7@1 +6:3:6@1"),"OK TXN COMMIT 21 epoch=2")!=NULL);
    CHECK(mem.points[2][3][5] && mem.points[5][3][7] && mem.points[6][3][6]);
    /* the new point conflicts with live state (BUS3 = tag 1) */
    const char *r=apply(22,1,"+3:3:0@2");
    CHECK(strstr(r,"ERR SAFETY_SHORT")!=NULL && dev.epoch==2);
    abort_txn(22);
    /* one clip may join several BUS lines when the tag is equal */
    CHECK(strstr(apply(23,1,"+0:9:0@1"),"OK TXN COMMIT 23 epoch=3")!=NULL);
}

static void hex_bytes(char *out, const uint8_t *bytes, size_t n)
{
    for (size_t i=0; i<n; ++i) (void)sprintf(out+2*i,"%02x",bytes[i]);
}

static void status_tags_format(void)
{
    setup(); hello();
    exact("JF2 SW STATUS\n","JF2 OK SW STATUS epoch=0 crc=32c5f669"
          " c0=00000000000000000000000000000000 c1=00000000000000000000000000000000"
          " c2=00000000000000000000000000000000 c3=00000000000000000000000000000000"
          " c4=00000000000000000000000000000000 c5=00000000000000000000000000000000"
          " c6=00000000000000000000000000000000 state=ARMED\n");
    CHECK(strstr(apply(1,4,"+0:3:0@1 +1:3:0@1 +5:15:7@9 +6:2:6@4"),"OK TXN COMMIT 1 epoch=1")!=NULL);
    /* independent bitmap: bit = x*8+y, per chip 16 bytes, then 64 tag bytes */
    uint8_t map[7][16]={{0}}, tags[7][64]={{0}};
    map[0][3]=0x01; map[1][3]=0x01;   /* bit 24 */
    map[5][15]=0x80;                  /* bit 127 */
    map[6][2]=0x40;                   /* bit 22 */
    tags[0][12]=0x01; tags[1][12]=0x01; /* bit 24: even, low nibble */
    tags[5][63]=0x90;                 /* bit 127: odd, high nibble */
    tags[6][11]=0x04;                 /* bit 22: even, low nibble */
    uint8_t shadow[7*80];
    memcpy(shadow,map,sizeof map); memcpy(shadow+sizeof map,tags,sizeof tags);
    CHECK(!memcmp(shadow,dev.live.bytes,sizeof shadow));

    char expected[1100], *p=expected;
    p+=sprintf(p,"JF2 OK SW STATUS epoch=1 crc=%08" PRIx32,jf2_crc32(shadow,sizeof shadow,0));
    for (unsigned c=0; c<7; ++c) { p+=sprintf(p," c%u=",c); hex_bytes(p,map[c],16); p+=32; }
    (void)sprintf(p," state=ARMED\n");
    exact("JF2 SW STATUS\n",expected);
    CHECK(strstr(expected," c5=00000000000000000000000000000080 c6=00004000000000000000000000000000 ")!=NULL);

    p=expected;
    p+=sprintf(p,"JF2 OK SW TAGS");
    for (unsigned c=0; c<7; ++c) { p+=sprintf(p," t%u=",c); hex_bytes(p,tags[c],64); p+=128; }
    (void)sprintf(p,"\n");
    CHECK(strlen(expected)==939); /* longer than JF2_FRAME_MAX, fits JF2_TX_MAX */
    exact("JF2 SW TAGS\n",expected);
    CHECK(dev.result_count==1 && dev.results[0].crc==jf2_crc32(shadow,sizeof shadow,0));
}

static void leds_48(void)
{
    setup(); hello();
    exact("JF2 LED BATCH 3 0:ffffff 31:102030 47:aBcDeF\n","JF2 OK LED BATCH 3\n");
    CHECK(mem.leds[0][0]==255 && mem.leds[31][2]==0x30);
    CHECK(mem.leds[47][0]==0xab && mem.leds[47][1]==0xcd && mem.leds[47][2]==0xef);
    /* clips 32..47 need the 64-bit duplicate mask (8 and 40 differ) */
    exact("JF2 LED BATCH 2 8:010101 40:020202\n","JF2 OK LED BATCH 2\n");
    CHECK(mem.leds[8][0]==1 && mem.leds[40][0]==2);
    const char *bad[]={"JF2 LED BATCH 1 48:010203\n","JF2 LED BATCH 49\n",
                       "JF2 LED BATCH 2 40:000000 40:ffffff\n","JF2 LED BATCH 1 99:010203\n",
                       "JF2 LED BATCH 1 047:010203\n"};
    for (unsigned i=0; i<sizeof bad/sizeof bad[0]; ++i) contains(bad[i],"ERR BAD_ARG");
    CHECK(mem.leds[40][0]==2 && mem.leds[47][0]==0xab);
    /* a batch of all 48 clips is accepted */
    char frame[512], *p=frame;
    p+=sprintf(p,"JF2 LED BATCH 48");
    for (unsigned c=0; c<48; ++c) p+=sprintf(p," %u:%02x0000",c,c);
    (void)sprintf(p,"\n");
    exact(frame,"JF2 OK LED BATCH 48\n");
    for (unsigned c=0; c<48; ++c) CHECK(mem.leds[c][0]==c && mem.leds[c][1]==0);
    /* the JF1 legacy path stays at clips 0..15 */
    contains("JF1 SET 16 1 2 3\n","JF1 ERR CLIP_OUT_OF_RANGE");
}

/* Fake measurement front end: clip c reads 1000+c mV on BUS 15 (temporary);
 * clips 40..47 report "no free BUS". Records the last request. */
static unsigned meas_calls,meas_clip,meas_window;
static bool fake_measure(void *ctx, uint8_t clip, uint16_t window, Jf2Meas *m)
{
    (void)ctx; ++meas_calls; meas_clip=clip; meas_window=window;
    if (clip>=40) return false;
    m->mv=1000+clip; m->min_mv=-3300; m->max_mv=5000; m->samples=window ? 7u : 1u;
    m->bus=15; m->temp=true;
    return true;
}

static void measurement(void)
{
    setup();
    Jf2Hw hw=memory_hw_binding(&mem);
    hw.measure=fake_measure;
    CHECK(jf2_init(&dev,&hw,memory_emit,&mem,UINT32_C(0x00ab12ef),JF2_RESET_POR));
    meas_calls=0;
    contains("JF2 INFO\n","caps=led,sw,txn,lease,meas chips=7 board=mini");
    exact("JF2 MEAS 3 0\n","JF2 ERR SAFE_MODE HELLO required without faults\n");
    CHECK(meas_calls==0);
    hello();
    exact("JF2 MEAS 3 0\n","JF2 OK MEAS 3 mv=1003 min=-3300 max=5000 n=1 bus=15 temp=1\n");
    CHECK(meas_calls==1 && meas_clip==3 && meas_window==0);
    exact("JF2 MEAS 47 1000\n","JF2 ERR BUSY no free BUS\n");
    CHECK(meas_clip==47 && meas_window==1000);
    exact("JF2 MEAS 48 0\n","JF2 ERR BAD_ARG invalid clip or window\n");
    exact("JF2 MEAS 0 1001\n","JF2 ERR BAD_ARG invalid clip or window\n");
    exact("JF2 MEAS 0\n","JF2 ERR BAD_FRAME incorrect argument count\n");
    exact("JF2 MEAS 01 0\n","JF2 ERR BAD_FRAME expected canonical unsigned integer\n");
    CHECK(meas_calls==2);
    begin(5,1);
    exact("JF2 MEAS 3 0\n","JF2 ERR BUSY transaction open\n");
    abort_txn(5);
    CHECK(meas_calls==2);
    /* the shadow is not touched: epoch and CRC stay those of the empty board */
    contains("JF2 SW STATUS\n","JF2 OK SW STATUS epoch=0 crc=32c5f669");

    /* without the hook (v01 boards, plain memory backend) MEAS is unsupported */
    setup(); hello();
    exact("JF2 MEAS 3 0\n","JF2 ERR UNSUPPORTED no sensing hardware\n");
    contains("JF2 INFO\n","caps=led,sw,txn,lease chips=7");
}

int core_self_tests(void)
{
    passed=failed=0;
    profile_info(); op_parsing(); service_chip_limits(); conflict_rules();
    status_tags_format(); leds_48(); measurement();
    printf("mini core self-tests: %zu passed, %zu failed\n",passed,failed);
    printf("sizeof: Jf2Device=%zu Jf2Shadow=%zu MemoryHw=%zu (host only)\n",
           sizeof(Jf2Device),sizeof(Jf2Shadow),sizeof(MemoryHw));
    return failed ? 1 : 0;
}
