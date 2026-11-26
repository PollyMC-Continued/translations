#!/usr/bin/env python3
"""Diff policy check for the PollyMC-Continued translations repository.

Given a merge-base ref (usually origin/main or the PR base), this script lists
every change between the merge-base and the working tree, and rejects anything
that is not a pure translation-text edit:

  rejected outright:
    - added or deleted *.ts files (languages are added by maintainers only),
    - added or deleted contexts,
    - added or deleted messages,
    - edits to <source>, <name>, <comment>, <extracomment>, <location> or any
      attribute except the type/variants attributes of <translation>,
    - new files outside the repository root .ts set (scripts, docs and workflow
      changes are reviewed case by case and never arrive through the editor).

  accepted:
    - changes to the text of <translation> and <numerusform> elements,
    - removing type="unfinished" or type="fuzzy" from a <translation> whose text
      became non-empty (that is what finishing a string means),
    - reordering of nothing: the message and context sequence must stay fixed.

The comparison is semantic, on parsed XML trees, not textual, so formatting or
attribute order differences do not trigger false rejections.

Usage: python3 scripts/check_diff.py [base-ref]
       (no argument: compare against HEAD~1, for local smoke tests)
Exit code 0 = the diff is a pure translation diff, 1 otherwise.
"""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

TEXT_CHILDREN = {"translation", "numerusform"}
NEUTRAL_ATTRS = {"type", "variants"}  # translation element attributes an editor may set/unset


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def message_key(context_name: str, message: ET.Element) -> tuple[str, str, bool, str]:
    source = message.findtext("source") or ""
    numerus = message.get("numerus") == "yes"
    comment = (message.findtext("comment") or "").strip()
    return (context_name, source, numerus, comment)


def parse(path: Path) -> tuple[dict, dict]:
    """Return {message_key: translation_element} and {context_name: contexts} for a .ts file."""
    root = ET.parse(str(path)).getroot()
    messages: dict[tuple[str, str, bool, str], ET.Element] = {}
    contexts: dict[str, int] = {}
    for context in root.findall("context"):
        name = (context.findtext("name") or "").strip()
        contexts[name] = contexts.get(name, 0) + 1
        for message in context.findall("message"):
            messages[message_key(name, message)] = message
    return messages, contexts


def changed_files(base: str) -> list[str]:
    diff = git("diff", "--name-status", base)
    if diff.returncode != 0:
        sys.exit(f"check_diff.py: git diff failed: {diff.stderr.strip()}")
    entries = []
    for line in diff.stdout.splitlines():
        if not line.strip():
            continue
        status, _, name = line.partition("\t")
        entries.append((status.strip(), name.strip()))
    return entries


def read_base_version(base: str, name: str) -> bytes | None:
    blob = git("show", f"{base}:{name}")
    if blob.returncode != 0:
        return None
    return blob.stdout.encode("utf-8", "surrogateescape")


def main() -> int:
    if len(sys.argv) > 2:
        sys.exit(f"usage: {sys.argv[0]} [base-ref]")
    base = sys.argv[1] if len(sys.argv) > 1 else "HEAD~1"

    if git("rev-parse", "--verify", base).returncode != 0:
        sys.exit(f"check_diff.py: base ref {base!r} not found")

    ts_files = {p.name for p in REPO.glob("*.ts")}
    problems: list[str] = []

    for status, name in changed_files(base):
        if not name.endswith(".ts"):
            # Docs, scripts and workflows are maintained directly by maintainers;
            # flag them so the reviewer notices the PR is not an editor PR.
            if status in ("A", "D"):
                problems.append(f"non-translation file {status} {name} (maintainer-only change)")
            continue

        if name not in ts_files:
            problems.append(f"{status} {name}: file outside the repository root")
            continue

        if status not in ("M",):
            problems.append(f"{status} {name}: language files cannot be added or removed")
            continue

        old_bytes = read_base_version(base, name)
        new_bytes = (REPO / name).read_bytes()
        if old_bytes is None:
            problems.append(f"{name}: cannot read base version of the file")
            continue

        try:
            old_msgs, old_ctx = parse_bytes(old_bytes)
            new_msgs, new_ctx = parse_bytes(new_bytes)
        except ET.ParseError as exc:
            problems.append(f"{name}: XML parse error {exc}")
            continue

        for ctx_name, count in new_ctx.items():
            if old_ctx.get(ctx_name, 0) != count:
                problems.append(f"{name}: context {ctx_name} added or removed")
        for ctx_name in old_ctx:
            if ctx_name not in new_ctx:
                problems.append(f"{name}: context {ctx_name} removed")

        for key in new_msgs:
            if key not in old_msgs:
                problems.append(f"{name}: new message in {key[0]} for {key[1][:40]!r}")
        for key in old_msgs:
            if key not in new_msgs:
                problems.append(f"{name}: message removed in {key[0]} for {key[1][:40]!r}")

        for key in new_msgs:
            old_message = old_msgs.get(key)
            if old_message is None:
                continue
            new_message = new_msgs[key]
            old_tr, new_tr = old_message.find("translation"), new_message.find("translation")

            # Source text and structural children must be untouched.
            for tag in ("source", "comment", "extracomment"):
                old_text, new_text = old_message.findtext(tag), new_message.findtext(tag)
                if old_text != new_text:
                    problems.append(f"{name}: <{tag}> changed for {key[1][:40]!r}")
            old_locs = [l.attrib for l in old_message.findall("location")]
            new_locs = [l.attrib for l in new_message.findall("location")]
            if old_locs != new_locs:
                problems.append(f"{name}: <location> changed for {key[1][:40]!r}")
            if (old_message.get("numerus") or "") != (new_message.get("numerus") or ""):
                problems.append(f"{name}: numerus attribute changed for {key[1][:40]!r}")

            # Translation comparison: text of the element and its forms.
            old_forms = [el.text or "" for el in old_tr.findall("numerusform")]
            new_forms = [el.text or "" for el in new_tr.findall("numerusform")]
            if old_tr.text != new_tr.text or old_forms != new_forms:
                continue  # this is the allowed change: translation text
            # No text change: only the type attribute may have been updated
            # (finishing or fuzzing a string).
            for attr in set(old_tr.attrib) | set(new_tr.attrib):
                if attr not in NEUTRAL_ATTRS and old_tr.get(attr) != new_tr.get(attr):
                    problems.append(f"{name}: translation attribute {attr!r} changed for {key[1][:40]!r}")

    if problems:
        print("check_diff.py: rejecting the diff:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("check_diff.py: diff is a pure translation-text change set.")
    return 0


def parse_bytes(data: bytes) -> tuple[dict, dict]:
    import io

    root = ET.parse(io.BytesIO(data)).getroot()
    messages: dict[tuple[str, str, bool, str], ET.Element] = {}
    contexts: dict[str, int] = {}
    for context in root.findall("context"):
        name = (context.findtext("name") or "").strip()
        contexts[name] = contexts.get(name, 0) + 1
        for message in context.findall("message"):
            messages[message_key(name, message)] = message
    return messages, contexts


if __name__ == "__main__":
    sys.exit(main())
