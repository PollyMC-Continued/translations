# Contributing translations

Thank you for helping translate PollyMC-Continued. This repository accepts
translation changes only; the source strings come from the launcher code.

## Ways to contribute

### Web editor

Open <https://pollymc.vercel.app/translation/>, choose a language, and translate
the strings that are still unfinished. The editor validates placeholders and
submits your changes to a moderation queue. You do not need a GitHub account.

### Manual pull request

1. Fork this repository.
2. Create a branch, for example `de/fix-login-strings`.
3. Edit one language file. Keep the XML structure intact.
4. Run the checks:

   ```shell
   python3 scripts/validate.py
   ```

5. Commit with a message such as `de: translate instance settings`.
6. Open a pull request against `main` and complete the template.

## Rules

- Change `<translation>` text only. Do not edit `<source>`, `<context>`,
  `<comment>`, `<location>` or the `numerus` attribute.
- Do not add, remove or reorder messages, contexts or language files.
- Keep placeholders intact: `%1`, `%2`, `%n`, `%L1` and similar tokens must
  appear in the translation. They may be reordered, but not dropped.
- Keep accelerator markers (`&`) and HTML tags (`<br>`, `<b>`, links) intact.
- For plural messages, fill every `<numerusform>` that already exists. Do not
  change the number of forms.
- Do not translate product names, file paths, URLs or command-line flags.
- Use the language's normal spelling and punctuation. Machine translation is
  allowed as a starting point, but review it before submitting.
- One language per pull request makes review easier.
- New languages need a maintainer: open an issue instead of adding a file.

## Review process

- CI validates the XML, checks the diff, and compiles the files with `lrelease`.
- A maintainer reviews the changes and merges the pull request.
- After merging, the launcher's release workflow rebuilds the `.qm` files and
  publishes them on the website.

## License

By contributing you agree that your changes are licensed under the GNU General
Public License v3.0, the same license as PollyMC-Continued.
