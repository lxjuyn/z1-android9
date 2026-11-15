"""Compile production codec functions against a fault-injecting fake regmap.

The rate table and all logic under test are extracted from mt6323.c on each
run. Expected rate values come from the original Z1 HAL, not a second driver.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'fork_linux/sound/soc/codecs/mt6323.c'


def function(source, name):
    start = re.search(r'static int ' + name + r'\([^;]+?\)\s*\{', source).start()
    first = source.index('{', start)
    depth = 1
    end = first + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#define BIT(n) (1u << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define SNDRV_PCM_STREAM_CAPTURE 1
#define SND_SOC_DAPM_PRE_PMU 1
#define SND_SOC_DAPM_POST_PMD 2
struct regmap {
 unsigned int regs[65536];
 int calls, writes, top_writes, fail_call;
};
struct mutex { int held; };
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
struct mt6323_codec_priv {
 struct regmap *regmap;
 struct mutex newif_lock;
 unsigned int mic_rate;
 bool dl_pwr;
};
struct snd_soc_component { struct mt6323_codec_priv *priv; };
struct snd_soc_dapm_context { struct snd_soc_component *component; };
struct snd_soc_dapm_widget { struct snd_soc_dapm_context *dapm; };
struct snd_kcontrol { int unused; };
struct snd_pcm_substream { int stream; };
struct snd_pcm_hw_params { unsigned int rate; };
#define params_rate(p) ((p)->rate)
#define snd_soc_component_get_drvdata(c) ((c)->priv)
#define snd_soc_dapm_to_component(d) ((d)->component)
static int regmap_read(struct regmap *r, unsigned int a, unsigned int *v) {
 if (++r->calls == r->fail_call) return -EIO;
 *v = r->regs[a]; return 0;
}
static int regmap_write(struct regmap *r, unsigned int a, unsigned int v) {
 if (++r->calls == r->fail_call) return -EIO;
 r->regs[a] = v; r->writes++; return 0;
}
static int regmap_update_bits(struct regmap *r, unsigned int a,
                              unsigned int mask, unsigned int v) {
 if (++r->calls == r->fail_call) return -EIO;
 r->regs[a] = (r->regs[a] & ~mask) | (v & mask);
 r->writes++; if (a == 0x4016) r->top_writes++; return 0;
}
'''


CHECKS = r'''
int main(void) {
 struct regmap r = {0};
 struct mt6323_codec_priv p = { .regmap = &r };
 struct snd_soc_component c = { .priv = &p };
 struct snd_soc_dapm_context d = { .component = &c };
 struct snd_soc_dapm_widget w = { .dapm = &d };
 struct snd_pcm_substream s = {0};
 struct snd_pcm_hw_params params = { .rate = 48000 };

 /* Cold configuration preserves UL and unrelated bits; no clockless trigger. */
 r.regs[0x4002] = 0xab19;
 r.regs[0x4016] = 0xae02;
 assert(mt6323_hw_params(&c, &s, &params) == 0);
 assert(r.regs[0x4024] == 0x8330);
 assert(r.regs[0x4002] == 0xab1a);
 assert(r.top_writes == 0 && r.regs[0x4016] == 0xae02);
 assert(!p.newif_lock.held);

 /* Actual NEWIF event applies configuration then enables only DL bit0. */
 r.regs[0x4000] = 0x102;
 assert(mt6323_newif_event(&w, 0, SND_SOC_DAPM_PRE_PMU) == 0);
 assert(r.regs[0x4016] == 0xaf02);
 assert(r.regs[0x4000] == 0x103);
 assert(r.top_writes == 1 && !p.newif_lock.held);

 /* ACK=1 requests bit8=0 while preserving DC and all unrelated bits. */
 r.regs[0x4016] = 0xaf03;
 assert(mt6323_top_trigger(&p) == 0);
 assert(r.regs[0x4016] == 0xae03);
 r.regs[0x4016] = 0xae02;
 assert(mt6323_top_trigger(&p) == 0);
 assert(r.regs[0x4016] == 0xaf02);

 /* Warm reopen must re-apply every rate without needing a new PMU event. */
 p.dl_pwr = true;
 params.rate = 44100;
 int n = r.top_writes;
 assert(mt6323_hw_params(&c, &s, &params) == 0);
 assert(r.regs[0x4024] == 0x7330 && r.regs[0x4002] == 0xab19);
 r.regs[0x4016] |= BIT(0); /* Hardware acknowledges the previous request. */
 params.rate = 48000;
 assert(mt6323_hw_params(&c, &s, &params) == 0);
 assert(r.regs[0x4024] == 0x8330 && r.regs[0x4002] == 0xab1a);
 assert(r.top_writes == n + 2 && !(r.regs[0x4016] & BIT(8)));

 /* Stream power-down cannot clobber the shared top/DC handshake. */
 unsigned int old_top = r.regs[0x4016];
 assert(mt6323_newif_event(&w, 0, SND_SOC_DAPM_POST_PMD) == 0);
 assert(r.regs[0x4000] == 0x102 && r.regs[0x4016] == old_top);

 /* Unsupported playback rate fails before any register operation. */
 int old_calls = r.calls;
 params.rate = 96000;
 assert(mt6323_hw_params(&c, &s, &params) == -EINVAL);
 assert(r.calls == old_calls && !p.newif_lock.held);

 /* Capture keeps its existing rate-only hw_params contract. */
 s.stream = SNDRV_PCM_STREAM_CAPTURE;
 params.rate = 16000;
 assert(mt6323_hw_params(&c, &s, &params) == 0);
 assert(p.mic_rate == 16000 && r.calls == old_calls);
 s.stream = 0;
 params.rate = 48000;

 /* Every hw_params regmap failure, including read and trigger, propagates. */
 for (int fail = 1; fail <= 4; fail++) {
  memset(&r, 0, sizeof(r)); r.fail_call = fail; p.dl_pwr = true;
  assert(mt6323_hw_params(&c, &s, &params) == -EIO);
  assert(r.calls == fail && !p.newif_lock.held);
 }
 /* NEWIF cannot report PMU success after a read/request/DL enable failure. */
 for (int fail = 1; fail <= 3; fail++) {
  memset(&r, 0, sizeof(r)); r.fail_call = fail;
  assert(mt6323_newif_event(&w, 0, SND_SOC_DAPM_PRE_PMU) == -EIO);
  assert(r.calls == fail && !p.newif_lock.held);
  assert(!(r.regs[0x4000] & BIT(0)));
 }
 puts("production codec rate/trigger/error tests: PASS");
 return 0;
}
'''


class CodecRateTests(unittest.TestCase):
    def test_production_functions(self):
        source = SOURCE.read_text()
        macro_names = ('ABB_AFE_CON', 'ABB_AFE_PMIC_NEWIF_CFG0',
                       'ABB_AFE_CON11_TOP_TRIG')
        macros = '\n'.join(line for line in source.splitlines()
                           if any(re.match(r'#define ' + n + r'(?:\(|\s)', line)
                                  for n in macro_names))
        table = re.search(r'static const struct \{\s*unsigned int rate;.*?'
                          r'} mt6323_dl_rates\[\] = \{.*?\n};', source, re.S).group()
        code = STUBS + macros + '\n' + table + '\n'
        for name in ('mt6323_top_trigger', 'mt6323_hw_params', 'mt6323_newif_event'):
            code += function(source, name) + '\n'
        with tempfile.TemporaryDirectory(prefix='mt6323_rate_test_') as tmp:
            path = Path(tmp)
            (path / 'test.c').write_text(code + CHECKS)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra',
                            '-Wno-unused-parameter', '-Wno-sign-compare',
                            '-Werror', str(path / 'test.c'), '-o', str(path / 'test')],
                           check=True)
            subprocess.run([str(path / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
