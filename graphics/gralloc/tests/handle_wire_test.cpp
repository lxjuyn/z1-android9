// Host stubs supply only the native_handle header and module type. The actual
// production private_handle_t constructor, validation and layout are exercised.
#include <stdio.h>
#define ALOGE(...) ((void)0)
#include "../gralloc_priv.h"
#include <assert.h>
#include <string.h>
int main() {
    private_handle_t original(7,311296,0);
    original.width=240; original.height=320; original.stride=240; original.format=1;
    private_handle_t imported(8,4096,0);
    memcpy(static_cast<void*>(&imported),&original,sizeof(original)); // native_handle integer transport
    imported.fd=8; imported.base=0; imported.pid=0; // process local state reset
    assert(private_handle_t::validate(&imported)==0);
    assert(imported.numFds==1 && imported.numInts==12);
    assert(imported.width==240 && imported.height==320 && imported.stride==240);
    assert(imported.format==1 && imported.size==311296 && imported.reserved==0);
    imported.numInts-=4;
    assert(private_handle_t::validate(&imported)==-EINVAL); // reject previous ABI
    puts("PASS: production handle 64-byte wire layout, metadata round trip, old ABI rejected");
}
