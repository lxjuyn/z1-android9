#!/usr/bin/env python3
"""Check the Z1 audio setup against values recovered from the factory kernel.

The MT6323 audio fields are undocumented. These assertions preserve the small
part of the factory bring-up that is backed by disassembly, and prevent the AFE
regmap from describing registers outside its DT mapping.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
CODEC = ROOT / "fork_linux/sound/soc/codecs/mt6323.c"
AFE = ROOT / "fork_linux/sound/soc/mediatek/mt6572/mt6572-afe-pcm.c"
DTS = ROOT / "fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dts"


def block(source, start):
    begin = source.index(start)
    brace = source.index("{", begin)
    depth = 0
    for end in range(brace, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[begin : end + 1]
    raise ValueError("unclosed block: " + start)


def function(source, name):
    return block(source, "static int " + name + "(")


class FactoryAudioContract(unittest.TestCase):
    def test_codec_idle_bias_and_depop_match_factory(self):
        source = CODEC.read_text()
        table = block(source, "static const struct reg_sequence mt6323_codec_init[]")
        writes = re.findall(
            r"\{\s*(?:AUDTOP_CON\((\d+)\)|([A-Z0-9_]+))\s*,\s*(0x[0-9a-fA-F]+)\s*\}",
            table,
        )
        sequence = [
            (f"AUDTOP_CON{number}" if number else name, value.lower())
            for number, name, value in writes
        ]

        self.assertIn(("AUDTOP_CON5", "0x1114"), sequence)
        con6 = [value for name, value in sequence if name == "AUDTOP_CON6"]
        self.assertEqual(con6[-2:], ["0x37a2", "0x37e2"])

    def test_playback_does_not_drop_factory_con5_bias_bit(self):
        event = function(CODEC.read_text(), "mt6323_dac_event")
        values = re.findall(
            r"AUDTOP_CON\(5\)\s*,\s*(0x[0-9a-fA-F]+)", event
        )
        self.assertEqual([value.lower() for value in values], ["0x1114", "0x1114"])

    def test_afe_dt_window_covers_regmap(self):
        afe_source = AFE.read_text()
        dts_source = DTS.read_text()
        max_register = int(
            re.search(r"\.max_register\s*=\s*(0x[0-9a-fA-F]+)", afe_source).group(1),
            0,
        )
        stride = int(
            re.search(r"\.reg_stride\s*=\s*(0x[0-9a-fA-F]+|\d+)", afe_source).group(1),
            0,
        )
        match = re.search(
            r"audsys:\s*audio@11140000\s*\{(?P<body>.*?)\n\s*\};",
            dts_source,
            re.S,
        )
        self.assertIsNotNone(match)
        reg = re.search(
            r"reg\s*=\s*<\s*0x11140000\s+(0x[0-9a-fA-F]+)\s*>",
            match.group("body"),
        )
        self.assertIsNotNone(reg)
        size = int(reg.group(1), 0)
        self.assertGreaterEqual(size, max_register + stride)


if __name__ == "__main__":
    unittest.main(verbosity=2)
