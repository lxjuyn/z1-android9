/* Android API 28 / ARMv7, EGL pbuffer only. No windows, swaps or device ioctls.
 * Historical starting point (read-only): lima_offscreen_smoke_20260717.
 * RGBA rows start at the GL lower left. Hardware attribution is a separate gate.
 */
#define _POSIX_C_SOURCE 200809L
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_DIM 512
#define MAX_FRAMES 16
#define MAX_PIXEL_FRAMES (4u * 1024u * 1024u)
static FILE *log_file, *manifest;
static const char *out_dir;
static int frame_index;
static GLuint program, texture_program, texture;
static GLint color_uniform, texture_uniform;
static void note(const char *fmt, ...) __attribute__((format(printf,1,2)));
static void note(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);
    if (log_file) { va_start(ap, fmt); vfprintf(log_file, fmt, ap); va_end(ap); fflush(log_file); }
    fflush(stdout);
}
static void die(const char *message) {
    note("PROBE_STATUS=FAIL reason=%s errno=%d\n", message, errno);
    exit(1);
}
static void path_for(char *path, size_t size, const char *name) {
    int n = snprintf(path, size, "%s/%s", out_dir, name);
    if (n < 0 || (size_t)n >= size) die("output path too long");
}
static FILE *new_file(const char *name) {
    char path[1200]; path_for(path, sizeof(path), name);
    FILE *file = fopen(path, "wx");
    if (!file) die("create output file");
    return file;
}
static void close_file(FILE *file) {
    if (fflush(file) || ferror(file) || fclose(file)) die("flush/close output");
}
static void check_gl(const char *where) {
    GLenum error; int failed = 0;
    while ((error = glGetError()) != GL_NO_ERROR) {
        note("GL_ERROR stage=%s code=0x%04x\n", where, error);
        if (++failed >= 32) die("GL error-drain bound");
    }
    if (failed) die(where);
}
static void check_egl(const char *where, int okay) {
    EGLint error = eglGetError();
    if (!okay || error != EGL_SUCCESS) {
        note("EGL_ERROR stage=%s code=0x%04x return_ok=%d\n", where, error, okay);
        die(where);
    }
}
static void save_maps(const char *name) {
    FILE *in = fopen("/proc/self/maps", "r");
    if (!in) die("read /proc/self/maps");
    FILE *out = new_file(name); char block[8192]; size_t n;
    while ((n = fread(block, 1, sizeof(block), in)) != 0)
        if (fwrite(block, 1, n, out) != n) die("write process maps");
    if (ferror(in) || fclose(in)) die("read/close process maps");
    close_file(out); note("MAPS_FILE=%s\n", name);
}
static void egl_string(EGLDisplay dpy, EGLint field, const char *key) {
    const char *value = eglQueryString(dpy, field);
    check_egl(key, value != NULL); note("%s=%s\n", key, value);
}
static void gl_string(GLenum field, const char *key) {
    const GLubyte *value = glGetString(field); check_gl(key);
    if (!value) die(key);
    note("%s=%s\n", key, (const char *)value);
}
static GLuint shader(GLenum type, const char *source) {
    GLuint id = glCreateShader(type); check_gl("glCreateShader");
    if (!id) die("shader name zero");
    glShaderSource(id, 1, &source, NULL); check_gl("glShaderSource");
    glCompileShader(id); check_gl("glCompileShader");
    GLint status = 0, length = 0;
    glGetShaderiv(id, GL_COMPILE_STATUS, &status);
    glGetShaderiv(id, GL_INFO_LOG_LENGTH, &length); check_gl("shader status");
    if (length > 1) {
        if (length > 1024 * 1024) die("shader log bound");
        char *text = calloc((size_t)length + 1, 1); if (!text) die("shader log allocation");
        glGetShaderInfoLog(id, length, NULL, text); check_gl("shader log");
        note("SHADER_LOG=%s\n", text); free(text);
    }
    note("SHADER_COMPILE type=0x%x status=%d\n", type, status);
    if (!status) die("shader compile");
    return id;
}
static GLuint link_program(const char *fs) {
    const char *vs = "attribute vec3 a_pos; attribute vec4 a_color; attribute vec2 a_uv;"
        "varying mediump vec4 v_color; varying mediump vec2 v_uv;"
        "void main(){gl_Position=vec4(a_pos,1.0); v_color=a_color; v_uv=a_uv;}";
    GLuint vert = shader(GL_VERTEX_SHADER, vs), frag = shader(GL_FRAGMENT_SHADER, fs);
    GLuint id = glCreateProgram(); check_gl("glCreateProgram"); if (!id) die("program name zero");
    glAttachShader(id, vert); glAttachShader(id, frag);
    glBindAttribLocation(id, 0, "a_pos"); glBindAttribLocation(id, 1, "a_color");
    glBindAttribLocation(id, 2, "a_uv"); glLinkProgram(id); check_gl("glLinkProgram");
    GLint status = 0, length = 0;
    glGetProgramiv(id, GL_LINK_STATUS, &status);
    glGetProgramiv(id, GL_INFO_LOG_LENGTH, &length); check_gl("program status");
    if (length > 1) {
        if (length > 1024 * 1024) die("program log bound");
        char *text = calloc((size_t)length + 1, 1); if (!text) die("program log allocation");
        glGetProgramInfoLog(id, length, NULL, text); check_gl("program log");
        note("PROGRAM_LOG=%s\n", text); free(text);
    }
    note("PROGRAM_LINK status=%d\n", status); if (!status) die("program link");
    glDeleteShader(vert); glDeleteShader(frag); check_gl("delete shaders");
    return id;
}
static void init_gl(void) {
    program = link_program("precision mediump float; varying mediump vec4 v_color;"
        "uniform vec4 u_color; void main(){gl_FragColor=v_color*u_color;}");
    texture_program = link_program("precision mediump float; varying mediump vec2 v_uv;"
        "uniform sampler2D u_tex; void main(){gl_FragColor=texture2D(u_tex,v_uv);}");
    color_uniform = glGetUniformLocation(program, "u_color");
    texture_uniform = glGetUniformLocation(texture_program, "u_tex"); check_gl("uniform locations");
    if (color_uniform < 0 || texture_uniform < 0) die("inactive required uniform");
    glGenTextures(1, &texture); glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_PACK_ALIGNMENT, 1); glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    check_gl("texture and pack state");
}
static void rotate_color(GLfloat *dst, const GLfloat *src) {
    for (int k = 0; k < 3; ++k) dst[k] = src[(k + frame_index % 3) % 3];
    dst[3] = src[3];
}
static void constant_color(const GLfloat *color) {
    GLfloat rotated[4]; rotate_color(rotated, color);
    glUseProgram(program); glUniform4fv(color_uniform, 1, rotated);
    glDisableVertexAttribArray(1); glVertexAttrib4f(1, 1, 1, 1, 1);
    glDisableVertexAttribArray(2); check_gl("constant color state");
}
static void rect(int w, int h, int x0, int y0, int x1, int y1, GLfloat z) {
    GLfloat a = 2.0f * (GLfloat)x0 / (GLfloat)w - 1.0f;
    GLfloat b = 2.0f * (GLfloat)x1 / (GLfloat)w - 1.0f;
    GLfloat c = 2.0f * (GLfloat)y0 / (GLfloat)h - 1.0f;
    GLfloat d = 2.0f * (GLfloat)y1 / (GLfloat)h - 1.0f;
    GLfloat vertices[] = {a,c,z, b,c,z, a,d,z, b,d,z};
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 0, vertices);
    glEnableVertexAttribArray(0); glDrawArrays(GL_TRIANGLE_STRIP, 0, 4); check_gl("rectangle draw");
}
static void draw_case(int w, int h, const char *name) {
    glViewport(0, 0, w, h); glDisable(GL_DITHER); glDisable(GL_BLEND);
    glDisable(GL_DEPTH_TEST); glDisable(GL_SCISSOR_TEST); glDisable(GL_CULL_FACE);
    glDisable(GL_STENCIL_TEST); glDisable(GL_SAMPLE_ALPHA_TO_COVERAGE); glDisable(GL_SAMPLE_COVERAGE);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE); glDepthMask(GL_TRUE);
    GLfloat base[] = {17.0f/255, 34.0f/255, 51.0f/255, 1}, clear[4]; rotate_color(clear, base);
    glClearColor(clear[0], clear[1], clear[2], clear[3]); glClearDepthf(1);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT); check_gl("case reset/clear");
    if (!strcmp(name, "clear")) return;
    if (!strcmp(name, "triangle")) {
        /* Integer window vertices: reference tests every pixel center. */
        int xy[] = {w/8, h/8, 7*w/8, h/4, w/3, 7*h/8};
        GLfloat vertices[9], colors[12];
        for (int i=0; i<3; ++i) {
            vertices[3*i] = 2.0f*(GLfloat)xy[2*i]/(GLfloat)w-1;
            vertices[3*i+1] = 2.0f*(GLfloat)xy[2*i+1]/(GLfloat)h-1; vertices[3*i+2]=0;
            GLfloat col[] = {0,0,0,1}; col[i]=1; rotate_color(&colors[4*i], col);
        }
        glUseProgram(program); glUniform4f(color_uniform, 1,1,1,1);
        glVertexAttribPointer(0,3,GL_FLOAT,GL_FALSE,0,vertices); glEnableVertexAttribArray(0);
        glVertexAttribPointer(1,4,GL_FLOAT,GL_FALSE,0,colors); glEnableVertexAttribArray(1);
        glDisableVertexAttribArray(2); glDrawArrays(GL_TRIANGLES,0,3); check_gl("triangle draw");
    } else if (!strcmp(name, "texture")) {
        const unsigned char source[] = {240,32,16,255, 24,224,48,192, 40,64,208,128, 232,192,56,64};
        unsigned char pixels[16];
        for(int i=0;i<4;++i) { for(int k=0;k<3;++k) pixels[4*i+k]=source[4*i+(k+frame_index%3)%3]; pixels[4*i+3]=source[4*i+3]; }
        glUseProgram(texture_program); glUniform1i(texture_uniform,0);
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D,texture);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,2,2,0,GL_RGBA,GL_UNSIGNED_BYTE,pixels);
        glDisableVertexAttribArray(1); glDisableVertexAttribArray(2); check_gl("texture upload");
        /* Fixed texel centers avoid odd-size seam ambiguity; all four texels and alpha checked. */
        for(int y=0;y<2;++y) for(int x=0;x<2;++x) {
            glVertexAttrib2f(2,x?0.75f:0.25f,y?0.75f:0.25f);
            rect(w,h,x?w/2:0,y?h/2:0,x?w:w/2,y?h:h/2,0);
        }
    } else if (!strcmp(name, "blend")) {
        GLfloat dst[]={32.0f/255,64.0f/255,96.0f/255,1}, src[]={192.0f/255,64.0f/255,128.0f/255,0.5f};
        constant_color(dst); rect(w,h,0,0,w,h,0);
        glEnable(GL_BLEND); glBlendEquationSeparate(GL_FUNC_ADD,GL_FUNC_ADD);
        glBlendFuncSeparate(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA,GL_ONE,GL_ONE_MINUS_SRC_ALPHA);
        constant_color(src); rect(w,h,0,0,w,h,0);
    } else if (!strcmp(name, "depth")) {
        GLfloat blue[]={24.0f/255,48.0f/255,216.0f/255,1}, green[]={32.0f/255,208.0f/255,64.0f/255,1}, red[]={224.0f/255,16.0f/255,32.0f/255,1};
        glEnable(GL_DEPTH_TEST); glDepthFunc(GL_LESS);
        constant_color(blue); rect(w,h,0,0,w,h,0.5f);
        constant_color(green); rect(w,h,w/4,h/4,3*w/4,3*h/4,-0.5f);
        constant_color(red); rect(w,h,0,0,w,h,0.75f);
    } else die("unknown render case");
    check_gl("draw case end");
}
static void run_size(EGLDisplay dpy, EGLConfig config, int w, int h, int frames) {
    EGLint pa[]={EGL_WIDTH,w,EGL_HEIGHT,h,EGL_LARGEST_PBUFFER,EGL_FALSE,EGL_NONE};
    EGLSurface surface=eglCreatePbufferSurface(dpy,config,pa); check_egl("eglCreatePbufferSurface",surface!=EGL_NO_SURFACE);
    EGLint cw=0,ch=0; check_egl("query width",eglQuerySurface(dpy,surface,EGL_WIDTH,&cw));
    check_egl("query height",eglQuerySurface(dpy,surface,EGL_HEIGHT,&ch));
    if(cw!=w || ch!=h) die("unexpected pbuffer size");
    EGLint ca[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};
    EGLContext context=eglCreateContext(dpy,config,EGL_NO_CONTEXT,ca); check_egl("eglCreateContext",context!=EGL_NO_CONTEXT);
    check_egl("eglMakeCurrent",eglMakeCurrent(dpy,surface,surface,context));
    check_gl("initial context errors"); note("SIZE=%dx%d frames=%d\n",w,h,frames);
    gl_string(GL_VENDOR,"GL_VENDOR"); gl_string(GL_RENDERER,"GL_RENDERER");
    gl_string(GL_VERSION,"GL_VERSION"); gl_string(GL_SHADING_LANGUAGE_VERSION,"GL_SHADING_LANGUAGE_VERSION");
    gl_string(GL_EXTENSIONS,"GL_EXTENSIONS");
    const GLenum bits[]={GL_RED_BITS,GL_GREEN_BITS,GL_BLUE_BITS,GL_ALPHA_BITS,GL_DEPTH_BITS,GL_SAMPLES}; GLint values[6];
    for(int i=0;i<6;++i) {glGetIntegerv(bits[i],&values[i]); check_gl("framebuffer bits");}
    note("BUFFER_BITS=%d,%d,%d,%d depth=%d samples=%d\n",values[0],values[1],values[2],values[3],values[4],values[5]);
    if(values[0]!=8 || values[1]!=8 || values[2]!=8 || values[3]!=8 || values[4]<16 || values[5]!=0) die("requires RGBA8 depth16+ single sample");
    char maps_name[80]; snprintf(maps_name,sizeof(maps_name),"maps_%dx%d_current.txt",w,h); save_maps(maps_name);
    init_gl();
    size_t size=(size_t)w*(size_t)h*4; unsigned char *pixels=malloc(size); if(!pixels) die("pixels allocation");
    const char *cases[]={"clear","triangle","texture","blend","depth"};
    for(frame_index=0;frame_index<frames;++frame_index) for(int i=0;i<5;++i) {
        draw_case(w,h,cases[i]); glFinish(); check_gl("glFinish");
        memset(pixels,0xa5,size); glReadPixels(0,0,w,h,GL_RGBA,GL_UNSIGNED_BYTE,pixels); check_gl("glReadPixels");
        char name[128]; snprintf(name,sizeof(name),"%dx%d_f%02d_%s.rgba",w,h,frame_index,cases[i]);
        FILE *file=new_file(name); if(fwrite(pixels,1,size,file)!=size) die("write RGBA"); close_file(file);
        if(fprintf(manifest,"%s\t%d\t%d\t%d\t%s\n",name,w,h,frame_index,cases[i])<0 || fflush(manifest)) die("write manifest");
        note("READBACK file=%s bytes=%zu\n",name,size);
    }
    snprintf(maps_name,sizeof(maps_name),"maps_%dx%d_after_readback.txt",w,h); save_maps(maps_name);
    free(pixels); glDeleteTextures(1,&texture); glDeleteProgram(program); glDeleteProgram(texture_program); check_gl("GL cleanup");
    check_egl("unbind",eglMakeCurrent(dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT));
    check_egl("destroy context",eglDestroyContext(dpy,context)); check_egl("destroy pbuffer",eglDestroySurface(dpy,surface));
}
static int integer(const char *text,int low,int high) {
    char *end=NULL; errno=0; long n=strtol(text,&end,10);
    if(errno || !*text || *end || n<low || n>high) {fprintf(stderr,"Invalid integer: %s (allowed %d..%d)\n",text,low,high); exit(2);} return (int)n;
}
int main(int argc,char **argv) {
    int frames=2,w=0,h=0;
    for(int i=1;i<argc;++i) {
        if(!strcmp(argv[i],"--help")) {
            puts("Usage: gles2probe --out NEW_DIRECTORY [--frames 1..16] [--width 8..512 --height 8..512]\n"
                 "Default suite: 63x63,64x64,65x65,240x320; five cases each frame.\n"
                 "PBUFFER ONLY; no window/swap/fb0/master operations. Maximum 4M pixel-frames.\n"
                 "Writes run.log, manifest.tsv, RGBA (bottom-up), process maps. Existing directory refused.\n"
                 "Exit 0 means API/readback completed, NOT pixel PASS or hardware proof.\n"
                 "Run analyze.py on host. Renderer text and LD_LIBRARY_PATH do not prove selected driver.\n"
                 "Host software validation is NOT board validation. External timeout still needed for driver hangs."); return 0;
        }
        if(i+1>=argc) {fprintf(stderr,"Missing value: %s\n",argv[i]);return 2;}
        if(!strcmp(argv[i],"--out")) out_dir=argv[++i];
        else if(!strcmp(argv[i],"--frames")) frames=integer(argv[++i],1,MAX_FRAMES);
        else if(!strcmp(argv[i],"--width")) w=integer(argv[++i],8,MAX_DIM);
        else if(!strcmp(argv[i],"--height")) h=integer(argv[++i],8,MAX_DIM);
        else {fprintf(stderr,"Unknown option: %s\n",argv[i]);return 2;}
    }
    if(!out_dir || !*out_dir || strlen(out_dir)>1000 || (!!w!=!!h)) {fprintf(stderr,"Need --out and paired width/height; see --help\n");return 2;}
    const int sizes[][2]={{63,63},{64,64},{65,65},{240,320}};
    size_t pixel_frames=w?(size_t)w*(size_t)h:63u*63u+64u*64u+65u*65u+240u*320u;
    if(pixel_frames*(size_t)frames>MAX_PIXEL_FRAMES) {fprintf(stderr,"Pixel-frame budget exceeded\n");return 2;}
    if(mkdir(out_dir,0700)) {perror("mkdir output (must be new)");return 2;}
    log_file=new_file("run.log"); manifest=new_file("manifest.tsv");
    note("SCHEMA=z1-gles2-pbuffer-v1\nPROBE_STATUS=STARTED\nPID=%ld\nFRAMES=%d\nPIXEL_ORIGIN=bottom-left\n",(long)getpid(),frames);
#ifdef __ANDROID__
    note("BUILD_TARGET=android-api28-armv7\n");
#else
    note("BUILD_TARGET=host-reference-NOT-BOARD\n");
#endif
    note("HARDWARE_ATTRIBUTION=UNPROVEN\nLIB_SELECTION=platform EGL wrapper; environment does not guarantee driver selection\n");
    note("REQUESTED_SIZES=");
    if(w) note("%dx%d\n",w,h); else note("63x63,64x64,65x65,240x320\n");
    save_maps("maps_before_egl.txt");
    EGLDisplay dpy=eglGetDisplay(EGL_DEFAULT_DISPLAY); check_egl("eglGetDisplay",dpy!=EGL_NO_DISPLAY);
    EGLint major=0,minor=0; check_egl("eglInitialize",eglInitialize(dpy,&major,&minor));
    note("EGL_INITIALIZED=%d.%d\n",major,minor);
    egl_string(dpy,EGL_VENDOR,"EGL_VENDOR"); egl_string(dpy,EGL_VERSION,"EGL_VERSION");
    egl_string(dpy,EGL_EXTENSIONS,"EGL_EXTENSIONS"); egl_string(dpy,EGL_CLIENT_APIS,"EGL_CLIENT_APIS");
    check_egl("eglBindAPI",eglBindAPI(EGL_OPENGL_ES_API));
    EGLint attrs[]={EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_ALPHA_SIZE,8,EGL_DEPTH_SIZE,16,EGL_SAMPLE_BUFFERS,0,EGL_NONE};
    EGLConfig configs[128],config=NULL; EGLint count=0;
    check_egl("eglChooseConfig",eglChooseConfig(dpy,attrs,configs,128,&count));
    for(int i=0;i<count;++i) {
        EGLint r,g,b,a,s; check_egl("config red",eglGetConfigAttrib(dpy,configs[i],EGL_RED_SIZE,&r));
        check_egl("config green",eglGetConfigAttrib(dpy,configs[i],EGL_GREEN_SIZE,&g));
        check_egl("config blue",eglGetConfigAttrib(dpy,configs[i],EGL_BLUE_SIZE,&b));
        check_egl("config alpha",eglGetConfigAttrib(dpy,configs[i],EGL_ALPHA_SIZE,&a));
        check_egl("config samples",eglGetConfigAttrib(dpy,configs[i],EGL_SAMPLES,&s));
        if(r==8 && g==8 && b==8 && a==8 && s==0) {config=configs[i];break;}
    }
    if(!config) die("no single-sample RGBA8/depth pbuffer config");
    if(w) run_size(dpy,config,w,h,frames);
    else for(int i=0;i<4;++i) run_size(dpy,config,sizes[i][0],sizes[i][1],frames);
    check_egl("eglTerminate",eglTerminate(dpy)); check_egl("eglReleaseThread",eglReleaseThread());
    close_file(manifest); manifest=NULL;
    note("API_ERRORS=0\nPROBE_STATUS=READBACK_COMPLETE\nPIXEL_VERIFICATION=PENDING_HOST_ANALYZER\nHARDWARE_ATTRIBUTION=UNPROVEN\n");
    close_file(log_file); log_file=NULL; return 0;
}
