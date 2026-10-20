# Internationalization

OpenLEG uses Flask-Babel/gettext for German (Switzerland, de_CH) and Swiss French
(fr_CH). German remains the installation default. The framework, interface and
printable documents support both locales. The four scheduled onboarding emails
and municipality interest notices use a persisted recipient preference.

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
normalization, fallback, session isolation, links and escaping.

## Interface coverage

Public pages, navigation, forms, member/admin/municipality/utility dashboards,
visible validation errors, accessibility text, JavaScript and demonstration media
are translated. Raw API schemas and machine identifiers remain unchanged. The
API playground adds localized human explanations beside the original response.

## Printable documents

Community agreements, participant contracts, VNB registration forms and member
invoices render their labels in the selected application locale. Rendering
outside a configured application keeps the German default. Trusted document
templates use escaped source values; names, meter identifiers, dates and frozen
invoice amounts keep their original values. Rendering a translated invoice
does not mutate its stored snapshot. Previously stored formation PDFs are
served as stored; language selection does not rewrite their bytes.

The raw contract-template API still describes its German sections as German.
PDF presentation does not relabel those source sections. This implementation
records no legal approval of the document text or its translation.

This stage adapts the document work from the contributor's
`i18n-fr-ch` commit `b4bc68f`, preserving the current authorization and
consent-filtering code.

## Recipient language

New registration forms store the selected German or Swiss French language as
`preferred_locale`. Building and unresolved coverage records accept only these
two normalized values. Existing records default to German. Public re-registration
cannot change a verified building's preference.

A verified resident can change their own preference in the private dashboard.
The update requires the dashboard session and CSRF token; a submitted building
identifier cannot select someone else's record. Page language selection alone
does not write this preference. The resident's profile export includes it.

The four scheduled onboarding emails read the current building preference at
delivery. Subjects, escaped HTML and destination links use that explicit locale,
independent of the cron request, tenant default or another recipient. The renderer
also works outside Flask and keeps personal values out of shared template globals.
Queue retry, address scrubbing and deletion rules continue to apply. The preference
shares its registration's retention and deletion rules.

This stage adapts the four email templates and translations from the same
contributor commit. Municipality notices use the language of the canonical
verified record for each recipient. The existing building-before-coverage
priority, case-insensitive deduplication, newcomer exclusion and update consent
continue to apply. An unverified or duplicate coverage submission cannot change
a verified building's language. Municipality names are escaped in HTML; no
recipient identity or newcomer contact details appear in the message.

Unresolved-address signup confirmations use the explicit validated choice from
that submission, including its verification link, without requiring a Flask
request or worker context. Missing choices retain German.

Building signup confirmations and resident dashboard access links use the
addressed profile's saved language. The profile email must match the addressee;
unknown or mismatched records use German. A requester cannot choose another
resident's email language through the access form or page language. Verification
and login links carry the recipient's locale through the existing token exchange.
Background confirmation mail works without a Flask context. These messages do
not write a language preference or change token, authorization or delivery rules.

Issued invoice delivery notices use the invoiced member's saved language and a
localized dashboard link. The delivery reservation reads the address and language
from that member together. Missing language defaults to German. The notice works
outside Flask; existing delivery retry rules and stored invoice amounts remain
unchanged.

Deletion confirmations use the language of the specific address profile or
coverage request bound to each active token and mailbox. A shared mailbox can
receive one link per record with different languages. Page language and submitted
preferences cannot change those choices. Missing language information uses German.
The existing confirmation, expiry and one-record deletion rules still apply.

Municipality dashboard access links use the saved language of the linked
administrator building only when its email matches the municipality addressee.
Unlinked or mismatched records use German. The public access request cannot
change this choice.

Utility registration saves the selected display language with the new account.
Subsequent login emails use that account's language and registered email together;
public login requests cannot override it. Updating other account fields preserves
the language unless the caller supplies a new supported choice.

Registry submissions accept an optional `preferred_locale` through the API.
Supported aliases normalize to German or Swiss French; omitted or unsupported
values use German. Verification reminders use each entry's saved language and
localized confirmation link, including outside Flask. The public directory API
does not expose the contact language. Both new preferences stay with their
existing account or registry record and share its retention rules. The guarded
schema upgrade defaults existing records to German.

Contact-request updates read each participant's saved language and mailbox
together during the authorized request or transition. Notifications contain
only the generic status message and localized dashboard link. They do not
include a participant's contact details or choice of shared fields.

Operator-entered correspondence remains in the author's language. Any future
generated mail must select an explicit recipient language before its sender
is enabled.
The registered formation-reminder template supports explicit German/French
worker rendering, plural day counts, translated domain labels and localized
links. Dynamic community, status and next-step text is escaped. It has no
active sender or schedule; a future sender must choose the recipient's saved
language for both subject and body.
This implementation records no human language or legal approval.


### Deferred registration confirmations and GRD preferences

The registration route copies the trusted tenant's platform and municipality
names into the confirmation callback before leaving the request. The worker
still resolves the recipient locale from the email-bound stored profile through
`email_localization`; it does not depend on another request or session. The
confirmation URL keeps the configured `APP_BASE_URL` origin.

Authenticated GRD accounts can edit their correspondence language at
`/utility/settings`. The form requires its session CSRF token. Its storage
operation updates only the session owner's `preferred_locale`, preserving other
account settings and other accounts. Subsequent login emails read that persisted
choice. Other roles retain their existing profile and administrator bindings;
this does not create a new generic preference or token system.
