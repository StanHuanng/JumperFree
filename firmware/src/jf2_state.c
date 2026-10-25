#include "jf2.h"
#include <assert.h>
#include <inttypes.h>
#include <string.h>

static const char *mode_name(Jf2Mode mode)
{ return mode==JF2_SAFE ? "SAFE" : mode==JF2_ARMED ? "ARMED" : "STAGING"; }
static const char *reset_name(Jf2ResetCause cause)
{ return cause==JF2_RESET_POR ? "por" : cause==JF2_RESET_IWDG ? "iwdg" : "sw"; }

static Jf2Result *result(Jf2Device *d, uint32_t id)
{
    for (unsigned i=0; i<d->result_count; ++i) if (d->results[i].id==id) return &d->results[i];
    return NULL;
}

static void remember(Jf2Device *d, Jf2Result value)
{
    for (unsigned i=0; i<d->result_count; ++i) {
        if (d->results[i].id==value.id) {
            memmove(&d->results[i],&d->results[i+1],(d->result_count-i-1)*sizeof value);
            --d->result_count;
            break;
        }
    }
    if (d->result_count==JF2_RESULT_CAPACITY) {
        memmove(d->results,d->results+1,(JF2_RESULT_CAPACITY-1)*sizeof value);
        --d->result_count;
    }
    d->results[d->result_count++]=value;
}

static void abort_open(Jf2Device *d)
{
    if (d->txn.open) {
        Jf2Result r={0}; r.id=d->txn.id;
        remember(d,r);
        d->txn.open=false;
    }
}

static void next_epoch(Jf2Device *d)
{
    /* Explicit lifetime resource bound, not silent unsigned wrap. There is no
     * protocol error for exhausting 2^64 epochs; reboot before that boundary. */
    assert(d->epoch<UINT64_MAX);
    ++d->epoch;
}

static void safe(Jf2Device *d, uint32_t fault)
{
    abort_open(d);
    d->hw.all_open(d->hw.ctx);
    memset(&d->live,0,sizeof d->live);
    next_epoch(d);
    d->mode=JF2_SAFE;
    d->faults|=fault;
}

void jf2_poll(Jf2Device *d)
{
    uint64_t now=d->hw.now_ms(d->hw.ctx);
    assert(now>=d->lease_at && now>=d->boot_at);
    if (d->hello && d->lease_ms && d->mode!=JF2_SAFE && now-d->lease_at>=d->lease_ms)
        safe(d,1);
}

void jf2_fault_hw(Jf2Device *d, bool active)
{
    d->hw_source=active;
    if (active) safe(d,4);
}

void jf2_interrupt_next_commit(Jf2Device *d) { d->interrupt_commit=true; }

void jf2_reboot(Jf2Device *d, uint32_t boot, Jf2ResetCause cause)
{
    Jf2Hw hw=d->hw;
    Jf2Emit emit=d->emit;
    void *ctx=d->emit_ctx;
    bool source=d->hw_source;
    memset(d,0,sizeof *d);
    d->hw=hw; d->emit=emit; d->emit_ctx=ctx; d->hw_source=source;
    d->boot=boot; d->reset_cause=cause;
    d->boot_at=d->lease_at=hw.now_ms(hw.ctx);
    d->faults=(source ? 4u : 0u) | (cause==JF2_RESET_IWDG ? 32u : 0u);
    hw.all_open(hw.ctx);
    for (uint8_t i=0; i<16; ++i) hw.set_led(hw.ctx,i,0,0,0);
}

bool jf2_init(Jf2Device *d, const Jf2Hw *hw, Jf2Emit emit, void *ctx,
              uint32_t boot, Jf2ResetCause cause)
{
    if (!d || !hw || !emit || !hw->set_crosspoint || !hw->all_open || !hw->set_led || !hw->now_ms ||
        (cause!=JF2_RESET_POR && cause!=JF2_RESET_IWDG && cause!=JF2_RESET_SW)) return false;
    Jf2Hw binding=*hw; /* permit hw to refer to storage inside d */
    memset(d,0,sizeof *d);
    d->hw=binding; d->emit=emit; d->emit_ctx=ctx;
    jf2_reboot(d,boot,cause);
    return true;
}

uint8_t jf2_shadow_tag(const Jf2Shadow *s, unsigned chip, unsigned bit)
{
    unsigned pos=32+chip*64+bit/2;
    return (uint8_t)((s->bytes[pos] >> (bit%2*4)) & 15);
}

static bool closed(const Jf2Shadow *s, unsigned chip, unsigned bit)
{ return (s->bytes[chip*16+bit/8] & (1u << (bit%8)))!=0; }

static void set_point(Jf2Shadow *s, const Jf2Op *op)
{
    unsigned bit=op->x*8u+op->y, pos=op->chip*16u+bit/8u;
    uint8_t mask=(uint8_t)(1u << (bit%8));
    if (op->tag) s->bytes[pos]|=mask; else s->bytes[pos]&=(uint8_t)~mask;
    pos=32+op->chip*64u+bit/2;
    unsigned shift=bit%2*4;
    s->bytes[pos]=(uint8_t)((s->bytes[pos] & ~(15u << shift)) | ((unsigned)op->tag << shift));
}

static bool parse_op(const char *s, Jf2Op *op)
{
    size_t n=strlen(s);
    if (n<6 || (s[0]!='+' && s[0]!='-') || s[1]<'0' || s[1]>'1' || s[2]!=':' ||
        s[3]<'0' || s[3]>'7' || s[4]!=':' || s[5]<'0' || s[5]>'7') return false;
    unsigned tag=0;
    if (s[0]=='-') { if (n!=6) return false; }
    else {
        if ((n!=8 && n!=9) || s[6]!='@') return false;
        if (n==8 && s[7]>='1' && s[7]<='9') tag=(unsigned)(s[7]-'0');
        else if (n==9 && s[7]=='1' && s[8]>='0' && s[8]<='5') tag=10u+(unsigned)(s[8]-'0');
        else return false;
    }
    *op=(Jf2Op){(uint8_t)(s[1]-'0'),(uint8_t)(s[3]-'0'),(uint8_t)(s[5]-'0'),(uint8_t)tag};
    return true;
}

static int hex_digit(char c)
{
    if (c>='0' && c<='9') return c-'0';
    if (c>='a' && c<='f') return c-'a'+10;
    if (c>='A' && c<='F') return c-'A'+10;
    return -1;
}

static bool hex_value(const char *s, size_t min, size_t max, uint32_t *value)
{
    size_t n=strlen(s);
    if (n<min || n>max) return false;
    uint32_t v=0;
    for (size_t i=0; i<n; ++i) {
        int h=hex_digit(s[i]); if (h<0) return false;
        v=(v<<4)|(unsigned)h;
    }
    *value=v; return true;
}

static bool require_session(Jf2Device *d)
{
    return (d->hello && d->mode!=JF2_SAFE) || jf2_error(d,"SAFE_MODE","HELLO required without faults");
}

static void info(Jf2Device *d)
{
    jf2_append(d,"fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=2 board=v01 boot=%08" PRIx32
               " epoch=%" PRIu64 " state=%s reset=%s",d->boot,d->epoch,mode_name(d->mode),reset_name(d->reset_cause));
}

static void result_info(Jf2Device *d, const Jf2Result *r)
{
    jf2_append(d," epoch=%" PRIu64 " crc=%08" PRIx32 " opened=%u closed=%u",r->epoch,r->crc,r->opened,r->closed);
}

static bool valid_target(Jf2Device *d)
{
    d->target=d->live;
    for (unsigned phase=0; phase<2; ++phase) {
        for (unsigned i=0; i<d->txn.count; ++i) {
            const Jf2Op *op=&d->txn.ops[i];
            if ((op->tag!=0)!=(phase!=0)) continue;
            if (closed(&d->target,op->chip,op->x*8u+op->y)==(phase!=0))
                return jf2_error(d,"BAD_ARG","break requires closed point; make requires open point");
            set_point(&d->target,op);
        }
    }
    uint8_t x_owner[2][8]={{0}}, y_owner[8]={0};
    for (unsigned c=0; c<2; ++c) for (unsigned x=0; x<8; ++x) for (unsigned y=0; y<8; ++y) {
        unsigned bit=x*8+y;
        if (closed(&d->target,c,bit)) {
            uint8_t tag=jf2_shadow_tag(&d->target,c,bit);
            if ((x_owner[c][x] && x_owner[c][x]!=tag) || (y_owner[y] && y_owner[y]!=tag))
                return jf2_error(d,"SAFETY_SHORT","line has different tags");
            x_owner[c][x]=tag; y_owner[y]=tag;
        }
    }
    return true;
}

static bool transaction(Jf2Device *d, const Jf2Request *r)
{
    const char *sub=jf2_token(r,1);
    uint32_t id=0; (void)jf2_uint(d,jf2_token(r,2),&id);
    Jf2Result *res=result(d,id);
    Jf2Transaction *t=&d->txn;
    if (!strcmp(sub,"QUERY")) {
        const char *state=t->open && t->id==id ? "OPEN" : !res ? "UNKNOWN" : res->committed ? "COMMITTED" : "ABORTED";
        jf2_append(d,"JF2 OK TXN QUERY %" PRIu32 " result=%s",id,state);
        if (!strcmp(state,"COMMITTED")) result_info(d,res);
        return true;
    }
    if (!strcmp(sub,"COMMIT") && res && res->committed) {
        jf2_append(d,"JF2 OK TXN COMMIT %" PRIu32,id); result_info(d,res);
        jf2_append(d," replayed=1"); return true;
    }
    if (!strcmp(sub,"ABORT") && (!t->open || t->id!=id)) {
        if (!res && !require_session(d)) return false;
        if (res && res->committed) return jf2_error(d,"TXN_UNKNOWN","transaction already committed");
        Jf2Result a={0}; a.id=id; remember(d,a);
        jf2_append(d,"JF2 OK TXN ABORT %" PRIu32,id); return true;
    }
    if (!require_session(d)) return false;
    if (!strcmp(sub,"BEGIN")) {
        uint32_t base=0,count=0;
        (void)jf2_uint(d,jf2_token(r,3),&base); (void)jf2_uint(d,jf2_token(r,4),&count);
        if (count>256) return jf2_error(d,"TXN_TOO_LARGE","max_ops is 256");
        if (t->open) {
            if (t->id!=id) return jf2_error(d,"BUSY","another transaction open");
            if (t->base!=base || t->declared!=count) return jf2_error(d,"TXN_SEQ_CONFLICT","BEGIN content changed");
        } else {
            if (res) return jf2_error(d,"TXN_UNKNOWN","transaction id already finished");
            if (base!=d->epoch) return jf2_error(d,"EPOCH_MISMATCH","base epoch differs");
            t->open=true; t->id=id; t->base=base; t->declared=count; t->count=0; t->frames=0; t->boundary[0]=0;
            d->mode=JF2_STAGING;
        }
        jf2_append(d,"JF2 OK TXN BEGIN %" PRIu32,id); return true;
    }
    if (!t->open || t->id!=id) return jf2_error(d,"TXN_UNKNOWN","transaction not open");
    if (!strcmp(sub,"ABORT")) {
        abort_open(d); d->mode=JF2_ARMED;
        jf2_append(d,"JF2 OK TXN ABORT %" PRIu32,id); return true;
    }
    uint32_t seq=0; (void)jf2_uint(d,jf2_token(r,3),&seq);
    if (!strcmp(sub,"ADD")) {
        size_t count=r->count-4;
        Jf2Op op;
        for (size_t i=4; i<r->count; ++i)
            if (!parse_op(jf2_token(r,i),&op)) return jf2_error(d,"BAD_ARG","invalid switch operation");
        if (seq<t->frames) {
            if (count!=(unsigned)(t->boundary[seq+1]-t->boundary[seq])) return jf2_error(d,"TXN_SEQ_CONFLICT","ADD content changed");
            for (size_t i=0; i<count; ++i) {
                (void)parse_op(jf2_token(r,i+4),&op);
                const Jf2Op *old=&t->ops[t->boundary[seq]+i];
                if (op.chip!=old->chip || op.x!=old->x || op.y!=old->y || op.tag!=old->tag)
                    return jf2_error(d,"TXN_SEQ_CONFLICT","ADD content changed");
            }
        } else {
            if (seq!=t->frames) return jf2_error(d,"TXN_SEQ_GAP","expected next sequence");
            if (t->count+count>256) return jf2_error(d,"TXN_TOO_LARGE","max_ops is 256");
            for (size_t i=0; i<count; ++i) (void)parse_op(jf2_token(r,i+4),&t->ops[t->count+i]);
            t->count=(uint16_t)(t->count+count); t->boundary[++t->frames]=t->count;
        }
        jf2_append(d,"JF2 OK TXN ADD %" PRIu32 " %" PRIu32,id,seq); return true;
    }
    uint32_t crc;
    if (!hex_value(jf2_token(r,4),8,8,&crc)) return jf2_error(d,"BAD_ARG","CRC requires eight hex digits");
    if (t->base!=d->epoch) return jf2_error(d,"EPOCH_MISMATCH","base epoch differs");
    if (seq!=t->frames || t->count!=t->declared || crc!=jf2_operations_crc(t->ops,t->count))
        return jf2_error(d,"TXN_CRC","frame count operation count or CRC differs");
    if (!valid_target(d)) return false;
    if (d->interrupt_commit) {
        d->interrupt_commit=false; safe(d,2);
        return jf2_error(d,"COMMIT_INTERRUPTED","commit interrupted all open");
    }
    uint16_t opened=0,closed_count=0;
    for (unsigned phase=0; phase<2; ++phase) for (unsigned i=0; i<t->count; ++i) {
        const Jf2Op *op=&t->ops[i];
        if ((op->tag!=0)!=(phase!=0)) continue;
        d->hw.set_crosspoint(d->hw.ctx,op->chip,op->x,op->y,phase!=0);
        if (phase) ++closed_count; else ++opened;
    }
    d->live=d->target; next_epoch(d); t->open=false; d->mode=JF2_ARMED;
    Jf2Result done={0};
    done.id=id; done.epoch=d->epoch; done.crc=jf2_crc32(d->live.bytes,160,0);
    done.opened=opened; done.closed=closed_count; done.committed=true;
    remember(d,done);
    jf2_append(d,"JF2 OK TXN COMMIT %" PRIu32,id); result_info(d,&done); return true;
}

static bool parse_led(const char *s, unsigned *clip, uint32_t *rgb)
{
    size_t n=strlen(s), colon=0;
    while (colon<n && s[colon]!=':') ++colon;
    if ((colon!=1 && colon!=2) || n!=colon+7 || s[0]<'0' || s[0]>'9' || (colon==2 && s[0]=='0')) return false;
    unsigned c=(unsigned)(s[0]-'0');
    if (colon==2) {
        if (s[1]<'0' || s[1]>'9') return false;
        c=c*10+(unsigned)(s[1]-'0');
    }
    if (c>15 || !hex_value(s+colon+1,6,6,rgb)) return false;
    *clip=c; return true;
}

bool jf2_dispatch(Jf2Device *d, const Jf2Request *r)
{
    const char *cmd=jf2_token(r,0), *sub=r->count>1 ? jf2_token(r,1) : "";
    uint32_t value=0;
    if (!strcmp(cmd,"HELLO")) {
        (void)jf2_uint(d,sub,&value);
        if (value!=2) return jf2_error(d,"UNSUPPORTED","unsupported protocol");
        if (!hex_value(jf2_token(r,2),1,8,&value)) return jf2_error(d,"BAD_ARG","invalid nonce");
        abort_open(d); d->hello=true; d->mode=d->faults ? JF2_SAFE : JF2_ARMED;
        jf2_append(d,"JF2 OK HELLO "); info(d); jf2_append(d," nonce=%s",jf2_token(r,2)); return true;
    }
    if (!strcmp(cmd,"INFO")) {
        jf2_append(d,"JF2 OK INFO "); info(d); jf2_append(d," brightness_cap=64 lease_default_ms=0 max_ops=256"); return true;
    }
    if (!strcmp(cmd,"PING")) {
        jf2_append(d,"JF2 OK PING %s up=%" PRIu64,sub,d->hw.now_ms(d->hw.ctx)-d->boot_at); return true;
    }
    if (!strcmp(cmd,"FAULT") && (!strcmp(sub,"GET") || !strcmp(sub,"CLEAR"))) {
        if (!strcmp(sub,"CLEAR")) d->faults=d->hw_source ? 4u : 0u;
        jf2_append(d,"JF2 OK FAULT %s f=%" PRIx32,sub,d->faults); return true;
    }
    if (!strcmp(cmd,"SW")) {
        if (!strcmp(sub,"ALLOPEN")) { safe(d,0); jf2_append(d,"JF2 OK SW ALLOPEN"); return true; }
        if (!strcmp(sub,"STATUS")) {
            jf2_append(d,"JF2 OK SW STATUS epoch=%" PRIu64 " crc=%08" PRIx32 " c0=",d->epoch,jf2_crc32(d->live.bytes,160,0));
            for (unsigned i=0; i<32; ++i) {
                if (i==16) jf2_append(d," c1=");
                jf2_append(d,"%02x",d->live.bytes[i]);
            }
            jf2_append(d," state=%s",mode_name(d->mode)); return true;
        }
        if (!strcmp(sub,"TAGS")) {
            jf2_append(d,"JF2 OK SW TAGS t0=");
            for (unsigned c=0; c<2; ++c) {
                if (c) jf2_append(d," t1=");
                for (unsigned i=0; i<32; ++i) jf2_append(d,"%02x",d->live.bytes[32+c*64+i]);
            }
            return true;
        }
    }
    if (!strcmp(cmd,"SELFTEST")) {
        (void)jf2_uint(d,sub,&value);
        if (value==1 || value==2) return jf2_error(d,"UNSUPPORTED","no sensing hardware");
        if (value) return jf2_error(d,"BAD_ARG","invalid selftest level");
        if (d->mode==JF2_STAGING) return jf2_error(d,"BUSY","transaction open");
        jf2_append(d,"JF2 OK SELFTEST 0 result=pass"); return true;
    }
    if (!strcmp(cmd,"CFG")) {
        if (!strcmp(sub,"SET")) return jf2_error(d,"UNSUPPORTED","no maintenance mode");
        if (!strcmp(sub,"GET")) {
            const char *key=jf2_token(r,2);
            if (!strcmp(key,"brightness_cap")) value=64;
            else if (!strcmp(key,"lease_default_ms")) value=0;
            else if (!strcmp(key,"max_ops")) value=256;
            else return jf2_error(d,"BAD_ARG","unknown configuration key");
            jf2_append(d,"JF2 OK CFG GET %s value=%" PRIu32,key,value); return true;
        }
    }
    if (!strcmp(cmd,"LEASE")) {
        if (!require_session(d)) return false;
        (void)jf2_uint(d,sub,&value);
        if (value>600000) return jf2_error(d,"BAD_ARG","lease exceeds 600000 ms");
        d->lease_ms=value; jf2_append(d,"JF2 OK LEASE %s",sub); return true;
    }
    if (!strcmp(cmd,"LED") && !strcmp(sub,"BATCH")) {
        if (!require_session(d)) return false;
        if (d->mode==JF2_STAGING) return jf2_error(d,"BUSY","transaction open");
        (void)jf2_uint(d,jf2_token(r,2),&value);
        if (value>16 || r->count-3!=value) return jf2_error(d,"BAD_ARG","invalid LED count");
        unsigned mask=0, clip=0; uint32_t rgb=0;
        bool duplicate=false;
        for (size_t i=3; i<r->count; ++i) {
            if (!parse_led(jf2_token(r,i),&clip,&rgb)) return jf2_error(d,"BAD_ARG","invalid LED update");
            if (mask & (1u << clip)) duplicate=true;
            mask|=1u << clip;
        }
        if (duplicate) return jf2_error(d,"BAD_ARG","duplicate LED clip");
        for (size_t i=3; i<r->count; ++i) {
            (void)parse_led(jf2_token(r,i),&clip,&rgb);
            d->leds[clip][0]=(uint8_t)(rgb>>16); d->leds[clip][1]=(uint8_t)(rgb>>8); d->leds[clip][2]=(uint8_t)rgb;
            d->hw.set_led(d->hw.ctx,(uint8_t)clip,d->leds[clip][0],d->leds[clip][1],d->leds[clip][2]);
        }
        jf2_append(d,"JF2 OK LED BATCH %" PRIu32,value); return true;
    }
    if (!strcmp(cmd,"TXN") && (!strcmp(sub,"BEGIN") || !strcmp(sub,"ADD") || !strcmp(sub,"COMMIT") || !strcmp(sub,"ABORT") || !strcmp(sub,"QUERY")))
        return transaction(d,r);
    return jf2_error(d,"UNSUPPORTED","unknown command");
}
