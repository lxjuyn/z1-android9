#include "../capacity_policy.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct Props {
    int batteryStatus, batteryLevel, batteryVoltage, batteryTemperature;
    int batteryHealth, batteryPresent, chargerUsbOnline;
};
static void put(const char* path, const char* value) {
    FILE* f=fopen(path,"w"); assert(f); fputs(value,f); assert(fclose(f)==0);
}
int main(int argc, char** argv) {
    assert(argc==2); const char* p=argv[1];
    const Props initial={3,0,3876,451,2,1,0};
    Props props=initial;
    assert(!z1_capacity_available(""));
    assert(!z1_capacity_available(nullptr));
    assert(!z1_capacity_available(p));
    z1_mark_capacity_unknown(&props,false);
    assert(props.batteryStatus==1);
    props.batteryStatus=initial.batteryStatus;
    assert(memcmp(&props,&initial,sizeof(props))==0); // all readings preserved
    const char* invalid[]={"", "-1\n", "101\n", "47junk\n", "unknown\n", "999999999999999999999999999999\n", " \n"};
    for(const char* text:invalid) { put(p,text); assert(!z1_capacity_available(p)); }
    const char* valid[]={"0\n", "100\n", "47\n", " 42 \n"};
    for(const char* text:valid) {
        put(p,text); assert(z1_capacity_available(p));
        props=initial; z1_mark_capacity_unknown(&props,true);
        assert(memcmp(&props,&initial,sizeof(props))==0); // measured 0 remains 0/DISCHARGING
    }
    unlink(p); assert(!z1_capacity_available(p));
    puts("PASS: missing/malformed capacity UNKNOWN; voltage/temp/online/presence unchanged; measured 0/47/100 accepted transparently");
}
