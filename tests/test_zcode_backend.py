"""Native ZCode profile, resume, and failure contracts; no provider requests."""
from __future__ import annotations

import argparse
import json
import importlib.util
import types
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import duet


class ZCodeBackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cwd = pathlib.Path(self.directory.name)
        (self.cwd / "zcode.json").write_text(json.dumps({"features": {"mcp": False}}))
        self.home = self.cwd / "home"
        home_patch = mock.patch.object(pathlib.Path, "home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.agent = duet.Agent(name="glm", backend="zcode", role="reviewer",
                                model="account:zai-individual-coding-plan/GLM-5.3")
        self.catalog = self.cwd / "catalog.json"
        self.catalog.write_text(json.dumps({"schemaVersion": 1, "config": {
            "providerConfigRules": {"providerRules": [
                {"providerId": "account:zai-individual-coding-plan", "config": {
                    "builtinModelIds": ["GLM-5.3", "GLM-5.3-Flash"],
                    "api": {"type": "anthropic-messages", "baseUrl": "https://api.example"}}},
                {"providerId": "account:other", "config": {"builtinModelIds": ["other"]}},
            ]},
            "modelConfigRules": {"modelRules": [
                {"modelMatch": ".*glm-5.*", "config": {"optionSpecs": {
                    "reasoningLevel": {"values": ["low", "high", "max"]}}}}],
                "modelApiRules": [], "providerSiteRules": [],
                "templateModelRules": [], "builtinProviderModelRules": []},
        }}))
        self.launch = mock.patch.object(duet, "_zcode_command", return_value=(
            ["zcode"], {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(self.catalog)}))
        self.launch.start()
        self.addCleanup(self.launch.stop)

    def invoke(self, **overrides):
        args = dict(agent=self.agent, system_prompt="system {literal}", message="peer message",
                    cwd=self.cwd, timeout=60, dry=False, reasoning="max")
        args.update(overrides)
        return duet.call_zcode(**args)

    @staticmethod
    def result(session="sess_abc", response="review complete", **kwargs):
        return 0, json.dumps(dict(sessionId=session, response=response, **kwargs)), ""

    def test_fresh_call_uses_private_profile_and_native_read_only_policy(self):
        seen = {}

        def run(command, **kwargs):
            profile = pathlib.Path(kwargs["env_overrides"]["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"])
            seen["profile"] = profile
            data = json.loads(profile.read_text())
            self.assertEqual(0o600, profile.stat().st_mode & 0o777)
            self.assertEqual([{ "providerId": "account:other", "config": {"visibility": "hidden"}}],
                             data["config"]["providerConfigRules"]["providerRules"])
            self.assertEqual([
                {"providerId": "account:zai-individual-coding-plan", "modelId": "GLM-5.3-Flash",
                 "config": {"enabled": False}},
                {"providerId": "account:zai-individual-coding-plan", "modelId": "GLM-5.3",
                 "config": {"optionSpecs": {"reasoningLevel": {"values": ["max"]}}}},
                {"providerId": "account:other", "modelId": "other", "config": {"enabled": False}},
            ], sorted(data["config"]["modelConfigRules"]["providerModelRules"],
                      key=lambda row: (row["providerId"] == "account:other", row["modelId"] == "GLM-5.3")))
            self.assertEqual({"providerId": "account:zai-individual-coding-plan",
                              "modelId": "GLM-5.3", "options": {"reasoningLevel": "max"}},
                             data["config"]["defaultModelSelection"])
            self.assertNotIn("apiKey", profile.read_text())
            self.assertEqual("plan", command[command.index("--mode") + 1])
            self.assertEqual("disabled", command[command.index("--workflow-mode") + 1])
            denied = command[command.index("--disallowed-tools") + 1].split(",")
            for tool in ("Task", "Agent", "Skill", "Bash", "Write", "Edit", "CreateWorkflow",
                         "AmendWorkflow", "FillWorkflowHole", "ResumeWorkflowRun", "SaveWorkflow",
                         "OffPeakCreate", "CronCreate", "CronUpdate", "CronDelete"):
                self.assertIn(tool, denied)
            self.assertNotIn("OffPeakTask", denied)
            self.assertNotIn("RunWorkflow", denied)
            self.assertNotIn("--resume", command)
            self.assertIn("system {literal}", command[-1])
            self.assertIsNone(kwargs["stdin"])
            return self.result()

        with mock.patch.object(duet, "_run", side_effect=run), mock.patch.dict(os.environ, {"TEST_SECRET": "not-in-profile"}):
            before = dict(os.environ)
            reply, session = self.invoke()
            self.assertEqual(before, dict(os.environ))
        self.assertEqual(("review complete", "sess_abc"), (reply, session))
        self.assertFalse(seen["profile"].exists())
        self.assertEqual({"model": self.agent.model, "reasoning": "max"}, self.agent.zcode_selection)

    def test_resume_is_pinned_and_reasserts_policy(self):
        with mock.patch.object(duet, "_run", return_value=self.result()) as run:
            _, self.agent.session_id = self.invoke()
            self.invoke()
        command = run.call_args.args[0]
        self.assertEqual("sess_abc", command[command.index("--resume") + 1])
        self.assertIn("--disallowed-tools", command)
        self.assertNotIn("--continue", command)

    def test_resume_rejects_model_or_effort_change_before_start(self):
        self.agent.session_id = "sess_abc"
        self.agent.zcode_selection = {"model": self.agent.model, "reasoning": "high"}
        with mock.patch.object(duet, "_run") as run:
            with self.assertRaisesRegex(ValueError, "original model/effort"):
                self.invoke()
            run.assert_not_called()

    def test_untracked_native_session_cannot_be_resumed(self):
        self.agent.session_id = "sess_external"
        with self.assertRaisesRegex(ValueError, "original model/effort"):
            self.invoke()

    def test_cwd_resume_and_extra_args_are_rejected(self):
        self.agent.session_id = "latest"
        with self.assertRaisesRegex(ValueError, "explicit sess_"):
            self.invoke()
        self.agent.session_id = None
        self.agent.extra_args = ["--mode", "yolo"]
        with self.assertRaisesRegex(ValueError, "extra_args"):
            self.invoke()

    def test_only_native_account_model_ids_are_accepted(self):
        for model in (None, "GLM-5.3", "opencode/glm-5.3", "account:zai/"):
            with self.subTest(model=model):
                self.agent.model = model
                with self.assertRaisesRegex(ValueError, "native account"):
                    self.invoke()

    def test_coder_role_is_rejected(self):
        self.agent.role = "coder"
        with self.assertRaisesRegex(ValueError, "read-only"):
            self.invoke()

    def test_profile_is_removed_on_failure_and_does_not_publish_pin(self):
        paths = []

        def run(command, **kwargs):
            paths.append(pathlib.Path(kwargs["env_overrides"]["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"]))
            return 1, "", "Select a model before continuing"

        with mock.patch.object(duet, "_run", side_effect=run):
            with self.assertRaises(duet.AgentRunError) as caught:
                self.invoke()
        self.assertEqual(duet.FINISHED_AGENT_ERROR, caught.exception.finished_reason)
        self.assertFalse(paths[0].exists())
        self.assertIsNone(self.agent.zcode_selection)

    def test_timeout_has_the_existing_timeout_contract(self):
        with mock.patch.object(duet, "_run", return_value=(124, "", "timeout")):
            with self.assertRaises(duet.AgentRunError) as caught:
                self.invoke()
        self.assertEqual(duet.FINISHED_TIMEOUT, caught.exception.finished_reason)

    def test_session_change_is_not_accepted(self):
        self.agent.session_id = "sess_expected"
        self.agent.zcode_selection = {"model": self.agent.model, "reasoning": "max"}
        with mock.patch.object(duet, "_run", return_value=self.result("sess_different")):
            with self.assertRaisesRegex(duet.AgentRunError, "changed its pinned"):
                self.invoke()

    def test_json_failure_and_missing_reply_are_not_success(self):
        cases = ["not json", "[]", '{}', json.dumps({"sessionId": "sess_a", "response": ""}),
                 json.dumps({"sessionId": "sess_a", "response": "partial", "projection": {"status": "error"}})]
        for output in cases:
            with self.subTest(output=output), mock.patch.object(duet, "_run", return_value=(0, output, "")):
                with self.assertRaises(duet.AgentRunError):
                    self.invoke()

    def test_supported_reasoning_levels_are_native_profile_values(self):
        for level in ("low", "high", "max"):
            with self.subTest(level=level):
                def run(command, **kwargs):
                    profile = json.loads(pathlib.Path(kwargs["env_overrides"]["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"]).read_text())
                    self.assertEqual(level, profile["config"]["defaultModelSelection"]["options"]["reasoningLevel"])
                    return self.result()
                with mock.patch.object(duet, "_run", side_effect=run):
                    self.invoke(reasoning=level)

    def test_invalid_catalog_selection_never_starts_a_model(self):
        for level in ("minimal", "medium", "xhigh"):
            with self.subTest(level=level), mock.patch.object(duet, "_run") as run:
                with self.assertRaisesRegex(duet.AgentRunError, "reasoning level is absent"):
                    self.invoke(reasoning=level)
                run.assert_not_called()
        self.agent.model = "account:zai-individual-coding-plan/missing-model"
        with mock.patch.object(duet, "_run") as run:
            with self.assertRaisesRegex(duet.AgentRunError, "account model is absent"):
                self.invoke()
            run.assert_not_called()

    def test_catalog_layers_preserve_provider_specific_effort_values(self):
        data = json.loads(self.catalog.read_text())
        data["config"]["modelConfigRules"]["modelApiRules"] = [
            {"modelMatch": "glm-5\\.3", "apiTypeMatch": "anthropic-messages",
             "config": {"optionSpecs": {"reasoningLevel": {"values": ["high"]}}}},
        ]
        self.catalog.write_text(json.dumps(data))
        with mock.patch.object(duet, "_run") as run:
            with self.assertRaisesRegex(duet.AgentRunError, "reasoning level is absent"):
                self.invoke(reasoning="max")
            run.assert_not_called()

    def test_catalog_patterns_are_anchored_and_api_case_is_preserved(self):
        data = json.loads(self.catalog.read_text())
        data["config"]["modelConfigRules"]["modelApiRules"] = [
            {"modelMatch": "glm-5", "apiTypeMatch": "anthropic-messages",
             "config": {"optionSpecs": {"reasoningLevel": {"values": ["low"]}}}},
            {"modelMatch": "glm-5\\.3", "apiTypeMatch": "ANTHROPIC-MESSAGES",
             "config": {"optionSpecs": {"reasoningLevel": {"values": ["low"]}}}},
        ]
        self.catalog.write_text(json.dumps(data))
        with mock.patch.object(duet, "_run", return_value=self.result()):
            self.invoke(reasoning="max")

    def test_path_cli_does_not_borrow_desktop_catalog(self):
        selection = {"providerId": "account:zai-individual-coding-plan", "modelId": "GLM-5.3"}
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "another installation's catalog"):
                duet._zcode_catalog(selection, {})

    def test_path_cli_resolves_catalog_from_its_real_installation(self):
        self.launch.stop()
        for bundled in (True, False):
            with self.subTest(bundled=bundled):
                installation = self.cwd / str(bundled)
                entry = installation / "apps/zcode-cli/packages/cli/dist/zcode.cjs"
                entry.parent.mkdir(parents=True)
                entry.write_text("// native entry")
                catalog = (entry.parent / "provider/zcode-builtin.json" if bundled else
                           installation / "config/provider/zcode-builtin.json")
                catalog.parent.mkdir(parents=True)
                catalog.write_text(self.catalog.read_text())
                link = installation / "zcode"
                link.symlink_to(entry)
                with mock.patch.object(duet.shutil, "which", return_value=str(link)), \
                        mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": ""}):
                    command, env = duet._zcode_command()
                self.assertEqual([str(link)], command)
                self.assertEqual(str(catalog.resolve()), env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"])
                selection = {"providerId": "account:zai-individual-coding-plan", "modelId": "GLM-5.3"}
                self.assertEqual(json.loads(catalog.read_text()), duet._zcode_catalog(selection, env))

    def test_path_cli_honors_explicit_catalog(self):
        self.launch.stop()
        with mock.patch.object(duet.shutil, "which", return_value="/runtime/zcode"), \
                mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(self.catalog)}):
            command, env = duet._zcode_command()
        self.assertEqual(["/runtime/zcode"], command)
        self.assertEqual(str(self.catalog), env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"])

    def test_path_link_into_desktop_bundle_uses_that_bundle_catalog(self):
        self.launch.stop()
        resources = self.cwd / "Resources"
        entry = resources / "glm/zcode.cjs"
        entry.parent.mkdir(parents=True)
        entry.write_text("// desktop entry")
        catalog = resources / "config/provider/zcode-builtin.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(self.catalog.read_text())
        link = self.cwd / "bin/zcode"
        link.parent.mkdir()
        link.symlink_to(entry)
        with mock.patch.object(duet, "_ZCODE_DESKTOP_RESOURCES", self.cwd / "elsewhere"), \
                mock.patch.object(duet.shutil, "which", return_value=str(link)), \
                mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": ""}):
            command, env = duet._zcode_command()
        self.assertEqual([str(link)], command)
        self.assertEqual(str(catalog.resolve()), env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"])

    def test_wrapper_beside_a_config_folder_does_not_use_it(self):
        self.launch.stop()
        wrapper = self.cwd / "install/bin/zcode"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n")
        catalog = self.cwd / "install/config/provider/zcode-builtin.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(self.catalog.read_text())
        with mock.patch.object(duet, "_ZCODE_DESKTOP_RESOURCES", self.cwd / "elsewhere"), \
                mock.patch.object(duet.shutil, "which", return_value=str(wrapper)), \
                mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": ""}):
            with self.assertRaisesRegex(ValueError, "PATH ZCode CLI's catalog"):
                duet._zcode_command()

    def test_missing_path_catalog_does_not_borrow_desktop_catalog(self):
        self.launch.stop()
        desktop = self.cwd / "desktop"
        catalog = desktop / "config/provider/zcode-builtin.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(self.catalog.read_text())
        entry = desktop / "glm/zcode.cjs"
        entry.parent.mkdir()
        entry.write_text("// desktop entry")
        with mock.patch.object(duet, "_ZCODE_DESKTOP_RESOURCES", desktop), \
                mock.patch.object(duet.shutil, "which", return_value=str(self.cwd / "cli/zcode")), \
                mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": ""}):
            with self.assertRaisesRegex(ValueError, "PATH ZCode CLI's catalog"):
                duet._zcode_command()

    def test_catalog_snapshot_freezes_validation_and_disables_remote_refresh(self):
        before = json.loads(self.catalog.read_text())
        snapshots = []

        def run(command, **kwargs):
            env = kwargs["env_overrides"]
            snapshot = pathlib.Path(env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"])
            snapshots.append(snapshot)
            self.catalog.write_text('{"changed":true}')
            self.assertEqual(before, json.loads(snapshot.read_text()))
            self.assertEqual(0o600, snapshot.stat().st_mode & 0o777)
            self.assertEqual("", env["ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE"])
            return self.result()

        with mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE": "elsewhere"}), \
                mock.patch.object(duet, "_run", side_effect=run):
            self.invoke()
            self.assertEqual("elsewhere", os.environ["ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE"])
        self.assertFalse(snapshots[0].exists())

    def test_native_site_url_normalization_and_template_api_inheritance(self):
        data = json.loads(self.catalog.read_text())
        provider = data["config"]["providerConfigRules"]["providerRules"][0]
        provider["templateId"] = "native-template"
        del provider["config"]["api"]
        data["config"]["providerConfigRules"]["templateRules"] = [{
            "templateId": "native-template", "config": {"api": {
                "type": "anthropic-messages", "baseUrl": "https://API.Example:443/"}}}]
        data["config"]["modelConfigRules"]["providerSiteRules"] = [{
            "modelMatch": "glm-5\\.3", "apiTypeMatch": "anthropic-messages",
            "baseUrlMatch": "https://api\\.example",
            "config": {"optionSpecs": {"reasoningLevel": {"values": ["high"]}}}}]
        self.catalog.write_text(json.dumps(data))
        with mock.patch.object(duet, "_run") as run:
            with self.assertRaisesRegex(duet.AgentRunError, "reasoning level is absent"):
                self.invoke(reasoning="max")
            run.assert_not_called()
        with mock.patch.object(duet, "_run", return_value=self.result()):
            self.invoke(reasoning="high")
        self.assertEqual("https://api.example/Path?q=Value#Section",
                         duet._zcode_rule_url("https://API.Example:443/Path/?q=Value#Section"))
        for unsupported in ("https://api.example/%2e/", "https://api.example/../", "https://api.example/?",
                            "https://user@api.example", "https://127.1", "https://0x7f000001/a",
                            "https://127.1./a", "https://api.example/a b"):
            with self.subTest(url=unsupported), self.assertRaises(ValueError):
                duet._zcode_rule_url(unsupported)

    def test_native_null_option_spec_clears_inherited_reasoning(self):
        data = json.loads(self.catalog.read_text())
        data["config"]["modelConfigRules"]["builtinProviderModelRules"] = [{
            "providerId": "account:zai-individual-coding-plan", "modelId": "GLM-5.3",
            "config": {"optionSpecs": {"reasoningLevel": None}}}]
        self.catalog.write_text(json.dumps(data))
        with mock.patch.object(duet, "_run") as run:
            with self.assertRaisesRegex(duet.AgentRunError, "reasoning level is absent"):
                self.invoke()
            run.assert_not_called()

    def test_native_null_reasoning_values_clear_inherited_levels(self):
        provider = {"providerId": "account:a", "config": {}}
        rules = {"modelRules": [
            {"modelMatch": ".*", "config": {"optionSpecs": {"reasoningLevel": {"values": ["high", "max"]}}}},
            {"modelMatch": ".*", "config": {"optionSpecs": {"reasoningLevel": {"values": None}}}},
        ]}
        self.assertEqual([], duet._zcode_catalog_reasoning(rules, provider, "GLM-5.3"))

    def test_mcp_must_be_disabled_by_native_config_before_review(self):
        path = self.cwd / "zcode.json"
        for data in ({}, {"features": {"mcp": True}}, {"features": {"skill": False}},
                     {"features": {"mcp": False, "skill": "false"}},
                     {"features": {"mcp": False}, "network": {"timeout": "bad"}},
                     {"features": {"mcp": 0}}):
            with self.subTest(data=data), mock.patch.object(duet, "_run") as run:
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(duet.AgentRunError, "native features.mcp=false"):
                    self.invoke()
                run.assert_not_called()

    def test_utf16_policy_cannot_certify_native_utf8_configuration(self):
        (self.cwd / "zcode.json").write_bytes('{"features":{"mcp":false}}'.encode("utf-16"))
        with mock.patch.object(duet, "_run") as run:
            with self.assertRaisesRegex(duet.AgentRunError, "Unsupported native ZCode permission"):
                self.invoke()
            run.assert_not_called()

    def test_native_user_and_project_denials_survive_cli_overrides(self):
        (self.cwd / ".git").mkdir()
        child = self.cwd / "subdir"
        child.mkdir()
        paths = duet._zcode_permission_paths(child)
        self.assertEqual([self.home / ".zcode/cli/config.json", self.cwd / "zcode.json",
                          self.cwd / ".zcode/config.json", child / "zcode.json", child / ".zcode/config.json"], paths)
        for path, data in [(paths[0], {"permission": {"disallowedTools": ["Read", "mcp__private__mutate"]}}),
                           (paths[1], {"permission": {"disallowedTools": ["Grep", "Bash(git *)"]}}),
                           (paths[2], {"features": {"mcp": False}}),
                           (paths[-1], {"permission": {"mode": "yolo"}})]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        with mock.patch.object(duet, "_run", return_value=self.result()) as run:
            self.invoke(cwd=child)
        command = run.call_args.args[0]
        denied = command[command.index("--disallowed-tools") + 1].split(",")
        self.assertTrue({"Read", "Grep", "Bash", "mcp__private__mutate"}.issubset(denied))
        self.assertEqual("plan", command[command.index("--mode") + 1])
        _, fingerprints = duet._zcode_permission_denials(child)
        paths[-1].write_text('{"permission":{"disallowedTools":["Glob"]}}')
        with self.assertRaisesRegex(ValueError, "changed before launch"):
            duet._zcode_check_permission_inputs(child, fingerprints)

    def test_unsupported_native_permission_config_fails_before_model_start(self):
        path = self.home / ".zcode/cli/config.json"
        path.parent.mkdir(parents=True)
        for value in ('{"permission": {"allowedTools":["Read"]}}', '{"permission":{"disallowedTools":"Read"}}',
                      '{"permission":null}', '[]', '// JSONC unsupported', '{"network":{"timeout":NaN}}', '{"permission":{"disallowedTools":["Read,Write"]}}'):
            with self.subTest(value=value), mock.patch.object(duet, "_run") as run:
                path.write_text(value)
                with self.assertRaisesRegex(duet.AgentRunError, "Unsupported native ZCode permission"):
                    self.invoke()
                run.assert_not_called()

    def test_loop_prerequisite_accepts_desktop_runtime_without_path_cli(self):
        spec = importlib.util.spec_from_file_location("duet_loop_e2e", ROOT / "scripts/duet_loop_e2e.py")
        loop = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = loop
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(loop)
        args = ["loop", "--lead-backend", "zcode", "--partner-backend", "codex", "--base-dir", str(self.cwd / "loop")]
        runtime = mock.Mock()
        with mock.patch.object(sys, "argv", args), mock.patch.object(loop.shutil, "which", side_effect=lambda name: None if name == "zcode" else name), \
                mock.patch.object(loop, "load_duet_module", return_value=runtime), mock.patch.object(loop, "scenarios", side_effect=RuntimeError("prerequisites passed")):
            with self.assertRaisesRegex(RuntimeError, "prerequisites passed"):
                loop.main()
        runtime._zcode_command.assert_called_once()
        fixture = self.cwd / "native-fixture"
        fixture.mkdir()
        (fixture / "LOCKED.md").write_text("Preserve this fixture.")
        loop.init_git_repo(fixture)
        loop.seed_zcode_review_policy(fixture)
        self.assertEqual({"features": {"mcp": False}}, json.loads((fixture / "zcode.json").read_text()))
        self.assertEqual("", loop.run_cmd(["git", "status", "--porcelain"], fixture).stdout)

    def test_pin_survives_state_restore(self):
        with mock.patch.object(duet, "_run", return_value=self.result()):
            _, self.agent.session_id = self.invoke()
        state = duet.agent_state(self.agent)
        partner = duet.agent_state(duet.Agent(name="gpt", backend="codex"))
        restored = duet._agents_from_state({"agents": [state, partner]},
                                            argparse.ArgumentParser(), "--continue")[0]
        self.assertEqual(self.agent.zcode_selection, restored.zcode_selection)

    def test_dry_run_does_not_start_zcode(self):
        with mock.patch.object(duet, "_run") as run:
            reply, session = self.invoke(dry=True)
        run.assert_not_called()
        self.assertTrue(session.startswith("sess_"))
        self.assertIn("dry-run", reply)


if __name__ == "__main__":
    unittest.main()
