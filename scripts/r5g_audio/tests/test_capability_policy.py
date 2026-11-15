#!/usr/bin/env python3
"""Static contract checks for the production primary audio policy."""

import os
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


DEFAULT_POLICY = (
    Path(__file__).resolve().parents[3]
    / "stock"
    / "primary_audio_policy_configuration.xml"
)
POLICY = Path(os.environ.get("Z1_AUDIO_POLICY", DEFAULT_POLICY))


class CapabilityPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = POLICY.read_text(encoding="utf-8")
        if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
            raise AssertionError("policy must not contain a DTD or entity")
        cls.root = ET.fromstring(raw)

    def test_primary_module_version(self):
        modules = [self.root] if self.root.tag == "module" else self.root.findall(".//module")
        self.assertEqual([m.get("name") for m in modules], ["primary"])
        self.assertEqual(modules[0].get("halVersion"), "2.0")

    def test_only_supported_output_profile(self):
        outputs = self.root.findall("./mixPorts/mixPort[@role='source']")
        self.assertEqual(len(outputs), 1)
        output = outputs[0]
        self.assertEqual(output.get("name"), "primary output")
        self.assertEqual(output.get("flags"), "AUDIO_OUTPUT_FLAG_PRIMARY")
        profiles = output.findall("./profile")
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile.get("format"), "AUDIO_FORMAT_PCM_16_BIT")
        self.assertEqual(profile.get("samplingRates"), "48000")
        self.assertEqual(profile.get("channelMasks"), "AUDIO_CHANNEL_OUT_STEREO")

    def test_no_unimplemented_capture_surface(self):
        self.assertEqual(self.root.findall("./mixPorts/mixPort[@role='sink']"), [])
        self.assertEqual(
            [
                p
                for p in self.root.findall("./devicePorts/devicePort")
                if (p.get("type") or "").startswith("AUDIO_DEVICE_IN_")
            ],
            [],
        )
        self.assertNotIn(
            "AUDIO_DEVICE_IN_BUILTIN_MIC",
            {p.get("type") for p in self.root.findall("./devicePorts/devicePort")},
        )

    def test_output_device_set_is_exact(self):
        ports = self.root.findall("./devicePorts/devicePort[@role='sink']")
        by_name = {p.get("tagName"): p.get("type") for p in ports}
        self.assertEqual(len(ports), len(by_name))
        self.assertEqual(
            by_name,
            {
                "Speaker": "AUDIO_DEVICE_OUT_SPEAKER",
                "Earpiece": "AUDIO_DEVICE_OUT_EARPIECE",
                "Wired Headset": "AUDIO_DEVICE_OUT_WIRED_HEADSET",
                "Wired Headphones": "AUDIO_DEVICE_OUT_WIRED_HEADPHONE",
            },
        )

    def test_routes_and_default_are_closed(self):
        attached = [
            item.text.strip()
            for item in self.root.findall("./attachedDevices/item")
            if item.text
        ]
        self.assertEqual(len(attached), len(set(attached)))
        self.assertEqual(attached, ["Speaker", "Earpiece"])
        self.assertEqual(self.root.findtext("./defaultOutputDevice"), "Speaker")

        mix_ports = {
            p.get("name"): p
            for p in self.root.findall("./mixPorts/mixPort")
        }
        devices = {
            p.get("tagName"): p
            for p in self.root.findall("./devicePorts/devicePort")
        }
        routes = self.root.findall("./routes/route")
        self.assertEqual(len(routes), len(devices))
        route_sinks = [route.get("sink") for route in routes]
        self.assertEqual(len(route_sinks), len(set(route_sinks)))
        self.assertEqual(set(route_sinks), set(devices))
        for route in routes:
            self.assertEqual(route.get("type"), "mix")
            self.assertIn(route.get("sink"), devices)
            self.assertEqual(devices[route.get("sink")].get("role"), "sink")
            sources = [
                source.strip()
                for source in (route.get("sources") or "").split(",")
                if source.strip()
            ]
            self.assertTrue(sources)
            for source in sources:
                self.assertIn(source, mix_ports)
                self.assertEqual(mix_ports[source].get("role"), "source")


if __name__ == "__main__":
    unittest.main(verbosity=2)
