#include "../pixel_copy.h"
#include <assert.h>
#include <stdio.h>
#include <vector>

static unsigned checks;
static void check(bool ok) { assert(ok); ++checks; }
static void colors_and_guards(int format, unsigned bpp) {
    const unsigned w = 240, h = 320, stride = 242, dst_stride = 512;
    const size_t src_size = stride * bpp * h, dst_size = dst_stride * h;
    std::vector<uint8_t> src(src_size + 32, 0xa5), dst(dst_size + 32, 0xcc);
    const uint16_t colors[] = {0xf800, 0x07e0, 0x001f, 0xffff, 0};
    for (unsigned y=0; y<h; ++y) for (unsigned x=0; x<w; ++x) {
        const unsigned c=(x+y)%5;
        uint8_t* p=&src[16+y*stride*bpp+x*bpp];
        if(format==4) { p[0]=colors[c]&255; p[1]=colors[c]>>8; }
        else {
            p[format==5?2:0]=(c==0||c==3)?255:0;
            p[1]=(c==1||c==3)?255:0;
            p[format==5?0:2]=(c==2||c==3)?255:0;
            if(bpp==4) p[3]=0; // Alpha must not alter the output color.
        }
    }
    const auto original = src;
    check(z1_copy_rgb565(&dst[16],dst_size,dst_stride,&src[16],src_size,
                        w,h,stride,format,w,h)==0);
    check(src==original);
    for(unsigned y=0;y<h;++y) {
        for(unsigned x=0;x<w;++x) {
            const size_t p=16+y*dst_stride+2*x;
            check((unsigned(dst[p])|(unsigned(dst[p+1])<<8))==colors[(x+y)%5]);
        }
        for(unsigned x=w*2;x<dst_stride;++x) check(dst[16+y*dst_stride+x]==0xcc);
    }
    for(unsigned x=0;x<16;++x) { check(dst[x]==0xcc); check(dst[16+dst_size+x]==0xcc); }
    const auto before = dst;
    check(z1_copy_rgb565(&dst[16],dst_size,dst_stride,&src[16],
        (h-1)*stride*bpp+w*bpp-1,w,h,stride,format,w,h)==-EINVAL);
    check(dst==before);
    check(z1_copy_rgb565(&dst[16],(h-1)*dst_stride+w*2-1,dst_stride,&src[16],
        src_size,w,h,stride,format,w,h)==-EINVAL);
    check(dst==before);
    check(z1_copy_rgb565(&dst[16],dst_size,479,&src[16],src_size,
        w,h,stride,format,w,h)==-EINVAL);
    check(dst==before);
}
static void stock32_colors_and_guards(int source_format, unsigned bpp, int destination_format) {
    const unsigned w=240, h=320, source_stride=243, destination_stride=976;
    const size_t source_size=source_stride*bpp*h, destination_size=destination_stride*h;
    std::vector<uint8_t> src(source_size+32,0xa5), dst(destination_size+32,0xcc);
    const uint16_t colors[]={0xf800,0x07e0,0x001f,0xffff,0};
    for(unsigned y=0;y<h;++y) for(unsigned x=0;x<w;++x) {
        const unsigned c=(x+y)%5;
        uint8_t* p=&src[16+y*source_stride*bpp+x*bpp];
        if(source_format==4) { p[0]=colors[c]&255; p[1]=colors[c]>>8; }
        else {
            p[source_format==5?2:0]=(c==0||c==3)?255:0;
            p[1]=(c==1||c==3)?255:0;
            p[source_format==5?0:2]=(c==2||c==3)?255:0;
            if(bpp==4) p[3]=37;
        }
    }
    const auto original=src;
    check(z1_copy_pixels(&dst[16],destination_size,destination_stride,destination_format,
        &src[16],source_size,w,h,source_stride,source_format,w,h)==0);
    check(src==original);
    for(unsigned y=0;y<h;++y) {
        for(unsigned x=0;x<w;++x) {
            const unsigned c=(x+y)%5;
            const uint8_t* p=&dst[16+y*destination_stride+4*x];
            check(p[destination_format==5?2:0]==((c==0||c==3)?255:0));
            check(p[1]==((c==1||c==3)?255:0));
            check(p[destination_format==5?0:2]==((c==2||c==3)?255:0));
            check(p[3]==((destination_format!=2 && (source_format==1||source_format==5))?37:255));
        }
        for(unsigned x=w*4;x<destination_stride;++x) check(dst[16+y*destination_stride+x]==0xcc);
    }
    for(unsigned x=0;x<16;++x) { check(dst[x]==0xcc); check(dst[16+destination_size+x]==0xcc); }
    const auto before=dst;
    check(z1_copy_pixels(&dst[16],destination_size,destination_stride,destination_format,
        &src[16],(h-1)*source_stride*bpp+w*bpp-1,w,h,source_stride,source_format,w,h)==-EINVAL);
    check(dst==before);
    check(z1_copy_pixels(&dst[16],(h-1)*destination_stride+w*4-1,destination_stride,destination_format,
        &src[16],source_size,w,h,source_stride,source_format,w,h)==-EINVAL);
    check(dst==before);
    check(z1_copy_pixels(&dst[16],destination_size,w*4-1,destination_format,
        &src[16],source_size,w,h,source_stride,source_format,w,h)==-EINVAL);
    check(dst==before);
}
int main() {
    size_t stride, size;
    check(z1_buffer_layout(240,320,4,&stride,&size)==0);
    check(stride==240 && size==307204); // Regression: never 512*320.
    check(((size+4095)&~size_t(4095))==311296);
    check(z1_buffer_layout(241,319,2,&stride,&size)==0);
    check(stride==242 && size==154884);
    check(z1_buffer_layout(-1,320,4,&stride,&size)==-EINVAL);
    check(z1_buffer_layout(INT_MAX,INT_MAX,8,&stride,&size)==-EINVAL);
    check(z1_buffer_layout(1,1,0,&stride,&size)==-EINVAL);

    colors_and_guards(1,4); colors_and_guards(2,4);
    colors_and_guards(5,4); colors_and_guards(3,3); colors_and_guards(4,2);
    for(int destination_format : {1,2,5}) {
        stock32_colors_and_guards(1,4,destination_format);
        stock32_colors_and_guards(2,4,destination_format);
        stock32_colors_and_guards(3,3,destination_format);
        stock32_colors_and_guards(4,2,destination_format);
        stock32_colors_and_guards(5,4,destination_format);
    }
    uint8_t src[8]={}, dst[8]={};
    check(z1_copy_pixels(dst,8,8,3,src,8,2,1,2,1,2,1)==-EINVAL);
    check(z1_copy_pixels(dst,8,8,1,src,8,2,1,2,0x16,2,1)==-EINVAL);
    // RGB565 intermediate levels expand bits, rather than becoming dark colors.
    const uint8_t mid565[]={0x10,0x84};
    check(z1_copy_pixels(dst,8,8,1,mid565,2,1,1,1,4,1,1)==0);
    check(dst[0]==132 && dst[1]==130 && dst[2]==132 && dst[3]==255);
    check(z1_copy_rgb565(dst,8,4,src,8,2,1,1,1,2,1)==-EINVAL);
    check(z1_copy_rgb565(dst,8,4,src,8,2,1,2,0x16,2,1)==-EINVAL);
    check(z1_copy_rgb565(dst,8,4,src,8,2,1,2,0x20,2,1)==-EINVAL);
    puts("PASS: 240x320 all five source formats to RGB565/RGBA/RGBX/BGRA, padded strides, alpha, guards, undersize rejection");
    printf("%u checks\n",checks);
}
