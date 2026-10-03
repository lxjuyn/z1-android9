// Host test calls the production FUSE attribute and permission derivation code.
#include <cassert>
#include <iostream>
#define PAGE_SIZE 4096  // Bionic supplies this for the production ARM build.
#include "../fuse.cpp"

static bool accessible(const fuse_attr& a, uid_t uid, gid_t gid, unsigned access) {
    unsigned bits = uid == a.uid ? (a.mode >> 6) : gid == a.gid ? (a.mode >> 3) : a.mode;
    return (bits & access) == access;
}

int main() {
    fuse_global global = {};
    AppIdMap packages;
    packages["app.one"] = 10001;
    packages["app.two"] = 10002;
    global.package_to_appid = &packages;
    struct fuse f = {};
    f.global = &global;
    struct stat lower = {};
    lower.st_mode = S_IFDIR | 0775;
    unsigned checks = 0;
    for (userid_t user : {0U, 10U}) {
        node root = {}; root.perm = PERM_ROOT; root.userid = user;
        node android = {}; android.name = const_cast<char*>("Android");
        derive_permissions_locked(&f, &root, &android);
        node data = {}; data.name = const_cast<char*>("data");
        derive_permissions_locked(&f, &android, &data);
        node app = {}; app.name = const_cast<char*>("app.one");
        derive_permissions_locked(&f, &data, &app);
        assert(app.uid == user * AID_USER_OFFSET + 10001); ++checks;
        assert(app.under_android); ++checks;
        for (bool normal : {false, true}) {
            global.default_normal = normal;
            for (unsigned view = 0; view < 3; ++view) {
                f.gid = view == 0 ? AID_SDCARD_RW : AID_EVERYBODY;
                f.mask = view == 1 ? 0027 : view == 2 ? 0007 : 0006;
                fuse_attr out = {};
                attr_from_stat(&f, &out, &lower, &app);
                gid_t expected = view == 0 && !normal ? AID_SDCARD_RW :
                                 user * AID_USER_OFFSET + f.gid;
                assert(out.gid == expected); ++checks;
                assert(out.uid == user * AID_USER_OFFSET + 10001); ++checks;
                mode_t mode = view == 0 ? 0771 : view == 1 ? 0750 : 0770;
                assert((out.mode & 0777) == mode); ++checks;
                assert(accessible(out, app.uid, 0, 6)); ++checks;
                assert(!accessible(out, user * AID_USER_OFFSET + 10002, 0, 6)); ++checks;
                if (normal || view != 0) {
                    // A different user's corresponding storage group cannot read/write.
                    gid_t other = (user == 0 ? 10 : 0) * AID_USER_OFFSET + f.gid;
                    assert(!accessible(out, (user == 0 ? 10 : 0) * AID_USER_OFFSET + 10001,
                                       other, 6)); ++checks;
                }
            }
        }
    }
    // Package list updates affect existing node owners through production recursion.
    node parent = {}; parent.perm = PERM_ANDROID_DATA; parent.userid = 10;
    node child = {}; child.name = const_cast<char*>("app.one"); parent.child = &child;
    packages["app.one"] = 10003;
    derive_permissions_recursive_locked(&f, &parent);
    assert(child.uid == 1010003); ++checks;
    std::cout << "production permissions matrix: " << checks << " checks passed\n";
}
