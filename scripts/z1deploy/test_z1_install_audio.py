#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("z1_install_audio.sh")

class AudioInstallerTest(unittest.TestCase):
    def test_dry_run_is_local_and_describes_atomic_install(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "snd-soc-mt6323.ko"
            src.write_bytes(b"candidate")
            env = os.environ.copy()
            env.pop("Z1LOG", None)
            p = subprocess.run([str(SCRIPT), "--dry-run", "install", str(src)], env=env, text=True, capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("dry-run target=/system/lib/modules/snd-soc-mt6323.ko", p.stdout)
            self.assertIn("atomic=cp,cmp,chmod,chown,mv", p.stdout)

    def test_script_never_unloads_or_loads_module_and_uses_atomic_ops(self):
        text = SCRIPT.read_text()
        self.assertNotRegex(text, r"\b(rmmod|insmod)\b")
        self.assertIn("tmp=$TARGET.z1tmp.\\$\\$", text)
        self.assertIn("cmp -s $STAGE/$REMOTE $TARGET", text)
        self.assertIn('mv -f \\"\\$tmp\\" $TARGET', text)
        self.assertIn("mount -o remount,ro /system", text)

    def test_status_and_rollback_are_exposed(self):
        text = SCRIPT.read_text()
        self.assertIn("status) status;;", text)
        self.assertIn("rollback) rollback;;", text)
        self.assertIn("$BACKUP", text)
        self.assertNotIn("cat $STAGE/out.txt", text)

if __name__ == "__main__":
    unittest.main()
