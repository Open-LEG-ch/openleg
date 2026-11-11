# Translation artifacts

From the repository root, run:

```sh
python scripts/translations.py sync
python scripts/translations.py check
```

`sync` extracts production Python, HTML, text and SVG messages, updates `messages.pot`, prunes obsolete PO entries and
appends missing entries. Surviving PO blocks in every locale, including German, and their draft comments
stay intact. New entries use fixed 100-column gettext string wrapping. New German
entries copy the source; existing German translations remain unchanged. Contexts and plural source forms identify distinct entries.

Author French, English and Italian translations in their `messages.po` files.
Missing text, fuzzy entries and placeholder mismatches stop sync before compilation.
Sync never fills foreign entries from German or grants translation approval. After
a successful validation it compiles every MO and renders localized SVG assets.
Run sync again after authoring translations, then run check before submitting.

`check` writes nothing. It checks the committed POT against current extraction,
requires all four complete POs without extra or obsolete entries, compares compiled
MO bytes and generated localized SVGs, and verifies semantic review digests.
Generated POT timestamps use a fixed epoch; PO author metadata is preserved.
`compile` remains available for image builds and validates all four catalogs before
compiling MOs and localized SVGs. Use sync for contributor work.

`review-status.json` identifies the exact source and locale revisions. Sync resets
approval when their text changes, retaining superseded review evidence under its
previous digest. Unchanged evidence stays attached to the reviewed revision.
Draft work remains draft; human native-language and legal review are separate.

## Retired execution helpers

The November 2026 execution directory used the following one-off helpers:

- `draft_translations.py` generated machine drafts using local model weights.
  Draft generation is not part of sync. Existing drafts remain in the POs.
- `apply_translation_overrides.py` combined `english-overrides.json`,
  `french-overrides.json` and `italian-overrides.json` with catalog entries.
  Its reusable merging, German source copying and pruning now live in
  `scripts/translations.py`. Edit POs explicitly instead of replaying overrides.
- `refine_bank_translations.py`, `refine_finance_translations.py`,
  `refine_finance_errors.py` and `refine_roles_translations.py` produced source-bound
  draft overrides from their review JSON and language text files.
  `fix-coderabbit-translations.py` applied later corrections. Those results are
  already held in the current POs, including draft markers. These scripts and
  intermediate JSON/text files are not inputs to the supported workflow.
- `refresh_review_digests.py` computed semantic digests. This behavior now lives in
  sync and check, including invalidation and retention of old review evidence.

No execution-directory script, local model, absolute workspace path or override
JSON is needed to sync or check catalogs.

Run isolated workflow tests with:

```sh
python -m pytest translations/test_workflow.py -q
```

CI should run this test module and `python scripts/translations.py check` after
installing project dependencies.
