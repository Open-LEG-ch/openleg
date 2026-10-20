# Privacy notice review packet (#558)

Status: legal/DPO review pending. Issue #558 has no assigned reviewer as of
20 October 2026. Code evidence below reflects commit
`1242af5898ecbed1bf7f755ca56d53809341475e`.

The reviewer must record their name, role and approval date before closing
[#558](https://github.com/Open-LEG-ch/openleg/issues/558). Use
[the retention matrix](retention-matrix.md) for the implemented deletion reach and
[the data catalog](data-catalog.md) for stored fields. The public notice is
`templates/datenschutz.html`.

## Decisions to record

- Identify the controller for each self-hosted or managed deployment, its contact,
  processors, hosting location and applicable contracts. The repository does not
  establish these deployment facts.
- Confirm the legal basis and purpose for each personal-data domain below.
- Choose retention periods where the matrix records no automated expiry. Distinguish
  a chosen policy from an implemented cleanup operation.
- Confirm the treatment of accounting and metering records after profile deletion,
  including requests for access, correction and deletion.
- Confirm retention and erasure for backups, exported archives, downloaded documents,
  outbound email and hosting logs with their actual operators.
- Review the implemented corrections for #550 and #551. #550 closed after #700
  corrected unsubscribe wording; #551 closed after verification of #591's
  removal of the match-deletion and log-deletion promises. Those engineering
  closures do not record legal approval of the complete notice.
- Review the French notice alongside the German source. The translated interface
  in #699 and the document/mail work in #707 do not establish a reviewed legal translation or deployment contact.

## Notice claims needing review

| Location | Current claim or omission | Evidence and question |
| --- | --- | --- |
| Section 1 | Names the platform as the responsible body | Confirm the legal entity and contact for the actual deployment. |
| Section 2 | Lists registration and system data | The product also stores private telemetry, equipment, documents, correspondence and accounting data. Decide which uses the notice must cover. |
| Section 3 | Describes interest, email, counts and consented map use | Review purposes for the additional domains below. |
| Section 4 | Refers to service provision and consent | Confirm the appropriate basis for each purpose; code inspection does not determine it. |
| Section 5 | Says municipalities and VNBs receive no names, addresses or contact details | Public municipality dashboards expose aggregate counts. Operator-authorized VNB formation packages also contain participant and document evidence. Clarify the scope of the statement against `vnb_exchange.py` and `store/vnb_exchange.py`. |
| Section 6 | States 30-day unconfirmed-interest and 12-month unresolved confirmed-interest cleanup | Check `store/interest.py`; these rules do not establish retention for every other domain. |
| Section 6 and unsubscribe | Refers to profile lifetime and accounting retention | `store/token.py::confirm_profile_deletion` deletes one bound registration; invoices, metering evidence and analytics rows can survive. #700 corrected the unsubscribe copy. Review its retained-domain wording and the failed deletion of a current administrator's profile below. |
| Sections 7–9 | Describes security, rights and contact | Confirm actual safeguards, request handling, responsible contact and escalation route. |

## Crosswalk for every retention-matrix domain

“Review” means a decision is pending, including whether the notice needs a
specific statement. It does not prescribe a legal basis or retention period.

| Domain | Current notice coverage and review question |
| --- | --- |
| store/building | Registration is covered. Review its saved email-language preference, profile-deletion wording and retained linked records. |
| store/interest | Sections 2, 3 and 6 cover interest and cleanup. Confirm refreshed-registration timing, the saved email-language preference and deletion of one bound record. |
| store/consent | Consent and revocation appear in sections 3, 4 and 8. Confirm the distinction between stopping disclosure and deleting records. |
| store/contact_request | Mutual disclosure appears in section 5. Review accepted/revoked snapshots, terminal history, access-time expiry and copies recipients already hold. |
| store/cluster | No separate statement. Review provisional neighbour assignments and their profile-deletion cascade. |
| store/metering | No specific retention statement. Review detached meter points, validated readings and the SDAT ledger. |
| store/telemetry | No specific statement. Review raw/hourly product defaults, shorter owner settings, sharing grants, expiry, replay watermark, deferred-notice retry marker and installation deletion. |
| store/telemetry_notices | No specific statement. Review private condition codes, episode and acknowledgement/cooldown/recovery timestamps. Only the current verified owner with confirmed membership has access; sample sharing grants no notice access. Active states are bounded per installation/condition/channel; resolved states expire at the raw-data horizon. Installation, membership/profile and community deletion cascade them. |
| store/equipment | No specific statement. Review device identifiers, private network details, named remote users, checklists and owner-downloaded handoffs. |
| store/advisories | No specific statement. Distinguish deployment-lifetime public evidence/maintainer audit from private equipment actions that cascade on deletion. |
| store/calculated_values | No specific statement. Review original VNB evidence, allocations and validation/replay records with no automated expiry. #703 adds append-only withdrawal events; withdrawing a source changes billing eligibility without erasing evidence or issued invoices. |
| store/operator_api | No specific statement. Review revoked credential hashes and delivery audit records. |
| store/operator_operations | No specific statement. Review minimal idempotency records and the absence of automated expiry. |
| store/sdat_ingestion | No specific statement. Review schedules and safe aggregate run metadata; they contain no source documents or meter IDs. |
| store/meter | No specific statement. CSV readings cascade on profile deletion; distinguish them from retained SDAT readings. |
| store/billing | Section 6 reserves accounting retention. Confirm its period and scope; the matrix's ten-year period is a policy statement, with no implemented expiry. |
| store/battery | No specific statement. Review community configuration and cost shares, including retained billing snapshots. |
| store/invoice_query | No specific statement. Review questions, messages and PDF evidence retained with accounting records. |
| store/profile | Public municipal energy facts. Record whether these require any personal-data wording. |
| store/email_queue | Email purposes appear in section 3. Review terminal-row cleanup, address scrubbing and provider-held messages. |
| store/utility | No specific statement. Review business contacts and account deletion/retention. |
| store/referral | No specific statement. Review referral-edge deletion, detached referrer links and aggregate counts. |
| store/document | No specific statement. Review signed and signing documents retained for the LEG lifetime. |
| store/formation | No specific statement. Ordinary memberships cascade on profile deletion. A current administrator's profile deletion fails while `communities.admin_building_id` references it, because that foreign key has no delete action. Decide the required administrator handover and retained community history. |
| store/formation_documents | No specific statement. Review the retained community document bundle. |
| store/formation_activation | No specific statement. Review the start-confirmation projection in `communities`, including source reference, attachment hash, effective date and recorded/activated actor IDs and times. The original attachment remains in `correspondence_log`; there is no automated expiry. |
| store/correspondence | No specific statement. Review the shared journal and attachments, including data about other people. |
| store/vnb_exchange | No specific statement. Review participant packages, response evidence and authorized handover to the VNB. |
| store/dashboard_profile | Registration wording partly covers it. Review the source tables rather than assuming this facade has its own retention. |
| store/token | Confirmation links are part of registration. Review expiry at use time, physical cleanup on profile deletion and deletion-confirmation language bound to the exact token target. Language selection does not delete the record. |
| store/access_token | No specific statement. Review hashed dashboard credentials, expiry and profile-deletion cleanup. |
| store/analytics | No specific retention statement. Events currently have no automated expiry and can outlive profile deletion. |
| store/tenant | No specific statement. Review deployment configuration and any personal contact fields. |
| store/api_client | No specific statement. Review hashed API credentials and deployment-lifetime records. |
| store/registry | No specific statement. Review public directory data, withdrawal and verification contacts. |
| store/ops | No specific statement. Review masked operational reports and snapshots with no automated expiry. |
| store/ranking | Public snapshot-derived rankings. Record whether these require any personal-data wording. |
| store/municipality | No specific statement. Review municipality account contacts and onboarding records. A current administrator's profile deletion is blocked by `municipalities.admin_building_id` until the retaining reference changes. |

## Approval record

Complete this record after the owner review:

- Reviewer name and legal/DPO role:
- Deployment or controller covered:
- Review date and code revision:
- Approved notice revision:
- #550 and #551 resolutions:
- Domain rows requiring no public wording, with reasons:
- Hosting/provider evidence held privately, with a safe reference:
- Follow-up issues, each quoting the current location and approved replacement:

Keep customer records, meter IDs, private contracts and raw log samples out of
this repository and public issue comments.
