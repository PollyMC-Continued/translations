#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET


HEADER_RE = re.compile(r'\A\s*(<\?xml[^>]*\?>\s*<!DOCTYPE[^>]*>)', re.DOTALL)
NUM_RE = re.compile(r'^\s*\[(\d+)\] ?')
PH_RE = re.compile(r'%L?\d+|%Ln|%n')          # %1  %L2  %n  %Ln
TAG_RE = re.compile(r'</?[A-Za-z][^<>]*>')    # simple HTML-ish tags

# Entities that AI models sometimes produce by HTML-escaping their answer.
ESC_RE = re.compile(r'&(lt|gt|quot|#39);')
ESC_MAP = {'lt': '<', 'gt': '>', 'quot': '"', '#39': "'"}

# Fallback only: used when an unfinished plural message has no <numerusform>
# children yet (lupdate normally creates the right number itself).
PLURAL_COUNT = {
    'ar': 6, 'cy': 6, 'ga': 5, 'sl': 4,
    'cs': 3, 'sk': 3, 'pl': 3, 'ru': 3, 'uk': 3, 'be': 3, 'sr': 3,
    'hr': 3, 'bs': 3, 'lt': 3, 'lv': 3, 'mk': 3, 'ro': 3,
    'fr': 2, 'pt': 2, 'es': 2, 'it': 2, 'de': 2, 'en': 2, 'nl': 2,
    'sv': 2, 'da': 2, 'nb': 2, 'no': 2, 'nn': 2, 'fi': 2, 'et': 2,
    'el': 2, 'he': 2, 'hu': 2, 'tr': 2, 'ca': 2, 'gl': 2, 'eu': 2,
    'fa': 2, 'hi': 2, 'bn': 2,
    'ja': 1, 'zh': 1, 'ko': 1, 'th': 1, 'vi': 1, 'id': 1, 'ms': 1,
}


# ---------- escaping ------------------------------------------------------

def escape(s: str) -> str:
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    s = s.replace('\\', '\\\\')
    return s.replace('\n', '\\n').replace('\t', '\\t')


def unescape(s: str) -> str:
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '\\' and i + 1 < n:
            nxt = s[i + 1]
            if nxt in 'nt\\':
                out.append({'n': '\n', 't': '\t', '\\': '\\'}[nxt])
                i += 2
                continue
        out.append(c)
        i += 1
    return ''.join(out)


def unescape_entities(s: str) -> str:
    """Undo accidental HTML-escaping (&lt; &gt; &quot; &#39;) done by the AI."""
    return ESC_RE.sub(lambda m: ESC_MAP[m.group(1)], s)


# ---------- .ts helpers ---------------------------------------------------

def parse_ts(path: Path) -> ET.ElementTree:
    # insert_comments keeps any <!-- comments --> instead of silently dropping them
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.parse(path, parser)


def is_unfinished(message: ET.Element) -> bool:
    tr = message.find('translation')
    if tr is None:
        return True
    t = tr.get('type')
    if t in ('vanished', 'obsolete'):
        return False
    if t == 'unfinished':
        return True
    forms = tr.findall('numerusform')
    if forms:
        return all(not (f.text or '').strip() for f in forms)
    return not (tr.text or '').strip()


def plurals_for(language: str) -> int:
    base = language.split('_')[0].lower()
    return PLURAL_COUNT.get(language, PLURAL_COUNT.get(base, 2))


def form_count(message: ET.Element, language: str) -> int:
    tr = message.find('translation')
    existing = len(tr.findall('numerusform')) if tr is not None else 0
    return existing or plurals_for(language)


def collect(root: ET.Element) -> list[tuple[str, ET.Element, str]]:
    """Return [(stable_key, <message>, context_name)] for translatable unfinished
    messages. The key is derived from context+source+comment+id (plus an
    occurrence counter), NOT from position, so merge survives .ts edits."""
    seen: Counter = Counter()
    found = []
    for ctx in root.findall('context'):
        cname = ctx.findtext('name') or ''
        for msg in ctx.findall('message'):
            base = json.dumps([cname, msg.findtext('source') or '',
                               msg.findtext('comment') or '',
                               msg.get('id') or ''], ensure_ascii=False)
            n = seen[base]
            seen[base] += 1
            key = f"{hashlib.sha1(base.encode('utf-8')).hexdigest()[:16]}:{n}"
            if is_unfinished(msg) and (msg.findtext('source') or '').strip():
                found.append((key, msg, cname))
    return found


def read_header(path: Path) -> str:
    m = HEADER_RE.match(path.read_text(encoding='utf-8-sig'))
    return m.group(1) if m else '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>'


def read_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    if raw.endswith('\n'):
        raw = raw[:-1]
    return raw.split('\n') if raw else []


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preview(s: str, n: int = 50) -> str:
    s = s.replace('\n', ' ')
    return s if len(s) <= n else s[:n - 1] + '…'


# ---------- validation ----------------------------------------------------

def validate(src: str, values: list[str], numerus: bool) -> tuple[list[str], list[str]]:
    """Return (problems, warnings). Problems block the write (unless --force)."""
    problems: list[str] = []
    warnings: list[str] = []
    src_ph, src_tags = Counter(PH_RE.findall(src)), Counter(TAG_RE.findall(src))
    for i, v in enumerate(values, 1):
        tag = f"form {i}: " if numerus else ""
        if not v.strip():
            problems.append(tag + "empty translation")
            continue
        ph = Counter(PH_RE.findall(v))
        if ph - src_ph:
            problems.append(f"{tag}unknown placeholder(s) {sorted((ph - src_ph))}")
        # plural forms may legitimately omit %n ("one file"), others may not
        if not numerus and src_ph - ph:
            problems.append(f"{tag}missing placeholder(s) {sorted((src_ph - ph))}")
        if Counter(TAG_RE.findall(v)) != src_tags:
            problems.append(f"{tag}HTML tags differ from source")
        if src[:1].isspace() != v[:1].isspace() or src[-1:].isspace() != v[-1:].isspace():
            warnings.append(f"{tag}leading/trailing whitespace differs")
        if src.count('\n') != v.count('\n'):
            warnings.append(f"{tag}newline count differs")
    return problems, warnings


# ---------- chunking ------------------------------------------------------

def chunk_ranges(units: list[dict], total: int, size: int) -> list[tuple[int, int]]:
    """1-based inclusive line ranges, only ever split between units."""
    if not size or total <= size:
        return [(1, total)]
    ranges, start = [], 1
    for u in units:
        end = u['start'] + u['count'] - 1
        if end - start + 1 >= size:
            ranges.append((start, end))
            start = end + 1
    if start <= total:
        ranges.append((start, total))
    return ranges


def build_prompt(language: str, numbered: bool) -> str:
    lang = language or '<target>'
    rules = [
        f"Translate every line below into the language with Qt code '{lang}'.",
        "Output exactly one line per input line, in the same order. Never merge, split, skip or add lines.",
        "Keep %1 %2 %n, &accelerator markers, HTML tags and the escapes \\n \\t exactly as they are.",
        "Output raw text. Do NOT HTML-escape anything: write < > \" ' & as the plain "
        "characters, never as &lt; &gt; &quot; &#39; or &amp;. Tags such as <b>, <a href=\"%1\"> "
        "and <html><head/><body><p> must stay character-for-character identical to the source. "
        "Translate only the human-readable text between the tags.",
        "Do not translate placeholders in angle brackets such as <SESSION ID>, <ACCESS TOKEN> or "
        "<PROFILE ID>, and do not translate HTML attributes, URLs or CSS (style=\"...\").",
        "Reply with plain text only: no code fences, no commentary.",
        f"Provide the result as a downloadable file named {lang}_translated.txt",
    ]
    if numbered:
        rules.insert(2, "Keep each line's [number] prefix unchanged.")
    return '\n'.join(f"- {r}" for r in rules)


def build_prompt_all(files: list[tuple[str, str]], numbered: bool) -> str:
    """Prompt for the 'extract all' zip: one text file per language inside."""
    listing = '\n'.join(
        f"    {name}  ->  Qt language code '{lang or '(see the file name)'}'"
        for name, lang in files)
    rules = [
        f"The attached .zip contains {len(files)} text file(s), each to be translated into a "
        "DIFFERENT language. Target language of each file:\n" + listing,
        "Open and translate EVERY file completely. Do not skip any file and do not stop early, "
        "however long they are.",
        "Inside each file, translate every line into that file's language. Output exactly one "
        "line per input line, in the same order. Never merge, split, skip or add lines.",
        "Keep %1 %2 %n, &accelerator markers, HTML tags and the escapes \\n \\t exactly as they are.",
        "Output raw text. Do NOT HTML-escape anything: write < > \" ' & as the plain "
        "characters, never as &lt; &gt; &quot; &#39; or &amp;. Tags such as <b>, <a href=\"%1\"> "
        "and <html><head/><body><p> must stay character-for-character identical to the source. "
        "Translate only the human-readable text between the tags.",
        "Do not translate placeholders in angle brackets such as <SESSION ID>, <ACCESS TOKEN> or "
        "<PROFILE ID>, and do not translate HTML attributes, URLs or CSS (style=\"...\").",
        "For every input file NAME.txt create a translated file NAME_translated.txt "
        "(same number of lines, same order, UTF-8, plain text, no code fences, no commentary).",
        "Put ALL the *_translated.txt files into ONE zip named translated_all.zip (files at the "
        "top level, no sub-folders) and give it to me as a downloadable file. If you cannot "
        "create a zip, give me each *_translated.txt as a separate downloadable file instead.",
    ]
    if numbered:
        rules.insert(3, "Keep each line's [number] prefix unchanged.")
    return '\n'.join(f"- {r}" for r in rules)


# ---------- extract -------------------------------------------------------

def cmd_extract(args: argparse.Namespace):
    ts_path = Path(args.ts_file)
    root = parse_ts(ts_path).getroot()
    language = args.language or root.get('language', '')
    numbered = not args.plain

    todo = collect(root)
    if not todo:
        print("No unfinished entries found — nothing to do.")
        return None

    lines: list[str] = []
    units: list[dict] = []
    unit_of: dict[tuple[str, int], int] = {}
    entries: list[dict] = []

    for key, msg, cname in todo:
        src = msg.findtext('source') or ''
        numerus = msg.get('numerus') == 'yes'
        n = form_count(msg, language) if numerus else 1
        ukey = (src, n)
        if args.dedupe and ukey in unit_of:
            u = unit_of[ukey]
        else:
            u = len(units)
            units.append({"start": len(lines) + 1, "count": n})
            lines.extend([escape(src)] * n)
            unit_of[ukey] = u
        entries.append({"key": key, "unit": u, "numerus": numerus})

    out_lines = [f"[{i}] {t}" for i, t in enumerate(lines, 1)] if numbered else lines
    ranges = chunk_ranges(units, len(lines), args.chunk_size)

    out_txt = Path(args.output)
    if len(ranges) == 1:
        paths = [out_txt]
    else:
        paths = [out_txt.with_name(f"{out_txt.stem}.part{k:02d}{out_txt.suffix}")
                 for k in range(1, len(ranges) + 1)]
    for p, (a, b) in zip(paths, ranges):
        p.write_text('\n'.join(out_lines[a - 1:b]) + '\n', encoding='utf-8')

    map_path = out_txt.with_suffix('.map.json')
    map_path.write_text(json.dumps({
        "language": language,
        "ts_file": ts_path.name,
        "ts_path": str(ts_path.resolve()),
        "ts_sha256": sha256_file(ts_path),
        "numbered": numbered,
        "txt_files": [p.name for p in paths],
        "line_count": len(lines),
        "units": units,
        "entries": entries,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    saved = len(entries) - len(units)
    print(f"Extracted {len(entries)} unfinished entries -> {len(lines)} lines"
          + (f" ({saved} duplicates folded)" if args.dedupe and saved else "") + ".")
    prompt = build_prompt(language, numbered)
    if getattr(args, 'quiet', False):
        return paths, prompt
    for p in paths:
        print(f"  -> {p}")
    print(f"  -> {map_path}   (keep for merge)")
    if not numbered and len(paths) > 1:
        print("  note: --plain with chunks: keep the translated files in the same order.")
    print("\nSuggested prompt:\n" + prompt)
    return paths, prompt


# ---------- merge ---------------------------------------------------------

def parse_translated(paths: list[Path], numbered: bool) -> tuple[dict[int, str], int]:
    raw: list[str] = []
    for p in paths:
        raw.extend(read_lines(p))
    if not numbered:
        return {i: line for i, line in enumerate(raw, 1)}, len(raw)
    out: dict[int, str] = {}
    junk = 0
    for line in raw:
        if not line.strip() or line.lstrip().startswith('```'):
            continue
        m = NUM_RE.match(line)
        if not m:
            junk += 1
            continue
        n = int(m.group(1))
        if n in out:
            print(f"warning: line number [{n}] appears more than once; keeping the last",
                  file=sys.stderr)
        out[n] = line[m.end():]
    return out, junk


def cmd_merge(args: argparse.Namespace) -> None:
    ts_path = Path(args.ts_file)
    mapping = json.loads(Path(args.map_file).read_text(encoding='utf-8'))
    numbered = mapping.get('numbered', False)

    if not args.output and not args.in_place:
        sys.exit("error: give -o OUTPUT or --in-place")
    if mapping.get('ts_sha256') and mapping['ts_sha256'] != sha256_file(ts_path):
        print(f"note: {ts_path.name} changed since extract; matching strings by "
              f"content, not position.", file=sys.stderr)

    translated, extra = parse_translated([Path(p) for p in args.translated_txt], numbered)
    expected = mapping['line_count']
    if numbered:
        if extra:
            print(f"warning: ignored {extra} line(s) without a [number] prefix.", file=sys.stderr)
    elif len(translated) != expected and not args.force:
        sys.exit(f"error: expected {expected} lines, got {len(translated)}. Line positions "
                 f"can't be trusted (use --force to merge anyway, or re-run without --plain).")

    tree = parse_ts(ts_path)
    root = tree.getroot()
    by_key = {key: (msg, cname) for key, msg, cname in collect(root)}

    updated = rejected = missing = gone = warned = fixed_esc = 0
    for e in mapping['entries']:
        hit = by_key.get(e['key'])
        if hit is None:
            gone += 1                      # already finished / removed since extract
            continue
        msg, cname = hit
        src = msg.findtext('source') or ''
        unit = mapping['units'][e['unit']]
        nums = range(unit['start'], unit['start'] + unit['count'])
        if any(n not in translated for n in nums):
            missing += 1
            print(f"  missing  [{cname}] {preview(src)!r}", file=sys.stderr)
            continue
        values = [unescape(translated[n]) for n in nums]

        # Safety net: if the AI HTML-escaped its answer (&lt;b&gt; instead of <b>)
        # undo it -- but only when the source itself has no such entities, so
        # strings that legitimately contain &lt; etc. are left alone.
        if not ESC_RE.search(src):
            fixed = [unescape_entities(v) for v in values]
            if fixed != values:
                fixed_esc += 1
                values = fixed

        problems, warnings = validate(src, values, e['numerus'])
        for w in warnings:
            warned += 1
            print(f"  warn     [{cname}] {preview(src)!r}: {w}", file=sys.stderr)
        if problems and not args.force:
            rejected += 1
            print(f"  rejected [{cname}] {preview(src)!r}: {'; '.join(problems)}", file=sys.stderr)
            continue

        tr = msg.find('translation')
        if tr is None:
            tr = ET.SubElement(msg, 'translation')
        for child in list(tr):
            tr.remove(child)
        tr.attrib.pop('type', None)
        if e['numerus']:
            tr.text = '\n            '
            for i, v in enumerate(values):
                nf = ET.SubElement(tr, 'numerusform')
                nf.text = v
                nf.tail = '\n        ' if i == len(values) - 1 else '\n            '
        else:
            tr.text = values[0]
        updated += 1

    body = ET.tostring(root, encoding='utf-8', xml_declaration=False)
    data = read_header(ts_path).encode('utf-8') + b'\n' + body + b'\n'
    if args.in_place:
        shutil.copy2(ts_path, ts_path.with_name(ts_path.name + '.bak'))
        out_path = ts_path
    else:
        out_path = Path(args.output)
    out_path.write_bytes(data)

    print(f"Updated {updated}; rejected {rejected} (left unfinished); "
          f"missing from AI output {missing}; already done/removed {gone}; "
          f"warnings {warned}.")
    if fixed_esc:
        print(f"Auto-fixed HTML-escaped text (&lt; &gt; &quot;) in {fixed_esc} entr"
              f"{'y' if fixed_esc == 1 else 'ies'}.")
    print(f"  -> {out_path}" + ("  (backup: .bak)" if args.in_place else ""))
    if rejected or missing:
        print("Tip: run `extract` on the merged file to retry only the leftovers.")


# ---------- interactive menu (plain text, no dependencies) ----------------

class Back(Exception):
    """The user cancelled a prompt (Ctrl-C / Ctrl-D)."""


SKIP_DIRS = {'node_modules', '__pycache__', 'venv', 'build', 'dist'}

ALL = object()   # menu sentinel: "all files at once"


def norm_txt_name(name: str) -> str:
    """'ur_translated.txt' / 'ur.translated.txt' / 'translated_ur.txt' -> 'ur.txt'."""
    p = Path(name)
    stem = re.sub(r'[._ -]*translated', '', p.stem, flags=re.I).strip('._ -')
    return (stem + p.suffix).lower()


def scan(root: Path, suffix: str, depth: int = 3) -> list[Path]:
    """Find files ending in `suffix` below root (a few levels deep, no junk dirs)."""
    found: list[Path] = []
    base = len(Path(root).parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith('.') and d not in SKIP_DIRS)
        if len(here.parts) - base >= depth:
            dirnames[:] = []
        found.extend(here / f for f in sorted(filenames) if f.lower().endswith(suffix))
    return found


def looks_like_ts(path: Path) -> bool:
    """Cheap sniff so TypeScript *.ts files are ignored."""
    try:
        with open(path, 'rb') as fh:
            return b'<TS' in fh.read(1024)
    except OSError:
        return False


def ts_stats(path: Path):
    """(language, translatable messages, unfinished) or None if not a Qt .ts."""
    try:
        root = parse_ts(path).getroot()
    except (ET.ParseError, OSError):
        return None
    if root.tag != 'TS':
        return None
    total = 0
    for ctx in root.findall('context'):
        for m in ctx.findall('message'):
            tr = m.find('translation')
            if tr is None or tr.get('type') not in ('vanished', 'obsolete'):
                total += 1
    return root.get('language', ''), total, len(collect(root))


def load_map(path: Path):
    try:
        m = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) and 'entries' in m and 'units' in m else None


def copy_to_clipboard(text: str) -> bool:
    for cmd in (['pbcopy'], ['clip'], ['wl-copy'],
                ['xclip', '-selection', 'clipboard'], ['xsel', '--clipboard', '--input']):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode('utf-8'), check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                pass
    return False


def can_copy() -> bool:
    return any(shutil.which(c) for c in ('pbcopy', 'clip', 'wl-copy', 'xclip', 'xsel'))


def when(path: Path) -> str:
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(path.stat().st_mtime))


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        s = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Back
    if len(s) >= 2 and s[0] == s[-1] and s[0] in '"\'':   # drag-and-drop quotes
        s = s[1:-1]
    return s or (default or '')


def confirm(prompt: str, default: bool = False) -> bool:
    while True:
        s = ask(f"{prompt} [{'Y/n' if default else 'y/N'}]").lower()
        if not s:
            return default
        if s in ('y', 'yes'):
            return True
        if s in ('n', 'no'):
            return False


def ask_int(prompt: str, default: int = 0) -> int:
    while True:
        s = ask(prompt, str(default))
        if s.isdigit():
            return int(s)
        print("  please enter a number")


def choose(title: str, labels: list[str], default: int = 1, allow_path: bool = False):
    """Numbered menu -> 0-based index, a typed path (str), or None for 'back'."""
    print(f"\n{title}")
    for i, label in enumerate(labels, 1):
        print(f"  {i}) {label}")
    print("  (b = back" + (", or type a path)" if allow_path else ")"))
    while True:
        s = ask("Select", str(default))
        if s.lower() in ('b', 'back'):
            return None
        if s.isdigit() and 1 <= int(s) <= len(labels):
            return int(s) - 1
        if allow_path and s and Path(s).expanduser().is_file():
            return s
        print("  not a valid choice")


class TUI:
    def __init__(self, folder: Path):
        self.cache: dict = {}
        self.set_folder(folder)

    # -- helpers --
    def set_folder(self, folder: Path) -> None:
        os.chdir(folder)
        self.folder = Path.cwd()

    def rel(self, p) -> str:
        try:
            return os.path.relpath(p, self.folder)
        except ValueError:
            return str(p)

    def stats(self, p: Path):
        try:
            key = (str(p), p.stat().st_mtime_ns)
        except OSError:
            return None
        if key not in self.cache:
            self.cache[key] = ts_stats(p)
        return self.cache[key]

    def find_ts(self) -> list[tuple[Path, tuple]]:
        out = []
        for p in scan(self.folder, '.ts'):
            if looks_like_ts(p):
                st = self.stats(p)
                if st:
                    out.append((p, st))
        return out

    def label(self, p: Path, st: tuple) -> str:
        return f"{self.rel(p)}  [{st[0] or '?'}]  {st[2]} unfinished / {st[1]}"

    def guarded(self, fn) -> None:
        try:
            fn()
        except Back:
            print("cancelled.")
        except SystemExit as e:
            if isinstance(e.code, str):
                print(f"\n{e.code}")
        except Exception as e:                      # keep the menu alive
            print(f"\nerror: {type(e).__name__}: {e}")

    # -- main loop --
    def run(self) -> None:
        while True:
            found = self.find_ts()
            todo = sum(1 for _, st in found if st[2])
            print(f"\n== extract ==  {self.folder}")
            print(f"   {len(found)} .ts file(s), {todo} with unfinished strings")
            print("  1) Extract   unfinished strings -> .txt for the AI")
            print("  2) Merge     translated .txt -> .ts")
            print("  3) Status    unfinished count per .ts file")
            print("  4) Folder    change folder")
            print("  q) Quit      (Ctrl-C cancels any prompt)")
            try:
                s = ask("Select").lower()
            except Back:
                return
            if s in ('1', 'e', 'extract'):
                self.guarded(self.do_extract)
            elif s in ('2', 'm', 'merge'):
                self.guarded(self.do_merge)
            elif s in ('3', 's', 'status'):
                self.guarded(self.do_status)
            elif s in ('4', 'f', 'folder'):
                self.guarded(self.do_folder)
            elif s in ('q', 'quit', 'exit', '0'):
                return

    # -- screens --
    def do_status(self) -> None:
        found = self.find_ts()
        if not found:
            print("\nNo Qt .ts files found here (searched 3 levels deep).")
        for p, st in found:
            print(f"  {self.label(p, st)}")

    def do_folder(self) -> None:
        p = Path(ask("Folder", str(self.folder))).expanduser()
        if p.is_dir():
            self.set_folder(p)
        else:
            print("  not a folder")

    def pick_ts(self, message: str, allow_all: bool = False):
        found = self.find_ts()
        if not found:
            print("\nNo Qt .ts files found here (searched 3 levels deep).")
            s = ask("Path to a .ts file (empty = back)")
            return Path(s).expanduser() if s and Path(s).expanduser().is_file() else None
        default = next((i + 1 for i, (_, st) in enumerate(found) if st[2]), 1)
        labels = [self.label(p, st) for p, st in found]
        todo = sum(1 for _, st in found if st[2])
        if allow_all and todo:
            labels.append(f"* ALL {todo} file(s) with unfinished strings -> one .zip + its own prompt")
        r = choose(message, labels, default, allow_path=True)
        if r is None:
            return None
        if isinstance(r, int):
            return ALL if r == len(found) else found[r][0]
        return Path(r).expanduser()

    def do_extract(self) -> None:
        ts = self.pick_ts("Which .ts file?", allow_all=True)
        if ts is None:
            return
        if ts is ALL:
            self.do_extract_all()
            return
        out = Path(ask("Output file", self.rel(ts.with_name(f"{ts.stem}.to_translate.txt")))).expanduser()
        chunk, dedupe, plain = 0, False, False
        if confirm("Advanced options (split into parts, fold duplicates, plain lines)?", False):
            chunk = ask_int("Lines per file (0 = one file)", 0)
            dedupe = confirm("Send identical strings only once?", True)
            plain = confirm("Plain lines, without [number] prefixes?", False)
        for p in (out, out.with_suffix('.map.json')):
            if p.exists() and not confirm(f"{self.rel(p)} exists. Overwrite?", True):
                return
        ns = argparse.Namespace(ts_file=str(ts), output=str(out), language=None,
                                chunk_size=chunk, dedupe=dedupe, plain=plain)
        print()
        res = cmd_extract(ns)
        if res and can_copy() and confirm("\nCopy the AI prompt to the clipboard?", True):
            print("  copied." if copy_to_clipboard(res[1]) else "  clipboard not available.")
        if res:
            print("\nNext: give the .txt file(s) to your AI, save its answer, then pick Merge.")

    def do_extract_all(self) -> None:
        todo = [(p, st) for p, st in self.find_ts() if st[2]]
        if not todo:
            print("\nNothing to extract.")
            return
        chunk, dedupe, plain = 0, False, False
        if confirm("Advanced options (split into parts, fold duplicates, plain lines)?", False):
            chunk = ask_int("Lines per file (0 = one file per language)", 0)
            dedupe = confirm("Send identical strings only once?", True)
            plain = confirm("Plain lines, without [number] prefixes?", False)
        out_dir = self.folder / 'extract_all'
        zip_path = self.folder / 'extract_all.zip'
        if zip_path.exists() and not confirm(f"{self.rel(zip_path)} exists. Overwrite?", True):
            return
        out_dir.mkdir(exist_ok=True)

        files: list[tuple[Path, str]] = []
        used: set[str] = set()
        for p, st in todo:
            name, k = p.stem, 2
            while name.lower() in used:            # two .ts files with the same name
                name, k = f"{p.stem}_{k}", k + 1
            used.add(name.lower())
            print(f"\n{self.rel(p)}  [{st[0] or '?'}]")
            ns = argparse.Namespace(ts_file=str(p), output=str(out_dir / f"{name}.txt"),
                                    language=None, chunk_size=chunk, dedupe=dedupe,
                                    plain=plain, quiet=True)
            res = cmd_extract(ns)
            if res:
                files.extend((q, st[0]) for q in res[0])
        if not files:
            return

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
            for q, _ in files:
                z.write(q, arcname=q.name)
        prompt = build_prompt_all([(q.name, lang) for q, lang in files], not plain)

        print(f"\nZipped {len(files)} file(s) -> {self.rel(zip_path)}")
        print(f"Keep the *.map.json files in {self.rel(out_dir)}/ for merging.")
        print("\nPrompt for the zip:\n" + prompt)
        if can_copy() and confirm("\nCopy the AI prompt to the clipboard?", True):
            print("  copied." if copy_to_clipboard(prompt) else "  clipboard not available.")
        print("\nNext: give the .zip + prompt to your AI, save the zip it returns, "
              "then pick Merge -> ALL.")

    def pick_translated(self, mp: Path, m: dict) -> list[Path]:
        extracted = set(m.get('txt_files', []))
        cands = sorted((p for p in mp.parent.glob('*.txt') if p.name not in extracted),
                       key=lambda p: p.name)
        note = f" (extract was split into {len(extracted)} parts)" if len(extracted) > 1 else ""
        print(f"\nTranslated file(s) from the AI{note}:")
        for i, p in enumerate(cands, 1):
            print(f"  {i}) {self.rel(p)}   {when(p)}")
        if cands:
            hint, default = "numbers (1,2), 'a' = all, or path(s)", ('a' if len(extracted) > 1 else '1')
        else:
            hint, default = "path(s), comma separated", None
        while True:
            s = ask(hint, default)
            picked, ok = [], bool(s)
            for tok in (t.strip().strip('"\'') for t in s.split(',')):
                if not tok:
                    continue
                if tok.lower() in ('a', 'all') and cands:
                    picked.extend(cands)
                elif tok.isdigit() and 1 <= int(tok) <= len(cands):
                    picked.append(cands[int(tok) - 1])
                elif Path(tok).expanduser().is_file():
                    picked.append(Path(tok).expanduser())
                else:
                    print(f"  not found: {tok}")
                    ok = False
            if ok and picked:
                return picked

    def do_merge(self) -> None:
        maps = []
        for p in scan(self.folder, '.map.json'):
            m = load_map(p)
            if m:
                maps.append((p, m))
        maps.sort(key=lambda t: t[0].stat().st_mtime, reverse=True)
        if not maps:
            print("\nNo .map.json files found. Run Extract first, or type a path.")
            s = ask("Path to a .map.json file (empty = back)")
            m = load_map(Path(s).expanduser()) if s else None
            if not m:
                return
            maps = [(Path(s).expanduser(), m)]
            r = 0
        else:
            labels = [f"{self.rel(p)}  ->  {Path(m.get('ts_file', '?')).name}, "
                      f"{m.get('line_count', '?')} lines, {when(p)}" for p, m in maps]
            if len(maps) > 1:
                labels.append("* ALL of them at once (translated .zip from the AI)")
            r = choose("Which extract does this belong to?", labels, 1, allow_path=True)
            if r is None:
                return
            if isinstance(r, int) and len(maps) > 1 and r == len(maps):
                self.do_merge_all(maps)
                return
            if not isinstance(r, int):
                m = load_map(Path(r).expanduser())
                if not m:
                    print("  not a valid map file")
                    return
                maps, r = [(Path(r).expanduser(), m)], 0
        mp, m = maps[r]

        # the .ts this map was made from
        name = Path(m.get('ts_file', '')).name
        cands = [p for p, _ in self.find_ts() if p.name == name]
        same = [p for p in cands if p.parent == mp.parent.resolve()] or cands
        if len(same) == 1:
            ts = same[0]
        elif same:
            r = choose("Which .ts file should receive the translations?",
                       [self.rel(p) for p in same], 1)
            if r is None:
                return
            ts = same[r]
        else:
            s = ask(f"Path to {name or 'the .ts file'}")
            ts = Path(s).expanduser()
            if not ts.is_file():
                print("  not found")
                return
        print(f"  .ts file: {self.rel(ts)}")

        txts = self.pick_translated(mp, m)

        new = ts.with_name(f"{ts.stem}.fixed.ts")
        r = choose("Write the result to:",
                   [f"{self.rel(new)}  (new file)",
                    f"{self.rel(ts)}  (overwrite, backup kept as {ts.name}.bak)",
                    "another name"], 1)
        if r is None:
            return
        in_place, out = r == 1, new
        if r == 2:
            s = ask("Output file")
            if not s:
                return
            out = Path(s).expanduser()
        if not in_place and out.exists() and not confirm(f"{self.rel(out)} exists. Overwrite?", False):
            return
        force = False
        if confirm("Advanced options?", False):
            force = confirm("Skip the placeholder/tag checks (accept everything)?", False)

        ns = argparse.Namespace(ts_file=str(ts), translated_txt=[str(p) for p in txts],
                                map_file=str(mp), output=None if in_place else str(out),
                                in_place=in_place, force=force)
        print()
        cmd_merge(ns)

    def do_merge_all(self, maps: list[tuple[Path, dict]]) -> None:
        zips = sorted(scan(self.folder, '.zip'), key=lambda z: z.stat().st_mtime, reverse=True)
        if zips:
            r = choose("Translated .zip from the AI:",
                       [f"{self.rel(z)}   {when(z)}" for z in zips], 1, allow_path=True)
            if r is None:
                return
            zpath = zips[r] if isinstance(r, int) else Path(r).expanduser()
        else:
            s = ask("Path to the translated .zip (empty = back)")
            zpath = Path(s).expanduser() if s else None
            if zpath is None or not zpath.is_file():
                return

        # unpack only plain .txt files, flattened (ignores folders / junk / unsafe paths)
        dest = self.folder / 'translated_all'
        dest.mkdir(exist_ok=True)
        index: dict[str, Path] = {}
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                base = Path(info.filename).name
                if info.is_dir() or not base.lower().endswith('.txt') or base.startswith('.'):
                    continue
                target = dest / base
                target.write_bytes(z.read(info))
                index[norm_txt_name(base)] = target
        if not index:
            print("\n  no .txt files found in that zip.")
            return
        print(f"\nUnpacked {len(index)} translated file(s) -> {self.rel(dest)}/")

        # pair every map with its translated file(s) and its .ts
        jobs = []
        for mp, m in maps:
            wanted = [norm_txt_name(n) for n in m.get('txt_files', [])]
            if not wanted or any(w not in index for w in wanted):
                print(f"  skip  {self.rel(mp)}: no translation for it in the zip")
                continue
            name = Path(m.get('ts_file', '')).name
            ts = Path(m['ts_path']) if m.get('ts_path') else None
            if ts is None or not ts.is_file():
                cands = [p for p, _ in self.find_ts() if p.name == name]
                same = [p for p in cands if p.parent == mp.parent.resolve()] or cands
                ts = same[0] if same else None
            if ts is None:
                print(f"  skip  {self.rel(mp)}: can't find {name or 'its .ts file'}")
                continue
            jobs.append((ts, mp, [index[w] for w in wanted]))
        if not jobs:
            print("\nNothing to merge.")
            return

        r = choose("Write the results to:",
                   ["<name>.fixed.ts next to each .ts  (new files)",
                    "overwrite each .ts  (backup kept as <name>.ts.bak)"], 1)
        if r is None:
            return
        in_place = r == 1
        if not in_place and any(ts.with_name(f"{ts.stem}.fixed.ts").exists() for ts, _, _ in jobs):
            if not confirm("Some .fixed.ts files already exist. Overwrite them?", False):
                return

        for ts, mp, txts in jobs:
            print(f"\n== {self.rel(ts)} ==")
            ns = argparse.Namespace(
                ts_file=str(ts), translated_txt=[str(t) for t in txts], map_file=str(mp),
                output=None if in_place else str(ts.with_name(f"{ts.stem}.fixed.ts")),
                in_place=in_place, force=False)
            try:
                cmd_merge(ns)
            except SystemExit as e:
                print(f"  error: {e.code}")
            except Exception as e:
                print(f"  error: {type(e).__name__}: {e}")


def run_tui(folder) -> None:
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        sys.exit(f"error: {folder} is not a folder")
    TUI(folder).run()


# ---------- CLI -----------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog='extract',
        description='Extract unfinished strings from a Qt .ts file to a '
                    'one-line-per-string .txt, then merge translations back.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    ex = sub.add_parser('extract', help='Write the .txt for the AI')
    ex.add_argument('ts_file')
    ex.add_argument('-o', '--output', required=True,
                    help='.txt to write (a .map.json is written next to it)')
    ex.add_argument('--language', help="override the .ts language code (e.g. pt_BR)")
    ex.add_argument('--chunk-size', type=int, default=0, metavar='N',
                    help='split into files of ~N lines (never splits a plural group)')
    ex.add_argument('--dedupe', action='store_true',
                    help='send identical source strings only once')
    ex.add_argument('--plain', action='store_true',
                    help='no [number] prefixes (merge then relies on line position)')
    ex.set_defaults(func=cmd_extract)

    mg = sub.add_parser('merge', help='Merge the AI .txt file(s) back into the .ts')
    mg.add_argument('ts_file')
    mg.add_argument('translated_txt', nargs='+', help='one or more translated .txt files')
    mg.add_argument('--map', dest='map_file', required=True)
    mg.add_argument('-o', '--output')
    mg.add_argument('--in-place', action='store_true',
                    help='overwrite the .ts (a .bak copy is kept)')
    mg.add_argument('--force', action='store_true',
                    help='accept translations even if placeholder/tag checks fail')
    mg.set_defaults(func=cmd_merge)

    tu = sub.add_parser('tui', help='Interactive menu (default when run with no arguments)')
    tu.add_argument('folder', nargs='?', default='.')
    tu.set_defaults(func=lambda a: run_tui(a.folder))

    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        if sys.stdin.isatty():
            run_tui(Path.cwd())
        else:
            ap.print_help()
        return

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()