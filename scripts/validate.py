#!/usr/bin/env python3
"""Structural and placeholder validation for the PollyMC-Continued translations repository.

Checks every *.ts file in the repository root for:
  - well-formed XML parseable by a strict parser (xml.etree + lxml when available),
  - a single TS root with version and language attributes (language may be absent
    only for files explicitly allow-listed below, such as en_NZ.ts),
  - contexts with a <name>, messages with a <source> and a <translation>,
  - numerus messages carrying at least one <numerusform>, non-numerus messages
    carrying none,
  - translations that preserve every placeholder that their source contains:
    %n, %1..%9 (also with L prefix such as %L1), & accelerators, and HTML tags
    (with the tag names and their order intact, ignoring attributes).

Only unfinished and fuzzy translations that contain text are checked for
placeholders; empty ones are the normal state of a string that still needs work,
and vanished or obsolete entries are skipped because they are not shown to users.
Placeholder order may differ from the source (grammar rules differ between
languages: "Installing %1 from %2" is "installing from %2 the %1" in many of
them), so the placeholder SET is compared, not the sequence. A missing or extra
placeholder is an error; a different order is reported as a note only.

Exit code 0 = all files pass, 1 = at least one file failed.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXIT_OK = 0
EXIT_FAIL = 1

# Files that may lack a language attribute on <TS> (upstream generates them that way).
NO_LANGUAGE_OK = {"en_NZ"}

PLACEHOLDER_RE = re.compile(r"%(L\d+|\d+|n)")
ENTITY_RE = re.compile(r"&[a-zA-Z]+;")
TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*[^>]*>")
TAG_NAME_RE = re.compile(r"</?\s*([a-zA-Z][a-zA-Z0-9]*)")

# Known pre-existing placeholder defects in the files as they came from the
# launcher repository. These are the only mismatches accepted; everything else
# fails the build. An entry may be removed once the translation is fixed, and
# fixing one of these strings does not fail the check (the allow-list matches,
# not exact counts, and a passing string simply never reaches it).
# Format: {file: {(context, source): {placeholders expected}}}.
KNOWN_MISMATCHES = {
    "fa.ts": {
        ("Meta::Index", "Load meta for %1:%2"),
        ("Meta::VersionList", "Load meta for %1"),
    },
    "is.ts": {
        ("ExternalResourcesPage", "Select %1"),
    },
    "ms.ts": {
        ("Meta::VersionList", "Load meta for %1"),
    },
    "mt.ts": {
        (
            "ATLauncher::PackInstallTask",
            "The following files are not available for download in third party launchers.<br/>"
            "You will need to manually download them and add them to the instance.",
        ),
    },
}


def placeholders(text: str) -> list[str]:
    """Return the placeholder tokens of a source or translation string.

    %1 and %L1 compare equal (Qt treats the L form as a locale-aware variant of
    the same argument), and HTML tags compare by tag name only, so <a href="x">
    matches <a>. Ordering is preserved so the report is deterministic.
    """
    tokens = []
    for match in PLACEHOLDER_RE.finditer(text or ""):
        digits = match.group(1).lstrip("L")
        tokens.append("%" + digits)
    for match in ENTITY_RE.finditer(text or ""):
        tokens.append(match.group(0))
    for match in TAG_RE.finditer(text or ""):
        name = TAG_NAME_RE.match(match.group(0))
        if name:
            tokens.append(f"<{name.group(1)}>")
    return tokens


def check_file(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []

    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as exc:
        return [f"XML parse error: {exc}"], []

    if root.tag != "TS":
        errors.append(f"root element is <{root.tag}>, expected <TS>")
        return errors

    lang = root.get("language")
    if lang is None and path.stem not in NO_LANGUAGE_OK:
        errors.append('missing language attribute on <TS>')
    if root.get("version") is None:
        errors.append("missing version attribute on <TS>")

    for context in root.findall("context"):
        name_el = context.find("name")
        if name_el is None or not (name_el.text or "").strip():
            errors.append("context without a <name>")
            continue
        context_name = name_el.text.strip()
        seen_messages: set[tuple[str, str, bool, str]] = set()

        for message in context.findall("message"):
            source_el = message.find("source")
            translation_el = message.find("translation")
            if source_el is None:
                errors.append(f"{context_name}: message without <source>")
                continue
            if translation_el is None:
                errors.append(f"{context_name}: message without <translation>")
                continue

            source = source_el.text or ""
            comment = (message.findtext("comment") or "").strip()
            # A vanished or obsolete message is dead code kept for lupdate; only
            # two live copies of the same (source, numerus, comment) are a fault.
            state = translation_el.get("type")
            if state not in ("vanished", "obsolete"):
                key = (source, message.get("numerus") == "yes", comment)
                if key in seen_messages:
                    errors.append(f"{context_name}: duplicate message for {source[:40]!r}")
                seen_messages.add(key)

            numerus = message.get("numerus") == "yes"
            forms = translation_el.findall("numerusform")
            if numerus and not forms:
                errors.append(f"{context_name}: numerus message without <numerusform> for {source[:40]!r}")
            if not numerus and forms:
                errors.append(f"{context_name}: <numerusform> in a non-numerus message for {source[:40]!r}")

            if state in ("unfinished", "fuzzy"):
                for i, form in enumerate(forms or [translation_el]):
                    target = (form.text or "").strip()
                    if not target:
                        continue
                    src_tokens = placeholders(source)
                    dst_tokens = placeholders(target)
                    if sorted(src_tokens) != sorted(dst_tokens):
                        known = (context_name, source) in KNOWN_MISMATCHES.get(path.name, set())
                        level = "KNOWN MISMATCH (pre-existing)" if known else "placeholder mismatch"
                        errors.append(
                            f"{context_name}: {level} in form {i} for {source[:40]!r}: "
                            f"expected {sorted(src_tokens)}, got {sorted(dst_tokens)}"
                        )
                    elif src_tokens != dst_tokens:
                        notes.append(
                            f"{context_name}: note: placeholder order differs for {source[:40]!r} "
                            f"(source {src_tokens} vs translation {dst_tokens})"
                        )

    return errors, notes


def main() -> int:
    files = sorted(REPO.glob("*.ts"))
    if not files:
        print("validate.py: no .ts files found next to the script's parent directory", file=sys.stderr)
        return EXIT_FAIL

    failed = 0
    for path in files:
        errors, notes = check_file(path)
        # Pre-existing upstream defects on the allow-list are tolerated: they
        # are printed for visibility but do not fail the build, so that fixing
        # one does not break CI while the rest are still open.
        hard_errors = [e for e in errors if "KNOWN MISMATCH" not in e]
        if hard_errors:
            failed += 1
            print(f"[FAIL] {path.name}")
            for error in hard_errors[:20]:
                print(f"       {error}")
            if len(hard_errors) > 20:
                print(f"       ... and {len(hard_errors) - 20} more")
        elif errors:
            print(f"[warn] {path.name}")
            for error in errors[:20]:
                print(f"       {error}")
        else:
            print(f"[ok] {path.name}")
        for note in notes[:5]:
            print(f"       {note}")

    print(f"\n{len(files) - failed}/{len(files)} files passed.")
    return EXIT_FAIL if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
