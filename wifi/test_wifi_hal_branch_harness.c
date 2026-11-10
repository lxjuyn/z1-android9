/* Execute the actual three ARM functions, not a hand-translated model.
 * Minimal ELF PT_LOAD mapping is NOT a full Android dynamic linker: only
 * firmware table pointers are relocated; two PLT slots get controlled stubs.
 * get_fw_path, IsFirmwareModeChangeNeeded and ChangeFirmwareMode execute
 * unchanged bytes from the input (except the candidate's 24-byte patch).
 * No Android services, hardware, network, firmware writes or dlopen.
 */
#include <elf.h>
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define IMAGE_SIZE 0x20000u
static int fw_result, fw_calls;
static const char *last_path;

static int firmware_stub(const char *path) {
    ++fw_calls;
    last_path = path;
    return fw_result;
}

/* Existing caller uses Thumb BLX immediate into ARM PLT. ARM trampoline BX
 * switches state based on the target pointer's Thumb bit. No callee-save
 * registers, stack alignment or return address are changed. */
static void jump_stub(unsigned char *address, uintptr_t target) {
    uint32_t stub[] = {0xe59fc000u, 0xe12fff1cu, (uint32_t)target};
    memcpy(address, stub, sizeof(stub));
}

static int parse_int(const char *text, int32_t *value) {
    char *end;
    errno = 0;
    long parsed = strtol(text, &end, 0);
    if (errno || *end || end == text || parsed < INT32_MIN || parsed > INT32_MAX) return 0;
    *value = (int32_t)parsed;
    return 1;
}

int main(int argc, char **argv) {
    int32_t mode, result;
    if (argc != 4 || !parse_int(argv[2], &mode) || !parse_int(argv[3], &result)) return 2;
    FILE *fp = fopen(argv[1], "rb");
    if (!fp) return 2;
    unsigned char file[95116];
    if (fread(file, 1, sizeof(file), fp) != sizeof(file) || fgetc(fp) != EOF) {
        fclose(fp); return 2;
    }
    fclose(fp);
    Elf32_Ehdr eh;
    memcpy(&eh, file, sizeof(eh));
    if (memcmp(eh.e_ident, "\177ELF\1\1\1", 7) || eh.e_machine != EM_ARM ||
        eh.e_type != ET_DYN || eh.e_phentsize != sizeof(Elf32_Phdr) ||
        (uint64_t)eh.e_phoff + (uint64_t)eh.e_phnum * eh.e_phentsize > sizeof(file)) return 2;
    unsigned char *image = mmap(NULL, IMAGE_SIZE, PROT_READ | PROT_WRITE | PROT_EXEC,
                               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (image == MAP_FAILED) return 2;
    for (unsigned i = 0; i < eh.e_phnum; ++i) {
        Elf32_Phdr ph;
        memcpy(&ph, file + eh.e_phoff + i * eh.e_phentsize, sizeof(ph));
        if (ph.p_type != PT_LOAD) continue;
        if (ph.p_filesz > ph.p_memsz || (uint64_t)ph.p_offset + ph.p_filesz > sizeof(file) ||
            (uint64_t)ph.p_vaddr + ph.p_memsz > IMAGE_SIZE) return 2;
        memcpy(image + ph.p_vaddr, file + ph.p_offset, ph.p_filesz);
    }
    uint32_t *table = (uint32_t *)(image + 0x16aa4);
    if (table[0] != 0x14baa || table[1] != 0x14bca || table[2] != 0) return 2;
    for (unsigned i = 0; i < 3; ++i) {
        if (table[i]) table[i] += (uint32_t)(uintptr_t)image;
    }
    jump_stub(image + 0x4bf0, (uintptr_t)(image + 0xdf39));
    jump_stub(image + 0x4c74, (uintptr_t)&firmware_stub);
    __builtin___clear_cache((char *)image, (char *)image + IMAGE_SIZE);

    const char *(*get_path)(int) = (const char *(*)(int))(image + 0xdf39);
    int (*is_needed)(void *, int) = (int (*)(void *, int))(image + 0x5547);
    /* int return deliberately observes all r0 bits: E006 used to leak the
     * non-NULL path pointer instead of canonical C++ bool 1. */
    int (*change)(void *, int) = (int (*)(void *, int))(image + 0x5557);
    const char *path = get_path(mode);
    int needed = is_needed(NULL, mode);
    fw_result = result;
    int changed = change(NULL, mode);
    uintptr_t offset = path ? (uintptr_t)path - (uintptr_t)image : 0;
    uintptr_t passed = last_path ? (uintptr_t)last_path - (uintptr_t)image : 0;
    printf("mode=%" PRId32 " path=%" PRIuPTR " needed=%d result=%d calls=%d passed=%" PRIuPTR "\n",
           mode, offset, needed, changed, fw_calls, passed);
    munmap(image, IMAGE_SIZE);
    return 0;
}
