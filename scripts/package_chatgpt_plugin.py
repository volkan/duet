#!/usr/bin/env python3
"""Validate and export separate private and public ChatGPT plugin packages."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import zipfile
import zlib


ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "duet-chatgpt"
PUBLIC = ROOT / "packaging" / "duet-chatgpt"
SKILL = "skills/duet-chatgpt/"
FILES = {
    "plugin.json", "LICENSE", SKILL + "SKILL.md",
    SKILL + "references/peer-brief.md", SKILL + "scripts/control.py",
}


def validate_interface(interface):
    if not isinstance(interface, dict):
        raise ValueError("plugin interface must be an object")
    for key in ("displayName", "shortDescription", "longDescription", "developerName"):
        if not isinstance(interface.get(key), str) or not interface[key].strip():
            raise ValueError("interface requires nonempty string: " + key)
    value = interface.get("defaultPrompt")
    prompts = [value] if isinstance(value, str) else value
    if (not isinstance(prompts, list) or not 1 <= len(prompts) <= 3
            or any(not isinstance(p, str) or not p.strip() or len(p) > 128
                   or "\n" in p or "\r" in p for p in prompts)):
        raise ValueError("invalid plugin presentation prompts")
    if len({" ".join(p.split()) for p in prompts}) != len(prompts):
        raise ValueError("duplicate plugin presentation prompts")
    if (len(interface["shortDescription"]) > 30
            or len(interface["displayName"]) > 30
            or len(interface["longDescription"]) > 4000
            or len(interface["developerName"]) > 80
            or interface.get("category") != "Developer Tools"
            or interface.get("capabilities") != []):
        raise ValueError("invalid skills-only plugin presentation")


def validate_png(icon):
    if len(icon) > 5 * 1024 * 1024 or icon[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("public icon must be a PNG below 5 MiB")
    position = 8
    header = None
    compressed = bytearray()
    palette = False
    idat_closed = False
    ended = False
    while position + 12 <= len(icon):
        length = struct.unpack(">I", icon[position:position + 4])[0]
        kind = icon[position + 4:position + 8]
        end = position + 8 + length
        if end + 4 > len(icon):
            raise ValueError("truncated PNG chunk")
        payload = icon[position + 8:end]
        checksum = struct.unpack(">I", icon[end:end + 4])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != checksum:
            raise ValueError("PNG chunk checksum mismatch")
        if header is None:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG requires its complete IHDR first")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IHDR":
            raise ValueError("duplicate PNG header")
        if kind == b"PLTE":
            if compressed or not length or length % 3 or length > 768:
                raise ValueError("invalid PNG palette")
            palette = True
        elif kind == b"IDAT":
            if idat_closed:
                raise ValueError("nonconsecutive PNG image data")
            compressed.extend(payload)
        elif compressed:
            idat_closed = True
        if kind == b"IEND":
            if length or end + 4 != len(icon):
                raise ValueError("invalid PNG ending")
            ended = True
            break
        if kind[0] & 32 == 0 and kind not in (b"IHDR", b"PLTE", b"IDAT", b"IEND"):
            raise ValueError("unsupported critical PNG chunk")
        position = end + 4
    if not ended or not header or not compressed:
        raise ValueError("incomplete PNG image")
    width, height, depth, color, compression, filtering, interlace = header
    depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if (not 256 <= width == height <= 4096 or depth not in depths.get(color, ())
            or compression or filtering or interlace not in (0, 1)
            or (color == 3 and not palette)):
        raise ValueError("invalid PNG dimensions or encoding")
    bits = depth * {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = ((0, 0, 1, 1),) if interlace == 0 else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
    )
    rows = []
    for x, y, dx, dy in passes:
        columns, count = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if columns and count:
            rows.extend([1 + (columns * bits + 7) // 8] * count)
    expected = sum(rows)
    inflater = zlib.decompressobj()
    try:
        # Bound inflation by the declared pixel layout instead of compressed size.
        pixels = inflater.decompress(compressed, expected + 1)
    except zlib.error as exc:
        raise ValueError("invalid PNG image data") from exc
    if len(pixels) != expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise ValueError("PNG image data does not match its pixel layout")
    offset = 0
    for size in rows:
        if pixels[offset] > 4:
            raise ValueError("invalid PNG row filter")
        offset += size


def validate_public(manifest, files):
    settings = manifest["extensions"]["com.openai"]
    if any(settings.get(key) is not None for key in ("apps", "hooks")):
        raise ValueError("public uploads cannot contain app bindings or lifecycle hooks")
    interface = settings["interface"]
    for key in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
        value = interface.get(key)
        if (not isinstance(value, str) or not value.startswith("https://")
                or len(value) > 1024):
            raise ValueError("public package requires a valid HTTPS listing URL: " + key)
    if any(interface.get(key) != "./assets/icon.png" for key in ("logo", "composerIcon")):
        raise ValueError("public icon references must point to the packaged icon")
    validate_png(files["assets/icon.png"])
    publication = settings.get("publication", {})
    if not isinstance(publication, dict):
        raise ValueError("public publication metadata must be an object")
    notes = publication.get("release_notes")
    if (publication.get("countries") != [] or not isinstance(notes, str) or not notes.strip()):
        raise ValueError("public package requires the confirmed targeting and release notes")
    review = settings.get("review", {})
    if not isinstance(review, dict) or review.get("commerce") is not False:
        raise ValueError("public package must preserve the confirmed no-commerce declaration")
    prompts = review.get("prompts")
    if (not isinstance(prompts, list) or not 1 <= len(prompts) <= 20
            or any(not isinstance(p, str) or not p.strip() or len(p) > 4000 for p in prompts)):
        raise ValueError("invalid public review prompts")


def validate(root=PLUGIN, public=False):
    paths = list(root.rglob("*"))
    if root.is_symlink() or any(path.is_symlink() for path in paths):
        raise ValueError("plugin must not contain symlinks")
    names = {path.relative_to(root).as_posix() for path in paths if path.is_file()}
    expected = FILES | ({"assets/icon.png"} if public else set())
    if names != expected:
        raise ValueError("unexpected or missing package files: " + str(sorted(names ^ expected)))
    files = {name: (root / name).read_bytes() for name in sorted(names)}
    manifest = json.loads(files["plugin.json"])
    if not isinstance(manifest, dict):
        raise ValueError("plugin manifest must be an object")
    if (manifest.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
            or manifest.get("name") != "duet-chatgpt"
            or not re.fullmatch(r"\d+\.\d+\.\d+", manifest.get("version", ""))):
        raise ValueError("invalid portable plugin identity, schema, or version")
    if any(key in manifest for key in ("skills", "mcpServers", "apps", "interface")):
        raise ValueError("portable components must use their standard locations")
    interface = manifest["extensions"]["com.openai"]["interface"]
    validate_interface(interface)
    if public:
        validate_public(manifest, files)
    if files["LICENSE"] != (ROOT / "LICENSE").read_bytes():
        raise ValueError("plugin must preserve the repository license")
    skill = files[SKILL + "SKILL.md"].decode("utf-8")
    if len(files[SKILL + "SKILL.md"]) > 1024 * 1024 or not skill.startswith("---\n"):
        raise ValueError("invalid skill frontmatter or size")
    # These two YAML scalars use JSON quoting, so no YAML dependency is needed.
    frontmatter, body = skill[4:].split("\n---\n", 1)
    metadata = dict(line.split(": ", 1) for line in frontmatter.splitlines())
    metadata = {key: json.loads(value) for key, value in metadata.items()}
    if (metadata.get("name") != "duet-chatgpt"
            or not isinstance(metadata.get("description"), str)
            or not metadata["description"].strip() or not body.strip()):
        raise ValueError("invalid skill name, description, or workflow")
    for target in re.findall(r"\]\(([^)]+)\)", body):
        if target.startswith(("/", "http:", "https:")) or ".." in Path(target).parts:
            raise ValueError("skill references must stay inside the package")
        if SKILL + target not in files:
            raise ValueError("missing skill reference: " + target)
    ast.parse(files[SKILL + "scripts/control.py"], feature_version=(3, 9))
    return manifest, files


def inspect_archive(path, files):
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != len(files) or set(archive.namelist()) != set(files):
            raise ValueError("archive members do not match the plugin")
        for name, content in files.items():
            if archive.read(name) != content:
                raise ValueError("archive content mismatch: " + name)


def package_contents(root=PLUGIN, public=False, publication=PUBLIC):
    manifest, files = validate(root)
    if not public:
        return manifest, files
    metadata = json.loads((publication / "openai.json").read_text(encoding="utf-8"))
    if (not isinstance(metadata, dict) or set(metadata) != {"interface", "review", "publication"}
            or any(not isinstance(value, dict) for value in metadata.values())):
        raise ValueError("invalid public metadata sections")
    settings = manifest["extensions"]["com.openai"]
    settings["interface"].update(metadata["interface"])
    settings.update({key: metadata[key] for key in ("review", "publication")})
    files["plugin.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    icon = publication / "assets/icon.png"
    if icon.is_symlink():
        raise ValueError("public icon must not be a symlink")
    files["assets/icon.png"] = icon.read_bytes()
    with tempfile.TemporaryDirectory(prefix="duet-public-package-") as temporary:
        staging = Path(temporary) / manifest["name"]
        for name, content in files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return validate(staging, public=True)


def build(output_dir, root=PLUGIN, public=False, publication=PUBLIC):
    manifest, files = package_contents(root, public, publication)
    output_dir = output_dir.resolve()
    if output_dir == root.resolve() or root.resolve() in output_dir.parents:
        raise ValueError("write archives outside the plugin source directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "-public" if public else ""
    destination = output_dir / ("duet-chatgpt-" + manifest["version"] + suffix + ".zip")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output_dir, delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        inspect_archive(temporary, files)
        if package_contents(root, public, publication)[1] != files:
            raise ValueError("source changed during packaging; rebuild")
        os.replace(str(temporary), str(destination))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="build an inspected ZIP in this directory")
    parser.add_argument("--public", action="store_true", help="apply confirmed public listing metadata and icon")
    args = parser.parse_args(argv)
    try:
        manifest, _ = package_contents(public=args.public)
        if args.output_dir:
            path = build(args.output_dir, public=args.public)
            print(str(path))
            print("SHA-256: " + hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            print("[chatgpt-plugin] ok: duet-chatgpt " + manifest["version"])
        return 0
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        print("[chatgpt-plugin] " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
