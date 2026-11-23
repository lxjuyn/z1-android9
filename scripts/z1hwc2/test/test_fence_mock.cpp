/* Template consumed by test_fence.py. Production bodies/types are inserted
 * verbatim; only external dependencies are mocked. Never touches a device. */
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cerrno>
#include <climits>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/sync_file.h>
#include <map>
#include <mutex>
#include <thread>
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
struct drmModeModeInfo { int unused; uint32_t vrefresh; };
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
static int sync_status, sync_error, sync_interruptions, sync_queries;
static bool real_syscalls;
static int allocations, commits, imports, copies, small_copies, plane_calls;
static int removed_fbs, import_result, copy_result, small_result;
static int reserve_failure, dup_failure, dup_interruptions, next_fd, dup_calls;
static int commit_failures, no_commit_fence, program_failure, add_failure;
static int32_t *out_fence_ptr;
static std::vector<int> copied_slots, small_slots;
static std::vector<uint32_t> closed_gems, commit_flags;
static std::vector<std::pair<uint32_t, hwc_frect_t>> programmed;
static bool zero_fb;
static int g_verbose;
static drmModeAtomicReq request;
static int mock_clock_gettime(clockid_t id, timespec *ts) {
    if (real_syscalls) return ::clock_gettime(id, ts);
    CHECK(id == CLOCK_MONOTONIC || id == CLOCK_THREAD_CPUTIME_ID);
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
static int hal_bpp(int format) { return format == 1 ? 4 : 0; }
static int copy_into_dumb(z1_hwc2 *h, z1_dumb *d, buffer_handle_t) {
    ++copies;
    copied_slots.push_back(h->client_copy_slot);
    d->fb_id = zero_fb ? 0 : 32 + h->client_copy_slot;
    return copy_result;
}
static int layer_copy_sync(z1_hwc2 *, z1_layer *l) {
    ++small_copies;
    small_slots.push_back(l->copy_slot);
    l->staging[l->copy_slot].fb_id = 33 + l->copy_slot;
    return small_result;
}
static drmModeAtomicReq *drmModeAtomicAlloc() { ++allocations; return &request; }
static void drmModeAtomicFree(drmModeAtomicReq *) {}
static int drmModeAtomicCommit(int, drmModeAtomicReq *, uint32_t flags, void *) {
    ++commits; commit_flags.push_back(flags);
    if (commit_failures > 0) { --commit_failures; errno = EINVAL; return -1; }
    CHECK(out_fence_ptr != nullptr);
    *out_fence_ptr = no_commit_fence ? -1 : 90 + commits;
    return 0;
}
static int drmModeDestroyPropertyBlob(int, uint32_t) { return 0; }
static int drmModeCreatePropertyBlob(int, const void *, size_t, uint32_t *b) { *b = 1; return 0; }
static bool add_prop(drmModeAtomicReq *, const char *name, uint32_t, uint32_t, uint64_t val) {
    if (add_failure) return false;
    if (!strcmp(name, "OUT_FENCE_PTR")) out_fence_ptr = reinterpret_cast<int32_t *>(val);
    return true;
}
static bool program_plane(z1_hwc2 *, drmModeAtomicReq *, int, uint32_t fb, int, int,
                          int, int, const hwc_frect_t *crop, float, int32_t, bool) {
    ++plane_calls;
    programmed.push_back({fb, crop ? *crop : hwc_frect_t{}});
    return !program_failure;
}
static int drmModeRmFB(int, uint32_t) { ++removed_fbs; return 0; }
static int drmCloseBufferHandle(int, uint32_t gem) { closed_gems.push_back(gem); return 0; }
static int mock_fcntl(int, int command, int) {
    CHECK(command == F_DUPFD_CLOEXEC);
    if (reserve_failure) { errno = EMFILE; return -1; }
    return next_fd++;
}
static int mock_dup3(int, int dest, int flags) {
    CHECK(flags == O_CLOEXEC); ++dup_calls;
    if (dup_interruptions) { --dup_interruptions; errno = EINTR; return -1; }
    if (dup_failure) { errno = EIO; return -1; }
    return dest;
}
static int mock_ioctl(int fd, unsigned long command, sync_file_info *info) {
    if (real_syscalls) return ::ioctl(fd, command, info);
    CHECK(command == SYNC_IOC_FILE_INFO); ++sync_queries;
    if (sync_interruptions) { --sync_interruptions; errno = EINTR; return -1; }
    if (sync_error) { errno = sync_error; return -1; }
    info->status = sync_status; return 0;
}
#define ioctl mock_ioctl
#define fcntl mock_fcntl
#define dup3 mock_dup3
#define poll mock_poll
#define clock_gettime mock_clock_gettime
#define close mock_close
static int32_t z1_retry_pending_wake(z1_hwc2 *) { return HWC2_ERROR_NONE; }
/* PRODUCTION_FUNCTIONS */
#undef ioctl
#undef fcntl
#undef dup3
#undef close
#undef clock_gettime
#undef poll

static void reset() {
    steps.clear(); timeouts.clear(); polled_fds.clear(); closed_fds.clear();
    step_index = 0; clock_ns = 1000000000; clock_error = 0; real_syscalls = false;
    sync_status = 1; sync_error = sync_interruptions = sync_queries = 0;
    allocations = commits = imports = copies = small_copies = plane_calls = 0;
    removed_fbs = 0;
    import_result = copy_result = small_result = 0; zero_fb = false; g_verbose = 0;
    reserve_failure = dup_failure = dup_interruptions = dup_calls = 0; next_fd = 200;
    commit_failures = no_commit_fence = program_failure = add_failure = 0;
    out_fence_ptr = nullptr; copied_slots.clear(); small_slots.clear();
    closed_gems.clear(); commit_flags.clear(); programmed.clear();
}
static z1_handle handle = {0, 1, 12, 81, Z1_HANDLE_MAGIC, 0, 4096, 0,
                           0, 0, 32, 32, 32, 1, 0};
static void setup_display(z1_hwc2 &h) {
    h.powered_on = true; h.validated = true; h.modeset_done = true;
    h.plane_count = 4; h.width = 240; h.height = 320;
    h.client_acquire_fence = -1; h.commit_fence = -1; h.crtc_out_fence_prop = 44;
    h.last_present_ms = 17;
    h.pending_release.push_back({71, 72});
}
static void layer(z1_hwc2 &h, int id, int fd, bool staging = true) {
    z1_layer l{};
    l.id = id; l.z = id; l.composition = HWC2_COMPOSITION_DEVICE;
    l.acquire_fence = fd; l.release_fence = -1; (void)staging;
    l.handle = &handle; l.frame = {0, 0, 32, 32}; l.alpha = 1;
    h.layers[id] = l;
}
static void dump_contract_queries_then_copies() {
    z1_hwc2 h{}; setup_display(h);
    layer(h, 1, -1);
    h.opened_ms = 900; h.frames = 2; h.present_ok = 2;
    h.copy_bytes = 4096;
    char canary = 'x';
    uint32_t needed = 1;
    z1_dump(&h, &needed, &canary);
    CHECK(needed == 0 && canary == 'x');
    z1_dump(&h, &needed, nullptr);
    CHECK(needed > 0);
    CHECK(h.dump_text.size() == needed);
    CHECK(h.dump_text.find("z1hwc2_metrics version=") == 0);
    CHECK(h.dump_text.find("copy_bytes=4096") != std::string::npos);

    h.copy_bytes = 8192;
    std::vector<char> short_buffer(needed - 17u);
    uint32_t capacity = static_cast<uint32_t>(short_buffer.size());
    z1_dump(&h, &capacity, short_buffer.data());
    CHECK(capacity == short_buffer.size());
    CHECK(!memcmp(short_buffer.data(), h.dump_text.data(), capacity));

    std::vector<char> full(needed);
    capacity = needed;
    z1_dump(&h, &capacity, full.data());
    CHECK(capacity == needed);
    CHECK(!memcmp(full.data(), h.dump_text.data(), needed));
    CHECK(h.dump_text.find("copy_bytes=4096") != std::string::npos);

    // A dump must not stall waiting for an in-flight present or read its
    // mutable counters without acquiring the lock.
    std::lock_guard<std::mutex> present(present_stats_lock);
    std::thread dump([&] {
        z1_dump(&h, &needed, nullptr);
        CHECK(h.dump_text == "z1hwc2_metrics unavailable=present_in_progress\n");
    });
    dump.join();
}
static void no_present_side_effects(const z1_hwc2 &h, int fence) {
    CHECK(fence == -1); CHECK(commits == 0); CHECK(allocations == 0);
    CHECK(small_copies == 0); CHECK(plane_calls == 0);
    CHECK(removed_fbs == 0); CHECK(dup_calls == 0);
    CHECK(h.commit_fence == -1);
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
    z1_hwc2 h{}; setup_display(h); layer(h, 1, 10); layer(h, 2, 11);
    h.client_expected = true; h.client_target = &handle; h.client_acquire_fence = 12;
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence);
    CHECK(h.present_calls == 1 && h.present_error == 1);
    CHECK(h.acquire_waits == 1 && h.acquire_wait_fail == 1);
    CHECK(h.copy_calls == 0 && h.commit_calls == 0);
    CHECK(imports == 0); CHECK(copies == 0); CHECK(closed_fds.empty());
    CHECK(h.layers[1].acquire_fence == 10); CHECK(h.layers[2].acquire_fence == 11);
    CHECK(h.client_acquire_fence == 12); CHECK(polled_fds == std::vector<int>{10});
}
static void present_layer_error() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, 10);
    steps = {{1, short(POLLIN | POLLERR), 0, 0}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(closed_fds.empty());
    CHECK(imports == 0); CHECK(copies == 0); CHECK(h.layers[1].acquire_fence == 10);
}
static void present_later_layer_failure() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, 10); layer(h, 2, 11);
    steps = {{1, POLLIN, 0, 0}, {1, POLLHUP, 0, 0}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence);
    CHECK(imports == 0); CHECK(copies == 0); CHECK(closed_fds == std::vector<int>{10});
    CHECK(h.layers[1].acquire_fence == -1); CHECK(h.layers[2].acquire_fence == 11);
}
static void present_client_timeout() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, 10);
    h.client_expected = true; h.client_target = &handle; h.client_acquire_fence = 12;
    steps = {{1, POLLIN, 0, 0}, {0, 0, 0, 100000000}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 0); CHECK(copies == 0);
    CHECK(closed_fds == std::vector<int>{10}); CHECK(h.client_acquire_fence == 12);
}
static void present_client_error() {
    z1_hwc2 h{}; setup_display(h); h.client_expected = true; h.client_target = &handle;
    h.client_acquire_fence = 12; steps = {{1, POLLNVAL, 0, 0}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 0); CHECK(copies == 0);
    CHECK(closed_fds.empty()); CHECK(h.client_acquire_fence == 12);
}
static void present_missing_required_client() {
    z1_hwc2 h{}; setup_display(h); h.client_expected = true; layer(h, 1, -1);
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 0); CHECK(copies == 0);
}
static void present_unusable_required_client() {
    z1_hwc2 h{}; setup_display(h); h.client_expected = true; h.client_target = &handle;
    layer(h, 1, -1); import_result = -EINVAL; copy_result = -ENOMEM;
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 0); CHECK(copies == 1);
}
static void present_zero_required_fb() {
    z1_hwc2 h{}; setup_display(h); h.client_expected = true; h.client_target = &handle; zero_fb = true;
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(imports == 0);
}
static void present_success_after_retry() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, 10);
    h.client_expected = true; h.client_target = &handle; h.client_acquire_fence = 12;
    steps = {{0, 0, 0, 100000000}};
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(closed_fds.empty());
    steps = {{1, POLLIN, 0, 0}, {-1, 0, EINTR, 25000000}, {1, POLLIN, 0, 0}};
    step_index = 0;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(commits == 1); CHECK(allocations == 1); CHECK(small_copies == 1);
    CHECK(h.present_calls == 2 && h.present_ok == 1);
    CHECK(h.present_error == 1 && h.client_frames == 1);
    CHECK(h.acquire_waits == 3 && h.acquire_wait_fail == 1);
    CHECK(h.copy_calls == 2 && h.copy_ok == 2 &&
          h.copy_bytes == 2u * 32u * 32u * 4u);
    CHECK(h.commit_calls == 1 && h.commit_ok == 1);
    CHECK(imports == 0); CHECK(closed_fds == (std::vector<int>{10, 12}));
    CHECK(h.layers[1].acquire_fence == -1); CHECK(h.client_acquire_fence == -1);
    CHECK(fence == 91); CHECK(h.pending_release.empty()); CHECK(removed_fbs == 0);
    CHECK(h.submitted_release.size() == 1); CHECK(h.commit_fence == 200);
}
static void present_client_retry() {
    z1_hwc2 h{}; setup_display(h); h.client_expected = true; h.client_target = &handle;
    h.client_acquire_fence = 12; int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NO_RESOURCES);
    no_present_side_effects(h, fence); CHECK(closed_fds.empty());
    steps = {{1, POLLIN, 0, 0}}; step_index = 0;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(commits == 1); CHECK(closed_fds == std::vector<int>{12});
}
static void present_unused_client() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1, false);
    h.client_expected = false; h.client_target = &handle; h.client_acquire_fence = 12;
    int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(commits == 1); CHECK(timeouts == std::vector<int>{0}); CHECK(closed_fds.empty());
    CHECK(h.client_acquire_fence == 12); CHECK(copies == 0);
}
static void present_client_copy_success() {
    z1_hwc2 h{}; setup_display(h); h.client_expected = true; h.client_target = &handle;
    import_result = -EINVAL; int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(commits == 1); CHECK(imports == 0); CHECK(copies == 1);
    CHECK(h.copy_calls == 1 && h.copy_ok == 1);
    CHECK(h.copy_bytes == 32u * 32u * 4u);
    CHECK(h.present_ok == 1 && h.client_frames == 1);
}
static void present_bad_display() {
    z1_hwc2 h{}; setup_display(h); int32_t fence = 999;
    CHECK(z1_present_display(&h, 1, &fence) == HWC2_ERROR_BAD_DISPLAY);
    CHECK(fence == -1); CHECK(commits == 0); CHECK(timeouts.empty());
}
static void present_power_off() {
    z1_hwc2 h{}; setup_display(h); h.powered_on = false; layer(h, 1, 10); int32_t fence = 999;
    CHECK(z1_present_display(&h, 0, &fence) == HWC2_ERROR_NONE);
    CHECK(fence == -1); CHECK(commits == 0); CHECK(timeouts.empty());
    CHECK(h.layers[1].acquire_fence == 10);
    CHECK(h.present_calls == 1 && h.present_skipped == 1);
}
static void completion_retires_only_submitted() {
    z1_hwc2 h{}; setup_display(h); h.submitted_release.push_back({73, 74}); h.commit_fence = 91;
    CHECK(!complete_commit(&h, 0)); CHECK(removed_fbs == 0);
    CHECK(h.pending_release.size() == 1); CHECK(h.submitted_release.size() == 1);
    steps = {{1, POLLIN, 0, 0}}; step_index = 0;
    CHECK(complete_commit(&h, 0)); CHECK(removed_fbs == 1);
    CHECK(closed_gems == std::vector<uint32_t>{74}); CHECK(h.pending_release.size() == 1);
    CHECK(h.submitted_release.empty()); CHECK(h.commit_fence == -1);
}
static void previous_commit_timeout_preserves_slots() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); h.commit_fence = 91;
    h.submitted_release.push_back({73, 74}); int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(commits == 0); CHECK(copies == 0); CHECK(small_copies == 0);
    CHECK(h.layers[1].copy_slot == 0); CHECK(h.commit_fence == 91);
    CHECK(removed_fbs == 0); CHECK(f == -1); CHECK(!h.commit_broken);
}
static void previous_commit_error_fails_closed() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); h.commit_fence = 91;
    steps = {{1, POLLNVAL, 0, 0}}; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(h.commit_broken); CHECK(small_copies == 0); CHECK(commits == 0);
}
static void commit_failure_does_not_flip() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); h.client_expected = true; h.client_target = &handle;
    commit_failures = 1; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(commits == 1); CHECK(h.layers[1].copy_slot == 0);
    CHECK(h.client_copy_slot == 0); CHECK(h.pending_release.size() == 1);
    CHECK(h.submitted_release.empty()); CHECK(removed_fbs == 0); CHECK(f == -1);
    CHECK(closed_fds == std::vector<int>{200});
}
static void copied_frames_use_only_inactive_slot() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); h.client_expected = true; h.client_target = &handle;
    int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NONE);
    CHECK(h.layers[1].copy_slot == 1); CHECK(h.client_copy_slot == 1);
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(copies == 1); CHECK(small_copies == 1);
    steps = {{1, POLLIN, 0, 0}}; step_index = 0;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NONE);
    CHECK(copied_slots == (std::vector<int>{0, 1}));
    CHECK(small_slots == (std::vector<int>{0, 1})); CHECK(imports == 0);
    CHECK(h.layers[1].copy_slot == 0); CHECK(h.client_copy_slot == 0);
}
static void no_fence_freezes_private_copies() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); no_commit_fence = 1; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(h.commit_broken); CHECK(h.layers[1].copy_slot == 0);
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(small_copies == 1); CHECK(commits == 1); CHECK(imports == 0);
}
static void descriptor_reservation_failure_precedes_commit() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); reserve_failure = 1; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(commits == 0); CHECK(h.layers[1].copy_slot == 0);
}
static void interrupted_dup_retries() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); dup_interruptions = 2; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NONE);
    CHECK(dup_calls == 3); CHECK(f == 91); CHECK(!h.commit_broken);
}
static void failed_dup_freezes_private_copies() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); dup_failure = 1; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(h.commit_broken); CHECK(h.commit_fence == 91); CHECK(f == -1);
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(small_copies == 1); CHECK(commits == 1);
}
static void copy_failure_never_commits_partial_frame() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); small_result = -EIO; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(commits == 0); CHECK(h.layers[1].copy_slot == 0);
}
static void plane_failure_never_commits() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); program_failure = 1; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(commits == 0); CHECK(dup_calls == 0);
}
static void out_fence_property_failure_never_commits() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); add_failure = 1; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NO_RESOURCES);
    CHECK(commits == 0); CHECK(h.layers[1].copy_slot == 0);
}
static void fallback_preserves_prepared_small_crop() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); h.modeset_done = false;
    commit_failures = 1; int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NONE);
    CHECK(commits == 2); CHECK(commit_flags[1] == 0);
    CHECK(programmed[0].first == 33); CHECK(programmed[4].first == 33);
    CHECK(programmed[0].second.right == 32); CHECK(programmed[4].second.right == 32);
}
static void copies_release_sources_without_waiting_for_scanout() {
    z1_hwc2 h{}; setup_display(h); layer(h, 1, -1); int32_t f = 8;
    CHECK(z1_present_display(&h, 0, &f) == HWC2_ERROR_NONE);
    uint32_t n = 0; CHECK(z1_get_release_fences(&h, 0, &n, nullptr, nullptr) == HWC2_ERROR_NONE);
    CHECK(n == 1); hwc2_layer_t id = 0; int32_t release = 7;
    CHECK(z1_get_release_fences(&h, 0, &n, &id, &release) == HWC2_ERROR_NONE);
    CHECK(id == 1); CHECK(release == -1); CHECK(h.commit_fence == 200);
    // No source import: a failed future present may keep private scanout safely.
    CHECK(imports == 0);
}
static void readable_failed_sync_is_not_completion() {
    z1_hwc2 h{}; setup_display(h); h.commit_fence = 91; h.submitted_release.push_back({73, 74});
    sync_status = -ETIMEDOUT; steps = {{1, POLLIN, 0, 0}};
    CHECK(!complete_commit(&h, 100)); CHECK(h.commit_broken);
    CHECK(removed_fbs == 0); CHECK(h.submitted_release.size() == 1);
}
static void readable_pending_sync_is_not_completion() {
    sync_status = 0; steps = {{1, POLLIN, 0, 0}};
    CHECK(wait_fence(10, 100) == -EIO);
}
static void sync_info_error_propagates() {
    sync_error = ENOTTY; steps = {{1, POLLIN, 0, 0}};
    CHECK(wait_fence(10, 100) == -ENOTTY);
}
static void sync_info_interrupt_retries() {
    sync_interruptions = 2; steps = {{1, POLLIN, 0, 0}};
    CHECK(wait_fence(10, 100) == 0); CHECK(sync_queries == 3);
}
static void host_real_poll() {
    real_syscalls = true;
    int fds[2]; CHECK(::pipe(fds) == 0);
    CHECK(wait_fence(fds[0], 5) == -ETIME);
    char c = 'x'; CHECK(::write(fds[1], &c, 1) == 1);
    CHECK(wait_fence(fds[0], 5) == -ENOTTY);
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
        CASE(dump_contract_queries_then_copies),
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
        CASE(completion_retires_only_submitted), CASE(previous_commit_timeout_preserves_slots),
        CASE(previous_commit_error_fails_closed), CASE(commit_failure_does_not_flip),
        CASE(copied_frames_use_only_inactive_slot), CASE(no_fence_freezes_private_copies),
        CASE(descriptor_reservation_failure_precedes_commit), CASE(interrupted_dup_retries),
        CASE(failed_dup_freezes_private_copies), CASE(copy_failure_never_commits_partial_frame),
        CASE(plane_failure_never_commits), CASE(out_fence_property_failure_never_commits),
        CASE(fallback_preserves_prepared_small_crop), CASE(copies_release_sources_without_waiting_for_scanout),
        CASE(readable_failed_sync_is_not_completion), CASE(readable_pending_sync_is_not_completion),
        CASE(sync_info_error_propagates), CASE(sync_info_interrupt_retries), CASE(host_real_poll)
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
