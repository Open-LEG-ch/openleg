# Localisation

The complete-site preview extends Flask-Babel with English (`en`) and Swiss
Italian (`it_CH`). Swiss German (`de_CH`) and Swiss French (`fr_CH`) remain in the
existing default. `SITE_RELEASE_ENABLED` enables four-language presentation.
The flag controls newly offered display choices, not stored mail preferences.

The standalone sandbox gateway reads the same `OPENLEG_SITE_RELEASE_ENABLED`
switch. It passes only that presentation setting into its isolated workers,
alongside their existing allowlisted scratch settings. Four-language choices
survive entry, the management page and reset. Service credentials and the actual
association intake configuration are never inherited by a worker.

Keep the existing paths and `?lang=` URLs. A second prefix router would duplicate
public and capability routes. Stable equivalents use the same path with
`lang=en`, `lang=fr-CH` or `lang=it-CH`. German keeps the bare canonical path on a
German-default installation. Other installation defaults use an explicit German
query too. APIs, callbacks, downloads and personal access links keep their paths.

An allowlisted explicit query wins, followed by the signed session, tenant and
installation default. Browser Accept-Language does not redirect visitors. Links
preserve filters; the language control preserves the section in the browser. A
membership confirmation language change opens the stable access-request page and
never consumes the confirmation token again.

HTML identifies Content-Language and varies on Cookie. Public metadata uses the
requested language's canonical query. `SITE_TRANSLATIONS_APPROVED` stays false
during drafting: the preview is noindex and exposes no indexing alternates.
Enable reciprocal alternates after recording the complete language review.
Catalog completeness and passing builds do not establish native-speaker approval.

Storage keeps stable locale identifiers. The complete-site schema expands
recipient constraints without changing existing choices. Turning off the flag
does not narrow an expanded constraint. Mail renders with the recipient's stored
choice, including outside requests and after presentation is disabled. Explicit
recipient links retain that stored language. Unknown identifiers still fall back
to German; new preference writes follow the enabled presentation choices.

Run `python scripts/translations.py extract` after changing source text.
Extraction includes the recipient-specific gettext alias used by Verein mail.
Update translations, then run `compile` and `check` through the same script.
Checks reject missing/fuzzy messages, changed named placeholders, wrong plural
forms, stale compiled catalogs and stale generated SVGs. Review labels, alt text,
errors, mail and documents. Identifiers and money calculations stay unchanged.

New drafts may be prepared locally with the Helsinki-NLP OPUS-MT German-English,
German-French and German-Italian models (Apache-2.0). Drafting preserves format
placeholders, legal names, URLs and markup, and rejects missing markers. Review
terminology, negations, rights, dates and amounts directly. Preserve existing
translations. Release approval must identify its source revision, reviewer,
date and scope; machine-generated text does not establish that approval.
