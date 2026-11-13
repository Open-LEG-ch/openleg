# Association privacy draft review

The screenshots show `/datenschutz#verein-datenschutz` with the complete-site
switch enabled locally. This is a legal-review draft, not an approved notice.

The browser audit covers the full privacy page in four locales at 320, 390,
768 and 1280 px. All 16 cases have no horizontal overflow and no axe violations.
The source is `templates/verein/privacy_notice.html`; controller details apply
only to association data. Membership intake and production config are unchanged.

The version is `verein-2026-11-13-draft-1`. Review prerequisites and code evidence
are in `docs/privacy-notice-review.md`. Native-language and legal review remain
pending. Site screenshots are in the preceding site PR's review artifacts.
