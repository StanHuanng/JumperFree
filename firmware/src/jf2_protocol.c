#include "jf2.h"
#include <assert.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

const char *jf2_token(const Jf2Request *r, size_t index)
{
    const char *p = r->body;
    assert(index < r->count);
    while (index--) p += strlen(p) + 1;
    return p;
}

void jf2_append(Jf2Device *d, const char *format, ...)
{
    va_list ap;
    va_start(ap, format);
    int n = vsnprintf(d->tx + d->tx_len, sizeof d->tx - d->tx_len, format, ap);
    va_end(ap);
    /* All protocol responses have a statically bounded expansion <512. */
    assert(n >= 0 && (size_t)n < sizeof d->tx - d->tx_len);
    d->tx_len += (size_t)n;
}

bool jf2_error(Jf2Device *d, const char *code, const char *message)
{
    d->tx_len = 0;
    jf2_append(d, "JF2 ERR %s %s", code, message);
    return false;
}

static bool canonical(const char *s)
{
    if (!*s || (s[0] == '0' && s[1])) return false;
    for (; *s; ++s) if (*s < '0' || *s > '9') return false;
    return true;
}

/* JF1 accepts arbitrarily long decimal values, then applies clip/RGB bounds.
 * Saturation above 256 preserves every frozen range/precedence decision. */
static uint32_t bounded_decimal(const char *s, uint32_t limit)
{
    uint32_t n = 0;
    for (; *s; ++s) {
        unsigned digit = (unsigned)(*s - '0');
        if (n > limit / 10 || (n == limit / 10 && digit > limit % 10))
            return limit;
        n = n * 10 + digit;
    }
    return n;
}

bool jf2_uint(Jf2Device *d, const char *s, uint32_t *value)
{
    if (!canonical(s))
        return jf2_error(d, "BAD_FRAME", "expected canonical unsigned integer");
    if (strlen(s) > 10 || (strlen(s) == 10 && strcmp(s, "4294967295") > 0))
        return jf2_error(d, "BAD_ARG", "integer exceeds uint32");
    *value = bounded_decimal(s, UINT32_MAX);
    return true;
}

/* Streaming protocol routing also works when the old name follows >512 spaces.
 * stages 0..2 prefix, 3 after JF2, 4 spaces, 5 old-name candidate,
 * 6 final JF1, 7 final JF2. Only three routing bytes survive overlong input. */
static void route_byte(uint8_t *stage, uint8_t *mask, uint8_t *index, uint8_t c)
{
    static const char *const names[] = {"SET", "CLR", "CLRALL", "STATUS"};
    if (*stage < 3) {
        if (c != (uint8_t)"JF2"[*stage]) *stage = 6;
        else ++*stage;
    } else if (*stage == 3) {
        *stage = c == ' ' ? 4 : 7;
        *mask = 15;
        *index = 0;
    } else if (*stage == 4 || *stage == 5) {
        if (*stage == 4 && c == ' ') return;
        if (c == ' ' || c == '\r' || c == '\n') {
            bool old = false;
            for (unsigned k = 0; k < 4; ++k)
                if ((*mask & (1u << k)) && strlen(names[k]) == *index) old = true;
            *stage = old ? 6 : 7;
        } else {
            *stage = 5;
            for (unsigned k = 0; k < 4; ++k)
                if (strlen(names[k]) <= *index || names[k][*index] != (char)c)
                    *mask &= (uint8_t)~(1u << k);
            if (!*mask) *stage = 7;
            else ++*index;
        }
    }
}

static bool route_jf2(const uint8_t *p, size_t len)
{
    uint8_t stage = 0, mask = 0, index = 0;
    for (size_t i = 0; i < len && stage < 6; ++i)
        route_byte(&stage, &mask, &index, p[i]);
    if (stage == 5) route_byte(&stage, &mask, &index, '\n');
    return stage != 6 && stage >= 3;
}

static void finish(Jf2Device *d, bool is2, bool success, bool has_fault)
{
    if (is2 && success) d->lease_at = d->hw.now_ms(d->hw.ctx);
    if (is2 && d->faults && !has_fault) jf2_append(d, " f=%" PRIx32, d->faults);
    jf2_append(d, "\n");
    d->emit(d->emit_ctx, (const uint8_t *)d->tx, d->tx_len);
}

static void frame_error(Jf2Device *d, bool is2)
{
    d->tx_len = 0;
    if (is2) jf2_error(d, "BAD_FRAME", "expected LF frame of at most 512 bytes");
    else jf2_append(d, "JF1 ERR MALFORMED_FRAME expected a complete JF1 line of at most 512 bytes");
    finish(d, is2, false, false);
}

static bool numeric_looking(const char *p)
{
    if (*p == '+' || *p == '-') ++p;
    if (!*p) return false;
    for (; *p; ++p) if (*p < '0' || *p > '9') return false;
    return true;
}

static bool prevalidate(Jf2Device *d, const Jf2Request *r)
{
    const char *cmd = jf2_token(r, 0);
    bool multi = !strcmp(cmd,"TXN") || !strcmp(cmd,"SW") || !strcmp(cmd,"LED") ||
                 !strcmp(cmd,"FAULT") || !strcmp(cmd,"CFG");
    if (multi && r->count < 2) return jf2_error(d,"BAD_FRAME","missing subcommand");
    const char *sub = r->count > 1 ? jf2_token(r,1) : "";
    size_t min = 0, max = 0;
    unsigned required = 0, hex = 0;
    if (!multi) {
        if (!strcmp(cmd,"HELLO")) { min=3; max=3; required=2; hex=4; }
        else if (!strcmp(cmd,"INFO")) { min=1; max=1; }
        else if (!strcmp(cmd,"PING") || !strcmp(cmd,"LEASE") || !strcmp(cmd,"SELFTEST")) {
            min=2; max=2; required=2;
        }
    } else if (!strcmp(cmd,"TXN")) {
        if (!strcmp(sub,"BEGIN")) { min=5; max=5; required=28; }
        else if (!strcmp(sub,"ADD")) { min=5; max=512; required=12; }
        else if (!strcmp(sub,"COMMIT")) { min=5; max=5; required=12; hex=16; }
        else if (!strcmp(sub,"ABORT") || !strcmp(sub,"QUERY")) { min=3; max=3; required=4; }
    } else if ((!strcmp(cmd,"SW") && (!strcmp(sub,"STATUS") || !strcmp(sub,"TAGS") || !strcmp(sub,"ALLOPEN"))) ||
               (!strcmp(cmd,"FAULT") && (!strcmp(sub,"GET") || !strcmp(sub,"CLEAR")))) {
        min=2; max=2;
    } else if (!strcmp(cmd,"LED") && !strcmp(sub,"BATCH")) { min=3; max=512; required=4; }
    else if (!strcmp(cmd,"CFG") && !strcmp(sub,"GET")) { min=3; max=3; }
    else if (!strcmp(cmd,"CFG") && !strcmp(sub,"SET")) { min=4; max=4; }
    if (min && (r->count < min || r->count > max))
        return jf2_error(d,"BAD_FRAME","incorrect argument count");
    for (size_t i=0; i<r->count; ++i) {
        const char *p=jf2_token(r,i);
        bool req = i < 5 && (required & (1u << i));
        bool exempt = i < 5 && (hex & (1u << i));
        uint32_t ignored;
        if ((req || (!exempt && numeric_looking(p))) && !jf2_uint(d,p,&ignored)) return false;
    }
    return true;
}

static bool jf1_error(Jf2Device *d, const char *code, const char *message)
{
    jf2_append(d,"JF1 ERR %s %s",code,message);
    return false;
}

static bool jf1(Jf2Device *d, const Jf2Request *r)
{
    if (strcmp(jf2_token(r,0),"JF1"))
        return jf1_error(d,"MALFORMED_FRAME","unsupported protocol version");
    if (r->count<2) return jf1_error(d,"INVALID_COMMAND","request is missing a command");
    for (size_t i=2; i<r->count; ++i)
        if (!canonical(jf2_token(r,i)))
            return jf1_error(d,"MALFORMED_FRAME","expected canonical unsigned decimal integers");
    const char *cmd=jf2_token(r,1);
    size_t n=r->count-2;
    bool set=!strcmp(cmd,"SET"), clr=!strcmp(cmd,"CLR"), all=!strcmp(cmd,"CLRALL"), status=!strcmp(cmd,"STATUS");
    if ((!set && !clr && !all && !status) || n != (set ? 4u : clr ? 1u : 0u))
        return jf1_error(d,"INVALID_COMMAND","unknown command or incorrect argument count");
    unsigned values[4]={0};
    for (size_t i=0; i<n; ++i) values[i]=bounded_decimal(jf2_token(r,i+2),256);
    if ((set || clr) && values[0]>15)
        return jf1_error(d,"CLIP_OUT_OF_RANGE","clip must be an integer in 0..15");
    if (set && (values[1]>255 || values[2]>255 || values[3]>255))
        return jf1_error(d,"INVALID_RGB","RGB channels must be integers in 0..255");
    jf2_append(d,"JF1 OK %s",cmd);
    if (set || clr || all) {
        unsigned start=all ? 0 : values[0], end=all ? 16 : start+1;
        for (unsigned c=start; c<end; ++c) {
            for (unsigned k=0; k<3; ++k) d->leds[c][k]=(uint8_t)(set ? values[k+1] : 0);
            d->hw.set_led(d->hw.ctx,(uint8_t)c,d->leds[c][0],d->leds[c][1],d->leds[c][2]);
        }
    }
    if (status) {
        for (unsigned c=0; c<16; ++c)
            for (unsigned k=0; k<3; ++k) jf2_append(d," %u",d->leds[c][k]);
    } else for (size_t i=0; i<n; ++i) jf2_append(d," %u",values[i]);
    return true;
}

void jf2_process_request(Jf2Device *d, const uint8_t *line, size_t len)
{
    jf2_poll(d);
    bool is2=route_jf2(line,len);
    d->tx_len=0;
    if (!len || len>JF2_FRAME_MAX || line[len-1]!='\n') { frame_error(d,is2); return; }
    size_t body_len=len-1;
    if (!is2 && body_len && line[body_len-1]=='\r') --body_len;
    bool ascii=true, printable=body_len>0;
    for (size_t i=0; i<body_len; ++i) {
        if (line[i]>127) ascii=false;
        if (line[i]<32 || line[i]>126 || (line[i]==' ' && (!i || i+1==body_len || line[i-1]==' '))) printable=false;
    }
    if (!ascii || !printable) {
        if (is2) jf2_error(d,"BAD_FRAME","expected printable ASCII and single spaces");
        else jf1_error(d,"MALFORMED_FRAME",!ascii ? "JF1 frames must be ASCII" : "expected printable tokens separated by single spaces");
        finish(d,is2,false,false); return;
    }
    memmove(d->rx,line,body_len);
    d->rx[body_len]=0;
    Jf2Request r={(char *)d->rx,1};
    for (size_t i=0; i<body_len; ++i) if (d->rx[i]==' ') { d->rx[i]=0; ++r.count; }
    bool success;
    bool has_fault=false;
    if (!is2) success=jf1(d,&r);
    else if (strcmp(jf2_token(&r,0),"JF2") || r.count<2)
        success=jf2_error(d,"BAD_FRAME","invalid JF2 prefix or command");
    else {
        r.body += 4; --r.count;
        success=prevalidate(d,&r) && jf2_dispatch(d,&r);
        has_fault=success && !strcmp(jf2_token(&r,0),"FAULT") &&
                  (!strcmp(jf2_token(&r,1),"GET") || !strcmp(jf2_token(&r,1),"CLEAR"));
    }
    finish(d,is2,success,has_fault);
}

void jf2_feed(Jf2Device *d, const uint8_t *bytes, size_t len)
{
    for (size_t i=0; i<len; ++i) {
        uint8_t c=bytes[i];
        route_byte(&d->route_stage,&d->route_mask,&d->route_index,c);
        if (d->rx_len<JF2_FRAME_MAX) d->rx[d->rx_len++]=c;
        else d->rx_overflow=true;
        if (c=='\n') {
            size_t n=d->rx_len;
            bool over=d->rx_overflow;
            bool is2=d->route_stage!=6 && d->route_stage>=3;
            d->rx_len=0; d->rx_overflow=false;
            d->route_stage=0; d->route_mask=0; d->route_index=0;
            if (over) { jf2_poll(d); frame_error(d,is2); }
            else jf2_process_request(d,d->rx,n);
        }
    }
}
