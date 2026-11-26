#!/usr/bin/env python3
"""Reject pull requests that change anything except translation text.

This is the safety gate for the repository. It compares every changed .ts file
with its version on the base branch and fails when a context, message, source
string, comment, location or plural form structure is added, removed or edited.
Only <translation> text and the unfinished marker may change.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

ALLOWED_TYPES = {None, "unfinished", "vanished", "obsolete"}


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def parse(text: str, path: str) -> ET.Element:
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"{path}: not well-formed XML: {exc}") from exc


def text_of(element: ET.Element | None) -> str:
    return (element.text or "") if element is not None else ""


def message_signature(message: ET.Element) -> tuple:
    """Everything about a message that a translator must not change."""
    locations = tuple(
        (
            location.get("filename") or "",
            location.get("line") or "",
            location.get("column") or "",
        )
        for location in message.findall("location")
    )
    return (
        text_of(message.find("source")),
        text_of(message.find("oldsource")),
        text_of(message.find("comment")),
        text_of(message.find("extracomment")),
        text_of(message.find("translatorcomment")),
        message.get("numerus") or "",
        message.get("id") or "",
        locations,
    )


def file_signature(root: ET.Element, path: str) -> tuple:
    if root.tag != "TS":
        raise ValueError(f"{path}: root element is <{root.tag}>, expected <TS>")
    contexts = []
    for context in root.findall("context"):
        name = (context.findtext("name") or "").strip()
        messages = tuple(message_signature(m) for m in context.findall("message"))
        contexts.append((name, messages))
    return tuple(contexts)


def translation_state(message: ET.Element) -> tuple[str | None, int]:
    translation = message.find("translation")
    if translation is None:
        return None, 0
    return translation.get("type"), len(translation.findall("numerusform"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "base",
        nargs="?",
        default="origin/main",
        help="base branch or commit (default: origin/main)",
    )
    args = parser.parse_args()

    try:
        merge_base = git("merge-base", args.base, "HEAD").strip()
    except subprocess.CalledProcessError:
        print(f"error: cannot find a merge base with {args.base!r}", file=sys.stderr)
        return 1

    try:
        status = git("diff", "--name-status", "--find-renames", f"{merge_base}...HEAD")
    except subprocess.CalledProcessError as exc:
        print(f"error: git diff failed: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    warnings: list[str] = []
    changed_files = 0

    for line in status.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        state = fields[0]
        paths = fields[1:]

        if state.startswith("R"):
            errors.append(
                f"{paths[0]} -> {paths[1]}: translation files cannot be renamed"
            )
            continue

        path = paths[-1]
        if not path.endswith(".ts"):
            warnings.append(f"{path}: non-translation file changed (review manually)")
            continue

        changed_files += 1
        if "/" in path:
            errors.append(f"{path}: translation files must stay at the repository root")
            continue

        if state == "D":
            errors.append(f"{path}: translation files cannot be deleted")
            continue
        if state == "A":
            errors.append(f"{path}: new languages need a maintainer")
            continue
        if state != "M":
            errors.append(f"{path}: unexpected change type {state!r}")
            continue

        if not os.path.exists(path):
            errors.append(f"{path}: file is missing from the working tree")
            continue

        try:
            base_text = git("show", f"{merge_base}:{path}")
        except subprocess.CalledProcessError:
            errors.append(f"{path}: no base version found")
            continue

        try:
            base_root = parse(base_text, path)
            with open(path, encoding="utf-8") as handle:
                head_root = parse(handle.read(), path)
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
            continue

        try:
            base_signature = file_signature(base_root, path)
            head_signature = file_signature(head_root, path)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if base_signature != head_signature:
            errors.append(
                f"{path}: contexts, messages, sources, comments or locations changed; "
                "only translation text may change"
            )
            continue

        base_messages = list(base_root.iter("message"))
        head_messages = list(head_root.iter("message"))
        for index, (old, new) in enumerate(zip(base_messages, head_messages), start=1):
            old_type, old_forms = translation_state(old)
            new_type, new_forms = translation_state(new)

            if new_type not in ALLOWED_TYPES:
                errors.append(f"{path}: message {index}: invalid translation type {new_type!r}")
                continue
            if new_type in ("vanished", "obsolete"):
                errors.append(
                    f"{path}: message {index}: translators cannot mark a message "
                    f"{new_type!r}"
                )
                continue
            if old_type in ("vanished", "obsolete") and new_type != old_type:
                errors.append(
                    f"{path}: message {index}: a {old_type!r} message cannot be revived"
                )
                continue

            numerus = old.get("numerus") == "yes"
            if numerus:
                if old_forms > 0 and new_forms != old_forms:
                    errors.append(
                        f"{path}: message {index}: plural form count changed "
                        f"({old_forms} -> {new_forms})"
                    )
                elif old_forms == 0 and new_forms > 6:
                    errors.append(
                        f"{path}: message {index}: too many plural forms ({new_forms})"
                    )
            elif new_forms:
                errors.append(
                    f"{path}: message {index}: <numerusform> added to a non-plural message"
                )

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  warning: {warning}")
        print()

    if errors:
        print("Pull request rejected:\n", file=sys.stderr)
        for error in errors:
            print(f"  error: {error}", file=sys.stderr)
        print(f"\n{len(errors)} error(s)", file=sys.stderr)
        return 1

    print(f"OK: {changed_files} translation file(s) changed; only translation text was modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
