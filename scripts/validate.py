#!/usr/bin/env python3
"""Validate the Qt Linguist files in this repository.

The checks are intentionally conservative: they catch broken XML, invalid
message structures and translations that drop placeholders. They do not judge
translation quality.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter
import xml.etree.ElementTree as ET

FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_@-]*\.ts$")
PLACEHOLDER_RE = re.compile(r"%(?:\d+|n|L\d+)")
TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
ACCEL_RE = re.compile(r"(?<!&)&(?=[A-Za-z0-9])")
HTML_TAGS = {
    "a", "b", "big", "blockquote", "br", "code", "div", "em", "font", "hr",
    "i", "img", "kbd", "li", "ol", "p", "pre", "s", "small", "span", "strong",
    "sub", "sup", "tt", "u", "ul",
}
ALLOWED_TYPES = {None, "unfinished", "vanished", "obsolete"}


class Report:
    def __init__(self, strict: bool = False) -> None:
        self.strict = strict
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def warn(self, path: str, message: str) -> None:
        if self.strict:
            self.errors.append(f"{path}: {message}")
        else:
            self.warnings.append(f"{path}: {message}")


def base_language(code: str) -> str:
    return re.split(r"[_@-]", code.lower(), maxsplit=1)[0]


def parse(path: str) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"not well-formed XML: {exc}") from exc


def translation_type(message: ET.Element) -> str | None:
    translation = message.find("translation")
    return translation.get("type") if translation is not None else None


def text_of(element: ET.Element | None) -> str:
    return (element.text or "") if element is not None else ""


def check_text(report: Report, path: str, source: str, text: str, label: str) -> None:
    if not text.strip():
        return

    source_placeholders = Counter(PLACEHOLDER_RE.findall(source))
    text_placeholders = Counter(PLACEHOLDER_RE.findall(text))
    if source_placeholders != text_placeholders:
        report.warn(
            path,
            f"{label}: placeholders differ "
            f"(source {sorted(source_placeholders.elements())}, "
            f"translation {sorted(text_placeholders.elements())})",
        )

    source_accels = set(ACCEL_RE.findall(source))
    text_accels = set(ACCEL_RE.findall(text))
    if report.strict and source_accels != text_accels:
        report.warn(
            path,
            f"{label}: accelerators differ "
            f"(source {sorted(source_accels)}, translation {sorted(text_accels)})",
        )

    source_tags = Counter(
        tag.lower() for tag in TAG_RE.findall(source) if tag.lower() in HTML_TAGS
    )
    text_tags = Counter(
        tag.lower() for tag in TAG_RE.findall(text) if tag.lower() in HTML_TAGS
    )
    if source_tags != text_tags:
        report.warn(
            path,
            f"{label}: HTML tags differ "
            f"(source {sorted(source_tags.elements())}, "
            f"translation {sorted(text_tags.elements())})",
        )


def validate_file(report: Report, path: str) -> dict[str, int]:
    counts = {
        "messages": 0,
        "unfinished": 0,
        "fuzzy": 0,
        "vanished": 0,
        "obsolete": 0,
    }

    name = os.path.basename(path)
    if not FILENAME_RE.match(name):
        report.error(path, "invalid file name; expected a language code such as de.ts")
        return counts

    try:
        root = parse(path)
    except ValueError as exc:
        report.error(path, str(exc))
        return counts

    if root.tag != "TS":
        report.error(path, f"root element is <{root.tag}>, expected <TS>")
        return counts

    version = root.get("version") or ""
    if not version.startswith("2."):
        report.warn(path, f"unexpected TS version {version!r}")

    language = root.get("language")
    file_language = name[:-3].replace("-", "_").lower()
    if language:
        normalised = language.replace("-", "_").lower()
        if "_" in file_language or "@" in file_language:
            if normalised != file_language:
                report.warn(
                    path,
                    f"language attribute {language!r} does not match file name",
                )
        elif base_language(normalised) != file_language:
            report.warn(
                path,
                f"language attribute {language!r} does not match file name",
            )

    contexts: set[str] = set()
    seen_messages: set[tuple[str, str, str, str]] = set()

    for context in root.findall("context"):
        context_name = (context.findtext("name") or "").strip()
        if not context_name:
            report.error(path, "context without a name")
            continue
        if context_name in contexts:
            report.warn(path, f"duplicate context {context_name!r}")
        contexts.add(context_name)

        for index, message in enumerate(context.findall("message"), start=1):
            counts["messages"] += 1
            label = f"{context_name} message {index}"

            source = text_of(message.find("source"))
            if not source.strip():
                report.error(path, f"{label}: empty <source>")

            kind = translation_type(message)
            if kind not in ALLOWED_TYPES:
                report.error(path, f"{label}: invalid translation type {kind!r}")
                continue

            if kind == "unfinished":
                counts["unfinished"] += 1
                if text_of(message.find("translation")).strip():
                    counts["fuzzy"] += 1
            elif kind == "vanished":
                counts["vanished"] += 1
            elif kind == "obsolete":
                counts["obsolete"] += 1

            comment = text_of(message.find("comment")).strip()
            key = (context_name, source, comment, message.get("numerus") or "")
            if key in seen_messages:
                report.warn(path, f"{label}: duplicate message")
            seen_messages.add(key)

            translation = message.find("translation")
            if translation is None:
                continue

            if message.get("numerus") == "yes":
                forms = translation.findall("numerusform")
                if not forms:
                    if kind not in ("unfinished", "vanished", "obsolete"):
                        report.warn(path, f"{label}: plural message has no <numerusform>")
                    continue
                for form_index, form in enumerate(forms, start=1):
                    check_text(
                        report,
                        path,
                        source,
                        text_of(form),
                        f"{label} plural form {form_index}",
                    )
            else:
                if translation.find("numerusform") is not None:
                    report.error(path, f"{label}: <numerusform> in a non-plural message")
                if kind not in ("vanished", "obsolete"):
                    check_text(report, path, source, text_of(translation), label)

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings (placeholder, accelerator and tag mismatches) as errors",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="maximum number of warnings to print (default: 50)",
    )
    args = parser.parse_args()

    files = sorted(glob.glob("*.ts"))
    if not files:
        print("error: no .ts files found; run this from the repository root", file=sys.stderr)
        return 1

    report = Report(strict=args.strict)
    totals = Counter()
    for path in files:
        totals.update(validate_file(report, path))

    if report.errors:
        print("Validation failed:\n", file=sys.stderr)
        for error in report.errors:
            print(f"  error: {error}", file=sys.stderr)
        print(f"\n{len(report.errors)} error(s)", file=sys.stderr)
        return 1

    if report.warnings:
        print("Warnings:\n")
        for warning in report.warnings[: args.limit]:
            print(f"  warning: {warning}")
        remaining = len(report.warnings) - args.limit
        if remaining > 0:
            print(f"  ... and {remaining} more")
        print()

    print(
        f"OK: {len(files)} files, {totals['messages']} messages, "
        f"{totals['unfinished']} unfinished, {totals['fuzzy']} fuzzy, "
        f"{totals['vanished']} vanished, {totals['obsolete']} obsolete, "
        f"{len(report.warnings)} warning(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
