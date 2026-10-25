#include "memory_hw.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

/* Host-only explicit resource limit. Requests are never truncated to 512. */
#define VEC_LINE_CAPACITY 65536u
typedef struct { const uint8_t *p; size_t n; } Span;
typedef struct { size_t passed, failed, directives, error_line; } VecStats;
static MemoryHw runner_hw;
static Jf2Device runner_device;
static uint8_t physical[VEC_LINE_CAPACITY+1u], request[VEC_LINE_CAPACITY+1u], expected[VEC_LINE_CAPACITY+1u];
static char fixture_path[4096];

static bool whitespace(uint8_t c)
{ return c==' ' || c=='\t' || c=='\r' || c=='\v' || c=='\f'; }
static bool next_token(const uint8_t *p, size_t n, size_t *i, Span *s)
{
    while (*i<n && whitespace(p[*i])) ++*i;
    size_t start=*i;
    while (*i<n && !whitespace(p[*i])) ++*i;
    *s=(Span){p+start,*i-start};
    return s->n!=0;
}
static bool ascii_payload(const uint8_t *p, size_t n)
{
    for (size_t i=0; i<n; ++i) if (p[i]>127 || p[i]=='\n') return false;
    return true;
}
static bool token_match(Span e, Span a)
{
    if (e.n==1 && e.p[0]=='*') return true;
    if (e.n>2 && e.p[e.n-2]=='=' && e.p[e.n-1]=='*')
        return a.n>e.n-1 && !memcmp(e.p,a.p,e.n-1);
    return e.n==a.n && !memcmp(e.p,a.p,e.n);
}
static bool match(const uint8_t *e, size_t en, const uint8_t *a, size_t an, size_t *diff)
{
    if (an && a[an-1]=='\n') --an;
    if (!ascii_payload(e,en) || !ascii_payload(a,an)) { *diff=0; return false; }
    size_t ei=0,ai=0,k=0;
    Span et,at;
    for (;;) {
        bool ep=next_token(e,en,&ei,&et), ap=next_token(a,an,&ai,&at);
        if (!ep && !ap) return true;
        *diff=++k;
        if (!ep || !ap || !token_match(et,at)) return false;
    }
}
static int hex(uint8_t c)
{
    if (c>='0' && c<='9') return c-'0';
    if (c>='a' && c<='f') return c-'a'+10;
    if (c>='A' && c<='F') return c-'A'+10;
    return -1;
}
static bool decode(const uint8_t *raw, size_t n, uint8_t *out, size_t *len)
{
    size_t j=0;
    for (size_t i=0; i<n; ++i) {
        uint8_t c=raw[i];
        if (c<32 || c>126) return false;
        if (c=='\\') {
            if (++i==n) return false;
            c=raw[i];
            if (c=='r') c='\r';
            else if (c=='t') c='\t';
            else if (c=='x') {
                if (n-i<3 || hex(raw[i+1])<0 || hex(raw[i+2])<0) return false;
                c=(uint8_t)(hex(raw[i+1])*16+hex(raw[i+2])); i+=2;
            } else if (c!='\\') return false;
        }
        if (c=='\n') return false;
        out[j++]=c;
    }
    *len=j; return true;
}
static void escaped(FILE *f, const uint8_t *p, size_t n)
{
    fputc('"',f);
    for (size_t i=0; i<n; ++i) {
        if (p[i]=='\\' || p[i]=='"') { fputc('\\',f); fputc(p[i],f); }
        else if (p[i]>=32 && p[i]<=126) fputc(p[i],f);
        else fprintf(f,"\\x%02x",p[i]);
    }
    fputc('"',f);
}
/* 0 invalid grammar, 1 valid, 2 valid grammar but uint64 resource exhaustion. */
static int directive(const uint8_t *raw, size_t n, bool execute)
{
    const char *s=(const char *)raw;
    if (memchr(raw,0,n)) return 0;
    if (n>13 && !memcmp(raw,"@ advance_ms ",13)) {
        const char *p=s+13;
        if (!*p || (*p=='0' && p[1])) return 0;
        bool overflow=false; uint64_t amount=0;
        for (; *p; ++p) {
            if (*p<'0' || *p>'9') return 0;
            unsigned digit=(unsigned)(*p-'0');
            if (amount>UINT64_MAX/10 || (amount==UINT64_MAX/10 && digit>UINT64_MAX%10)) overflow=true;
            if (!overflow) amount=amount*10+digit;
        }
        if (overflow) return 2;
        if (execute && !memory_advance(&runner_hw,&runner_device,amount)) return 2;
        return 1;
    }
    if (!strcmp(s,"@ fault hw")) { if (execute) jf2_fault_hw(&runner_device,true); }
    else if (!strcmp(s,"@ fault_source_gone hw")) { if (execute) jf2_fault_hw(&runner_device,false); }
    else if (!strcmp(s,"@ reboot por") || !strcmp(s,"@ reboot iwdg")) {
        if (execute) memory_reboot(&runner_hw,&runner_device,runner_device.boot+1u,
                                  !strcmp(s,"@ reboot por") ? JF2_RESET_POR : JF2_RESET_IWDG);
    } else if (!strcmp(s,"@ interrupt_next_commit")) { if (execute) jf2_interrupt_next_commit(&runner_device); }
    else return 0;
    return 1;
}
static bool syntax_error(const char *name, size_t line, const char *message, const uint8_t *raw, size_t n, bool quiet, VecStats *stats)
{
    stats->error_line=line; ++stats->failed;
    if (!quiet) {
        fprintf(stderr,"%s:%zu: %s ",name,line,message);
        escaped(stderr,raw,n); fputc('\n',stderr);
    }
    return false;
}
static bool run_stream(FILE *f, const char *name, bool execute, bool quiet, VecStats *stats)
{
    memory_hw_init(&runner_hw);
    Jf2Hw hw=memory_hw_binding(&runner_hw);
    if (!jf2_init(&runner_device,&hw,memory_emit,&runner_hw,UINT32_C(0x1234abcd),JF2_RESET_POR)) return false;
    size_t line=0, req_line=0, req_len=0;
    bool pending=false, ok=true;
    for (;;) {
        size_t n=0; int c;
        while ((c=fgetc(f))!=EOF && c!='\n') {
            if (n==VEC_LINE_CAPACITY)
                return syntax_error(name,line+1,"HARNESS_RESOURCE physical line exceeds 65536 bytes",physical,n,quiet,stats);
            physical[n++]=(uint8_t)c;
        }
        if (c==EOF && n==0) break;
        ++line;
        if (n && physical[n-1]=='\r') --n;
        physical[n]=0;
        bool blank=true;
        for (size_t i=0; i<n; ++i) if (physical[i]!=' ' && physical[i]!='\t') blank=false;
        if (blank || physical[0]=='#') continue;
        if (physical[0]=='@') {
            int kind=pending ? 0 : directive(physical,n,execute);
            if (kind!=1) return syntax_error(name,line,kind==2 ? "HARNESS_RESOURCE uint64 clock exhausted" : "VEC_SYNTAX unknown directive or position",physical,n,quiet,stats);
            ++stats->directives; continue;
        }
        if (n<2 || physical[1]!=' ' || (physical[0]!='>' && physical[0]!='<'))
            return syntax_error(name,line,"VEC_SYNTAX invalid line prefix",physical,n,quiet,stats);
        if (physical[0]=='>') {
            if (pending) return syntax_error(name,line,"VEC_SYNTAX previous request lacks response",physical,n,quiet,stats);
            if (!decode(physical+2,n-2,request,&req_len)) return syntax_error(name,line,"VEC_SYNTAX invalid request payload",physical,n,quiet,stats);
            request[req_len++]='\n'; req_line=line; pending=true;
        } else {
            size_t en=0, ei=0; Span et;
            if (!pending) return syntax_error(name,line,"VEC_SYNTAX orphan response",physical,n,quiet,stats);
            if (!decode(physical+2,n-2,expected,&en) || !ascii_payload(expected,en) || !next_token(expected,en,&ei,&et))
                return syntax_error(name,line,"VEC_SYNTAX invalid expected payload",physical,n,quiet,stats);
            bool passed=true; size_t diff=0;
            if (execute) {
                memory_clear_responses(&runner_hw);
                jf2_feed(&runner_device,request,req_len);
                jf2_poll(&runner_device); /* same virtual instant before reading capture */
                passed=runner_hw.response_count==1 && !runner_hw.response_overflow && !runner_hw.log_overflow &&
                       match(expected,en,runner_hw.responses[0],runner_hw.response_len[0],&diff) && memory_matches(&runner_hw,&runner_device);
                if (!passed && !quiet) {
                    fprintf(stderr,"%s:%zu: FAIL token=%zu responses=%zu epoch=%" PRIu64 " faults=%" PRIx32 "\n  request=",name,req_line,diff,runner_hw.response_count,runner_device.epoch,runner_device.faults);
                    escaped(stderr,request,req_len); fputs("\n  expected=",stderr); escaped(stderr,expected,en);
                    fputs("\n  actual=",stderr); escaped(stderr,runner_hw.responses[0],runner_hw.response_len[0]); fputc('\n',stderr);
                }
            }
            if (passed) ++stats->passed; else { ++stats->failed; ok=false; }
            pending=false;
        }
    }
    if (ferror(f)) return syntax_error(name,line,"HARNESS_RESOURCE file read failed",physical,0,quiet,stats);
    if (pending) return syntax_error(name,req_line,"VEC_SYNTAX request lacks response at EOF",request,req_len,quiet,stats);
    return ok;
}

static bool fixture(const char *text, bool execute, bool want, size_t error_line)
{
    FILE *f=fopen(fixture_path,"w+b");
    if (!f) return false;
    size_t n=strlen(text);
    if (fwrite(text,1,n,f)!=n) { fclose(f); (void)remove(fixture_path); return false; }
    rewind(f);
    VecStats stats={0}; bool actual=run_stream(f,"<fixture>",execute,true,&stats);
    int close_error=fclose(f),remove_error=remove(fixture_path);
    return !close_error && !remove_error && actual==want && (!error_line || stats.error_line==error_line);
}

static int runner_tests(void)
{
    size_t passed=0,failed=0;
#define CHECK(x) do { if (x) ++passed; else { ++failed; fprintf(stderr,"runner self-test line %d failed\n",__LINE__); } } while (0)
    CHECK(fixture("",false,true,0));
    CHECK(fixture("\n \t\n# Chinese comments allowed: 中文\r\n",false,true,0));
    CHECK(fixture("# comment\r\n> JF1 STATUS\r\n\n# middle\n< JF1 OK STATUS 0\n> \n< ERR empty",false,true,0));
    CHECK(fixture(">  a  \n< OK # literal\n",false,true,0));
    CHECK(fixture("> x\n< a\\x1cb\\x1dc\\x1ed\\x1fe\\x00f\\x0bg\\x0ch",false,true,0));
    const char *bad[]={"garbage",">","<"," > request"," # comment","< OK","> a\n< OK\n< extra","> a\n> b\n< OK","# comment\n> a\n# end\n","> a\n< ","> a\n< \\t\\r","@ fault broken","> a\n@ advance_ms 10\n< OK","@","> 中文\n< OK","> a\t\n< OK","> a\rbroken\n< OK","> a\n< \\xff","> \\x0a\n< OK","> a\n< OK\\x0A"};
    const size_t lines[]={1,1,1,1,1,1,3,2,2,2,2,1,2,1,1,1,1,2,1,2};
    for (size_t i=0; i<sizeof bad/sizeof bad[0]; ++i) CHECK(fixture(bad[i],false,false,lines[i]));
    const char *escapes[]={"\\","\\n","\\b","\\0","\\X41","\\u0041","\\x","\\x1","\\xGG","\\x0g"};
    char text[256];
    for (size_t i=0; i<sizeof escapes/sizeof escapes[0]; ++i) {
        (void)snprintf(text,sizeof text,"> %s\n< OK",escapes[i]); CHECK(fixture(text,false,false,1));
        (void)snprintf(text,sizeof text,"> x\n< %s",escapes[i]); CHECK(fixture(text,false,false,2));
    }
    const char *bad_directives[]={"@ advance_ms -1","@ advance_ms +1","@ advance_ms 01","@ advance_ms","@ fault power","@ fault overcurrent","@ reboot sw","@ interrupt_next_commit 1","@  fault hw","@ fault hw ","@ reboot por x","@ fault_source_gone hw x"};
    for (size_t i=0; i<sizeof bad_directives/sizeof bad_directives[0]; ++i) CHECK(fixture(bad_directives[i],false,false,1));
    CHECK(fixture("@ advance_ms 0\n@ fault hw\n@ fault_source_gone hw\n@ reboot por\n@ reboot iwdg\n@ interrupt_next_commit\n> x\n< OK",false,true,0));
    CHECK(fixture("@ advance_ms 4294967296",true,true,0));
    CHECK(fixture("@ advance_ms 18446744073709551616",true,false,1));
    CHECK(fixture("@ advance_ms 18446744073709551615\n@ advance_ms 1",true,false,2));
    CHECK(fixture("> JF1 CLRALL\n< JF1 OK WRONG",true,false,0));
    CHECK(fixture("> JF1 CLRALL\n< JF1 OK CLRALL",true,true,0));
    CHECK(fixture("> JF1 STATUS\\r\n< JF1 OK STATUS * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * *",true,true,0));
    uint8_t out[128]; size_t out_len=0;
    const char *raw="\\\\\\r\\t\\x00\\x7f\\xAf\\xFF\\x20\\x414";
    const uint8_t want[]={'\\','\r','\t',0,127,175,255,' ','A','4'};
    CHECK(decode((const uint8_t *)raw,strlen(raw),out,&out_len) && out_len==sizeof want && !memcmp(out,want,sizeof want));
    CHECK(decode((const uint8_t *)"\\\\x41",5,out,&out_len) && out_len==4 && !memcmp(out,"\\x41",4));
    const uint8_t nul_raw[]={'a',0}; CHECK(!decode(nul_raw,sizeof nul_raw,out,&out_len));
    FILE *long_file=fopen(fixture_path,"w+b");
    CHECK(long_file!=NULL);
    if (long_file) {
        for (size_t i=0; i<VEC_LINE_CAPACITY+1u; ++i) fputc('A',long_file);
        rewind(long_file);
        VecStats stats={0};
        CHECK(!run_stream(long_file,"<resource-fixture>",false,true,&stats) && stats.error_line==1 && stats.failed==1);
        CHECK(!fclose(long_file)); CHECK(!remove(fixture_path));
    }
    struct MatchCase { const char *e,*a; bool ok; } cases[]={
        {"JF1 OK STATUS","JF1 OK STATUS\n",true},{"JF1 OK STATUS","JF1 OK STATUS\r\n",true},
        {"JF1 OK STATUS","JF1 OK STATUS",true},{"JF1 OK STATUS"," JF1\tOK  STATUS \r\n",true},
        {"","\n",true},{"a\034b\035c\036d\037e","a\034b\035c\036d\037e\n",true},
        {"a b","a\034b\n",false},{"JF2 * boot=* epoch=0","JF2 OK boot=ab12 epoch=0\n",true},
        {"* key=*","anything key=a=b\n",true},{"abc* =* key=*tail","abc* =* key=*tail\n",true},
        {"*","\n",false},{"*","two tokens\n",false},{"key=*","key=\n",false},
        {"key=*","other=value\n",false},{"key=*","key\n",false},{"abc*","abcdef\n",false},
        {"=*","=value\n",false},{"key=*tail","key=value-tail\n",false},{"JF1 OK","JF1 ERR\n",false},
        {"OK","OK extra\n",false},{"OK","\xff\n",false},{"*","\xff\n",false},
        {"OK","OK\n\n",false},{"OK OK","OK\nOK\n",false}
    };
    for (size_t i=0; i<sizeof cases/sizeof cases[0]; ++i) {
        size_t diff=0;
        CHECK(match((const uint8_t *)cases[i].e,strlen(cases[i].e),(const uint8_t *)cases[i].a,strlen(cases[i].a),&diff)==cases[i].ok);
    }
    const uint8_t control[]={'a',0,'b','\v','c','\f','d'}; size_t diff=0;
    CHECK(match(control,sizeof control,control,sizeof control,&diff));
    CHECK(decode((const uint8_t *)"\\x2a",4,out,&out_len) && match(out,out_len,(const uint8_t *)"anything\n",9,&diff));
    printf("runner self-tests: %zu passed, %zu failed\n",passed,failed);
#undef CHECK
    return failed ? 1 : 0;
}

int main(int argc, char **argv)
{
    if (argc==2 && !strcmp(argv[1],"--self-test")) {
        int n=snprintf(fixture_path,sizeof fixture_path,"%s.self-test.vec",argv[0]);
        if (n<0 || (size_t)n>=sizeof fixture_path) { fputs("HARNESS_RESOURCE fixture path too long\n",stderr); return 2; }
        int a=core_self_tests(),b=runner_tests(); return a || b;
    }
    if (argc<2) { fprintf(stderr,"usage: %s --self-test | file.vec [...]\n",argv[0]); return 2; }
    VecStats total={0}; bool all=true;
    for (int i=1; i<argc; ++i) {
        FILE *f=fopen(argv[i],"rb");
        if (!f) { perror(argv[i]); all=false; ++total.failed; continue; }
        VecStats stats={0};
        bool ok=run_stream(f,argv[i],true,false,&stats);
        if (fclose(f)) { perror(argv[i]); ok=false; ++stats.failed; }
        printf("%s: %zu passed, %zu failed, %zu directives\n",argv[i],stats.passed,stats.failed,stats.directives);
        total.passed+=stats.passed; total.failed+=stats.failed; total.directives+=stats.directives;
        if (!ok) all=false;
    }
    printf("TOTAL: %zu passed, %zu failed, %zu directives\n",total.passed,total.failed,total.directives);
    return all ? 0 : 1;
}
