#!/usr/bin/env python3
"""Exercise DAPM supply decisions given mocked consumer power demand.

Supply recursion, sequencing arrays, widget definitions and routes come from
the checked-out kernel sources. Consumer demand is mocked; separate structural
checks cover signal edges, switch connectivity and supply registration rules.
This does not execute generic DAPM signal power traversal or regmap events and
does not prove microphone wiring/recording.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
CODEC = ROOT / 'fork_linux/sound/soc/codecs/mt6323.c'
DAPM = ROOT / 'fork_linux/sound/soc/soc-dapm.c'
HEADER = ROOT / 'fork_linux/include/sound/soc-dapm.h'


def block(source, start):
    begin = source.index(start)
    brace = source.index('{', begin)
    depth = 0
    for end in range(brace, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0:
            return source[begin:end + 1]
    raise ValueError('Unclosed production declaration: ' + start)


def harness(codec):
    dapm, header = DAPM.read_text(), HEADER.read_text()
    macros = []
    # Preserve the real macro bodies, including the supply subsequence field.
    for name in ('SND_SOC_DAPM_INIT_REG_VAL', 'SND_SOC_DAPM_SUPPLY',
                 'SND_SOC_DAPM_SUPPLY_S'):
        match = re.search(r'^#define ' + name + r'\([\s\S]*?(?=\n(?![^\n]*\\\n))', header, re.M)
        if not match:
            raise ValueError('Macro not found: ' + name)
        # Read logical preprocessor line rather than copying a partial macro.
        lines = header[match.start():].splitlines()
        take = []
        for line in lines:
            take.append(line)
            if not line.endswith('\\'):
                break
        macros.append('\n'.join(take))
    widgets = block(codec, 'static const struct snd_soc_dapm_widget mt6323_dapm_widgets[]')
    routes = block(codec, 'static const struct snd_soc_dapm_route mt6323_dapm_routes[]')
    return r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define BUILD_BUG_ON(e) _Static_assert(!(e), #e)
#define WARN_ONCE(e, ...) assert(!(e))
#define DAPM_UPDATE_STAT(w, s) ((void)(w))
#define SND_SOC_NOPM -1
#define SND_SOC_DAPM_PRE_PMU 1
#define SND_SOC_DAPM_POST_PMD 2
#define mt6323_mic_event 0
#define mt6323_clk_event 0
#define mt6323_newif_event 0
''' + block(header, 'enum snd_soc_dapm_type') + r''';
struct snd_soc_dapm_path;
struct snd_soc_dapm_widget {
    enum snd_soc_dapm_type id;
    const char *name;
    int subseq, reg, shift, mask, on_val, off_val, event, event_flags;
    void *dapm;
    bool power_checked, new_power, force;
    int demand;
    int (*power_check)(struct snd_soc_dapm_widget *);
    struct snd_soc_dapm_path *paths[16];
    size_t path_count;
};
struct snd_soc_dapm_path {
    struct snd_soc_dapm_widget *source, *sink;
    int (*connected)(struct snd_soc_dapm_widget *, struct snd_soc_dapm_widget *);
};
struct snd_soc_dapm_route { const char *sink, *control, *source; };
#define SIMPLE_WIDGET(n, i) { .name=(n), .id=(i), .reg=SND_SOC_NOPM }
#define SND_SOC_DAPM_SWITCH(n, ...) SIMPLE_WIDGET(n, snd_soc_dapm_switch)
#define SND_SOC_DAPM_INPUT(n) SIMPLE_WIDGET(n, snd_soc_dapm_input)
#define SND_SOC_DAPM_OUTPUT(n) SIMPLE_WIDGET(n, snd_soc_dapm_output)
#define SND_SOC_DAPM_DAC_E(n, ...) SIMPLE_WIDGET(n, snd_soc_dapm_dac)
#define SND_SOC_DAPM_OUT_DRV_E(n, ...) SIMPLE_WIDGET(n, snd_soc_dapm_out_drv)
#define snd_soc_dapm_widget_for_each_sink_path(w, p) \
    for (size_t i=0; i<(w)->path_count && ((p)=(w)->paths[i]); ++i)
''' + '\n'.join(macros) + '\n' + widgets + ';\n' + routes + ';\n' + \
        block(dapm, 'static int dapm_up_seq[]') + ';\n' + \
        block(dapm, 'static int dapm_down_seq[]') + ';\n' + \
        block(dapm, 'static int dapm_widget_power_check(') + '\n' + \
        block(dapm, 'static int dapm_supply_check_power(') + '\n' + \
        block(dapm, 'static int dapm_seq_compare(') + r'''
static struct snd_soc_dapm_widget ws[32];
static struct snd_soc_dapm_path ps[64];
static size_t nw, np;
static int endpoint_demand(struct snd_soc_dapm_widget *w) { return w->demand; }
static struct snd_soc_dapm_widget *widget(const char *name)
{
    for (size_t i=0; i<nw; ++i)
        if (!strcmp(ws[i].name, name)) return &ws[i];
    assert(nw < ARRAY_SIZE(ws));
    ws[nw] = (struct snd_soc_dapm_widget) { .name=name,
        .power_check=endpoint_demand };
    return &ws[nw++];
}
static void setup(void)
{
    nw=ARRAY_SIZE(mt6323_dapm_widgets);
    memcpy(ws, mt6323_dapm_widgets, sizeof(mt6323_dapm_widgets));
    for (size_t i=0; i<nw; ++i)
        ws[i].power_check=ws[i].id == snd_soc_dapm_supply ?
            dapm_supply_check_power : endpoint_demand;
    for (size_t i=0; i<ARRAY_SIZE(mt6323_dapm_routes); ++i) {
        const struct snd_soc_dapm_route *r=&mt6323_dapm_routes[i];
        struct snd_soc_dapm_widget *s=widget(r->source), *d=widget(r->sink);
        /* soc-dapm.c rejects controlled supplies and non-supply inputs to
         * supplies before a route is registered. Do not accept either here. */
        assert(s->id != snd_soc_dapm_supply || r->control == NULL);
        assert(d->id != snd_soc_dapm_supply || s->id == snd_soc_dapm_supply);
        assert(np < ARRAY_SIZE(ps) && s->path_count < ARRAY_SIZE(s->paths));
        ps[np]=(struct snd_soc_dapm_path){.source=s, .sink=d};
        s->paths[s->path_count++]=&ps[np++];
    }
}
static bool signal_reaches(const char *from, const char *to, bool switch_on,
                           size_t remaining)
{
    if (!strcmp(from, to)) return true;
    if (!remaining || widget(from)->id == snd_soc_dapm_supply) return false;
    for (size_t i=0; i<ARRAY_SIZE(mt6323_dapm_routes); ++i) {
        const struct snd_soc_dapm_route *r=&mt6323_dapm_routes[i];
        if (strcmp(r->source, from) ||
            widget(r->sink)->id == snd_soc_dapm_supply ||
            (r->control && !switch_on)) continue;
        if (signal_reaches(r->sink, to, switch_on, remaining-1)) return true;
    }
    return false;
}
static void power(int playback, int capture, int clock, int mic, int dl)
{
    for (size_t i=0; i<nw; ++i) {
        ws[i].power_checked=false;
        ws[i].new_power=false;
        ws[i].demand=0;
    }
    widget("DAC")->demand=playback;
    widget("MIC Route")->demand=capture;
    assert(dapm_widget_power_check(widget("AUDCLK")) == clock);
    assert(dapm_widget_power_check(widget("MIC UL")) == mic);
    assert(dapm_widget_power_check(widget("NEWIF")) == dl);
}
int main(void)
{
    setup();
    assert(signal_reaches("MIC Input", "AIF1 Capture", true, nw));
    assert(!signal_reaches("MIC Input", "AIF1 Capture", false, nw));
    assert(!signal_reaches("MIC Input", "DAC", true, nw));
    power(0, 0, 0, 0, 0); /* idle */
    power(0, 1, 1, 1, 0); /* capture only: no downlink NEWIF */
    power(1, 1, 1, 1, 1); /* start playback during capture */
    power(0, 1, 1, 1, 0); /* stop playback: capture keeps its clock */
    power(1, 0, 1, 0, 1); /* stop capture: playback keeps its clock */
    power(0, 0, 0, 0, 0); /* last user stops */
    assert(dapm_seq_compare(widget("AUDCLK"), widget("MIC UL"), true)<0);
    assert(dapm_seq_compare(widget("MIC UL"), widget("AUDCLK"), false)<0);
    puts("PASS real DAPM supply lifetime and capture power ordering");
    return 0;
}
'''


class CaptureClock(unittest.TestCase):
    def execute(self, source):
        with tempfile.TemporaryDirectory(prefix='z1-dapm-clock-') as tmp:
            source_file, executable = Path(tmp)/'test.c', Path(tmp)/'test'
            source_file.write_text(harness(source))
            build = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra',
                '-Werror', str(source_file), '-o', str(executable)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stderr)
            return subprocess.run([str(executable)], cwd=tmp,
                capture_output=True, text=True, timeout=5)

    def test_real_supply_lifetime_and_sequence(self):
        result = self.execute(CODEC.read_text())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_removing_capture_clock_edge_is_detected(self):
        source = CODEC.read_text()
        changed = re.sub(r'\{\s*"MIC UL",\s*NULL,\s*"AUDCLK"\s*\},', '', source)
        self.assertNotEqual(changed, source)
        self.assertNotEqual(self.execute(changed).returncode, 0)

    def test_losing_supply_subsequence_is_detected(self):
        source = CODEC.read_text()
        changed = source.replace('SND_SOC_DAPM_SUPPLY_S("MIC UL", 1,',
                                 'SND_SOC_DAPM_SUPPLY_S("MIC UL", 0,')
        self.assertNotEqual(changed, source)
        self.assertNotEqual(self.execute(changed).returncode, 0)

    def test_enabling_downlink_for_capture_is_detected(self):
        source = CODEC.read_text()
        changed = source.replace('{ "MIC UL", NULL, "AUDCLK" },',
            '{ "MIC UL", NULL, "AUDCLK" },\n{ "MIC UL", NULL, "NEWIF" },')
        self.assertNotEqual(changed, source)
        self.assertNotEqual(self.execute(changed).returncode, 0)

    def test_missing_signal_edges_are_detected(self):
        source = CODEC.read_text()
        for edge in ('{ "AIF1 Capture", NULL, "MIC Route" },',
                     '{ "MIC Route", "Switch", "MIC Input" },'):
            with self.subTest(edge=edge):
                changed = source.replace(edge, '')
                self.assertNotEqual(changed, source)
                self.assertNotEqual(self.execute(changed).returncode, 0)

    def test_controlled_supply_route_is_rejected(self):
        source = CODEC.read_text()
        changed = source.replace('{ "MIC UL", NULL, "AUDCLK" },',
                                 '{ "MIC UL", "Switch", "AUDCLK" },')
        self.assertNotEqual(changed, source)
        self.assertNotEqual(self.execute(changed).returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
