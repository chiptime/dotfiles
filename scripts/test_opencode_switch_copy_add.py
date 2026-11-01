"""Tests for `opencode-switch copy`, `add`, and dynamic profile resolution."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SWITCH = ROOT / "scripts/opencode-switch-profile.sh"


class SwitchCopyAddTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oc-switch-copy-add-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.models = self.root / "models"
        self.models.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home = self.root / "home"
        (self.home / ".config/opencode").mkdir(parents=True)
        self.settings = self.root / "settings"
        self.settings.mkdir()
        self.catalogue = self.root / "catalogue"
        self.catalogue.write_text("sub/model-a\nsub/model-b\n")

        fake = self.bin / "opencode"
        fake.write_text('#!/bin/sh\n[ "$1" = models ] || exit 2\ncat "$TEST_CATALOGUE"\nexit 0\n')
        fake.chmod(0o755)

        quotas = self.bin / "ai-quotas"
        quotas.write_text("#!/bin/sh\nexit 0\n")
        quotas.chmod(0o755)

        self.profile = {
            "$comment": "test base profile",
            "small_model": "sub/model-a",
            "agents": {
                "worker": {"model": "sub/model-a", "variant": "max"},
            },
            "inherit": [],
        }
        self.default_file = self.models / "models.default.json"
        self.default_file.write_text(json.dumps(self.profile, indent=2))
        self.active_file = self.models / "models.v2.json"
        self.active_file.write_text(json.dumps(self.profile, indent=2))

        self.config = self.home / ".config/opencode/opencode.json"
        self.config.write_text(json.dumps({"agent": {"worker": {"model": "sub/model-a"}}}))

        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.root / "state"),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "OPENCODE_SWITCH_MODELS_DIR": str(self.models),
            "OPENCODE_SWITCH_INSTALLER": str(ROOT / "scripts/install-opencode-settings-v2.sh"),
            "OPENCODE_CONFIG_FILE": str(self.config),
            "OPENCODE_MODELS_FILE": str(self.active_file),
            "OPENCODE_LOCAL_FRAGMENT": str(self.root / "no-local.json"),
            "OPENCODE_SETTINGS_DIR": str(self.settings),
            "TEST_CATALOGUE": str(self.catalogue),
        }

    def run_cmd(self, *args, input_str=""):
        return subprocess.run(
            ["bash", str(SWITCH), *args],
            input=input_str,
            text=True,
            capture_output=True,
            env=self.env,
            timeout=10,
        )

    def test_copy_profile_success(self):
        result = self.run_cmd("copy", "default", "custom-profile")
        self.assertEqual(result.returncode, 0, result.stderr)
        new_file = self.models / "models.custom-profile.json"
        self.assertTrue(new_file.exists())
        self.assertEqual(json.loads(new_file.read_text()), self.profile)
        self.assertIn("creado exitosamente", result.stdout)

    def test_copy_profile_missing_args(self):
        result = self.run_cmd("copy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Uso: opencode-switch copy", result.stderr)

    def test_copy_profile_invalid_dest_name(self):
        result = self.run_cmd("copy", "default", "bad/name*")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("caracteres inválidos", result.stderr)

    def test_copy_profile_reserved_name(self):
        result = self.run_cmd("copy", "default", "v2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reservado", result.stderr)

    def test_copy_profile_nonexistent_source(self):
        result = self.run_cmd("copy", "nonexistent-src", "new-dest")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no encontrado", result.stderr)

    def test_add_profile_non_interactive(self):
        result = self.run_cmd("add", "new-test", "default")
        self.assertEqual(result.returncode, 0, result.stderr)
        new_file = self.models / "models.new-test.json"
        self.assertTrue(new_file.exists())

    def test_switch_to_custom_profile_dynamically(self):
        custom = self.models / "models.custom-dynamic.json"
        custom_data = {
            **self.profile,
            "agents": {"worker": {"model": "sub/model-b", "variant": "high"}},
        }
        custom.write_text(json.dumps(custom_data, indent=2))

        result = self.run_cmd("custom-dynamic")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cambiando a perfil: CUSTOM-DYNAMIC", result.stdout)
        active = json.loads(self.active_file.read_text())
        self.assertEqual(active["agents"]["worker"]["model"], "sub/model-b")
        config = json.loads(self.config.read_text())
        self.assertEqual(config["agent"]["worker"]["model"], "sub/model-b")


if __name__ == "__main__":
    unittest.main()
