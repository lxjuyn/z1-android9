#!/usr/bin/env python3
"""Regression test for the rootd output publication protocol."""

from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


ROOTD = Path(__file__).with_name("z1rootd.sh")


def run_script_function() -> str:
    source = ROOTD.read_text()
    match = re.search(
        r"(?ms)^run_script\(\) \{.*?^\}\n\n# --- start-up",
        source,
    )
    if not match:
        raise AssertionError("could not extract run_script from z1rootd.sh")
    return match.group(0).split("\n\n# --- start-up", 1)[0] + "\n"


class RootdOutputTest(unittest.TestCase):
    def test_command_can_read_previous_output_without_self_replicating(self):
        function = run_script_function()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "out.txt"
            command = root / "command.sh"
            output.write_text("OLD\n")
            command.write_text(
                f'cat "{output}"\nprintf \'MARK\\n\'\n'
            )
            harness = textwrap.dedent(
                f"""
                #!/bin/sh
                set -eu
                T={root}
                RM=/bin/rm
                CHMOD=/bin/chmod
                CHOWN=/bin/chown
                MV=/bin/mv
                DATE=/bin/date
                SH=/bin/sh
                TIMEOUT=/bin/timeout
                MAX_SECS=5
                SHELL_UID=$(id -u)
                SHELL_GID=$(id -g)
                log() {{ printf '%s\\n' "$*" >> "$T/log"; }}
                uptime_s() {{ printf '0'; }}
                file_size() {{ wc -c < "$1"; }}
                {function}
                run_script "{command}" "{output}" selfread
                cat "{output}"
                test ! -e "{output}.tmp.$$"
                """
            )
            result = subprocess.run(
                ["/bin/sh"],
                input=harness,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "OLD\nMARK\n###RC=0 ###DONE\n",
            )
            self.assertLess(output.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
