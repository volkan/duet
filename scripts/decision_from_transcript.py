#!/usr/bin/env python3
"""Build DECISION_vN.{md,html} from a duet repo-compare transcript.

Used when a duet run produces all the per-repo JSON rubric blocks but the
agents fail to write the deliverables themselves (deadlock, sandbox path
issues, etc. — see runs/20260506-235839/ for the original failure that
motivated this script).

Usage:
    python3 scripts/decision_from_transcript.py \\
        --transcript runs/<run_id>/transcript.md \\
        --out-dir ../    # where DECISION_v2.{md,html} should land

Schema expected per JSON block (one per repo, fenced ```json … ``` in the
codex turn 1 output):

    {
      "owner": "...", "repo": "...",
      "stars": int|null, "forks": int|null,
      "license": str|null, "pushedAt": "YYYY-MM-DDTHH:MM:SSZ"|null,
      "language": str|null,
      "h1".."h8": "✅"|"🟡"|"❌"|"❓",
      "s1".."s9": "✅"|"🟡"|"❌"|"❓",
      "evidence": { "h1": "quote", ... }    # may be H-only
    }

Scoring: ✅=1.0, 🟡=0.5, ❌/❓=0.0. Total /17 = 8 hard + 9 soft.

The HTML is single-file, self-contained (inline SVG + CSS + ≤30-line JS),
prefers-color-scheme dark/light, and matches the spec laid out in
examples/repo-compare.yaml's "## HTML deliverable" section.
"""
import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sys


EMOJI_SCORE = {"✅": 1.0, "🟡": 0.5, "❌": 0.0, "❓": 0.0}
H_KEYS = [f"h{i}" for i in range(1, 9)]
S_KEYS = [f"s{i}" for i in range(1, 10)]


def parse_repos(transcript_path: pathlib.Path) -> list[dict]:
    text = transcript_path.read_text()
    # Take the first codex turn (after the seed). Stop at the worktree-changes
    # block, otherwise we'd pick up JSON inside that diff too.
    parts = text.split("## codex-researcher (coder) — agent", 1)
    if len(parts) < 2:
        sys.exit(f"no codex turn found in {transcript_path}")
    codex1 = parts[1].split("---\n#### worktree changes", 1)[0]
    blocks = re.findall(r"```json\s*\n(\{.*?\})\s*```", codex1, re.DOTALL)
    if not blocks:
        sys.exit("no JSON blocks in codex turn 1")
    return [json.loads(b) for b in blocks]


def total_h(r): return sum(EMOJI_SCORE.get(r.get(k, "❓"), 0.0) for k in H_KEYS)
def total_s(r): return sum(EMOJI_SCORE.get(r.get(k, "❓"), 0.0) for k in S_KEYS)
def total(r):   return total_h(r) + total_s(r)
def fmt_id(r):  return f"{r['owner']}/{r['repo']}"


def compute_ranks(repos: list[dict]) -> list[str]:
    """Returns rank strings; ties get '=' suffix on both sides."""
    ranks: list[str] = []
    for i, r in enumerate(repos):
        if i == 0 or total(r) != total(repos[i - 1]):
            ranks.append(str(i + 1))
        else:
            ranks.append(ranks[-1].rstrip("=") + "=")
            if not ranks[-1 - 1].endswith("="):
                ranks[-2] += "="
    return ranks


def build_md(repos: list[dict], ranks: list[str], today: str) -> str:
    out: list[str] = []
    winner = repos[0]
    out.append(f"# DECISION v2 — duet vs candidate harnesses ({today})\n")
    out.append("> Re-scored using `gh` as the primary source (real stars, "
               "license, last commit, full README).\n\n")

    out.append("## 1. Final matrix\n\n")
    out.append("| rank | repo | hard /8 | soft /9 | total /17 | stars | license |\n")
    out.append("|---|---|---|---|---|---|---|\n")
    for r, rk in zip(repos, ranks):
        bold = f"**{total(r)}**" if r is winner else f"{total(r)}"
        stars = r.get("stars") if r.get("stars") is not None else "—"
        lic = r.get("license") or "—"
        out.append(
            f"| {rk} | {fmt_id(r)} | {total_h(r):.1f} | {total_s(r):.1f} | "
            f"{bold} | {stars} | {lic} |\n"
        )
    out.append("\nLegend: ✅=1.0  ·  🟡=0.5  ·  ❌=0.0  ·  ❓=0.0 (couldn't verify)\n\n")

    out.append("## 2. Per-criterion grid\n\n")
    out.append("| repo | " + " | ".join(f"H{i}" for i in range(1, 9)) +
               " | " + " | ".join(f"S{i}" for i in range(1, 10)) + " |\n")
    out.append("|---|" + "---|" * 17 + "\n")
    for r in repos:
        cells = [r.get(k, "❓") for k in H_KEYS + S_KEYS]
        out.append(f"| {fmt_id(r)} | " + " | ".join(cells) + " |\n")

    margin = total(winner) - total(repos[1])
    out.append("\n## 3. Recommendation\n\n")
    out.append(
        f"**Keep {fmt_id(winner)}.** Total {total(winner)}/17 vs "
        f"{total(repos[1])} for the next-best — a {margin:.1f}-point margin "
        f"(≈{margin/17*100:.0f}% of the rubric).\n"
    )
    return "".join(out)


def build_html(repos: list[dict], ranks: list[str], today: str,
               source_run: str) -> str:
    def esc(s):
        return html.escape(str(s) if s is not None else "—", quote=True)

    def cell_class(emoji):
        return {"✅": "pass", "🟡": "partial", "❌": "fail",
                "❓": "unknown"}.get(emoji, "unknown")

    winner = repos[0]
    margin = total(winner) - total(repos[1])

    BAR_MAX = 700
    SCALE = BAR_MAX / 17
    ROW_H = 36
    SVG_W = 1000
    SVG_H = ROW_H * len(repos) + 30

    bars: list[str] = []
    for i, r in enumerate(repos):
        y = 20 + i * ROW_H
        h_w = total_h(r) * SCALE
        s_w = total_s(r) * SCALE
        if r is winner:
            bars.append(f'<rect x="0" y="{y-14}" width="{SVG_W}" '
                        f'height="{ROW_H-2}" class="winner-row"/>')
        bars.append(f'<text x="270" y="{y+5}" class="bar-label" '
                    f'text-anchor="end">{esc(fmt_id(r))}</text>')
        bars.append(f'<rect x="280" y="{y-10}" width="{h_w:.1f}" '
                    f'height="20" class="bar-h">'
                    f'<title>hard /8: {total_h(r):.1f}</title></rect>')
        bars.append(f'<rect x="{280+h_w:.1f}" y="{y-10}" width="{s_w:.1f}" '
                    f'height="20" class="bar-s">'
                    f'<title>soft /9: {total_s(r):.1f}</title></rect>')
        bars.append(f'<text x="{280+h_w+s_w+8:.1f}" y="{y+5}" '
                    f'class="bar-total">{total(r):.1f} / 17</text>')

    rows: list[str] = []
    for r, rk in zip(repos, ranks):
        cells: list[str] = []
        for k in H_KEYS + S_KEYS:
            emoji = r.get(k, "❓")
            ev = r.get("evidence", {}).get(k, "")
            score = EMOJI_SCORE.get(emoji, 0.0)
            cells.append(f'<td class="cell {cell_class(emoji)}" '
                         f'title="{esc(ev)}" data-sort="{score}">{emoji}</td>')
        cells.append(f'<td class="total" data-sort="{total(r):.2f}">'
                     f'<b>{total(r):.1f}</b></td>')
        cls = "row winner" if r is winner else "row"
        rows.append(
            f'<tr class="{cls}">'
            f'<td data-sort="{rk.rstrip("=")}">{rk}</td>'
            f'<td data-sort="{esc(fmt_id(r))}"><b>{esc(fmt_id(r))}</b></td>'
            f'{"".join(cells)}</tr>'
        )

    th_h = "".join(f'<th data-sort-kind="num">H{i}</th>' for i in range(1, 9))
    th_s = "".join(f'<th data-sort-kind="num">S{i}</th>' for i in range(1, 10))

    w_stars = winner.get("stars")
    w_stars = "—" if w_stars is None else str(w_stars)
    w_push = (winner.get("pushedAt") or "—").split("T")[0]

    css = """:root {
  --bg: #ffffff; --bg-soft: #f6f8fa; --text: #1f2328; --text-mut: #57606a;
  --border: #d0d7de; --accent: #0969da;
  --c-pass: #1f883d; --c-partial: #bf8700; --c-fail: #cf222e; --c-unknown: #6e7781;
  --winner: #fff8c5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --bg-soft: #161b22; --text: #c9d1d9; --text-mut: #8b949e;
    --border: #30363d; --accent: #58a6ff;
    --c-pass: #3fb950; --c-partial: #d29922; --c-fail: #f85149; --c-unknown: #8b949e;
    --winner: #2d2a1c;
  }
}
* { box-sizing: border-box; }
body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  color: var(--text); background: var(--bg); margin: 0 auto; padding: 32px 40px 64px;
  max-width: 1200px; }
h1, h2 { border-bottom: 1px solid var(--border); padding-bottom: 8px; }
h1 { font-size: 28px; } h2 { font-size: 20px; margin-top: 32px; }
a { color: var(--accent); }
.winner-card { background: var(--bg-soft); border: 2px solid var(--c-pass);
  border-radius: 10px; padding: 24px 28px; margin: 16px 0 32px;
  display: grid; grid-template-columns: auto 1fr; gap: 16px 32px; align-items: center; }
.winner-card .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-mut); font-weight: 600; margin: 0; }
.winner-card .repo { font-size: 28px; margin: 4px 0; font-weight: 700; }
.winner-card .score { font-size: 48px; font-weight: 700; color: var(--c-pass); white-space: nowrap; }
.winner-card .rationale { margin: 8px 0; color: var(--text-mut); }
.winner-card .facts { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap;
  gap: 16px; font-size: 13px; color: var(--text-mut); }
.winner-card .facts li b { color: var(--text); }
.bars { background: var(--bg-soft); padding: 16px; border: 1px solid var(--border);
  border-radius: 8px; overflow-x: auto; }
.bars svg { width: 100%; min-width: 800px; height: auto; display: block; }
.bar-label { font: 13px -apple-system, sans-serif; fill: var(--text); }
.bar-h { fill: var(--c-pass); } .bar-s { fill: var(--accent); opacity: 0.8; }
.bar-total { font: 600 13px -apple-system, sans-serif; fill: var(--text); }
.winner-row { fill: var(--winner); }
.legend { font-size: 13px; color: var(--text-mut); margin-top: 8px; display: flex; gap: 16px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 14px; height: 14px; display: inline-block; border-radius: 3px; }
.matrix { width: 100%; border-collapse: collapse; font-size: 13px; border: 1px solid var(--border); }
.matrix th, .matrix td { padding: 6px 8px; border: 1px solid var(--border); text-align: center; }
.matrix th { background: var(--bg-soft); position: sticky; top: 0; cursor: pointer; user-select: none; }
.matrix th:hover { background: var(--border); }
.matrix th[aria-sort="ascending"]::after  { content: " ▲"; opacity: 0.6; }
.matrix th[aria-sort="descending"]::after { content: " ▼"; opacity: 0.6; }
.matrix td:nth-child(2) { text-align: left; white-space: nowrap; }
.matrix tr.winner { background: var(--winner); }
.matrix .cell { font-size: 16px; cursor: help; }
.matrix .cell.pass    { background: color-mix(in srgb, var(--c-pass) 18%, transparent); }
.matrix .cell.partial { background: color-mix(in srgb, var(--c-partial) 18%, transparent); }
.matrix .cell.fail    { background: color-mix(in srgb, var(--c-fail) 18%, transparent); }
.matrix .cell.unknown { background: color-mix(in srgb, var(--c-unknown) 18%, transparent); }
.matrix .total { font-weight: 700; }
.delta-empty { color: var(--text-mut); font-style: italic; padding: 12px 16px;
  background: var(--bg-soft); border-left: 3px solid var(--text-mut); }
footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--border);
  color: var(--text-mut); font-size: 13px; }"""

    js = """(function() {
  const tbl = document.querySelector('table.matrix');
  if (!tbl) return;
  const tbody = tbl.tBodies[0];
  tbl.querySelectorAll('thead th').forEach((th, idx) => {
    th.addEventListener('click', () => {
      const kind = th.dataset.sortKind || 'str';
      const cur = th.getAttribute('aria-sort');
      const dir = cur === 'descending' ? 'ascending' : 'descending';
      tbl.querySelectorAll('thead th').forEach(o => o.removeAttribute('aria-sort'));
      th.setAttribute('aria-sort', dir);
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => {
        const av = a.cells[idx].dataset.sort ?? a.cells[idx].textContent;
        const bv = b.cells[idx].dataset.sort ?? b.cells[idx].textContent;
        const cmp = kind === 'num' ? (parseFloat(av) - parseFloat(bv))
                                   : av.localeCompare(bv);
        return dir === 'ascending' ? cmp : -cmp;
      });
      rows.forEach(r => tbody.appendChild(r));
    });
  });
})();"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DECISION v2 — duet vs candidate harnesses</title>
<style>{css}</style>
</head>
<body>

<h1>DECISION v2 — agent-to-agent harness comparison</h1>
<p style="color: var(--text-mut)">Re-scored against <code>REQUIREMENTS.md</code> using <code>gh</code> as the primary source. Source run: <code>{esc(source_run)}</code>.</p>

<section class="winner-card" aria-label="Recommended winner">
  <div>
    <p class="label">Winner</p>
    <p class="repo">{esc(fmt_id(winner))}</p>
    <p class="rationale">{margin:.1f}-point lead over the next-best ({total(winner):.1f}/17 vs {total(repos[1]):.1f}/17). See the matrix below for per-criterion evidence.</p>
    <ul class="facts">
      <li><b>Stars:</b> {esc(w_stars)}</li>
      <li><b>License:</b> {esc(winner.get('license') or '—')}</li>
      <li><b>Last push:</b> {esc(w_push)}</li>
      <li><b>Language:</b> {esc(winner.get('language') or '—')}</li>
    </ul>
  </div>
  <div class="score" aria-label="Total score {total(winner):.1f} out of 17">{total(winner):.1f} / 17</div>
</section>

<h2>Score bars (sorted by total)</h2>
<div class="bars" role="img" aria-label="Score bars chart, hard requirements green, soft blue">
<svg viewBox="0 0 {SVG_W} {SVG_H}" xmlns="http://www.w3.org/2000/svg">
{chr(10).join(bars)}
</svg>
<div class="legend">
  <span><i style="background:var(--c-pass)"></i>Hard /8</span>
  <span><i style="background:var(--accent);opacity:0.8"></i>Soft /9</span>
  <span><i style="background:var(--winner);border:1px solid var(--border)"></i>Winner row</span>
</div>
</div>

<h2>Full matrix</h2>
<p style="color: var(--text-mut); font-size: 13px">Hover any cell to see the evidence quote. Click any column header to sort.</p>
<table class="matrix">
<thead><tr>
  <th data-sort-kind="num">#</th>
  <th data-sort-kind="str">repo</th>
  {th_h}
  {th_s}
  <th data-sort-kind="num">/17</th>
</tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
<p class="legend" style="margin-top:12px">
  <span><i style="background:color-mix(in srgb, var(--c-pass) 18%, transparent)"></i>✅ meets (1.0)</span>
  <span><i style="background:color-mix(in srgb, var(--c-partial) 18%, transparent)"></i>🟡 partial (0.5)</span>
  <span><i style="background:color-mix(in srgb, var(--c-fail) 18%, transparent)"></i>❌ fails (0.0)</span>
  <span><i style="background:color-mix(in srgb, var(--c-unknown) 18%, transparent)"></i>❓ unverified (0.0)</span>
</p>

<h2>Delta vs DECISION.md</h2>
<p class="delta-empty">v1 (DECISION.md) wasn't auto-diffed by this script — pass it explicitly to a future revision if you want the per-cell flip list.</p>

<footer>
  Generated <time datetime="{today}">{today}</time> from <code>{esc(source_run)}</code>.
  Markdown view: <a href="DECISION_v2.md">DECISION_v2.md</a>.
</footer>

<script>
{js}
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", required=True,
                    help="path to runs/<id>/transcript.md")
    ap.add_argument("--out-dir", required=True,
                    help="dir to write DECISION_v2.md and DECISION_v2.html into")
    ap.add_argument("--name", default="DECISION_v2",
                    help="basename for output files (default: DECISION_v2)")
    args = ap.parse_args()

    transcript = pathlib.Path(args.transcript).expanduser().resolve()
    out_dir = pathlib.Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    repos = parse_repos(transcript)
    repos.sort(key=lambda r: (-total(r), r["owner"] + "/" + r["repo"]))
    ranks = compute_ranks(repos)
    today = dt.date.today().isoformat()

    md_path = out_dir / f"{args.name}.md"
    html_path = out_dir / f"{args.name}.html"
    md_path.write_text(build_md(repos, ranks, today))
    html_path.write_text(build_html(repos, ranks, today, str(transcript)))

    print(f"wrote {md_path} ({md_path.stat().st_size} bytes)")
    print(f"wrote {html_path} ({html_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
