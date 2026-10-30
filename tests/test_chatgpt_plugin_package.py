"""Private plugin exports must preserve contents and exclude accidental files."""

import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zipfile
import zlib


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("package_chatgpt_plugin", ROOT / "scripts/package_chatgpt_plugin.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class TestChatGPTPluginPackage(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plugin = self.root / "duet-chatgpt"
        shutil.copytree(package.PLUGIN, self.plugin)

    def test_export_is_self_contained_and_matches_source(self):
        manifest, files = package.validate(self.plugin)
        archive = package.build(self.root / "dist", self.plugin)
        self.assertEqual(archive.name, "duet-chatgpt-" + manifest["version"] + ".zip")
        package.inspect_archive(archive, files)
        with zipfile.ZipFile(archive) as exported:
            self.assertIn("plugin.json", exported.namelist())
            self.assertNotIn("mcp.json", exported.namelist())
        self.assertEqual(archive.read_bytes(), package.build(self.root / "dist", self.plugin).read_bytes())

    def test_unexpected_files_are_rejected_before_export(self):
        (self.plugin / ".env").write_text("SECRET=must-not-be-packaged", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            package.build(self.root / "dist", self.plugin)
        self.assertFalse((self.root / "dist").exists())

    def test_symlinks_are_rejected(self):
        (self.plugin / "extra").symlink_to(self.plugin / "LICENSE")
        with self.assertRaisesRegex(ValueError, "symlink"):
            package.validate(self.plugin)

    def test_dangling_reference_is_rejected(self):
        skill = self.plugin / (package.SKILL + "SKILL.md")
        skill.write_text(skill.read_text() + "\n[Missing](references/missing.md)\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing skill reference"):
            package.validate(self.plugin)

    def test_archive_cannot_be_written_into_source(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            package.build(self.plugin / "dist", self.plugin)

    def test_upload_prompt_rejects_overlong_or_multiline_values(self):
        manifest_path = self.plugin / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        for prompt in ("x" * 129, "First line\nSecond line", "First line\rSecond line"):
            with self.subTest(prompt=prompt):
                manifest["extensions"]["com.openai"]["interface"]["defaultPrompt"] = prompt
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "presentation"):
                    package.validate(self.plugin)

    def test_public_export_preserves_private_source_and_declares_publication(self):
        before = (self.plugin / "plugin.json").read_bytes()
        archive = package.build(self.root / "public", self.plugin, public=True)
        manifest, files = package.package_contents(self.plugin, public=True)
        package.inspect_archive(archive, files)
        self.assertEqual((self.plugin / "plugin.json").read_bytes(), before)
        self.assertEqual(manifest["name"], "duet-chatgpt")
        settings = manifest["extensions"]["com.openai"]
        self.assertEqual(settings["publication"]["countries"], [])
        self.assertIs(settings["review"]["commerce"], False)
        self.assertEqual(settings["interface"]["logo"], "./assets/icon.png")
        self.assertEqual(len(settings["interface"]["defaultPrompt"]), 3)
        self.assertEqual(set(files), package.FILES | {"assets/icon.png"})
        self.assertNotIn("apps", settings)
        self.assertNotIn("mcp.json", files)

    def test_public_icon_and_bindings_are_checked(self):
        manifest, files = package.package_contents(self.plugin, public=True)
        icon = files["assets/icon.png"]
        for invalid in (b"not a png", icon[:16], icon[:24], icon[:-12], icon + b"trailing"):
            with self.subTest(icon=invalid[:10]):
                with self.assertRaisesRegex(ValueError, "PNG"):
                    package.validate_public(manifest, {**files, "assets/icon.png": invalid})
        manifest["extensions"]["com.openai"]["apps"] = "./.app.json"
        with self.assertRaisesRegex(ValueError, "bindings"):
            package.validate_public(manifest, files)
        manifest["extensions"]["com.openai"].pop("apps")
        manifest["extensions"]["com.openai"]["hooks"] = "./hooks/hooks.json"
        with self.assertRaisesRegex(ValueError, "hooks"):
            package.validate_public(manifest, files)

    def test_public_metadata_rejects_duplicate_or_too_many_prompts(self):
        for prompts in (["Same", " Same "], ["One", "Two", "Three", "Four"], ["One", None]):
            with self.subTest(prompts=prompts):
                manifest = json.loads((self.plugin / "plugin.json").read_text())
                interface = manifest["extensions"]["com.openai"]["interface"]
                interface["defaultPrompt"] = prompts
                with self.assertRaisesRegex(ValueError, "presentation"):
                    package.validate_interface(interface)

    def test_public_listing_requires_all_four_https_urls(self):
        manifest, files = package.package_contents(self.plugin, public=True)
        interface = manifest["extensions"]["com.openai"]["interface"]
        interface.pop("privacyPolicyURL")
        with self.assertRaisesRegex(ValueError, "privacyPolicyURL"):
            package.validate_public(manifest, files)

    def test_png_rejects_corrupt_chunks_and_invalid_image_layout(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        header = chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 6, 0, 0, 0))
        ending = chunk(b"IEND", b"")
        prefix = b"\x89PNG\r\n\x1a\n" + header
        valid = prefix + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00" * 1024) * 256)) + ending
        package.validate_png(valid)
        corrupt = bytearray(valid)
        corrupt[45] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            package.validate_png(bytes(corrupt))
        short_image = prefix + chunk(b"IDAT", zlib.compress(b"\x00")) + ending
        with self.assertRaisesRegex(ValueError, "pixel layout"):
            package.validate_png(short_image)
        bad_filter = prefix + chunk(b"IDAT", zlib.compress((b"\x05" + b"\x00" * 1024) * 256)) + ending
        with self.assertRaisesRegex(ValueError, "row filter"):
            package.validate_png(bad_filter)


if __name__ == "__main__":
    unittest.main()
