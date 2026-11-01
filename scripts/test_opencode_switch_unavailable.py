"""Public-interface tests for unavailable models, with no network or real config."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SWITCH = Path(os.environ.get("OC_SWITCH_SCRIPT", ROOT / "scripts/opencode-switch-profile.sh"))


class UnavailableModelsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oc-switch-unavailable-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.models = self.root / "model profiles"
        self.models.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home = self.root / "home"
        (self.home / ".config/opencode").mkdir(parents=True)
        self.settings = self.root / "settings"
        self.settings.mkdir()
        self.catalogue = self.root / "catalogue"
        self.catalogue.write_text("opencode/live-free\nsub/new\nsub/other\n")
        fake = self.bin / "opencode"
        fake.write_text('#!/bin/sh\n[ "$1" = models ] || exit 2\ncat "$TEST_CATALOGUE"\nexit "${TEST_CATALOGUE_RC:-0}"\n')
        fake.chmod(0o755)
        quotas = self.bin / "ai-quotas"
        quotas.write_text("#!/bin/sh\nexit 0\n")
        quotas.chmod(0o755)
        self.profile = {
            "$comment": "keep metadata",
            "small_model": "opencode/live-free",
            "agents": {
                "worker": {"model": "sub/retired", "variant": "max"},
                "helper": "sub/retired",
                "untouched": {"model": "sub/other", "variant": "high"},
            },
            "inherit": ["inherited"],
        }
        self.source = self.models / "models.openai.json"
        self.source.write_text(json.dumps(self.profile))
        self.active = self.models / "models.v2.json"
        self.active.write_text(json.dumps({**self.profile, "$comment": "previous active"}))
        self.other = self.models / "models.claude.json"
        self.other.write_bytes(self.source.read_bytes())
        for name in ("default", "zai", "opencode-go"):
            (self.models / f"models.{name}.json").write_bytes(self.source.read_bytes())
        self.unrelated = self.models / "models.unrelated.json"
        self.unrelated.write_text(json.dumps({
            "small_model": "opencode/live-free",
            "agents": {"worker": {"model": "sub/other", "variant": "high"}},
            "inherit": [],
        }))
        self.config = self.home / ".config/opencode/opencode.json"
        self.config.write_text(json.dumps({"agent": {
            name: {"model": "sub/other", "prompt": "keep", "permission": {"edit": "deny"}}
            for name in self.profile["agents"]
        }}))
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.root / "state"),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "OPENCODE_SWITCH_MODELS_DIR": str(self.models),
            "OPENCODE_SWITCH_INSTALLER": str(ROOT / "scripts/install-opencode-settings-v2.sh"),
            "OPENCODE_CONFIG_FILE": str(self.config),
            "OPENCODE_MODELS_FILE": str(self.active),
            "OPENCODE_LOCAL_FRAGMENT": str(self.root / "no-local.json"),
            "OPENCODE_SETTINGS_DIR": str(self.settings),
            "TEST_CATALOGUE": str(self.catalogue),
        }
        self.env.pop("OPENCODE_MODELS_LIST", None)

    def run_switch(self, answers="", action="openai"):
        return subprocess.run(
            ["bash", str(SWITCH), action], input=answers, text=True,
            capture_output=True, env=self.env, timeout=15,
        )

    def snapshot(self):
        return {p: p.read_bytes() for p in (*self.models.glob("models.*.json"), self.config)}

    def assert_rejected_without_writes(self, answers=""):
        before = self.snapshot()
        result = self.run_switch(answers)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(list(self.root.rglob("*.bak*")))
        return result

    def test_replacement_is_saved_once_for_all_affected_entries(self):
        old_source = self.source.read_bytes()
        old_active = self.active.read_bytes()
        original_profiles = {p: p.read_bytes() for p in self.models.glob("models.*.json")}
        result = self.run_switch("sub/new\ns\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sub/retired", result.stdout)
        self.assertIn("worker", result.stdout)
        self.assertIn("helper", result.stdout)
        source = json.loads(self.source.read_text())
        for name in ("worker", "helper"):
            self.assertEqual(source["agents"][name], {"model": "sub/new", "variant": ""})
        self.assertEqual(source["agents"]["untouched"], self.profile["agents"]["untouched"])
        self.assertEqual(source["$comment"], self.profile["$comment"])
        self.assertEqual(source["inherit"], self.profile["inherit"])
        self.assertEqual(self.active.read_bytes(), self.source.read_bytes())
        for path, data in original_profiles.items():
            if path == self.unrelated:
                self.assertEqual(path.read_bytes(), data)
                continue
            saved = json.loads(path.read_text())
            for name in ("worker", "helper"):
                self.assertEqual(saved["agents"][name], {"model": "sub/new", "variant": ""})
            self.assertEqual(saved["agents"]["untouched"], self.profile["agents"]["untouched"])
            self.assertIn(path.name, result.stdout)
        config = json.loads(self.config.read_text())
        self.assertEqual(config["agent"]["worker"]["model"], "sub/new")
        self.assertEqual(config["agent"]["worker"]["prompt"], "keep")
        self.assertEqual(config["agent"]["worker"]["permission"], {"edit": "deny"})
        backups = [p.read_bytes() for p in (self.root / "state").rglob("*.json")]
        self.assertIn(old_source, backups)
        self.assertIn(old_active, backups)
        for path, data in original_profiles.items():
            if path != self.unrelated:
                self.assertIn(data, backups)

    def test_cancel_eof_and_unknown_replacement_do_not_write(self):
        for answers in ("", "\n", "sub/not-in-catalogue\n", "sub/new\nn\n", "sub/new\n"):
            with self.subTest(answers=answers):
                self.assert_rejected_without_writes(answers)

    def test_missing_provider_is_inconclusive(self):
        self.catalogue.write_text("opencode/live-free\n")
        result = self.assert_rejected_without_writes("opencode/live-free\ns\n")
        self.assertIn("sub", result.stdout + result.stderr)
        self.assertIn("proveedor", result.stdout + result.stderr)

    def test_empty_or_failed_catalogue_does_not_write(self):
        self.catalogue.write_text("")
        self.assert_rejected_without_writes()
        self.catalogue.write_text("opencode/live-free\nsub/new\n")
        self.env["TEST_CATALOGUE_RC"] = "1"
        self.assert_rejected_without_writes("sub/new\ns\n")

    def test_free_model_cannot_silently_become_paid(self):
        self.profile["small_model"] = "opencode/retired-free"
        self.profile["agents"] = {}
        self.source.write_text(json.dumps(self.profile))
        self.assert_rejected_without_writes("sub/new\ns\n")

    def test_free_model_can_be_replaced_by_available_free_model(self):
        self.profile["small_model"] = "opencode/retired-free"
        self.profile["agents"] = {}
        self.source.write_text(json.dumps(self.profile))
        result = self.run_switch("opencode/live-free\ns\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.source.read_text())["small_model"], "opencode/live-free")

    def test_apply_can_repair_active_map(self):
        result = self.run_switch("sub/new\ns\n", action="apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.source.read_text())["agents"]["worker"]["model"], "sub/new")
        self.assertEqual(json.loads(self.active.read_text())["agents"]["worker"]["model"], "sub/new")

    def test_global_replacement_covers_small_model_and_legacy_in_inactive_profile(self):
        other = json.loads(self.other.read_text())
        other["$comment"] = "sub/retired is mentioned as documentation only"
        other["small_model"] = "sub/retired"
        other["agents"]["legacy-only"] = "sub/retired"
        self.other.write_text(json.dumps(other))
        result = self.run_switch("sub/new\ns\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(self.other.read_text())
        self.assertEqual(saved["small_model"], "sub/new")
        self.assertEqual(saved["agents"]["legacy-only"], {"model": "sub/new", "variant": ""})
        self.assertEqual(saved["$comment"], other["$comment"])
        self.assertEqual(result.stdout.count("Modelo no disponible: sub/retired"), 1)

    def test_global_agent_named_small_model_preserves_global_field(self):
        other = json.loads(self.other.read_text())
        other["small_model"] = "opencode/live-free"
        other["agents"]["small_model"] = {"model": "sub/retired", "variant": "max"}
        self.other.write_text(json.dumps(other))
        original = self.snapshot()
        for action in ("openai", "apply"):
            with self.subTest(action=action):
                for path, data in original.items():
                    path.write_bytes(data)
                result = self.run_switch("sub/new\ny\n", action=action)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                saved = json.loads(self.other.read_text())
                self.assertEqual(saved["small_model"], "opencode/live-free")
                self.assertEqual(saved["agents"]["small_model"], {"model": "sub/new", "variant": ""})

    def test_valid_profile_needs_no_replacement_or_confirmation(self):
        self.catalogue.write_text(self.catalogue.read_text() + "sub/retired\n")
        old_source = self.source.read_bytes()
        result = self.run_switch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.source.read_bytes(), old_source)
        self.assertEqual(self.active.read_bytes(), old_source)
        self.assertIn("Cambiando a perfil: OPENAI", result.stdout)
        self.assertNotIn("reemplazo", result.stdout)

    def test_existing_profile_commands_and_aliases_still_apply(self):
        self.catalogue.write_text(self.catalogue.read_text() + "sub/retired\n")
        for action in ("default", "zai", "claude", "subs", "openai", "sol",
                       "subs-sol", "subs-openai", "opencode-go", "zen", "go", "apply"):
            with self.subTest(action=action):
                result = self.run_switch(action=action)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                config = json.loads(self.config.read_text())
                self.assertEqual(config["agent"]["worker"]["model"], "sub/retired")

    def test_later_cancellation_discards_earlier_staged_replacement(self):
        self.profile["small_model"] = "opencode/retired-free"
        self.source.write_text(json.dumps(self.profile))
        self.assert_rejected_without_writes("opencode/live-free\n\n")

    def test_final_confirmation_eof_never_commits(self):
        original = self.snapshot()
        for action in ("openai", "apply"):
            for answer in ("s", "S", "y", "Y"):
                with self.subTest(action=action, answer=answer):
                    for path, data in original.items():
                        path.write_bytes(data)
                    result = self.run_switch("sub/new\n" + answer, action=action)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(self.snapshot(), original)
                    self.assertIn("Operación cancelada", result.stdout)

    def test_force_apply_does_not_supply_confirmation(self):
        before = self.snapshot()
        result = subprocess.run(
            ["bash", str(SWITCH), "--apply", "openai"],
            input="sub/new\n", text=True, capture_output=True,
            env=self.env, timeout=15,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_tty_free_candidates_and_escape(self):
        import pty

        self.profile["small_model"] = "opencode/retired-free"
        self.profile["agents"] = {}
        self.source.write_text(json.dumps(self.profile))
        choices = self.root / "fzf-choices"
        fake = self.bin / "fzf"
        fake.write_text(
            '#!/bin/sh\ncat > "$TEST_FZF_CHOICES"\n'
            '[ "$TEST_FZF_CANCEL" = 0 ] || exit 130\n'
            "printf '%s\\n' opencode/live-free\n"
        )
        fake.chmod(0o755)
        original = self.snapshot()
        for cancel in ("1", "0"):
            with self.subTest(cancel=cancel):
                for path, data in original.items():
                    path.write_bytes(data)
                master, slave = pty.openpty()
                try:
                    process = subprocess.Popen(
                        ["bash", str(SWITCH), "openai"],
                        stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, env={
                            **self.env, "TEST_FZF_CHOICES": str(choices),
                            "TEST_FZF_CANCEL": cancel,
                        },
                    )
                    os.close(slave)
                    slave = None
                    if cancel == "0":
                        os.write(master, b"y\n")
                    stdout, stderr = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 1 if cancel == "1" else 0, stdout + stderr)
                    self.assertEqual(choices.read_text(), "opencode/live-free\n")
                    if cancel == "1":
                        self.assertEqual(self.snapshot(), original)
                finally:
                    if slave is not None:
                        os.close(slave)
                    os.close(master)


if __name__ == "__main__":
    unittest.main()
