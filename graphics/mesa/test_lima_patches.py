#!/usr/bin/env python3
"""Host regression fixture extracting the actual patched Mesa functions.

Mocks check lifetime/control-flow invariants; this is not GPU validation.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import apply_lima_patches as patcher

def function(source, name):
    pos=source.index(name+'(')
    start=source.rfind('\n',0,pos)+1
    if start==pos:
        start=source.rfind('\n',0,start-1)+1
    opening=source.index('{',start); depth=1
    # These selected functions have no braces in strings/comments.
    for i in range(opening+1,len(source)):
        if source[i]=='{': depth+=1
        elif source[i]=='}':
            depth-=1
            if not depth: return source[start:i+1]
    raise ValueError(name)

HARNESS=r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define LIMA_SUBMIT_BO_READ 1
#define PIPE_FLUSH_END_OF_FRAME 1
#define CHECK(x) do { if (!(x)) {fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x);exit(1);} } while(0)
#define lima_dump_command_stream_print(...) ((void)0)
struct lima_bo { void *map; unsigned va; };
struct pipe_resource { int ref; };
struct lima_resource { struct pipe_resource base; struct lima_bo *bo; };
struct lima_screen { int num_pp; int fd; };
struct pipe_context { struct lima_screen *screen; };
struct lima_context_framebuffer { int tiled_w,tiled_h; };
struct lima_ctx_plb_pp_stream_key {uint32_t plb_index,tiled_w,tiled_h;};
struct lima_ctx_plb_pp_stream { struct lima_bo *bo; uint32_t offset[4]; };
struct hash_entry { void *data; };
struct util_dynarray {struct lima_bo **data;unsigned count;};
struct lima_submit {int in_sync_fd;struct lima_screen *screen;unsigned in_sync,out_sync;struct util_dynarray bos,gem_bos;};
#define util_dynarray_foreach(a,type,v) for(unsigned ix=0;ix<(a)->count;ix++) for(type *v=&(a)->data[ix];v;v=NULL)
struct lima_pp_stream_state {struct lima_bo *bo;unsigned bo_offset;uint32_t offset[4];};
struct lima_context {struct pipe_context base;bool z1_context_error;bool dirty;int plb_index;
 struct lima_context_framebuffer framebuffer;void *plb_pp_stream,*uploader;
 struct lima_pp_stream_state pp_stream;struct lima_submit *gp_submit,*pp_submit;};
enum pipe_reset_status {PIPE_NO_RESET,PIPE_UNKNOWN_CONTEXT_RESET};
struct pipe_fence_handle {int fd;};
static struct lima_bo bo; static struct lima_resource resource;
static struct hash_entry *hit;static int upload_mode,bo_refs,resource_releases,generated,allocs;
static int closes,exports,merges,flushed;static bool export_ok,merge_ok,flush_ok,fence_ok;
static struct pipe_fence_handle result_fence;
static int finished_arrays,destroyed_sync;
static void lima_bo_free(struct lima_bo *b) {CHECK(b==&bo);bo_refs--;}
static void util_dynarray_fini(struct util_dynarray *a) {a->count=0;finished_arrays++;}
static int z1_drmSyncobjDestroy(int fd,unsigned sync) {(void)fd;(void)sync;destroyed_sync++;return 0;}
#define lima_context(p) ((struct lima_context *)(p))
#define lima_screen(p) (p)
#define lima_resource(p) ((struct lima_resource *)(p))
static int lima_get_pp_stream_size(int pp,int w,int h,uint32_t *off) {CHECK(pp==2);off[0]=0;off[1]=64;return w*h*8;}
static void u_upload_alloc(void *u,unsigned min,int size,int align,unsigned *off,struct pipe_resource **p,void **cpu) {
 (void)u;CHECK(min==0&&size>0&&align==0x40);allocs++;
 *off=128;*p=upload_mode==1?NULL:&resource.base;*cpu=upload_mode==2?NULL:bo.map;
}
static void pipe_resource_reference(struct pipe_resource **p,void *none) {CHECK(!none);if(*p)resource_releases++;*p=NULL;}
static void *lima_bo_map(struct lima_bo *b) {return b->map;}
static struct lima_bo *lima_bo_create(struct lima_screen *s,int size,int flags) {(void)s;(void)size;(void)flags;return &bo;}
static bool lima_submit_add_bo(struct lima_submit *s,struct lima_bo *b,int flags) {(void)s;CHECK(b==&bo&&flags==1);bo_refs++;return true;}
static void lima_update_pp_stream(struct lima_context *c,int x,int y,int w,int h) {CHECK(x==0&&y==0&&w==20&&h==15);CHECK(c->pp_stream.bo_offset==128);generated++;}
static struct hash_entry *_mesa_hash_table_search(void *t,const void *key) {(void)t;(void)key;return hit;}
static bool lima_ctx_dirty(struct lima_context *c) {return c->dirty;}
static bool _lima_flush(struct lima_context *c,bool end) {(void)end;flushed++;if(!flush_ok)c->z1_context_error=true;return flush_ok;}
static int close(int fd) {CHECK(fd>=0);closes++;return 0;}
static int sync_accumulate(const char *name,int *fd,int incoming) {CHECK(!strcmp(name,"lima")&&*fd==41&&incoming==55);merges++;if(merge_ok)*fd=42;return merge_ok?0:-1;}
static bool lima_submit_get_out_sync(struct lima_submit *s,int *fd) {(void)s;exports++;if(export_ok)*fd=41;return export_ok;}
static struct pipe_fence_handle *lima_fence_create(int fd) {if(!fence_ok){close(fd);return NULL;}result_fence.fd=fd;return &result_fence;}
/* EXTRACTED */
static void reset(void) {upload_mode=bo_refs=resource_releases=generated=allocs=closes=exports=merges=flushed=0;hit=NULL;bo.map=&bo;resource.bo=&bo;export_ok=merge_ok=flush_ok=fence_ok=true;}
int main(void) {
 struct lima_screen screen={.num_pp=2};struct lima_submit gp={.in_sync_fd=-1},pp={.in_sync_fd=-1};
 struct lima_context c={.base={&screen},.framebuffer={20,15},.gp_submit=&gp,.pp_submit=&pp};
 reset();CHECK(lima_update_full_pp_stream(&c));CHECK(allocs==1&&generated==1&&bo_refs==1&&resource_releases==1&&c.pp_stream.bo_offset==128);
 puts("PASS cache miss generates full framebuffer stream, offset and submit ownership retained");
 for(int mode=1;mode<=3;mode++) {reset();upload_mode=mode;if(mode==3)bo.map=NULL;CHECK(!lima_update_uncached_pp_stream(&c));CHECK(!generated&&!bo_refs);CHECK(resource_releases==(mode==1?0:1));}
 puts("PASS uploader allocation/map failures clean up without generation or BO submit");
 reset();struct lima_ctx_plb_pp_stream cached={.bo=&bo,.offset={0,64}};struct hash_entry entry={&cached};hit=&entry;
 CHECK(lima_update_full_pp_stream(&c));CHECK(!allocs&&!generated&&bo_refs==1&&c.pp_stream.bo_offset==0);
 puts("PASS cache hit unchanged");
 struct pipe_fence_handle *f=NULL;
 reset();c.dirty=false;c.z1_context_error=false;gp.in_sync_fd=-1;lima_pipe_flush(&c.base,&f,0);CHECK(f&&f->fd==41&&exports==1&&!flushed&&!merges);
 reset();gp.in_sync_fd=55;lima_pipe_flush(&c.base,&f,0);CHECK(f&&f->fd==42&&merges==1&&gp.in_sync_fd==55);
 puts("PASS empty flush exports latest out_sync and includes pending server dependency without consuming it");
 reset();c.dirty=true;lima_pipe_flush(&c.base,&f,1);CHECK(f&&flushed==1);
 reset();flush_ok=false;c.z1_context_error=false;lima_pipe_flush(&c.base,&f,0);CHECK(!f&&c.z1_context_error&&!exports);
 puts("PASS failed submit refuses fence publication");
 for(int mode=0;mode<3;mode++) {reset();c.dirty=false;c.z1_context_error=false;if(mode==0)export_ok=false;if(mode==1)merge_ok=false;if(mode==2)fence_ok=false;lima_pipe_flush(&c.base,&f,0);CHECK(!f&&c.z1_context_error);CHECK(closes==(mode==0?0:1));}
 puts("PASS export/merge/fence-allocation failures close owned FD and fail context");
 reset();c.z1_context_error=true;lima_pipe_flush(&c.base,&f,0);CHECK(!f&&!exports&&!flushed);
 CHECK(lima_get_device_reset_status(&c.base)==PIPE_UNKNOWN_CONTEXT_RESET);c.z1_context_error=false;CHECK(lima_get_device_reset_status(&c.base)==PIPE_NO_RESET);
 puts("PASS context error cannot publish a stale completed fence; reset API exposes error");
 struct lima_bo *queued[2]={&bo,&bo};struct lima_submit pending={.in_sync_fd=-1,.screen=&screen,.bos={queued,2}};bo_refs=2;finished_arrays=destroyed_sync=0;lima_submit_free(&pending);CHECK(bo_refs==0&&finished_arrays==2&&destroyed_sync==2&&!pending.bos.count);
 puts("PASS context destruction releases queued BO references after failure");
}
'''

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--archive',required=True,type=Path);ap.add_argument('--stage',type=Path);args=ap.parse_args()
    before,after=patcher.expected_files(args.archive)
    src=after[patcher.BASE/'lima_draw.c'];submit=after[patcher.BASE/'lima_submit.c']
    funcs=[function(src,n) for n in ('lima_update_uncached_pp_stream','lima_update_full_pp_stream')]
    funcs += [function(submit,'lima_submit_free'),function(submit,'lima_submit_merge_pending_sync'),function(src,'lima_pipe_flush'),function(src,'lima_get_device_reset_status')]
    # Assert actual kernel submission replaces out_sync, not just the mock.
    actual=function(submit,'lima_submit_start')
    assert '.out_sync = submit->out_sync,' in actual
    assert 'ctx->z1_context_error = true;' in src and 'PIPE_UNKNOWN_CONTEXT_RESET' in src
    with tempfile.TemporaryDirectory(prefix='z1-lima-tests-') as tmp:
        root=Path(tmp);cpp=root/'fixture.c';binary=root/'fixture';cpp.write_text(HARNESS.replace('/* EXTRACTED */','\n'.join(funcs)))
        subprocess.run(['cc','-std=c99','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(cpp),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)
        for p,data in before.items():
            f=root/p;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(data)
        assert len(patcher.apply(root,args.archive))==6
        snapshots={p:(root/p).read_bytes() for p in before}
        assert patcher.apply(root,args.archive)==[]
        assert snapshots=={p:(root/p).read_bytes() for p in before}
        bad=root/patcher.BASE/'lima_draw.c';bad.write_text(bad.read_text()+'\n/* unknown local edit */\n')
        try:patcher.apply(root,args.archive)
        except ValueError:pass
        else:raise AssertionError('unknown edits were overwritten')
        assert bad.read_text().endswith('/* unknown local edit */\n')
    if args.stage:
        args.stage.mkdir(parents=True,exist_ok=False)
        for path,data in after.items():
            dest=args.stage/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(data)
    print('PASS clean archive application, byte-idempotent rerun, unknown-edit rejection, real out_sync assignment')

if __name__=='__main__':main()
