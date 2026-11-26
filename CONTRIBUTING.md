# Contributing to PollyMC-Continued translations

Thank you for helping translate the launcher. This document describes what a
valid translation change looks like, how it is validated, and how the review
process keeps the files safe to merge.

## Two ways to contribute

### Web editor (recommended)

Open <https://pollymc.vercel.app/translation/>, pick a language and start
translating. The editor lists only the strings that still need work, shows the
source text and its context, and checks placeholders while you type. When you
submit, you provide a name (required, used for credit) and optionally a contact
address (kept private, used only if a reviewer needs to ask about your change).
A GitHub account is not required.

Submissions are queued for maintainer review; approved ones are applied to the
latest file and opened as a pull request from this repository. You never write
to the repository directly, so there is no way to break anything.

### Manual pull request

1. Fork this repository and create a branch for your language.
2. Edit the one `*.ts` file for that language. Qt Linguist (`linguist`) works,
   any XML editor works, and so does the plain file in a text editor.
3. Run the checks locally (see below).
4. Push the branch, open a pull request against `main`, and fill in the
   template.

## Rules for a valid change

A translation pull request may only change the text of `<translation>` and
`<numerusform>` elements in the `*.ts` files. Everything else is generated
from the launcher source code by `lupdate`, and the integrity checks reject
it:

- Do not edit source strings (`<source>`), context names, comments,
  `<location>` lines, or any attribute.
- Do not add, remove, reorder, rename or delete messages, contexts, whole
  files, or any other file in the repository. New languages are added by
  maintainers through an issue (see the README).
- Preserve every placeholder: `%1` through `%9`, `%n`, and their `%L` variants
  (for example `%L1`). A translation may reorder placeholders when the grammar
  of the language requires it, but may not drop or add one.
- Preserve markup: HTML tags such as `<br/>`, `<p>`, `<a href="...">` and
  keyboard accelerators like `&File` must survive into the translation.
- Plural (`numerus="yes"`) messages must keep exactly the same number of
  `<numerusform>` forms as the original, each translated.
- If you start from an empty `type="unfinished"` translation, your text
  replaces it and the `type` attribute is removed (that is what finishing a
  string means). Do not finish a string by leaving it empty.
- Do not commit compiled `.qm` files; they are built by the launcher's
  release workflow.

## Validation

Run exactly what CI runs, from the repository root:

```shell
# every file: XML structure, message shape, plural shape, placeholders
python3 scripts/validate.py

# pull requests: the diff must be a pure translation-text change set
python3 scripts/check_diff.py origin/main
```

`validate.py` exits non-zero on any structural problem or placeholder
mismatch. `check_diff.py` compares the pull request semantically, not
textually, so formatting differences do not matter but structural ones always
fail. CI additionally runs `xmllint` and compiles every file with `lrelease`
so a broken file can never reach the launcher.

## Review

Every pull request is reviewed by a maintainer (see `.github/CODEOWNERS`)
before it is merged. The reviewer checks:

- the diff only touches translation text (the CI check already enforces this),
- the translation reads naturally and matches the meaning of the source,
- placeholders, markup and plural forms are intact (also enforced by CI),
- the language is consistent with the rest of the file, in spelling and
  terminology.

Multiple contributors may work on the same language at the same time. This is
expected and safe: changes are merged per message, not per line, so
translations of different strings never conflict. If two submissions translate
the same string differently, the maintainer picks one and the other contributor
is credited for the overlap in the merge commit when appropriate. The website
editor handles this automatically by applying each approved suggestion to the
latest file state.

## Credits

Contributors are credited in the pull request and, where they asked for it,
in the commit message of the merged change. Names given in the web editor are
used exactly as provided.

## Licensing

Translations are part of the launcher's user interface and are distributed
under GPL-3.0. By contributing you agree that your contribution is licensed
under GPL-3.0 as well.
