/* Template consumed by test_fence.py. Production bodies/types are inserted
 * verbatim; only external dependencies are mocked. Never touches a device. */
#include <algorithm>
#include <cerrno>
#include <climits>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>
#include <map>
#include <poll.h>
#include <pthread.h>
#include <stdexcept>
#include <string>
#include <time.h>
#include <unistd.h>
#include <vector>

using hwc2_display_t = uint64_t;
using hwc2_layer_t = uint64_t;
using buffer_handle_t = const void *;
using hwc2_callback_data_t = void *;
using hwc2_function_pointer_t = void (*)();
struct hwc2_device_t {};
struct hwc_rect_t { int left, top, right, bottom; };
struct hwc_frect_t { float left, top, right, bottom; };
struct drmModeModeInfo { int unused; };
struct drmModeAtomicReq { int unused; };
constexpr uint32_t DRM_MODE_ATOMIC_ALLOW_MODESET = 0x400;
constexpr uint32_t DRM_FORMAT_ABGR8888 = 1, DRM_FORMAT_ARGB8888 = 2;
/* PRODUCTION_TYPES */

#define CHECK(x) do { if (!(x)) throw std::runtime_error( \
    std::string(__func__) + ":" + std::to_string(__LINE__) + ": " #x); } while (0)
struct PollStep { int ret; short events; int error; int64_t elapsed_ns; };
static std::vector<PollStep> steps;
static std::vector<int> timeouts, polled_fds, closed_fds;
static size_t step_index;
static int64_t clock_ns;
static int clock_error;
static bool real_syscalls;
static int allocations, commits, imports, copies, small_copies, plane_calls;
static int timeline_calls, fence_calls, removed_fbs, import_result, copy_result;
static bool zero_fb;
static int g_verbose;
static drmModeAtomicReq request;
static int mock_clock_gettime(clockid_t id, timespec *ts) {
    if (real_syscalls) return ::clock_gettime(id, ts);
    CHECK(id == CLOCK_MONOTONIC);
    if (clock_error) { errno = clock_error; return -1; }
    ts->tv_sec = clock_ns / 1000000000;
    ts->tv_nsec = clock_ns % 1000000000;
    return 0;
}
static int mock_poll(pollfd *pfd, nfds_t n, int timeout) {
    if (real_syscalls) return ::poll(pfd, n, timeout);
    CHECK(n == 1);
    CHECK(pfd->events == POLLIN);
    timeouts.push_back(timeout);
    polled_fds.push_back(pfd->fd);
    // Exhausted scripts are ordinary full timeouts, not successful wakeups.
    PollStep step = step_index < steps.size() ? steps[step_index++] :
        PollStep{0, 0, 0, int64_t(std::max(timeout, 0)) * 1000000};
    clock_ns += step.elapsed_ns;
    pfd->revents = step.events;
    errno = step.error;
    return step.ret;
}
static int mock_close(int fd) { closed_fds.push_back(fd); return 0; }
static z1_hwc2 *H(hwc2_device_t *d) { return static_cast<z1_hwc2 *>(d); }
static void klogf(int, const char *, ...) {}
static const char *hal_fmt_name(int) { return "mock"; }
static void fb_forget(z1_hwc2 *, z1_fb *fb) { *fb = {}; }
static int fb_import(z1_hwc2 *, buffer_handle_t handle, z1_fb *fb) {
    ++imports;
    if (import_result) return import_result;
    fb->handle = handle;
    fb->src_fd = static_cast<const z1_handle *>(handle)->fd;
    fb->fb_id = zero_fb ? 0 : 31;
    return 0;
}
static int copy_into_dumb(z1_hwc2 *, z1_dumb *d, buffer_handle_t) {
    ++copies;
    d->fb_id = zero_fb ? 0 : 32;
    return copy_result;
}
static int small_layer_sync(z1_hwc2 *, z1_layer *l) {
    ++small_copies;
    l->small.fb_id = 33;
    return 0;
}
static drmModeAtomicReq *drmModeAtomicAlloc() { ++allocations; return &request; }
static void drmModeAtomicFree(drmModeAtomicReq *) {}
static int drmModeAtomicCommit(int, drmModeAtomicReq *, uint32_t, void *) { ++commits; return 0; }
static int drmModeDestroyPropertyBlob(int, uint32_t) { return 0; }
static int drmModeCreatePropertyBlob(int, const void *, size_t, uint32_t *b) { *b = 1; return 0; }
static bool add_prop(drmModeAtomicReq *, const char *, uint32_t, uint32_t, uint64_t) { return true; }
static void program_plane(z1_hwc2 *, drmModeAtomicReq *, int, uint32_t, int, int,
                          int, int, const hwc_frect_t *, float, int32_t, bool) { ++plane_calls; }
static int drmModeRmFB(int, uint32_t) { ++removed_fbs; return 0; }
static int drmCloseBufferHandle(int, uint32_t) { return 0; }
static int sw_timeline_inc(int, uint32_t) { ++timeline_calls; return 0; }
static int sw_fence_create(int, const char *, uint32_t) { ++fence_calls; return 99; }
#define poll mock_poll
#define clock_gettime mock_clock_gettime
#define close mock_close
/* PRODUCTION_FUNCTIONS */
#undef close
#undef clock_gettime
#undef poll

static void reset() {
    steps.clear(); timeouts.clear(); polled_fds.clear(); closed_fds.clear();
    step_index = 0; clock_ns = 1000000000; clock_error = 0; real_syscalls = false;
    allocations = commits = imports = copies = small_copies = plane_calls = 0;
    timeline_calls = fence_calls = removed_fbs = 0;
    import_result = copy_result = 0; zero_fb = false; g_verbose = 0;
}
static z1_handle handle = {0, 1, 12, 81, Z1_HANDLE_MAGIC, 0, 4096, 0,
                           0, 0, 32, 32, 32, 1, 0};
static z1_hwc2 display() {
    z1_hwc2 h{};
    h.powered_on = true; h.validated = true; h.modeset_done = true;
    h.plane_count = 4; h.width = 240; h.height = 320;
    h.client_acquire_fence = -1; h.present_tl = -1;
    h.last_present_ms = 17; h.timeline_val = 8; h.present_val = 9;
    h.fences_pending = true; h.present_pending = true;
    h.pending_release.push_back({71, 72});
    return h;
}
static void layer(z1_hwc2 &h, int id, int fd, bool small = true) {
    z1_layer l{};
    l.id = id; l.z = id; l.composition = HWC2_COMPOSITION_DEVICE;
    l.acquire_fence = fd; l.release_fence = -1; l.use_small = small;
    l.handle = &handle; l.frame = {0, 0, 32, 32}; l.alpha = 1;
    h.layers[id] = l;
}
static void no_present_side_effects(const z1_hwc2 &h, int fence) {
    CHECK(fence == -1); CHECK(commits == 0); CHECK(allocations == 0);
    CHECK(small_copies == 0); CHECK(plane_calls == 0);
    CHECK(timeline_calls == 0); CHECK(fence_calls == 0); CHECK(removed_fbs == 0);
    CHECK(h.timeline_val == 8); CHECK(h.present_val == 9);
    CHECK(h.fences_pending); CHECK(h.present_pending);
    CHECK(h.pending_release.size() == 1); CHECK(h.last_present_ms == 17);
    CHECK(h.validated);
}
static void absent_fence() { CHECK(wait_fence(-1, 100) == 0); CHECK(timeouts.empty()); }
static void timeout_budget() {
    CHECK(wait_fence(10, 100) == -ETIME);
    CHECK(timeouts == std::vector<int>{100}); CHECK(clock_ns == 1100000000);
}
static void signaled() {
    steps = {{1, POLLIN, 0, 0}};
    CHECK(wait_fence(10, 100) == 0); CHECK(timeouts == std::vector<int>{100});
}
static void interrupted_success() {
    steps = {{-1, 0, EINTR, 30000000}, {-1, 0, EINTR, 40000000},
             {1, POLLIN, 0, 5000000}};
    CHECK(wait_fence(10, 100) == 0);
    CHECK(timeouts == (std::vector<int>{100, 70, 30}));
}
static void interrupted_timeout() {
    steps = {{-1, 0, EINTR, 40000000}, {0, 0, 0, 60000000}};
    CHECK(wait_fence(10, 100) == -ETIME);
    CHECK(timeouts == (std::vector<int>{100, 60})); CHECK(clock_ns == 1100000000);
}
static void interrupted_at_deadline() {
    steps = {{-1, 0, EINTR, 100000000}};
    CHECK(wait_fence(10, 100) == -ETIME); CHECK(timeouts.size() == 1);
}
static void many_interruptions() {
    for (int i = 0; i < 4; ++i) steps.push_back({-1, 0, EINTR, 10000000});
    steps.push_back({1, POLLIN, 0, 0});
    CHECK(wait_fence(10, 100) == 0);
    CHECK(timeouts == (std::vector<int>{100, 90, 80, 70, 60}));
}
static void submillisecond_budget() {
    steps = {{-1, 0, EINTR, 99500000}, {1, POLLIN, 0, 0}};
    CHECK(wait_fence(10, 100) == 0); CHECK(timeouts == (std::vector<int>{100, 1}));
}
static void poll_syscall_error() {
    steps = {{-1, 0, EINVAL, 0}};
    CHECK(wait_fence(10, 100) == -EINVAL); CHECK(timeouts.size() == 1);
}
static void pollnval() { steps = {{1, POLLNVAL, 0, 0}}; CHECK(wait_fence(10, 100) == -EBADF); }
static void pollerr() { steps = {{1, POLLERR, 0, 0}}; CHECK(wait_fence(10, 100) == -EIO); }
static void pollhup() { steps = {{1, POLLHUP, 0, 0}}; CHECK(wait_fence(10, 100) == -EIO); }
static void readable_error() {
    for (short event : {POLLERR, POLLHUP, POLLNVAL}) {
        steps = {{1, short(POLLIN | event), 0, 0}}; step_index = 0;
        CHECK(wait_fence(10, 100) == (event == POLLNVAL ? -EBADF : -EIO));
    }
}
static void unexpected_event() { steps = {{1, POLLPRI, 0, 0}}; CHECK(wait_fence(10, 100) == -EIO); }
static void zero_timeout_success() {
    steps = {{1, POLLIN, 0, 0}}; CHECK(wait_fence(10, 0) == 0);
    CHECK(timeouts == std::vector<int>{0});
}
static void zero_timeout_failure() {
    CHECK(wait_fence(10, 0) == -ETIME); CHECK(timeouts == std::vector<int>{0});
}
static void negative_timeout() {
    CHECK(wait_fence(10, -1) == -EINVAL); CHECK(timeouts.empty());
}
static void clock_failure() {
    clock_error = EFAULT; CHECK(wait_fence(10, 100) == -EFAULT); CHECK(timeouts.empty());
}
static void large_budget() {
    steps = {{-1, 0, EINTR, 1000000}, {1, POLLIN, 0, 0}};
    CHECK(wait_fence(10, INT_MAX) == 0);
    CHECK(timeouts == (std::vector<int>{INT_MAX, INT_MAX - 1}));
}
static void present_layer_timeout() {
    auto h = display(); layer(h, 1, 10); layer(h, 2, 11);
    h.client_expected = true; h.client_target = &handle; h.client_acquire_fence = 12;
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence);
    CHECK(imports == 0); CHECK(copies == 0); CHECK(closed_fds.empty());
    CHECK(h.layers[1].acquire_fence == 10); CHECK(h.layers[2].acquire_fence == 11);
    CHECK(h.client_acquire_fence == 12); CHECK(polled_fds == std::vector<int>{10});
}
static void present_layer_error() {
    auto h = display(); layer(h, 1, 10);
    steps = {{1, short(POLLIN | POLLERR), 0, 0}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(closed_fds.empty());
    CHECK(imports == 0); CHECK(copies == 0); CHECK(h.layers[1].acquire_fence == 10);
}
static void present_later_layer_failure() {
    auto h = display(); layer(h, 1, 10); layer(h, 2, 11);
    steps = {{1, POLLIN, 0, 0}, {1, POLLHUP, 0, 0}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence);
    CHECK(imports == 0); CHECK(copies == 0); CHECK(closed_fds == std::vector<int>{10});
    CHECK(h.layers[1].acquire_fence == -1); CHECK(h.layers[2].acquire_fence == 11);
}
static void present_client_timeout() {
    auto h = display(); layer(h, 1, 10);
    h.client_expected = true; h.client_target = &handle; h.client_acquire_fence = 12;
    steps = {{1, POLLIN, 0, 0}, {0, 0, 0, 100000000}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 0); CHECK(copies == 0);
    CHECK(closed_fds == std::vector<int>{10}); CHECK(h.client_acquire_fence == 12);
}
static void present_client_error() {
    auto h = display(); h.client_expected = true; h.client_target = &handle;
    h.client_acquire_fence = 12; steps = {{1, POLLNVAL, 0, 0}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 0); CHECK(copies == 0);
    CHECK(closed_fds.empty()); CHECK(h.client_acquire_fence == 12);
}
static void present_missing_required_client() {
    auto h = display(); h.client_expected = true; layer(h, 1, -1);
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 0); CHECK(copies == 0);
}
static void present_unusable_required_client() {
    auto h = display(); h.client_expected = true; h.client_target = &handle;
    layer(h, 1, -1); import_result = -EINVAL; copy_result = -ENOMEM;
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 1); CHECK(copies == 1);
}
static void present_zero_required_fb() {
    auto h = display(); h.client_expected = true; h.client_target = &handle; zero_fb = true;
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 1);
}
static void present_success_after_retry() {
    auto h = display(); layer(h, 1, 10);
    h.client_expected = true; h.client_target = &handle; h.client_acquire_fence = 12;
    steps = {{0, 0, 0, 100000000}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(closed_fds.empty());
    steps = {{1, POLLIN, 0, 0}, {-1, 0, EINTR, 25000000}, {1, POLLIN, 0, 0}};
    step_index = 0;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(commits == 1); CHECK(allocations == 1); CHECK(small_copies == 1);
    CHECK(imports == 1); CHECK(closed_fds == (std::vector<int>{10, 12}));
    CHECK(h.layers[1].acquire_fence == -1); CHECK(h.client_acquire_fence == -1);
    CHECK(fence == -1); CHECK(h.pending_release.empty()); CHECK(removed_fbs == 1);
}
static void present_client_retry() {
    auto h = display(); h.client_expected = true; h.client_target = &handle;
    h.client_acquire_fence = 12; int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(closed_fds.empty());
    steps = {{1, POLLIN, 0, 0}}; step_index = 0;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(commits == 1); CHECK(closed_fds == std::vector<int>{12});
}
static void present_unused_client() {
    auto h = display(); layer(h, 1, -1, false);
    h.client_expected = false; h.client_target = &handle; h.client_acquire_fence = 12;
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(commits == 1); CHECK(timeouts.empty()); CHECK(closed_fds.empty());
    CHECK(h.client_acquire_fence == 12); CHECK(copies == 0);
}
static void present_client_copy_success() {
    auto h = display(); h.client_expected = true; h.client_target = &handle;
    import_result = -EINVAL; int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(commits == 1); CHECK(imports == 1); CHECK(copies == 1);
}
static void present_bad_display() {
    auto h = display(); int32_t fence = 999;
    CHECK(z1_present_display(&h, 1, &fence) == HWC2_ERROR_BAD_DISPLAY);
    CHECK(fence == -1); CHECK(commits == 0); CHECK(timeouts.empty());
}
static void present_power_off() {
    auto h = display(); h.powered_on = false; layer(h, 1, 10); int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(fence == -1); CHECK(commits == 0); CHECK(timeouts.empty());
    CHECK(h.layers[1].acquire_fence == 10);
}
static void host_real_poll() {
    real_syscalls = true;
    int fds[2]; CHECK(::pipe(fds) == 0);
    CHECK(wait_fence(fds[0], 5) == -ETIME);
    char c = 'x'; CHECK(::write(fds[1], &c, 1) == 1);
    CHECK(wait_fence(fds[0], 5) == 0);
    CHECK(::read(fds[0], &c, 1) == 1);
    CHECK(::close(fds[1]) == 0);
    CHECK(wait_fence(fds[0], 5) == -EIO);
    CHECK(::close(fds[0]) == 0);
    CHECK(wait_fence(fds[0], 5) == -EBADF);
}
int main() {
    struct Test { const char *name; void (*run)(); };
#define CASE(x) {#x, x}
    const Test tests[] = {
        CASE(absent_fence), CASE(timeout_budget), CASE(signaled), CASE(interrupted_success),
        CASE(interrupted_timeout), CASE(interrupted_at_deadline), CASE(many_interruptions),
        CASE(submillisecond_budget), CASE(poll_syscall_error), CASE(pollnval), CASE(pollerr),
        CASE(pollhup), CASE(readable_error), CASE(unexpected_event), CASE(zero_timeout_success),
        CASE(zero_timeout_failure), CASE(negative_timeout), CASE(clock_failure), CASE(large_budget),
        CASE(present_layer_timeout), CASE(present_layer_error), CASE(present_later_layer_failure),
        CASE(present_client_timeout), CASE(present_client_error), CASE(present_missing_required_client),
        CASE(present_unusable_required_client), CASE(present_zero_required_fb),
        CASE(present_success_after_retry), CASE(present_client_retry), CASE(present_unused_client),
        CASE(present_client_copy_success), CASE(present_bad_display), CASE(present_power_off),
        CASE(host_real_poll)
    };
    int failed = 0;
    for (const auto &t : tests) {
        reset();
        try { t.run(); std::printf("PASS %s\n", t.name); }
        catch (const std::exception &e) { ++failed; std::printf("FAIL %s: %s\n", t.name, e.what()); }
    }
    std::printf("%zu tests, %d failed\n", sizeof(tests) / sizeof(tests[0]), failed);
    return failed ? 1 : 0;
}
