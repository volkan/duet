#!/usr/bin/env python3
"""Validate duet packaging and plugin metadata.

This is intentionally stdlib-only so CI can run the source metadata checks
before installing any build tools. Pass ``--artifacts dist`` after building to
also inspect the wheel and source distribution that would be uploaded.
"""
from __future__ import annotations

import argparse
import email.parser
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - CI runs this on Python 3.11+.
    tomllib = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PLUGIN_ROOT = ROOT / "plugins" / "duet-claude"
CLAUDE_PLUGIN_JSON = CLAUDE_PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
CLAUDE_MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
CLAUDE_COMMAND = CLAUDE_PLUGIN_ROOT / "commands" / "duet.md"
CODEX_PLUGIN_ROOT = ROOT / "plugins" / "duet"
CODEX_PLUGIN_JSON = CODEX_PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
CODEX_MARKETPLACE_JSON = ROOT / ".agents" / "plugins" / "marketplace.json"
CODEX_SKILL = CODEX_PLUGIN_ROOT / "skills" / "duet" / "SKILL.md"
CODEX_PLUGIN_DOC = ROOT / "docs" / "CODEX_PLUGIN.md"
PYPROJECT = ROOT / "pyproject.toml"
FORBIDDEN_TEXT = "volkan.altan@" + "vestiaire" + "collective.com"
EXPECTED_EMAIL = "volkanaltan@gmail.com"
README_ABSOLUTE_LINKS = [
    "https://github.com/volkan/duet/blob/main/docs/USAGE.md",
    "https://github.com/volkan/duet/blob/main/docs/CODEX_PLUGIN.md",
    "https://github.com/volkan/duet/blob/main/.github/BRANCH_PROTECTION.md",
    "https://github.com/volkan/duet/blob/main/CLAUDE.md",
    "https://github.com/volkan/duet/blob/main/AGENTS.md",
]


def _fail(message: str) -> None:
    print(f"[check_distribution_metadata] {message}", file=sys.stderr)
    raise SystemExit(1)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"{path.relative_to(ROOT)} is invalid JSON: {exc}")
    if not isinstance(data, dict):
        _fail(f"{path.relative_to(ROOT)} must contain a JSON object")
    return data


def _load_pyproject() -> dict[str, Any]:
    if tomllib is None:
        _fail("Python 3.11+ is required for tomllib-backed pyproject checks")
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _require(mapping: dict[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        _fail(f"{label} is missing required key {key!r}")
    return mapping[key]


def _project_version(pyproject: dict[str, Any]) -> str:
    project = _require(pyproject, "project", "pyproject.toml")
    if not isinstance(project, dict):
        _fail("pyproject.toml [project] must be a table")
    version = _require(project, "version", "pyproject.toml [project]")
    if not isinstance(version, str) or not version:
        _fail("pyproject.toml [project].version must be a non-empty string")
    return version


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def _assert_no_forbidden_text() -> None:
    matches = []
    for path in _tracked_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if FORBIDDEN_TEXT in text:
            matches.append(path.relative_to(ROOT).as_posix())
    if matches:
        _fail(f"forbidden employer email remains in tracked files: {', '.join(matches)}")


def _assert_project_metadata(project: dict[str, Any]) -> None:
    if project.get("name") != "duet-cli":
        _fail("pyproject.toml project.name must remain 'duet-cli'")
    if project.get("dependencies") != []:
        _fail("pyproject.toml project.dependencies must stay empty")
    scripts = project.get("scripts")
    if not isinstance(scripts, dict) or scripts.get("duet") != "duet:main":
        _fail("pyproject.toml must expose the console script duet = 'duet:main'")


def _assert_claude_plugin_metadata(
    plugin: dict[str, Any],
    marketplace: dict[str, Any],
    version: str,
) -> None:
    if plugin.get("name") != "duet":
        _fail(".claude-plugin/plugin.json name must be 'duet'")
    if plugin.get("version") != version:
        _fail(".claude-plugin/plugin.json version must match pyproject.toml project.version")
    author = plugin.get("author")
    if not isinstance(author, dict) or author.get("name") != "Volkan Altan":
        _fail(".claude-plugin/plugin.json author.name must be 'Volkan Altan'")
    if author.get("email") != EXPECTED_EMAIL:
        _fail(f".claude-plugin/plugin.json author.email must be {EXPECTED_EMAIL!r}")

    owner = marketplace.get("owner")
    if not isinstance(owner, dict) or owner.get("name") != "Volkan Altan":
        _fail(".claude-plugin/marketplace.json owner.name must be 'Volkan Altan'")
    if owner.get("email") != EXPECTED_EMAIL:
        _fail(f".claude-plugin/marketplace.json owner.email must be {EXPECTED_EMAIL!r}")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        _fail(".claude-plugin/marketplace.json plugins must be a non-empty list")
    if not any(p.get("name") == "duet" and p.get("source") == "./plugins/duet-claude"
               for p in plugins if isinstance(p, dict)):
        _fail(".claude-plugin/marketplace.json must list the duet plugin source './plugins/duet-claude'")

    if not CLAUDE_COMMAND.exists():
        _fail("plugins/duet-claude/commands/duet.md must exist for the Claude Code plugin")
    if (ROOT / ".claude-plugin" / "plugin.json").exists():
        _fail("root .claude-plugin/plugin.json must be removed; the Claude plugin now lives under plugins/duet-claude/")
    if (ROOT / "commands" / "duet.md").exists():
        _fail("root commands/duet.md must be removed; the Claude command now lives under plugins/duet-claude/commands/")


def _assert_codex_plugin_metadata(
    plugin: dict[str, Any],
    marketplace: dict[str, Any],
    version: str,
) -> None:
    if plugin.get("name") != "duet":
        _fail(".codex-plugin/plugin.json name must be 'duet'")
    if plugin.get("version") != version:
        _fail(".codex-plugin/plugin.json version must match pyproject.toml project.version")
    if plugin.get("skills") != "./skills/":
        _fail(".codex-plugin/plugin.json must expose skills via './skills/'")

    author = plugin.get("author")
    if not isinstance(author, dict) or author.get("name") != "Volkan Altan":
        _fail(".codex-plugin/plugin.json author.name must be 'Volkan Altan'")
    if author.get("email") != EXPECTED_EMAIL:
        _fail(f".codex-plugin/plugin.json author.email must be {EXPECTED_EMAIL!r}")

    interface = plugin.get("interface")
    if not isinstance(interface, dict):
        _fail(".codex-plugin/plugin.json interface must be an object")
    for key in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
    ):
        if not isinstance(interface.get(key), str) or not interface[key].strip():
            _fail(f".codex-plugin/plugin.json interface.{key} must be a non-empty string")
    default_prompt = interface.get("defaultPrompt")
    if not isinstance(default_prompt, list) or not default_prompt:
        _fail(".codex-plugin/plugin.json interface.defaultPrompt must be a non-empty list")
    capabilities = interface.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        _fail(".codex-plugin/plugin.json interface.capabilities must be a non-empty list")

    if marketplace.get("name") != "volkan-duet":
        _fail(".agents/plugins/marketplace.json name must be 'volkan-duet'")
    market_interface = marketplace.get("interface")
    if not isinstance(market_interface, dict) or not market_interface.get("displayName"):
        _fail(".agents/plugins/marketplace.json interface.displayName is required")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        _fail(".agents/plugins/marketplace.json plugins must be a non-empty list")
    duet_entries = [p for p in plugins if isinstance(p, dict) and p.get("name") == "duet"]
    if not duet_entries:
        _fail(".agents/plugins/marketplace.json must list the duet plugin")
    source = duet_entries[0].get("source")
    if (
        not isinstance(source, dict)
        or source.get("source") != "local"
        or source.get("path") != "./plugins/duet"
    ):
        _fail(".agents/plugins/marketplace.json duet source must be local './plugins/duet'")
    policy = duet_entries[0].get("policy")
    if not isinstance(policy, dict):
        _fail(".agents/plugins/marketplace.json duet policy must be an object")
    if policy.get("installation") != "AVAILABLE":
        _fail(".agents/plugins/marketplace.json duet policy.installation must be AVAILABLE")
    if policy.get("authentication") != "ON_INSTALL":
        _fail(".agents/plugins/marketplace.json duet policy.authentication must be ON_INSTALL")
    if duet_entries[0].get("category") != "Productivity":
        _fail(".agents/plugins/marketplace.json duet category must be Productivity")

    if not CODEX_PLUGIN_DOC.exists():
        _fail("docs/CODEX_PLUGIN.md must exist for the Codex plugin")
    if not CODEX_SKILL.exists():
        _fail("plugins/duet/skills/duet/SKILL.md must exist for the Codex plugin")
    skill_text = CODEX_SKILL.read_text(encoding="utf-8")
    for required in (
        "command -v duet",
        "command -v claude",
        "command -v codex",
        "duet --recap --cwd",
        "claude -p /review",
        "--partner codex:coder",
    ):
        if required not in skill_text:
            _fail(f"plugins/duet/skills/duet/SKILL.md is missing required text: {required!r}")


def _assert_source_metadata() -> str:
    pyproject = _load_pyproject()
    claude_plugin = _load_json(CLAUDE_PLUGIN_JSON)
    claude_marketplace = _load_json(CLAUDE_MARKETPLACE_JSON)
    codex_plugin = _load_json(CODEX_PLUGIN_JSON)
    codex_marketplace = _load_json(CODEX_MARKETPLACE_JSON)
    version = _project_version(pyproject)

    project = pyproject["project"]
    _assert_project_metadata(project)
    _assert_claude_plugin_metadata(claude_plugin, claude_marketplace, version)
    _assert_codex_plugin_metadata(codex_plugin, codex_marketplace, version)
    _assert_no_forbidden_text()
    return version


def _read_wheel_metadata(wheel_path: Path, version: str) -> str:
    metadata_name = f"duet_cli-{version}.dist-info/METADATA"
    with zipfile.ZipFile(wheel_path) as wheel:
        try:
            return wheel.read(metadata_name).decode("utf-8")
        except KeyError:
            _fail(f"{wheel_path.name} is missing {metadata_name}")


def _assert_wheel_metadata(metadata_text: str, version: str) -> None:
    parsed = email.parser.Parser().parsestr(metadata_text)
    if parsed.get("Name") != "duet-cli":
        _fail("wheel METADATA Name must be duet-cli")
    if parsed.get("Version") != version:
        _fail(f"wheel METADATA Version must be {version}")
    if parsed.get("Author") != "Volkan Altan":
        _fail("wheel METADATA Author must be Volkan Altan")
    if parsed.get("Requires-Python") != ">=3.9":
        _fail("wheel METADATA Requires-Python must be >=3.9")
    for link in README_ABSOLUTE_LINKS:
        if link not in metadata_text:
            _fail(f"wheel long-description is missing absolute README link {link}")
    relative_repo_links = re.findall(r"\]\(((?:docs/|\.github/|CLAUDE\.md|AGENTS\.md)[^)]+)\)",
                                     metadata_text)
    if relative_repo_links:
        _fail("wheel long-description contains relative repository links: "
              + ", ".join(sorted(set(relative_repo_links))))


def _assert_sdist_members(sdist_path: Path, version: str) -> None:
    prefix = f"duet_cli-{version}/"
    required = {
        f"{prefix}LICENSE",
        f"{prefix}README.md",
        f"{prefix}duet.py",
        f"{prefix}pyproject.toml",
        f"{prefix}tests/test_duet.py",
    }
    with tarfile.open(sdist_path, "r:gz") as sdist:
        names = set(sdist.getnames())
    missing = sorted(required - names)
    if missing:
        _fail(f"{sdist_path.name} is missing expected member(s): {', '.join(missing)}")


def _assert_artifacts(dist_dir: Path, version: str) -> None:
    wheel_path = dist_dir / f"duet_cli-{version}-py3-none-any.whl"
    sdist_path = dist_dir / f"duet_cli-{version}.tar.gz"
    if not wheel_path.exists():
        _fail(f"missing wheel artifact {wheel_path}")
    if not sdist_path.exists():
        _fail(f"missing source artifact {sdist_path}")
    _assert_wheel_metadata(_read_wheel_metadata(wheel_path, version), version)
    _assert_sdist_members(sdist_path, version)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path,
                        help="dist directory containing built sdist/wheel artifacts")
    args = parser.parse_args(argv)

    version = _assert_source_metadata()
    if args.artifacts:
        _assert_artifacts(args.artifacts, version)
    print(f"[check_distribution_metadata] ok: duet-cli {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
