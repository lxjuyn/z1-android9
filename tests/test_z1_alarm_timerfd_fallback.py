#!/usr/bin/env python3
"""Compile/run the real AlarmManager timerfd creation loop with host mocks.

This checks control flow and diagnostics, not kernel permissions or suspend.
Run from any directory; requires a host C++ compiler (CXX or c++).
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'android9/los16/frameworks/base/services/core/jni/com_android_server_AlarmManagerService.cpp'
LOG = ROOT / 'out/stock_boottime_timerfd_fallback_20261004/FIXTURE.log'


def extract_loop(source):
    init = source.index('static jlong android_server_AlarmManagerService_init(')
    start = source.index('for (size_t i = 0; i < fds.size(); i++) {', init)
    opening = source.index('{', start)
    depth = 1
    # Ignore braces inside comments and strings when finding the real block.
    token = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', re.S)
    for match in token.finditer(source, opening + 1):
        if match.group() == '{':
            depth += 1
        elif match.group() == '}':
            depth -= 1
            if depth == 0:
                return source[start:match.end()]
    raise ValueError('unclosed production timerfd loop')


HARNESS = r'''
#include <cerrno>
#include <ctime>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <initializer_list>
struct Reply { int fd; int error; };
static std::vector<Reply> replies;
static std::vector<clockid_t> calls;
static std::vector<clockid_t> errors;
static std::vector<int> closed;
static int warnings;
static void check(bool ok, const char* label) {
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", label); std::exit(1); }
}
static int timerfd_create(clockid_t clock, int flags) {
    check(flags == 0, "timerfd flags retained");
    calls.push_back(clock);
    check(calls.size() <= replies.size(), "unexpected syscall");
    const auto reply = replies[calls.size()-1];
    if (reply.fd < 0) errno = reply.error;
    return reply.fd;
}
static void log_timerfd_create_error(clockid_t clock) { errors.push_back(clock); }
static int close(int fd) { closed.push_back(fd); return 0; }
#define ALOGW(...) (++warnings)
static int production_loop(const std::vector<clockid_t>& android_alarm_to_clockid) {
    std::vector<int> fds(android_alarm_to_clockid.size(), -1);
    const int epollfd = 99;
    /* REAL_PRODUCTION_LOOP */
    return 1;
}
static void reset(std::initializer_list<Reply> script) {
    replies = script; calls.clear(); errors.clear(); closed.clear(); warnings = 0;
    errno = 0;
}
int main() {
    reset({{10,0}});
    check(production_loop({CLOCK_BOOTTIME}) == 1, "BOOTTIME success");
    check(calls == std::vector<clockid_t>{CLOCK_BOOTTIME} && warnings == 0,
          "successful BOOTTIME has no fallback");
    std::puts("PASS BOOTTIME original success");

    reset({{-1,EINVAL},{11,0}});
    check(production_loop({CLOCK_BOOTTIME}) == 1, "EINVAL fallback succeeds");
    check(calls == std::vector<clockid_t>{CLOCK_BOOTTIME,CLOCK_BOOTTIME_ALARM}
          && warnings == 1 && errors.empty(), "EINVAL fallback clock and warning");
    std::puts("PASS only BOOTTIME EINVAL falls back to BOOTTIME_ALARM");

    for (int error : {EPERM, ENOMEM, EMFILE}) {
        reset({{-1,error}});
        check(production_loop({CLOCK_BOOTTIME}) == 0, "non-EINVAL fails");
        check(calls.size() == 1 && warnings == 0 && errors == calls,
              "non-EINVAL does not fallback");
    }
    std::puts("PASS EPERM/ENOMEM/EMFILE do not fallback");

    for (int error : {EINVAL, EPERM}) {
        reset({{10,0},{-1,EINVAL},{-1,error}});
        check(production_loop({CLOCK_REALTIME,CLOCK_BOOTTIME}) == 0,
              "fallback failure returns zero");
        check(errors == std::vector<clockid_t>{CLOCK_BOOTTIME_ALARM},
              "failure diagnostic uses actual fallback clock");
        check(errno == error && closed == std::vector<int>{99,10},
              "fallback error retained and previous descriptors closed");
    }
    std::puts("PASS fallback failures report BOOTTIME_ALARM and clean up");

    for (clockid_t clock : {CLOCK_REALTIME_ALARM, CLOCK_REALTIME,
                           CLOCK_BOOTTIME_ALARM, CLOCK_MONOTONIC}) {
        reset({{-1,EINVAL}});
        check(production_loop({clock}) == 0, "other clock EINVAL fails");
        check(calls == std::vector<clockid_t>{clock} && errors == calls
              && warnings == 0, "other clock must not fallback");
    }
    std::puts("PASS other clocks never fallback");
}
'''


def main():
    source = SOURCE.read_text()
    loop = extract_loop(source)
    harness = HARNESS.replace('/* REAL_PRODUCTION_LOOP */', loop)
    compiler = os.environ.get('CXX', 'c++')
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = ['Source: ' + str(SOURCE),
             'Source SHA256: ' + hashlib.sha256(source.encode()).hexdigest(),
             'Extracted loop SHA256: ' + hashlib.sha256(loop.encode()).hexdigest(),
             'Scope: real production creation loop; mocked syscall/errno/error reporter.']
    try:
        with tempfile.TemporaryDirectory(prefix='z1_alarm_timerfd_') as tmp:
            cpp, binary = Path(tmp)/'fixture.cpp', Path(tmp)/'fixture'
            cpp.write_text(harness)
            for command in ([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                             str(cpp), '-o', str(binary)], [str(binary)]):
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                lines += [result.stdout.rstrip(), result.stderr.rstrip()]
                if result.returncode:
                    raise RuntimeError('fixture command failed: ' + str(result.returncode))
        lines += ['PASS: all fixture assertions; no device/runtime suspend validation.']
    except Exception as error:
        lines += ['FAIL: ' + str(error)]
        LOG.write_text('\n'.join(line for line in lines if line)+'\n')
        raise
    LOG.write_text('\n'.join(line for line in lines if line)+'\n')
    print(LOG.read_text(), end='')


if __name__ == '__main__':
    main()
