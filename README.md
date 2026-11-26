# PollyMC-Continued translations

This repository is the source of truth for the Qt Linguist (`.ts`) translation
files used by [PollyMC-Continued](https://github.com/PollyMC-Continued/launcher).

The launcher compiles these files into `.qm` files, which are published on the
website for the launcher's language downloads. Translations are reviewed here;
the launcher repository only consumes them.

## Contributing

The preferred way to contribute is the web editor:

<https://pollymc.vercel.app/translation/>

It shows only the strings that still need work, checks placeholders, and submits
changes for review. A GitHub account is not required to suggest a translation.

Manual pull requests are also welcome:

1. Fork this repository.
2. Edit one language file at a time.
3. Run `python3 scripts/validate.py`.
4. Open a pull request and fill in the template.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the rules and the review process.

## What belongs here

- `*.ts` Qt Linguist source files, one per language, at the repository root.
- Translation text only. Source strings are generated from the launcher code
  with `lupdate` and must not be edited by hand.
- Do not commit compiled `.qm` files. They are built by the launcher's release
  workflow.

## Validation

The repository has two checks:

```shell
# Structural and placeholder validation for every file
python3 scripts/validate.py

# On a pull request: reject message/context additions, removals or source edits
python3 scripts/check_diff.py origin/main
```

CI also runs `xmllint` and `lrelease` on every file. A pull request cannot be
merged unless the workflow passes.

## Review and merge

Every change goes through a pull request. A maintainer reviews the diff before
merging. The review is what guarantees that a contribution only changes
translation text and cannot remove messages, contexts or language files.

## Adding a language

Open an issue with the language code and the desired locale. A maintainer will
generate the file with `lupdate`, add it to this repository, and register it in
the launcher. Do not add a new `.ts` file in a pull request.

## Related repositories

- [launcher](https://github.com/PollyMC-Continued/launcher) - the application
- [website](https://github.com/PollyMC-Continued/website) - downloads, changelog
  and the translation editor

## License

The translation files are part of PollyMC-Continued and are licensed under the
GNU General Public License v3.0. See [LICENSE](LICENSE).
