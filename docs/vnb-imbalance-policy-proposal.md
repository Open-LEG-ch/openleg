# VNB imbalance allowance proposal

Status: draft for accounting review. No accounting approval is recorded.

The billing readiness work in [#684](https://github.com/Open-LEG-ch/openleg/issues/684) leaves unequal VNB community totals blocked. [#758](https://github.com/Open-LEG-ch/openleg/pull/758) retains that rule while fixing balanced VNB billing. This document asks whether a future change should permit unequal totals and, if so, who should carry the difference.

This proposal changes no runtime code, schema or billing rule. The previous implementation is preserved at [6766942](https://github.com/msgallagher/openleg/commit/67669420866be690d81478dcd9f7d43f14675e25); it has no accounting approval and is not a release candidate.

## Proposed mechanism

The earlier implementation proposed a default monthly relative allowance of 0.1%, calculated as:

```text
abs(VNB production allocated to LEG - VNB consumption allocated to LEG)
/ VNB consumption allocated to LEG
```

Within that allowance, consumer quantities stayed at the VNB values. Producer credits were scaled proportionally so their total matched consumer quantities. Quantities were stored at six decimal places; leftover millionths were assigned by largest fractional remainder with a deterministic participant tie-break. A named operator could propose a reasoned exception for one period, capped at 2%; that person could not approve the resulting draft.

These are proposed policy choices. A precision certificate indicates that interval values can be consistent with rounded source data; it does not authorize transferring the difference to producers.

## Worked examples

All figures below are synthetic. They show energy quantities before monetary rounding. They assume no batteries, fees or taxes and a uniform internal energy price of CHF 0.15/kWh solely to illustrate the amount transferred.

| Case | Consumer LEG quantity | VNB producer quantities A / B | Difference | Relative difference | Proposed credited quantities A / B |
| --- | ---: | ---: | ---: | ---: | ---: |
| Exact balance | 100.000000 kWh | 40.000000 / 60.000000 kWh | 0 kWh | 0% | 40.000000 / 60.000000 kWh |
| More production | 100.000000 kWh | 40.000000 / 60.040000 kWh | +0.040000 kWh | 0.04% | 39.984006 / 60.015994 kWh |
| More consumption | 100.040000 kWh | 40.000000 / 60.000000 kWh | -0.040000 kWh | about 0.039984% | 40.016000 / 60.024000 kWh |
| Above default | 100.000000 kWh | 40.000000 / 60.200000 kWh | +0.200000 kWh | 0.2% | Blocked without an approved exception |
| Above proposed cap | 100.000000 kWh | 40.000000 / 63.000000 kWh | +3.000000 kWh | 3% | Blocked; corrected VNB evidence required |

In the more-production example, A loses 0.015994 kWh and B loses 0.024006 kWh of credited energy. The combined change is CHF 0.006 before invoice rounding. In the more-consumption example, A gains 0.016000 kWh and B gains 0.024000 kWh, again CHF 0.006 before rounding. A participant's stored credited energy would therefore differ from the VNB statement.

Under the retained rule in #758, only the exact-balance row proceeds. Every nonzero difference above stops billing, regardless of size or the existence of an old exception record.

## Decisions needed before implementation

An accounting reviewer and the LEG operator need to record answers to these questions:

- Should any nonzero difference be accepted, or must the VNB correct its delivery?
- If a difference is accepted, should producers carry it, should the LEG carry it in an explicit clearing account, or is another treatment required? How should the invoice explain it?
- Is a monthly relative threshold appropriate? What absolute energy and CHF limits should apply, including periods with very little energy?
- Should exceptions exist? Who may grant one, who may approve billing, and must approval bind to the exact accepted delivery fingerprint as well as the period?
- How should six-decimal energy allocation, participant tie-breaks and cent rounding interact? What entries balance the ledger when displayed invoice amounts differ?
- Which audit evidence, archive compatibility and correction procedure must be retained?

Any implementation needs signed-off examples in both directions, zero/one-sided energy cases, threshold boundaries, multiple producers, rounding ties and corrected deliveries. Battery treatment needs its own explicit accounting decision; this proposal does not expand battery support.

Until those decisions are approved, keep exact balance and request corrected VNB evidence for unequal totals. Human QA remains required for billing changes.
