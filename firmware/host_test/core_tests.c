#include "memory_hw.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

/* Host fixtures deliberately live outside the MCU instance and outside the
 * core stack. Hardware bitmaps/logs are independent acceptance oracles. */
static Jf2Device dev;
static MemoryHw mem;
static size_t passed,failed;
#define CHECK(x) do { if (x) ++passed; else { ++failed; fprintf(stderr,"core self-test %s:%d: %s\n",__func__,__LINE__,#x); } } while (0)

static void setup(void)
{
    memory_hw_init(&mem);
    Jf2Hw hw=memory_hw_binding(&mem);
    CHECK(jf2_init(&dev,&hw,memory_emit,&mem,UINT32_C(0x00ab12ef),JF2_RESET_POR));
    memory_clear_log(&mem);
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
static void add(uint32_t id, uint32_t seq, const char *ops)
{
    char frame[512];
    (void)snprintf(frame,sizeof frame,"JF2 TXN ADD %" PRIu32 " %" PRIu32 " %s\n",id,seq,ops);
    contains(frame,"JF2 OK TXN ADD");
}
/* Produces a valid request from stored canonical operations; fixed vector CRCs
 * and known CRC answers below test the CRC implementation independently. The
 * target, epoch and callback assertions never derive their expected state from
 * core validation or from core shadow. */
static const char *commit(uint32_t id)
{
    char frame[128];
    (void)snprintf(frame,sizeof frame,"JF2 TXN COMMIT %" PRIu32 " %u %08" PRIx32 "\n",id,dev.txn.frames,jf2_operations_crc(dev.txn.ops,dev.txn.count));
    return send(frame);
}
typedef struct {
    Jf2Shadow live;
    uint8_t leds[16][3];
    bool points[2][8][8];
    uint64_t epoch;
    size_t log_count;
} Snapshot;
static Snapshot before;
static void snapshot(void)
{
    before.live=dev.live; memcpy(before.leds,mem.leds,sizeof before.leds);
    memcpy(before.points,mem.points,sizeof before.points);
    before.epoch=dev.epoch; before.log_count=mem.log_count;
}
static void unchanged(void)
{
    CHECK(!memcmp(&before.live,&dev.live,sizeof dev.live));
    CHECK(!memcmp(before.leds,mem.leds,sizeof before.leds));
    CHECK(!memcmp(before.points,mem.points,sizeof before.points));
    CHECK(dev.epoch==before.epoch && mem.log_count==before.log_count);
    CHECK(memory_matches(&mem,&dev));
}

static void init_crc_info(void)
{
    setup();
    CHECK(jf2_crc32("123456789",9,0)==UINT32_C(0xcbf43926));
    CHECK(jf2_crc32("",0,0)==0);
    CHECK(jf2_crc32("56789",5,jf2_crc32("1234",4,0))==UINT32_C(0xcbf43926));
    CHECK(jf2_crc32(dev.live.bytes,160,0)==UINT32_C(0xaa075363));
    Jf2Op golden[]={{0,3,2,5},{1,1,2,5}};
    CHECK(sizeof(Jf2Op)==4);
    CHECK(jf2_operations_crc(golden,2)==UINT32_C(0x4f8aca33));
    CHECK(jf2_operations_crc(golden,0)==0);
    Jf2Hw valid=memory_hw_binding(&mem),bad=valid;
    CHECK(!jf2_init(NULL,&valid,memory_emit,&mem,0,JF2_RESET_POR));
    CHECK(!jf2_init(&dev,NULL,memory_emit,&mem,0,JF2_RESET_POR));
    CHECK(!jf2_init(&dev,&valid,NULL,&mem,0,JF2_RESET_POR));
    bad.set_crosspoint=NULL; CHECK(!jf2_init(&dev,&bad,memory_emit,&mem,0,JF2_RESET_POR));
    bad=valid; bad.all_open=NULL; CHECK(!jf2_init(&dev,&bad,memory_emit,&mem,0,JF2_RESET_POR));
    bad=valid; bad.set_led=NULL; CHECK(!jf2_init(&dev,&bad,memory_emit,&mem,0,JF2_RESET_POR));
    bad=valid; bad.now_ms=NULL; CHECK(!jf2_init(&dev,&bad,memory_emit,&mem,0,JF2_RESET_POR));
    CHECK(!jf2_init(&dev,&valid,memory_emit,&mem,0,(Jf2ResetCause)99));
    CHECK(jf2_init(&dev,&valid,memory_emit,&mem,UINT32_C(0x00ab12ef),JF2_RESET_POR));
    CHECK(mem.log_count==17 && mem.log[0].kind==MEMORY_ALL_OPEN);
    for (unsigned i=1; i<17; ++i) CHECK(mem.log[i].kind==MEMORY_LED && mem.log[i].clip==i-1);
    exact("JF2 INFO\n","JF2 OK INFO fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=2 board=v01 boot=00ab12ef epoch=0 state=SAFE reset=por brightness_cap=64 lease_default_ms=0 max_ops=256\n");
    exact("JF2 HELLO 2 aBc0\n","JF2 OK HELLO fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=2 board=v01 boot=00ab12ef epoch=0 state=ARMED reset=por nonce=aBc0\n");
    CHECK(memory_advance(&mem,&dev,125));
    exact("JF2 PING 4294967295\n","JF2 OK PING 4294967295 up=125\n");
    contains("JF2 INFO\n","boot=00ab12ef");
    CHECK(dev.boot==UINT32_C(0x00ab12ef));
}

static void bbm_and_history(void)
{
    setup(); hello();
    begin(41,2); add(41,0,"+0:3:2@5 +1:1:2@5");
    CHECK(mem.log_count==0);
    exact("JF2 TXN COMMIT 41 1 4f8aca33\n","JF2 OK TXN COMMIT 41 epoch=1 crc=577233cf opened=0 closed=2\n");
    CHECK(mem.points[0][3][2] && mem.points[1][1][2]);
    memory_clear_log(&mem);
    begin(42,3); add(42,0,"+0:3:3@6 -0:3:2 -1:1:2");
    CHECK(mem.log_count==0);
    exact("JF2 TXN COMMIT 42 1 1c3cc67c\n","JF2 OK TXN COMMIT 42 epoch=2 crc=81c2b26e opened=2 closed=1\n");
    CHECK(mem.log_count==3);
    CHECK(mem.log[0].kind==MEMORY_OFF && mem.log[0].chip==0 && mem.log[0].x==3 && mem.log[0].y==2);
    CHECK(mem.log[1].kind==MEMORY_OFF && mem.log[1].chip==1 && mem.log[1].x==1 && mem.log[1].y==2);
    CHECK(mem.log[2].kind==MEMORY_ON && mem.log[2].chip==0 && mem.log[2].x==3 && mem.log[2].y==3);
    CHECK(!mem.points[0][3][2] && !mem.points[1][1][2] && mem.points[0][3][3]);
    snapshot();
    exact("JF2 TXN COMMIT 42 4294967295 garbage\n","JF2 OK TXN COMMIT 42 epoch=2 crc=81c2b26e opened=2 closed=1 replayed=1\n"); unchanged();
    exact("JF2 TXN COMMIT 42 01 garbage\n","JF2 ERR BAD_FRAME expected canonical unsigned integer\n"); unchanged();
    begin(43,0); snapshot(); hello(); unchanged();
    exact("JF2 TXN QUERY 43\n","JF2 OK TXN QUERY 43 result=ABORTED\n");
    CHECK(dev.mode==JF2_ARMED && mem.points[0][3][3]);
    begin(44,0);
    exact("JF2 TXN COMMIT 44 0 00000000\n","JF2 OK TXN COMMIT 44 epoch=3 crc=81c2b26e opened=0 closed=0\n");
    CHECK(dev.epoch==3 && mem.log_count==3);
    contains("JF2 TXN ABORT 50\n","OK TXN ABORT 50");
    CHECK(dev.result_count==4 && dev.results[0].id==42 && dev.results[1].id==43 && dev.results[2].id==44 && dev.results[3].id==50);
    contains("JF2 TXN ABORT 43\n","OK TXN ABORT 43");
    CHECK(dev.result_count==4 && dev.results[0].id==42 && dev.results[1].id==44 && dev.results[2].id==50 && dev.results[3].id==43);
    contains("JF2 TXN ABORT 51\n","OK TXN ABORT 51");
    exact("JF2 TXN QUERY 42\n","JF2 OK TXN QUERY 42 result=UNKNOWN\n");
    contains("JF2 TXN QUERY 44\n","result=COMMITTED epoch=3 crc=81c2b26e opened=0 closed=0");
    begin(55,0); CHECK(strstr(commit(55),"OK TXN COMMIT")!=NULL);
    begin(52,0);
    contains("JF2 TXN ABORT 43\n","OK TXN ABORT 43"); CHECK(dev.txn.open && dev.txn.id==52);
    contains("JF2 TXN ABORT 55\n","ERR TXN_UNKNOWN transaction already committed"); CHECK(dev.txn.open);
    exact("JF2 SW ALLOPEN\n","JF2 OK SW ALLOPEN\n"); CHECK(dev.epoch==5 && dev.mode==JF2_SAFE);
    exact("JF2 TXN QUERY 52\n","JF2 OK TXN QUERY 52 result=ABORTED\n");
    snapshot(); contains("JF2 TXN ABORT 52\n","OK TXN ABORT 52"); unchanged();
    snapshot(); contains("JF2 TXN COMMIT 55 99 x\n","opened=0 closed=0 replayed=1"); unchanged();
    exact("JF2 SW ALLOPEN\n","JF2 OK SW ALLOPEN\n"); CHECK(dev.epoch==6);
    CHECK(mem.log[mem.log_count-1].kind==MEMORY_ALL_OPEN);
}

static void validation_atomicity(void)
{
    setup(); hello(); begin(1,1); add(1,0,"+0:0:0@1"); snapshot();
    exact("JF2 TXN COMMIT 1 1 00000000\n","JF2 ERR TXN_CRC frame count operation count or CRC differs\n"); unchanged();
    exact("JF2 TXN COMMIT 1 2 26a19df6\n","JF2 ERR TXN_CRC frame count operation count or CRC differs\n"); unchanged();
    exact("JF2 TXN COMMIT 1 1 zzzzzzzz\n","JF2 ERR BAD_ARG CRC requires eight hex digits\n"); unchanged();
    ++dev.epoch; snapshot();
    contains("JF2 TXN COMMIT 1 1 26a19df6\n","ERR EPOCH_MISMATCH base epoch differs"); unchanged();
    --dev.epoch;
    contains("JF2 TXN ABORT 1\n","OK");
    begin(2,0); add(2,0,"+0:0:0@1"); snapshot();
    CHECK(strstr(commit(2),"ERR TXN_CRC")!=NULL); unchanged();
    contains("JF2 TXN ABORT 2\n","OK");
    const char *bad_ops[]={"-0:0:0","+0:0:0@1 +0:0:0@1","+0:0:0@1 +0:0:1@2","+0:0:0@1 +1:0:0@2"};
    const char *errors[]={"ERR BAD_ARG","ERR BAD_ARG","ERR SAFETY_SHORT","ERR SAFETY_SHORT"};
    for (unsigned i=0; i<4; ++i) {
        uint32_t id=10+i; begin(id,i ? 2 : 1); add(id,0,bad_ops[i]); snapshot();
        CHECK(strstr(commit(id),errors[i])!=NULL); unchanged(); CHECK(dev.txn.open);
        char frame[64]; (void)snprintf(frame,sizeof frame,"JF2 TXN ABORT %" PRIu32 "\n",id); contains(frame,"OK");
    }
    begin(20,1); add(20,0,"+0:0:0@1"); CHECK(strstr(commit(20),"OK TXN COMMIT")!=NULL);
    begin(21,1); add(21,0,"+0:0:0@1"); snapshot(); CHECK(strstr(commit(21),"ERR BAD_ARG")!=NULL); unchanged();
    contains("JF2 TXN ABORT 21\n","OK");
    begin(22,2); add(22,0,"-0:0:0 -0:0:0"); snapshot(); CHECK(strstr(commit(22),"ERR BAD_ARG")!=NULL); unchanged();
    contains("JF2 TXN ABORT 22\n","OK");
    /* break then make the same point changes tag, even with make first input */
    begin(23,2); add(23,0,"+0:0:0@15 -0:0:0");
    memory_clear_log(&mem); CHECK(strstr(commit(23),"OK TXN COMMIT")!=NULL);
    CHECK(mem.log_count==2 && mem.log[0].kind==MEMORY_OFF && mem.log[1].kind==MEMORY_ON);
    CHECK(jf2_shadow_tag(&dev.live,0,0)==15);
    /* shared lines with equal tag are legal */
    begin(24,2); add(24,0,"+0:0:1@15 +1:1:0@15"); CHECK(strstr(commit(24),"OK TXN COMMIT")!=NULL);
    CHECK(mem.points[0][0][1] && mem.points[1][1][0]);
    for (unsigned c=0; c<2; ++c) {
        for (unsigned i=8; i<16; ++i) CHECK(dev.live.bytes[c*16+i]==0);
        for (unsigned i=64; i<128; ++i) CHECK(jf2_shadow_tag(&dev.live,c,i)==0);
    }
}

static void operation_capacity(void)
{
    setup(); hello(); begin(UINT32_MAX,256);
    for (unsigned i=0; i<256; ++i) add(UINT32_MAX,i,"+0:0:0@1");
    CHECK(dev.txn.count==256 && dev.txn.frames==256 && dev.txn.boundary[256]==256 && mem.log_count==0);
    snapshot();
    contains("JF2 TXN ADD 4294967295 255 +0:0:0@1\n","OK TXN ADD"); unchanged();
    contains("JF2 TXN ADD 4294967295 255 +0:0:0@2\n","ERR TXN_SEQ_CONFLICT"); unchanged();
    contains("JF2 TXN ADD 4294967295 256 +0:0:0@1\n","ERR TXN_TOO_LARGE"); unchanged();
    contains("JF2 TXN ADD 4294967295 4294967295 +0:0:0@1\n","ERR TXN_SEQ_GAP"); unchanged();
    contains("JF2 TXN ADD 4294967295 999 +0:0:0@0\n","ERR BAD_ARG invalid switch operation"); unchanged();
    CHECK(strstr(commit(UINT32_MAX),"ERR BAD_ARG")!=NULL); unchanged();
    contains("JF2 TXN ABORT 4294967295\n","OK");
    /* 128 distinct makes in one commit; then 128 breaks + 128 makes =256
     * legal operations in 256 accepted one-operation frames, same-tag safety. */
    begin(1,128);
    char op[32];
    for (unsigned i=0; i<128; ++i) {
        (void)snprintf(op,sizeof op,"+%u:%u:%u@1",i/64,(i%64)/8,i%8); add(1,i,op);
    }
    memory_clear_log(&mem); CHECK(strstr(commit(1),"opened=0 closed=128")!=NULL);
    CHECK(mem.log_count==128);
    begin(2,256);
    for (unsigned i=0; i<256; ++i) {
        unsigned p=i%128;
        (void)snprintf(op,sizeof op,i<128 ? "-%u:%u:%u" : "+%u:%u:%u@2",p/64,(p%64)/8,p%8); add(2,i,op);
    }
    memory_clear_log(&mem); CHECK(strstr(commit(2),"opened=128 closed=128")!=NULL);
    CHECK(mem.log_count==256 && dev.epoch==2);
    for (unsigned i=0; i<256; ++i) CHECK(mem.log[i].kind==(i<128 ? MEMORY_OFF : MEMORY_ON));
    CHECK(memory_matches(&mem,&dev));
}

static void interrupts(void)
{
    setup(); hello(); begin(1,1); add(1,0,"+0:0:0@1"); CHECK(strstr(commit(1),"OK")!=NULL);
    CHECK(mem.points[0][0][0]);
    begin(2,1); add(2,0,"+1:0:0@2"); jf2_interrupt_next_commit(&dev); snapshot();
    contains("JF2 TXN COMMIT 2 1 00000000\n","ERR TXN_CRC"); unchanged(); CHECK(dev.interrupt_commit);
    CHECK(strstr(commit(2),"ERR SAFETY_SHORT")!=NULL); unchanged(); CHECK(dev.interrupt_commit);
    contains("JF2 TXN ABORT 2\n","OK"); begin(3,1); add(3,0,"+0:0:1@1");
    memory_clear_log(&mem);
    CHECK(strstr(commit(3),"JF2 ERR COMMIT_INTERRUPTED commit interrupted all open f=2\n")!=NULL);
    CHECK(!dev.interrupt_commit && dev.mode==JF2_SAFE && dev.epoch==2 && dev.faults==2);
    CHECK(mem.log_count==1 && mem.log[0].kind==MEMORY_ALL_OPEN);
    CHECK(!mem.points[0][0][0] && !mem.points[0][0][1] && memory_matches(&mem,&dev));
    exact("JF2 TXN QUERY 3\n","JF2 OK TXN QUERY 3 result=ABORTED f=2\n");
    exact("JF2 FAULT CLEAR\n","JF2 OK FAULT CLEAR f=0\n"); CHECK(dev.mode==JF2_SAFE);
}

static void lease_fault_reboot(void)
{
    setup(); hello(); contains("JF2 LEASE 10\n","OK");
    CHECK(memory_advance(&mem,&dev,9)); exact("JF2 PING 1\n","JF2 OK PING 1 up=9\n"); CHECK(dev.lease_at==9);
    CHECK(memory_advance(&mem,&dev,9)); CHECK(dev.mode==JF2_ARMED);
    contains("JF2 TXN QUERY 999\n","result=UNKNOWN"); CHECK(dev.lease_at==18);
    CHECK(memory_advance(&mem,&dev,9)); begin(1,0); CHECK(dev.lease_at==27 && dev.mode==JF2_STAGING);
    CHECK(memory_advance(&mem,&dev,9)); contains("JF2 LEASE 10\n","OK"); CHECK(dev.lease_at==36);
    CHECK(memory_advance(&mem,&dev,9)); contains("JF2 SW TAGS\n","OK"); CHECK(dev.lease_at==45);
    CHECK(memory_advance(&mem,&dev,9)); CHECK(strstr(commit(1),"OK")!=NULL); CHECK(dev.lease_at==54);
    CHECK(memory_advance(&mem,&dev,9)); contains("JF2 TXN COMMIT 1 99 nope\n","replayed=1"); CHECK(dev.lease_at==63);
    CHECK(memory_advance(&mem,&dev,9));
    contains("JF2 NOTREAL\n","ERR"); CHECK(dev.lease_at==63);
    contains("JF2  INFO\n","ERR"); CHECK(dev.lease_at==63);
    contains("JF1 SET 0 255 200 100\n","JF1 OK"); CHECK(dev.lease_at==63);
    begin(2,0); /* successful BEGIN refreshes at72 */
    CHECK(memory_advance(&mem,&dev,9)); CHECK(dev.mode==JF2_STAGING);
    memory_clear_log(&mem); CHECK(memory_advance(&mem,&dev,1));
    CHECK(dev.mode==JF2_SAFE && dev.faults==1 && dev.epoch==2 && !dev.txn.open);
    CHECK(mem.log_count==1 && mem.log[0].kind==MEMORY_ALL_OPEN && mem.response_count==1);
    CHECK(mem.leds[0][0]==255); /* all-open never clears LEDs */
    exact("JF2 TXN QUERY 2\n","JF2 OK TXN QUERY 2 result=ABORTED f=1\n");
    uint64_t epoch=dev.epoch;
    CHECK(memory_advance(&mem,&dev,100)); jf2_poll(&dev); CHECK(dev.epoch==epoch);
    exact("JF2 FAULT CLEAR\n","JF2 OK FAULT CLEAR f=0\n");
    contains("JF2 LEASE 0\n","ERR SAFE_MODE"); CHECK(dev.mode==JF2_SAFE);
    hello(); contains("JF2 LEASE 0\n","OK"); CHECK(memory_advance(&mem,&dev,1000000)); CHECK(dev.mode==JF2_ARMED);
    begin(3,1); add(3,0,"+0:0:0@1"); CHECK(strstr(commit(3),"OK")!=NULL);
    begin(4,0); contains("JF2 LEASE 10\n","OK");
    /* Clock can advance outside harness polling; request must expire first,
     * even when that request is invalid and would otherwise be atomic. */
    mem.now+=10; memory_clear_log(&mem);
    contains("JF2 PING 01\n","ERR BAD_FRAME expected canonical unsigned integer f=1\n");
    CHECK(!mem.points[0][0][0] && mem.log_count==1 && dev.mode==JF2_SAFE && dev.epoch==epoch+2);
    contains("JF2 TXN QUERY 4\n","result=ABORTED f=1");
    jf2_fault_hw(&dev,true); CHECK(dev.faults==5); uint64_t after=dev.epoch;
    jf2_fault_hw(&dev,true); CHECK(dev.epoch==after+1 && dev.faults==5);
    exact("JF2 FAULT CLEAR\n","JF2 OK FAULT CLEAR f=4\n");
    contains("JF2 HELLO 2 A\n","state=SAFE reset=por nonce=A f=4");
    contains("JF1 SET 1 255 254 253\n","OK");
    jf2_feed(&dev,(const uint8_t *)"JF2 PING ",9); CHECK(dev.rx_len==9);
    uint32_t old_boot=dev.boot;
    memory_reboot(&mem,&dev,UINT32_C(0xdeadbeef),JF2_RESET_IWDG);
    CHECK(dev.boot!=old_boot && dev.boot==UINT32_C(0xdeadbeef) && dev.hw_source && dev.faults==36);
    CHECK(dev.epoch==0 && !dev.hello && dev.result_count==0 && !dev.txn.open && !dev.rx_len && !dev.tx_len);
    CHECK(mem.response_count==0 && mem.responses[0][0]==0 && dev.rx[0]==0 && dev.tx[0]==0 && memory_matches(&mem,&dev));
    exact("JF2 PING 2\n","JF2 OK PING 2 up=0 f=24\n");
    CHECK(memory_advance(&mem,&dev,37)); exact("JF2 PING 3\n","JF2 OK PING 3 up=37 f=24\n");
    contains("JF2 INFO\n","boot=deadbeef epoch=0 state=SAFE reset=iwdg");
    jf2_fault_hw(&dev,false); CHECK(dev.faults==36); exact("JF2 FAULT CLEAR\n","JF2 OK FAULT CLEAR f=0\n");
    CHECK(dev.mode==JF2_SAFE);
    contains("JF2 HELLO 2 1\n","state=ARMED reset=iwdg");
    memory_reboot(&mem,&dev,1,JF2_RESET_SW);
    exact("JF2 PING 0\n","JF2 OK PING 0 up=0\n");
    contains("JF2 INFO\n","boot=00000001 epoch=0 state=SAFE reset=sw");
}

static void framing(void)
{
    setup();
    memory_clear_responses(&mem);
    jf2_feed(&dev,(const uint8_t *)"JF2 PI",6); CHECK(mem.response_count==0);
    jf2_feed(&dev,(const uint8_t *)"NG 1",4); CHECK(mem.response_count==0);
    jf2_feed(&dev,(const uint8_t *)"\n",1); CHECK(mem.response_count==1);
    CHECK(!strcmp((const char *)mem.responses[0],"JF2 OK PING 1 up=0\n"));
    const uint8_t nul[]={'J','F','2',' ','P','I','N','G',' ',0,'1','\n'};
    memory_clear_responses(&mem);
    jf2_feed(&dev,nul,10); CHECK(mem.response_count==0); jf2_feed(&dev,nul+10,2);
    CHECK(mem.response_count==1 && strstr((const char *)mem.responses[0],"expected printable ASCII"));
    exact("JF2 PING 1\r\n","JF2 ERR BAD_FRAME expected printable ASCII and single spaces\n");
    exact("JF1 CLRALL\r\n","JF1 OK CLRALL\n");
    memory_clear_responses(&mem);
    jf2_feed(&dev,(const uint8_t *)"JF2 PING 1\nJF1 CLRALL\n",22);
    CHECK(mem.response_count==2 && !mem.response_overflow);
    uint8_t frame[4096];
    memset(frame,'A',sizeof frame); memcpy(frame,"JF2 ",4); frame[511]='\n';
    memory_clear_responses(&mem); jf2_feed(&dev,frame,512);
    CHECK(mem.response_count==1 && !strcmp((const char *)mem.responses[0],"JF2 ERR UNSUPPORTED unknown command\n"));
    frame[511]='A'; frame[512]='\n';
    memory_clear_responses(&mem); jf2_feed(&dev,frame,513);
    CHECK(mem.response_count==1 && !strcmp((const char *)mem.responses[0],"JF2 ERR BAD_FRAME expected LF frame of at most 512 bytes\n"));
    memory_clear_responses(&mem); jf2_process_request(&dev,frame,513);
    CHECK(mem.response_count==1 && strstr((const char *)mem.responses[0],"expected LF frame"));
    frame[512]='A'; memcpy(frame+3000,"JF1 SET 0 1 2 3",15); frame[4095]='\n';
    memory_clear_responses(&mem); jf2_feed(&dev,frame,4000); CHECK(mem.response_count==0 && dev.rx_len==512);
    jf2_feed(&dev,frame+4000,96); CHECK(mem.response_count==1 && mem.leds[0][0]==0);
    exact("JF2 PING 0\n","JF2 OK PING 0 up=0\n");
    memset(frame,' ',sizeof frame); memcpy(frame,"JF2",3); memcpy(frame+3000,"STATUS\n",7);
    memory_clear_responses(&mem); jf2_feed(&dev,frame,3007);
    CHECK(mem.response_count==1 && strstr((const char *)mem.responses[0],"JF1 ERR MALFORMED_FRAME expected a complete"));
    memory_clear_responses(&mem); jf2_process_request(&dev,frame,3007);
    CHECK(mem.response_count==1 && strstr((const char *)mem.responses[0],"JF1 ERR MALFORMED_FRAME expected a complete"));
    memory_clear_responses(&mem); jf2_process_request(&dev,(const uint8_t *)"JF2 PING 0",10);
    CHECK(mem.response_count==1 && strstr((const char *)mem.responses[0],"expected LF frame"));
    memory_clear_responses(&mem); jf2_process_request(&dev,(const uint8_t *)"",0);
    CHECK(mem.response_count==1 && strstr((const char *)mem.responses[0],"JF1 ERR MALFORMED_FRAME"));
    /* A direct frame can contain internal LF: it must not become two requests. */
    memory_clear_responses(&mem); jf2_process_request(&dev,(const uint8_t *)"JF2 INFO\nJF2 INFO\n",18);
    CHECK(mem.response_count==1 && strstr((const char *)mem.responses[0],"ERR BAD_FRAME"));
    /* Deterministic binary framing exercise with a bounded log/capture drain. */
    uint32_t random=123;
    for (unsigned trial=0; trial<512; ++trial) {
        size_t n=trial%512+1;
        for (size_t i=0; i<n; ++i) { random=random*UINT32_C(1664525)+UINT32_C(1013904223); frame[i]=(uint8_t)(random>>24); }
        memory_clear_responses(&mem); snapshot(); jf2_process_request(&dev,frame,n);
        CHECK(mem.response_count==1 && !mem.response_overflow); unchanged();
    }
}

static void permissions_led_precedence(void)
{
    setup(); snapshot();
    contains("JF2 LED BATCH 0\n","ERR SAFE_MODE"); unchanged();
    contains("JF2 TXN ADD 1 0 broken\n","ERR SAFE_MODE"); unchanged();
    contains("JF2 TXN COMMIT 1 0 wrong\n","ERR SAFE_MODE"); unchanged();
    contains("JF2 TXN ABORT 1\n","ERR SAFE_MODE"); unchanged();
    exact("JF2 CFG SET key 01\n","JF2 ERR BAD_FRAME expected canonical unsigned integer\n");
    exact("JF2 CFG GET 4294967296\n","JF2 ERR BAD_ARG integer exceeds uint32\n");
    exact("JF2 UNKNOWN +1\n","JF2 ERR BAD_FRAME expected canonical unsigned integer\n");
    exact("JF2 HELLO 2 000001\n","JF2 OK HELLO fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=2 board=v01 boot=00ab12ef epoch=0 state=ARMED reset=por nonce=000001\n");
    contains("JF2 HELLO 2 4294967296\n","ERR BAD_ARG invalid nonce");
    exact("JF2 TXN\n","JF2 ERR BAD_FRAME missing subcommand\n");
    exact("JF2 PING\n","JF2 ERR BAD_FRAME incorrect argument count\n");
    exact("JF2 PING 4294967296\n","JF2 ERR BAD_ARG integer exceeds uint32\n");
    const char *unknowns[]={"JF2 SW bad\n","JF2 TXN bad\n","JF2 LED bad\n","JF2 FAULT bad\n","JF2 CFG bad\n"};
    for (unsigned i=0; i<5; ++i) contains(unknowns[i],"ERR UNSUPPORTED unknown command");
    contains("JF2 TXN ADD 1 0 +0:0:0@1\n","ERR TXN_UNKNOWN");
    contains("JF2 TXN COMMIT 1 0 wrong\n","ERR TXN_UNKNOWN");
    contains("JF2 LEASE 600001\n","ERR BAD_ARG lease exceeds 600000 ms");
    contains("JF2 LEASE 600000\n","OK");
    exact("JF2 LED BATCH 2 0:ffffff 15:aBcDeF\n","JF2 OK LED BATCH 2\n");
    CHECK(mem.leds[0][0]==255 && mem.leds[15][0]==171 && mem.leds[15][1]==205 && mem.leds[15][2]==239);
    snapshot();
    const char *bad[]={"JF2 LED BATCH 2 0:000000 0:ffffff\n","JF2 LED BATCH 2 0:000000 1:gggggg\n","JF2 LED BATCH 1\n","JF2 LED BATCH 17\n","JF2 LED BATCH 1 01:010203\n","JF2 LED BATCH 1 16:010203\n","JF2 LED BATCH 2 0:ffffff 0:xxxxxx\n"};
    for (unsigned i=0; i<sizeof bad/sizeof bad[0]; ++i) { contains(bad[i],"ERR BAD_ARG"); unchanged(); }
    exact("JF2 LED BATCH 0\n","JF2 OK LED BATCH 0\n"); unchanged();
    begin(1,0); snapshot();
    contains("JF2 LED BATCH 0\n","ERR BUSY transaction open"); unchanged();
    contains("JF2 SELFTEST 0\n","ERR BUSY transaction open"); unchanged();
    contains("JF2 SELFTEST 1\n","ERR UNSUPPORTED no sensing hardware"); unchanged();
    contains("JF2 SELFTEST 2\n","ERR UNSUPPORTED no sensing hardware"); unchanged();
    contains("JF2 SELFTEST 3\n","ERR BAD_ARG invalid selftest level"); unchanged();
    contains("JF2 INFO\n","state=STAGING"); unchanged();
    contains("JF2 SW STATUS\n","state=STAGING"); unchanged();
    contains("JF2 SW TAGS\n","OK"); unchanged();
    contains("JF2 CFG GET max_ops\n","value=256"); unchanged();
    contains("JF2 FAULT GET\n","f=0"); unchanged();
    contains("JF2 TXN QUERY 1\n","result=OPEN"); unchanged();
    /* Capacity check precedes BUSY; identical BEGIN does not touch hardware. */
    contains("JF2 TXN BEGIN 2 99 257\n","ERR TXN_TOO_LARGE"); unchanged();
    contains("JF2 TXN BEGIN 2 99 0\n","ERR BUSY"); unchanged();
    contains("JF2 TXN BEGIN 1 0 0\n","OK"); unchanged();
    contains("JF2 TXN ADD 1 1 +0:0:0@0\n","ERR BAD_ARG invalid switch operation"); unchanged();
    contains("JF2 TXN ADD 1 1 +0:0:0@1\n","ERR TXN_SEQ_GAP"); unchanged();
    /* All LED formats get validated before duplicate detection. */
    contains("JF1 SET 1 200 201 202\n","OK"); CHECK(mem.leds[1][0]==200 && dev.mode==JF2_STAGING);
}

int core_self_tests(void)
{
    passed=failed=0;
    init_crc_info(); bbm_and_history(); validation_atomicity(); operation_capacity();
    interrupts(); lease_fault_reboot(); framing(); permissions_led_precedence();
    printf("core self-tests: %zu passed, %zu failed\n",passed,failed);
    printf("sizeof: Jf2Device=%zu Jf2Transaction=%zu Jf2Op=%zu Jf2Result=%zu Jf2Shadow=%zu MemoryHw=%zu (host only)\n",
           sizeof(Jf2Device),sizeof(Jf2Transaction),sizeof(Jf2Op),sizeof(Jf2Result),sizeof(Jf2Shadow),sizeof(MemoryHw));
    return failed ? 1 : 0;
}
