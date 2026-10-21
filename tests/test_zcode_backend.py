"""Native ZCode profile, resume, and failure contracts; no provider requests."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import importlib.util
import types
import os
import pathlib
import re
import subprocess
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
        self.assertIn("\n[duet] hint: ZCode has no usable model.", str(caught.exception))
        self.assertFalse(paths[0].exists())
        self.assertIsNone(self.agent.zcode_selection)

    def test_login_failures_add_sign_in_hint(self):
        for marker, expected in (("project_token_login_failed", "could not get a project token"),
                                 ("project_token_login_required", "no stored login token"),
                                 ("Select a model before continuing", "no usable model")):
            with self.subTest(marker=marker), \
                    mock.patch.object(duet, "_run", return_value=(1, "", f"Error: {marker}")):
                with self.assertRaises(duet.AgentRunError) as caught:
                    self.invoke()
            message = str(caught.exception)
            self.assertTrue(message.splitlines()[-1].startswith("[duet] hint: ZCode "), message)
            self.assertIn(expected, message)
            self.assertEqual("project_token_login_failed" == marker, "network" in message)
            self.assertNotIn("/model", message)

    def test_error_summary_with_marker_adds_hint_at_rc_zero(self):
        summary = json.dumps({"sessionId": "sess_a", "response": "partial",
                              "error": "project_token_login_required"})
        with mock.patch.object(duet, "_run", return_value=(0, summary, "")):
            with self.assertRaisesRegex(duet.AgentRunError, "failed turn\n\\[duet\\] hint: ZCode has no stored login"):
                self.invoke()

    def test_timeout_never_adds_login_hint(self):
        with mock.patch.object(duet, "_run", return_value=(124, "", "project_token_login_failed")):
            with self.assertRaises(duet.AgentRunError) as caught:
                self.invoke()
        self.assertNotIn("[duet] hint", str(caught.exception))

    def test_failure_without_marker_has_no_hint(self):
        for result in ((1, "", "something else"), (0, "not json", "")):
            with self.subTest(result=result), mock.patch.object(duet, "_run", return_value=result):
                with self.assertRaises(duet.AgentRunError) as caught:
                    self.invoke()
                self.assertNotIn("[duet] hint", str(caught.exception))

    def test_two_markers_add_one_hint_line(self):
        err = "project_token_login_required then project_token_login_failed"
        with mock.patch.object(duet, "_run", return_value=(1, "", err)):
            with self.assertRaises(duet.AgentRunError) as caught:
                self.invoke()
        message = str(caught.exception)
        self.assertEqual(1, message.count("[duet] hint:"))
        self.assertIn("could not get a project token", message)

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
                with self.assertRaisesRegex(duet.AgentRunError, "reasoning level is absent") as caught:
                    self.invoke(reasoning=level)
                run.assert_not_called()
            message = str(caught.exception)
            self.assertIn(f"catalog: {level} for account:zai-individual-coding-plan/GLM-5.3. "
                          "Supported levels: low, high, max.\n", message)
            self.assertIn("set its reasoning_effort in a --config file", message)
            self.assertIn("docs/ZCODE.md#native-review-peer", message)
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

    def test_other_entry_in_a_glm_folder_does_not_use_the_sibling_catalog(self):
        self.launch.stop()
        resources = self.cwd / "Resources"
        entry = resources / "glm/other.cjs"
        entry.parent.mkdir(parents=True)
        entry.write_text("// other entry")
        catalog = resources / "config/provider/zcode-builtin.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(self.catalog.read_text())
        link = self.cwd / "bin/zcode"
        link.parent.mkdir()
        link.symlink_to(entry)
        with mock.patch.object(duet, "_ZCODE_DESKTOP_RESOURCES", self.cwd / "elsewhere"), \
                mock.patch.object(duet.shutil, "which", return_value=str(link)), \
                mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": ""}):
            with self.assertRaisesRegex(ValueError, "PATH ZCode CLI's catalog"):
                duet._zcode_command()

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
        wrapper = self.cwd / "cli/zcode"
        wrapper.parent.mkdir()
        wrapper.write_text("#!/bin/sh\n")
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                with mock.patch.object(duet, "_ZCODE_DESKTOP_RESOURCES", desktop), \
                        mock.patch.object(duet.sys, "platform", platform), \
                        mock.patch.object(duet.shutil, "which", return_value=str(wrapper)), \
                        mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": ""}):
                    with self.assertRaisesRegex(ValueError, "PATH ZCode CLI's catalog") as caught:
                        duet._zcode_command()
                message = str(caught.exception)
                self.assertIn("symbolic link", message)
                self.assertIn("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=", message)
                self.assertEqual(platform == "darwin", str(catalog) in message)

    def test_linked_cli_without_catalog_names_its_entry_file(self):
        self.launch.stop()
        entry = self.cwd / "native/zcode.cjs"
        entry.parent.mkdir()
        entry.write_text("// native entry")
        link = self.cwd / "bin/zcode"
        link.parent.mkdir()
        link.symlink_to(entry)
        with mock.patch.object(duet, "_ZCODE_DESKTOP_RESOURCES", self.cwd / "elsewhere"), \
                mock.patch.object(duet.shutil, "which", return_value=str(link)), \
                mock.patch.dict(os.environ, {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": ""}):
            with self.assertRaisesRegex(ValueError, "PATH ZCode CLI's catalog") as caught:
                duet._zcode_command()
        message = str(caught.exception)
        self.assertIn(str(entry.resolve()), message)
        self.assertIn("no catalog is in a known place", message)
        self.assertIn("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=", message)
        self.assertNotIn("symbolic link", message)

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
            with self.assertRaisesRegex(duet.AgentRunError, "reasoning level is absent") as caught:
                self.invoke()
            run.assert_not_called()
        message = str(caught.exception)
        self.assertIn("catalog: max for account:zai-individual-coding-plan/GLM-5.3. "
                      "The catalog lists no reasoning levels for this model.\n", message)
        self.assertIn("Do not set reasoning for this agent.", message)
        self.assertIn("set reasoning_effort only for the other agent in a --config file", message)
        self.assertNotIn("Supported levels", message)

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
                with self.assertRaisesRegex(duet.AgentRunError, "native features.mcp=false") as caught:
                    self.invoke()
                run.assert_not_called()
                first_line = str(caught.exception).splitlines()[0]
                self.assertIn(f"create {self.cwd / '.zcode/config.json'} with exactly "
                              '{"features": {"mcp": false}}', first_line)
                self.assertIn("A file that is not committed does not reach a new Duet worktree.",
                              str(caught.exception))

    def test_mcp_gate_advice_makes_the_check_pass(self):
        project = self.cwd / "zcode.json"
        nested = self.cwd / ".zcode/config.json"
        user = self.home / ".zcode/cli/config.json"
        minimal = json.dumps({"features": {"mcp": False}})
        permission_only = json.dumps({"permission": {"disallowedTools": ["Read"]}})
        cases = [
            ("user file enables MCP, no project file", {user: json.dumps({"features": {"mcp": True}})},
             f"create {nested} with exactly", nested),
            ("project file has only permissions", {nested: permission_only},
             f"create {project} with exactly", project),
            ("project file has other features", {project: json.dumps({"features": {"mcp": False, "skill": False}}),
                                                  nested: permission_only},
             f"change {project} to exactly", project),
        ]
        for name, files, action, fix in cases:
            with self.subTest(case=name):
                project.unlink(missing_ok=True)
                for path in (nested, user):
                    path.unlink(missing_ok=True)
                for path, text in files.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                with self.assertRaises(ValueError) as caught:
                    duet._zcode_permission_denials(self.cwd)
                message = str(caught.exception)
                self.assertEqual(f"ZCode review requires native features.mcp=false: {action} "
                                 '{"features": {"mcp": false}}', message.splitlines()[0])
                self.assertIn(f"{user}, then zcode.json and .zcode/config.json in each of these folders: "
                              f"{self.cwd}.", message)
                self.assertEqual("create" in action, "are lost" not in message)
                cfg = self.config()
                self.assertIn(str(fix), duet._error_summary(
                    duet._zcode_preflight(cfg, worktree_ready=False), "setup_error"))
                fix.parent.mkdir(parents=True, exist_ok=True)
                fix.write_text(minimal)
                duet._zcode_permission_denials(self.cwd)
                self.assertIsNone(duet._zcode_preflight(cfg, worktree_ready=False))

    def test_mcp_gate_change_advice_warns_that_other_settings_are_lost(self):
        project = self.cwd / "zcode.json"
        nested = self.cwd / ".zcode/config.json"
        nested.parent.mkdir()
        for path in (project, nested):
            path.write_text(json.dumps({"permission": {"disallowedTools": ["Read"]}}))
        with self.assertRaises(ValueError) as caught:
            duet._zcode_permission_denials(self.cwd)
        lines = str(caught.exception).splitlines()
        self.assertEqual(f"ZCode review requires native features.mcp=false: change {nested} to exactly "
                         '{"features": {"mcp": false}}', lines[0])
        self.assertIn(f"Other settings in {nested} are lost, so move any that you need to another file first.",
                      lines[1])
        nested.write_text(json.dumps({"features": {"mcp": False}}))
        denied, _ = duet._zcode_permission_denials(self.cwd)
        self.assertIn("Read", denied)

    def test_mcp_gate_advice_lists_every_folder_and_targets_the_deepest(self):
        (self.cwd / ".git").mkdir()
        (self.cwd / "zcode.json").unlink()
        child = self.cwd / "subdir"
        child.mkdir()
        with self.assertRaises(ValueError) as caught:
            duet._zcode_permission_denials(child)
        message = str(caught.exception)
        self.assertEqual(f"ZCode review requires native features.mcp=false: create "
                         f"{child / '.zcode/config.json'} with exactly "
                         '{"features": {"mcp": false}}', message.splitlines()[0])
        self.assertIn(f"each of these folders: {self.cwd}, {child}.", message)

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

    def config(self, agents=None, **overrides):
        partner = duet.Agent(name="gpt", backend="codex", role="reviewer")
        args = dict(cwd=self.cwd, agents=agents or [self.agent, partner], reasoning="max",
                    runs_dir=self.cwd / "runs", metrics_enabled=False)
        args.update(overrides)
        return duet.DuetConfig(**args)

    def prepare(self, cfg):
        with contextlib.ExitStack() as stack:
            for name in ("_install_sigint", "_install_sigterm", "_install_run_stop_signal",
                         "_register_run_in_home_index"):
                stack.enter_context(mock.patch.object(duet, name))
            self.stdout = io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(self.stdout))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            return duet._prepare_run(cfg)

    def git(self, *args):
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                       cwd=self.cwd, check=True, capture_output=True)

    def git_repo_with_seed(self):
        self.git("init", "-q")
        (self.cwd / "seed.txt").write_text("seed\n")
        self.git("add", "seed.txt")
        self.git("commit", "-qm", "seed")

    def test_launch_check_uses_the_per_turn_predicate(self):
        prefix = "ZCode launch check failed for agent glm: "
        catalog_text = self.catalog.read_text()
        model = self.agent.model

        def broken_template():
            data = json.loads(catalog_text)
            data["config"]["providerConfigRules"]["providerRules"][1]["templateId"] = "missing"
            self.catalog.write_text(json.dumps(data))

        def set_model(value):
            return lambda: setattr(self.agent, "model", value)

        cases = [
            ("passes", "max", lambda: None, None),
            ("mcp file is empty", "max", lambda: (self.cwd / "zcode.json").write_text("{}"), None),
            ("level is absent", "xhigh", lambda: None, None),
            ("model is not in the catalog", "max",
             set_model("account:zai-individual-coding-plan/missing-model"), None),
            ("model ID is missing", "max", set_model(None), None),
            ("template is broken on another provider", "max", broken_template, None),
            ("runtime is missing", "max", lambda: None, ValueError("no ZCode runtime")),
        ]
        for name, reasoning, change, command_error in cases:
            with self.subTest(case=name):
                self.catalog.write_text(catalog_text)
                (self.cwd / "zcode.json").write_text(json.dumps({"features": {"mcp": False}}))
                self.agent.model = model
                change()
                cfg = self.config(reasoning=reasoning)
                with contextlib.ExitStack() as stack:
                    run = stack.enter_context(mock.patch.object(duet, "_run", return_value=self.result()))
                    if command_error:
                        stack.enter_context(mock.patch.object(duet, "_zcode_command", side_effect=command_error))
                    preflight = duet._zcode_preflight(cfg, worktree_ready=False)
                    run.assert_not_called()
                    try:
                        self.invoke(reasoning=reasoning)
                        turn_error = None
                    except (duet.AgentRunError, ValueError) as exc:
                        turn_error = str(exc)
                        run.assert_not_called()
                if turn_error is None:
                    self.assertIsNone(preflight)
                else:
                    self.assertTrue(preflight.startswith(prefix), preflight)
                    self.assertEqual(turn_error.removeprefix("ZCode invocation failed: "),
                                     preflight[len(prefix):])

    def test_launch_check_stops_before_kickoff_and_first_turn(self):
        (self.cwd / "zcode.json").write_text("{}")
        info = self.cwd / "run.json"
        cfg = self.config(task_from_cmd="printf seed", run_info_file=info)
        with mock.patch.object(duet, "run_task_from_cmd", side_effect=AssertionError("kickoff ran")) as kickoff, \
                mock.patch.object(duet, "call_agent", side_effect=AssertionError("turn ran")) as turn, \
                mock.patch.object(duet, "_install_sigint"), mock.patch.object(duet, "_install_sigterm"), \
                mock.patch.object(duet, "_install_run_stop_signal"), \
                mock.patch.object(duet, "_register_run_in_home_index"), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            state = duet.run_duet(cfg)
        kickoff.assert_not_called()
        turn.assert_not_called()
        self.assertEqual(duet.FINISHED_SETUP_ERROR, state["finished_reason"])
        self.assertEqual("finished", state["phase"])
        self.assertEqual(0, state["turns_used"])
        self.assertTrue(state["error"].startswith("ZCode launch check failed for agent glm: "))
        self.assertIn("native features.mcp=false", state["error"])
        self.assertIn("[duet] ERROR: ZCode launch check failed for agent glm", stderr.getvalue())
        run_info = json.loads(info.read_text())
        self.assertEqual(1, run_info["schema_version"])
        status = duet.build_run_status(run_info["run_dir"])
        self.assertEqual(("terminal", 0), (status["health"], status["exit_code"]))
        self.assertEqual("run finished with setup_error; inspect artifacts", status["error"])

    def prepare_worktree_peer(self, cwd=None):
        lead = duet.Agent(name="gpt", backend="codex", role="reviewer")
        cfg = self.config(agents=[lead, self.agent], worktree=True, require_worktree=True,
                          cwd=cwd or self.cwd)
        with mock.patch.object(duet, "_zcode_permission_denials",
                               wraps=duet._zcode_permission_denials) as denials:
            context, failure = self.prepare(cfg)
        self.assertEqual(context.wt_path, pathlib.Path(self.agent.cwd_override))
        self.assertEqual([mock.call(context.wt_path, self.cwd)], denials.call_args_list)
        return failure

    def test_worktree_peer_mcp_is_checked_after_worktree_setup(self):
        self.git_repo_with_seed()
        failure = self.prepare_worktree_peer()
        self.assertEqual(duet.FINISHED_SETUP_ERROR, failure["finished_reason"])
        self.assertEqual(
            "ZCode launch check failed for agent glm: ZCode review requires native features.mcp=false: "
            f"create and commit {self.cwd / '.zcode/config.json'} with exactly "
            '{"features": {"mcp": false}}', failure["error"])
        worktree, branch = self.agent.cwd_override, failure["worktree_branch"]
        self.assertTrue(pathlib.Path(worktree).is_dir())
        self.assertIn(f"[duet] worktree left intact at {worktree} (branch {branch}).", self.stdout.getvalue())
        self.assertIn(f"drop:   git -C {self.cwd} worktree remove {worktree} && "
                      f"git -C {self.cwd} branch -D {branch}", self.stdout.getvalue())

    def test_advised_file_committed_from_a_subfolder_reaches_a_new_worktree(self):
        self.git_repo_with_seed()
        subfolder = self.cwd / "sub"
        subfolder.mkdir()
        failure = self.prepare_worktree_peer(subfolder)
        advised = pathlib.Path(re.search(r"create and commit (\S+) with exactly", failure["error"]).group(1))
        self.assertEqual(self.cwd / ".zcode/config.json", advised)
        self.git("worktree", "remove", "--force", self.agent.cwd_override)
        self.git("branch", "-D", failure["worktree_branch"])
        self.agent.cwd_override = None
        advised.parent.mkdir()
        advised.write_text(json.dumps({"features": {"mcp": False}}))
        self.git("add", str(advised))
        self.git("commit", "-qm", "policy")
        self.assertIsNone(self.prepare_worktree_peer(subfolder))

    def test_reused_worktree_keeps_the_plain_mcp_advice(self):
        self.git_repo_with_seed()
        worktree = self.cwd / "reused"
        self.git("worktree", "add", "-b", "reused", str(worktree))
        cfg = self.config(agents=[duet.Agent(name="gpt", backend="codex", role="reviewer"), self.agent],
                          worktree_path=worktree)
        context, failure = self.prepare(cfg)
        self.assertIn(f"create {worktree.resolve() / '.zcode/config.json'} with exactly", failure["error"])
        self.assertNotIn("commit", failure["error"])

    def test_per_turn_gate_names_the_main_checkout_for_a_new_worktree(self):
        (self.cwd / "zcode.json").write_text("{}")
        worktree = self.cwd / "wt"
        (worktree / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(duet.AgentRunError, "create and commit " + re.escape(
                str(self.cwd / ".zcode/config.json"))):
            self.invoke(cwd=worktree, origin=self.cwd)

    def test_turn_backend_passes_the_main_checkout_for_a_new_worktree(self):
        (self.cwd / "zcode.json").write_text("{}")
        worktree = self.cwd / "wt"
        (worktree / ".git").mkdir(parents=True)
        self.agent.cwd_override = worktree
        cfg = self.config(worktree=True)
        with mock.patch.object(duet, "_run") as run:
            with self.assertRaisesRegex(duet.AgentRunError, "create and commit " + re.escape(
                    str(self.cwd / ".zcode/config.json"))):
                duet._call_zcode_backend(self.agent, "system", "message", cfg, True, None, None, "max")
            run.assert_not_called()
        cfg.worktree_path = worktree
        with mock.patch.object(duet, "_run") as run:
            with self.assertRaisesRegex(duet.AgentRunError, "create " + re.escape(
                    str(worktree / ".zcode/config.json"))):
                duet._call_zcode_backend(self.agent, "system", "message", cfg, True, None, None, "max")

    def test_committed_mcp_file_reaches_the_worktree_peer(self):
        self.git_repo_with_seed()
        self.git("add", "zcode.json")
        self.git("commit", "-qm", "policy")
        self.assertIsNone(self.prepare_worktree_peer())

    def test_worktree_fallback_checks_the_main_checkout(self):
        (self.cwd / "zcode.json").write_text("{}")
        cfg = self.config(worktree=True)
        cfg.agents.reverse()
        with mock.patch.object(duet, "_zcode_permission_denials",
                               wraps=duet._zcode_permission_denials) as denials:
            context, failure = self.prepare(cfg)
        self.assertIsNone(context.wt_path)
        self.assertEqual([mock.call(self.cwd, None)], denials.call_args_list)
        self.assertEqual(duet.FINISHED_SETUP_ERROR, failure["finished_reason"])
        self.assertIn("agent glm", failure["error"])

    def test_launch_check_names_the_failed_agent_in_two_zcode_configs(self):
        other = duet.Agent(name="other", backend="zcode", role="reviewer",
                           model="account:zai-individual-coding-plan/missing-model")
        cfg = self.config(agents=[self.agent, other])
        self.assertIn("agent other: Requested ZCode account model is absent",
                      duet._zcode_preflight(cfg, worktree_ready=False))
        self.agent.model = "account:zai-individual-coding-plan/also-missing"
        self.assertIn("agent glm: Requested ZCode account model is absent",
                      duet._zcode_preflight(cfg, worktree_ready=False))

    def test_launch_check_keeps_resume_state(self):
        pin = {"model": self.agent.model, "reasoning": "max"}
        self.agent.session_id = "sess_abc"
        self.agent.zcode_selection = dict(pin)
        cfg = self.config()
        self.assertIsNone(duet._zcode_preflight(cfg, worktree_ready=False))
        self.assertEqual(("sess_abc", pin), (self.agent.session_id, self.agent.zcode_selection))
        self.agent.zcode_selection = {"model": self.agent.model, "reasoning": "high"}
        self.assertIn("original model/effort pin", duet._zcode_preflight(cfg, worktree_ready=False))
        self.assertEqual("sess_abc", self.agent.session_id)

    def test_missing_backend_warning_leaves_zcode_to_launch_check(self):
        with mock.patch.object(duet.shutil, "which", return_value=None), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            duet._warn_missing_backends(self.config())
        duet._zcode_command.assert_not_called()
        self.assertIn("'codex' not on PATH", stderr.getvalue())
        self.assertNotIn("zcode", stderr.getvalue())

    def test_dry_run_does_not_start_zcode(self):
        with mock.patch.object(duet, "_run") as run:
            reply, session = self.invoke(dry=True)
        run.assert_not_called()
        self.assertTrue(session.startswith("sess_"))
        self.assertIn("dry-run", reply)


if __name__ == "__main__":
    unittest.main()
