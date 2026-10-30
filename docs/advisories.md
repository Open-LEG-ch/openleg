# Reviewed equipment advisories

Owners open the private advisory page from `/dashboard/equipment`. The catalog
starts empty; notices are disabled until an authorized maintainer publishes an
exact reviewed revision and configures successful recurring source reviews.
Nothing in this repository seeds a reviewed advisory or an operational reviewer.

## Review and coverage

The catalog uses manual vendor/NTC publications. It does not fetch sources,
subscribe to an undocumented feed or claim complete vulnerability coverage.
A research announcement does not establish affected device models.

A maintainer first creates a draft. The response includes its immutable content,
revision number and SHA-256 digest. Publication requires an explicit approval of
that exact latest revision, a named reviewer and a non-future review date at or
after the source's publication date. A correction is a new full draft revision,
with a change note. Publishing it supersedes earlier published revisions;
unpublished corrections do not replace the last published source. A withdrawal
is another reviewed revision with `status: "withdrawn"`. Old source content,
review dates and withdrawals remain available in the admin history.

Before notices can become active, configure a named source-review owner, cadence
of 1 to 90 days, public coverage description and exact source URLs. The named owner
records a successful review of every configured source under that exact policy
revision. A changed policy requires a new review. The latest failure, an overdue
review or a disabled policy pauses active notices and new owner actions. Prior
published evidence remains visible with its current source status. A source is
overdue at the start of its due date in UTC.

Reviewer identifiers are private admin metadata. Under the shared admin-token
model they are identifiers attested by an authorized maintainer, not identities
verified by a separate login provider. Owners see public scope, reviewed URLs,
last successful review, last failure and the next due date, not these identifiers.
A published advisory's exact URL must be in the currently reviewed source list
before its notice can be active.

## Matching

Manufacturer and model identities use Unicode NFKC, case folding and collapsed
whitespace. Punctuation remains significant. Only an exact normalized canonical
name or an explicit, reviewed alias matches. Manufacturer names alone do not
establish affected equipment. Missing identity remains unresolved and does not
create an active notice.

Each model carries source range text plus an explicit version profile:

- `dotted-numeric`: one to four dot-separated ASCII integer components, each
  0 to 99999 without leading zeroes. Equipment and every bound must have the same
  component count. `1.2` is not silently treated as `1.2.0`.
- `sma-r`: exactly three such numeric components followed by literal `.R`.
  Compare the numeric tuple. `.r`, other suffixes, extra components and bare
  numeric versions remain unknown under this profile.
- `unsupported`: retain the original range text without interpreted bounds.
  All firmware observations remain unresolved.

`lower_inclusive` includes the lower bound; `upper_exclusive` excludes the fixed
version. A supported profile needs at least one bound. Both bounds must use
compatible component counts and increase strictly. Missing firmware is always
unknown. Results distinguish `affected`, `possible`, `not_within` this particular
range, and `no_identity_match`. None establishes that an installation is secure.

## Owner acknowledgement and remedy evidence

Notices stay in the private application. Owners can acknowledge an exact
published revision and record a planned or owner-reported remedy with dated
owner/device/installer evidence. A reported completed remedy needs a known,
non-future evidence date; it does not change the firmware classification or
verify that a device was fixed.

Actions bind the exact manufacturer/model/firmware context and source revision.
Changed firmware and corrected publications require a new acknowledgement.
Prior evidence remains private history. Equipment/profile deletion cascades to
all action rows. Neither neighbour consent, LEG administration, telemetry grants
nor admin catalog credentials grant another owner's inventory or remedy access.

Source text and notes render as escaped plain text. Public documentation links
must be HTTPS without credentials, query parameters, fragments, IP literals or
local/internal hostnames. OpenLEG never follows these links on the server. No
messages go to third parties and no firmware, network or electrical settings are
changed. This feature does not detect active compromise.

## HTTP API

Admin routes require `X-Admin-Token`; URL/form credentials are rejected. The
configured `ADMIN_TOKEN` must already exist. Never put its value in a URL, source
record or committed file.

| Method | Path under `/api/admin/advisories/v1` | Result |
| --- | --- | --- |
| GET, POST | `/catalog` | Latest drafts/published revisions; create a draft |
| GET | `/catalog/<uuid>` | Immutable revision/publication history |
| POST | `/catalog/<uuid>/revisions` | Append a complete correction or withdrawal draft |
| POST | `/catalog/<uuid>/revisions/<n>/publish` | Review and publish the exact latest digest |
| GET, POST | `/source-policy` | Current source policy/health; append a new policy |
| GET, POST | `/source-reviews` | Last 50 manual review events; record success/failure |

A publication body has `digest`, private `reviewer_id`, `reviewed_on` and
`approve: true`. A source policy has `enabled`, private `review_owner`,
`cadence_days`, public `coverage` and `source_urls`. A source review has the exact
`policy_revision`, matching `review_owner`, `reviewed_on`, `outcome`,
`source_urls` and `failure_code`. Success must cover the entire configured URL
set and has no failure code. Failure codes are `fetch_failed`, `format_changed`,
`source_missing` and `manual_incomplete`. Collection is manual; recording an
outcome does not fetch or verify a source automatically.

Owner routes require the verified dashboard session:

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/equipment/v1/records/<equipment>/advisories` | Coverage, published assessments/notices and private action history |
| POST | `/api/equipment/v1/records/<equipment>/advisories/<advisory>/<n>/actions` | Save acknowledgement/remedy evidence |

Owner mutations require `X-CSRF-Token`. An action body has the returned
`context_digest`, boolean `acknowledged`, `remedy_status` (`unknown`, `planned`,
`reported_done`), `evidence` (`kind`, `reviewed_on`, optional `source_url`) and
optional `private_note`. Evidence kinds are `unknown`, `owner_report`,
`device_observation` and `installer_evidence`. Unknown evidence has no date or
source link; a known kind needs a date. New actions require an active reviewed
source, current published revision and exact known device identity.

Requests accept uncompressed JSON up to 32,768 bytes. The catalog holds at most
200 advisories, each at most 50 immutable revisions, 20 models and 20 aliases per
identity. Source configuration permits 20 URLs, 1,000 policy revisions and
10,000 review events. Each equipment record holds at most 500 action contexts;
existing contexts can still be edited at the limit. Owner views include current
published records, the latest 50 superseded publications and the latest 100 owner
action contexts. Catalog creation/revision limits return 409. All owner/admin
responses, including errors, use no-store and no-referrer headers.

## Unreviewed import example

Public sources checked on **2026-10-15**:
[SMA security notifications](https://www.sma.de/en/cybersecurity/security-notifications)
links its Sunny Boy notice to
[CERT@VDE VDE-2025-066](https://certvde.com/en/advisories/VDE-2025-066/).
CERT@VDE supplies the five exact model IDs, the affected firmware boundary,
source publication date 2025-08-27 and source revision 1.0.0. SMA recommends
firmware at least 3.10.27.R. These are a concrete draft starting point; this
source check is not catalog approval or a configured ongoing review policy.

The source's separate CVE metadata date differs from the advisory publication
date. This example uses the advisory's publication/revision fields and identifier.
The proposed `SMA` alias also needs explicit maintainer review. Do not extend
these model conditions to unrelated inverter models or vendor-cloud advisories.

POST this body to `/api/admin/advisories/v1/catalog` only after inspecting the
current publications. It creates a draft and does not publish or enable notices:

```json
{
  "reference": "VDE-2025-066",
  "publisher": "CERT@VDE / SMA",
  "source_url": "https://certvde.com/en/advisories/VDE-2025-066/",
  "source_revision": "1.0.0",
  "publication_date": "2025-08-27",
  "title": "SMA Sunny Boy: VDE-2025-066",
  "summary": "Die Veröffentlichung beschreibt den Zugriff authentifizierter Angreifer auf Dateien ausserhalb des vorgesehenen Web-Verzeichnisses.",
  "vendor": "SMA Solar Technology AG",
  "vendor_aliases": ["SMA"],
  "models": [
    {"model": "SB3.0-1AV-41", "aliases": [], "firmware": {"scheme": "sma-r", "upper_exclusive": "3.10.27.R", "range_text": "Firmware vor 3.10.27.R"}},
    {"model": "SB3.6-1AV-41", "aliases": [], "firmware": {"scheme": "sma-r", "upper_exclusive": "3.10.27.R", "range_text": "Firmware vor 3.10.27.R"}},
    {"model": "SB4.0-1AV-41", "aliases": [], "firmware": {"scheme": "sma-r", "upper_exclusive": "3.10.27.R", "range_text": "Firmware vor 3.10.27.R"}},
    {"model": "SB5.0-1AV-41", "aliases": [], "firmware": {"scheme": "sma-r", "upper_exclusive": "3.10.27.R", "range_text": "Firmware vor 3.10.27.R"}},
    {"model": "SB6.0-1AV-41", "aliases": [], "firmware": {"scheme": "sma-r", "upper_exclusive": "3.10.27.R", "range_text": "Firmware vor 3.10.27.R"}}
  ],
  "remedy": {
    "text": "SMA empfiehlt Firmware ab 3.10.27.R. Lassen Sie die passende Version durch die zuständige Fachperson prüfen und installieren.",
    "url": "https://www.sma.de/en/cybersecurity/security-notifications"
  },
  "status": "active",
  "change_note": "Ungeprüftes Importbeispiel. Öffentliche Quellen am 2026-10-15 gelesen. Keine Katalogfreigabe."
}
```
