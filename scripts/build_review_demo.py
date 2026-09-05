#!/usr/bin/env python3
"""Build the offline public demo from its inspected snapshot (no agent calls)."""

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "docs" / "demos"


def build() -> str:
    source = (DEMO / "finding-review.source.json").read_text(encoding="utf-8")
    data = json.loads(source)
    if data.get("kind") != "duet.public.demo" or data.get("schema_version") != 1:
        raise ValueError("Unsupported demo source")
    for name, digest in data["fixture_sha256"].items():
        snapshot = data["fixture_sources"][name].encode("utf-8")
        if hashlib.sha256(snapshot).hexdigest() != digest:
            raise ValueError("Captured fixture checksum differs: " + name)
    if [run["label"] for run in data["runs"]] != ["review", "continuation"]:
        raise ValueError("Expected the recorded review and its continuation")
    for run in data["runs"]:
        report = run["report"]
        if report["truncated"] or not report["available"]:
            raise ValueError("The demo requires complete finding reports")
        observed = {f["id"]: f["disposition"] for f in report["findings"]}
        if observed != {"L1": "supported", "L2": "refuted", "L3": "unresolved"}:
            raise ValueError("Captured findings changed; rewrite the storyboard")
        if report["executed_checks"]:
            raise ValueError("Captured checks changed; rewrite the storyboard")
    # Escape JSON for an inert script element, including hostile HTML in evidence.
    embedded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace("&", "\\u0026")
    template = (DEMO / "finding-review.template.html").read_text(encoding="utf-8")
    if template.count("__DEMO_DATA__") != 1:
        raise ValueError("Expected exactly one source marker")
    return template.replace("__DEMO_DATA__", embedded)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check the committed HTML is current")
    args = parser.parse_args()
    output = DEMO / "finding-review.html"
    html = build()
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != html:
            raise SystemExit("Demo HTML is stale; run python3 scripts/build_review_demo.py")
        print("Demo snapshot checksums and generated HTML are current.")
    else:
        output.write_text(html, encoding="utf-8")
        print("Built docs/demos/finding-review.html (offline; no agents invoked).")


if __name__ == "__main__":
    main()
