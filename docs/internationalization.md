# Internationalization

OpenLEG uses Flask-Babel/gettext for German (Switzerland, de_CH) and Swiss French
(fr_CH). German remains the installation default. This first contribution ships
the generic framework and language selection; interface, documents and recipient
communications are added in subsequent contributions.

## Language selection

Use the Deutsch / Français links or ?lang=fr-CH. Supported aliases are de, de-CH,
de_CH, fr, fr-CH and fr_CH. The explicit choice wins over the signed session,
tenant locale and BABEL_DEFAULT_LOCALE. Unsupported values are ignored;
Accept-Language does not change the historical default. Set BABEL_DEFAULT_LOCALE
to fr_CH to choose a French installation default. A presentation selection does
not identify an account or write a recipient preference.

The Jinja filters run at render time, including literal arguments, so compiled
templates cannot cache another request's language. Domain helpers without a
configured Flask application retain their German values. Machine identifiers,
calculations and stored data remain unchanged.

## Catalog maintenance

Run python scripts/translations.py extract, update the PO files, then run
python scripts/translations.py compile and python scripts/translations.py check.
The validator rejects missing/fuzzy messages, changed named placeholders,
context mismatches and stale compiled plural rules. Each staged contribution
contains only its extracted messages and the corresponding validated translations.

Use complete sentences and named parameters; use ngettext for plural forms.
German literals in domain data use translation_markers.gettext_noop and are
translated only at the presentation boundary. No external translation service is
required. Contributions remain under AGPL-3.0-or-later.

For a future language such as it-CH, add its aliases and display label, catalog
and tests together. Features added later must also update their explicit locale
constraints and media variants. No separate application is necessary.

Run scripts/tdd_cycle.sh gate before proposing changes. The framework tests cover
normalization, fallback, session isolation, links and escaping. French interface
coverage is deliberately introduced in the next contribution.

## Interface coverage

Public pages, navigation, forms, member/admin/municipality/utility dashboards,
visible validation errors, accessibility text, JavaScript and demonstration media
are translated. Raw API schemas and machine identifiers remain unchanged. The
API playground adds localized human explanations beside the original response.

Documents/PDF and recipient email preferences are separate subsequent changes.
No persistent language preference schema is introduced by this interface layer.
