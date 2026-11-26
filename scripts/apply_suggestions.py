#!/usr/bin/env python3
"""Apply translation suggestions collected from the website editor.

The website's /translation/editor/ page queues anonymous suggestions in Redis
(see the website's api/translations.js). A maintainer pops them with
api/translations-admin.js, saves the JSON to a file, and runs:

    python3 scripts/apply_suggestions.py de.ts submission.json -o de.ts

or, to preview only:

    python3 scripts/apply_suggestions.py de.ts submission.json --dry-run

The script applies each suggestion by message position (the same stable index
the editor derives from parsing the file), checks that the message is still
unfinished and that the text the contributor started from matches `base`,
then writes the new translation and drops the unfinished marker. Placeholder
linting uses exactly the rules of validate.py, so anything that would fail CI
is reported here before a PR is opened.

Exit code 0 = applied (or dry-run clean), 1 = rejected, 2 = partial conflicts
(apply with --force to take the non-conflicting ones).
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PERCENT_RE = re.compile(r"%(L\d+|\d+|n)")
ENTITY_RE = re.compile(r"&[a-zA-Z]+;")
TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*[^>]*>")
TAG_NAME_RE = re.compile(r"</?\s*([a-zA-Z][a-zA-Z0-9]*)")


def placeholders(text: str) -> list[str]:
    tokens = []
    for m in PERCENT_RE.finditer(text or ""):
        tokens.append("%" + m.group(1).lstrip("L") if m.group(1) != "n" else "%n")
    for m in ENTITY_RE.finditer(text or ""):
        tokens.append(m.group(0))
    for m in TAG_RE.finditer(text or ""):
        name = TAG_NAME_RE.match(m.group(0))
        if name:
            tokens.append(f"<{name.group(1)}>")
    return tokens


def lint(source: str, target: str) -> str | None:
    if not target.strip():
        return None
    want, got = placeholders(source), placeholders(target)
    if sorted(want) != sorted(got):
        return f"placeholders: source {want} vs translation {got}"
    return None


def collect_messages(root: ET.Element) -> list[ET.Element]:
    """All <message> elements in document order, matching the editor's index."""
    out = []
    for context in root.findall("context"):
        out.extend(context.findall("message"))
    return out


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    dry_run = "--dry-run" in args
    force = "--force" in args
    args = [a for a in args if not a.startswith("--")]
    if len(args) < 2:
        print("usage: apply_suggestions.py <file.ts> <submission.json> [--dry-run] [--force]")
        return 1
    ts_path, json_path = Path(args[0]), Path(args[1])

    submission = json.loads(json_path.read_text(encoding="utf-8"))
    suggestions = submission.get("suggestions", [])

    tree = ET.parse(str(ts_path))
    root = tree.getroot()
    messages = collect_messages(root)

    applied, skipped, conflicts = [], [], []
    for s in suggestions:
        idx = s["index"]
        if not isinstance(idx, int) or idx < 0 or idx >= len(messages):
            skipped.append((idx, "index out of range"))
            continue
        message = messages[idx]
        tr = message.find("translation")
        if tr is None:
            skipped.append((idx, "no translation element"))
            continue
        state = tr.get("type") or ""
        if state not in ("unfinished", "fuzzy", ""):
            skipped.append((idx, f"message is {state}; someone else already changed it"))
            continue

        forms = tr.findall("numerusform")
        current = [f.text or "" for f in forms] if forms else [tr.text or ""]
        base = s.get("base", current)
        if [b.strip() for b in base] != [c.strip() for c in current]:
            conflicts.append((idx, "base mismatch: the file changed since the contributor saw it"))
            continue

        problem = lint(message.findtext("source") or "", "\n".join(s["texts"]))
        if problem:
            skipped.append((idx, f"lint failed: {problem}"))
            continue

        if forms:
            for form, text in zip(forms, s["texts"]):
                form.text = text
        else:
            tr.text = s["texts"][0]
        if "type" in tr.attrib:
            del tr.attrib["type"]
        applied.append(idx)

    # Report.
    for idx, why in skipped:
        print(f"skip  #{idx}: {why}")
    for idx, why in conflicts:
        print(f"conflict #{idx}: {why}")
    print(f"applied {len(applied)} / {len(suggestions)} suggestions")

    if conflicts and not force:
        print("the file was NOT written; re-run with --force to apply the non-conflicting ones")
        return 2
    if dry_run:
        return 0

    tree.write(str(ts_path), encoding="utf-8", xml_declaration=True)
    print(f"wrote {ts_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
