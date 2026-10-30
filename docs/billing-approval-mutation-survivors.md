# Billing approval mutation survivors

Issue #672, within #478. This record covers `billing_approval.py` in the
unchanged ten-file mutation scope, not the whole repository.

## Native evidence

At baseline commit `eeee71e`, mutmut 3.7.0 generated 7,069 mutants,
killed 5,386 and retained 1,683 survivors, with zero uncovered mutants and
no other statuses. The strict score was 76.19%. Billing approval held
1,261 mutants: 844 killed and 417 survived.

At test revision `1242b32`, the module has 1,192 killed
and 69 survived, retiring 348 survivors. The full configured run has
5,734 killed and 1,335 survived, strict score 81.11%,
with zero uncovered mutants and no other statuses. The following change
to this record only documents the reviewed schema bound; tested Python and
configuration files are unchanged. Each run starts with an absent `mutants/` cache, keeps the
tracked `pyproject.toml` unchanged, and executes `python -m pytest -q tests`
before and after the unfiltered native run. The external evidence contains
`evidence.json`, native `results --all true`, native CI statistics and every
source file's native metadata. The focused command is:

```bash
python -m pytest tests/test_billing_approval.py -q
```

## Behavior now checked

The public `prepare_invoice_snapshots` seam covers precise malformed-draft,
reconciliation, line-item and battery diagnostics; settlement pricing;
per-line energy precision boundaries; rounding residual bounds; half-up VAT
and battery value rounding; zero costs; multiple battery consumers; frozen
provenance and timestamps; and finite energy and rounding-sum overflow.
The public date seam retains the previous Zurich-calendar regression.
No allocation, reconciliation or billing policy changed.

## Remaining classifications for review

IDs below mean `billing_approval.x_<function>__mutmut_<number>`; private
function names already begin with an underscore. Every listed record stays
a native survivor and blocks the automated 100% strong gate. These semantic
classifications do not override statuses or narrow configured scope.

The guard arguments assume plain persisted values and the ordinary Decimal
context. They do not establish a contract for arbitrary Decimal subclasses,
caller-installed traps or changes to precision/exponent limits.

| Function and numbers | Classification and evidence |
| --- | --- |
| `_as_decimal` 5, 6 | Equivalent NaN spelling. Decimal parses `NaN`, `nan` and `NAN` as the same non-finite value. Malformed numeric input is refused through the public finite-value checks. |
| `_require_order` 2 | Equivalent equal-window refusal. The next guard requires a strictly increasing window and emits the same public diagnostic. The equal-period regression covers it. |
| `_require_canonical_reconciliation` 87, 89, 91 | Unreachable lookup defaults. The immediately preceding set equality requires every billed participant to be present. The participant coverage and mismatch tests exercise this guard. |
| `_require_canonical_reconciliation` 100, 111 | Equivalent sum start. Quantities are Decimals. For a nonempty sequence both starts produce the same Decimal; for an empty direction, integer zero and Decimal zero compare equally to the validated total. |
| `_require_canonical_reconciliation` 118, 119, 120, 121 | Guarded arithmetic diagnostic. The same per-kind nonnegative quantities have already accumulated successfully in input order, with duplicate participant/type keys refused. Dictionary insertion order preserves that order for this repeated sum. Finite-energy sum-overflow tests check the earlier reachable failures. |
| `_require_rounding_adjustment` 3, 5 | Equivalent ordering key. Validated producer ids are strings, so natural minimum and `key=str` select the same participant. Wrong-recipient and multi-producer tests cover that choice. |
| `_require_rounding_adjustment` 10, 18 | Guarded sum defaults. Without rounding items, balanced money does not read the total; unbalanced money is refused for missing cardinality before reading it. With items, the sum is Decimal either way. |
| `_require_rounding_adjustment` 48, 50 | Equivalent rounding at an existing quantum. Every non-rounding amount is already validated against a product quantized to six decimals. The residual therefore needs no half-way rounding at that same quantum. Precision and residual tests cover the public result. |
| `_require_rounding_adjustment` 61, 62, 63, 64 | Guarded arithmetic diagnostic. A successfully accumulated finite rounding sum is compared with a finite bound using `copy_abs`; neither operation raises ArithmeticError in the ordinary context. The separately reachable sum-overflow diagnostic has a regression test. |
| `_require_wellformed_line_items` 45 | Unobserved local list entry. `battery_items` is collected but never read. Battery validation instead uses the original line items through `_require_battery_cost_shares`. Battery acceptance and refusal tests exercise that path. |
| `_require_wellformed_line_items` 58, 59, 60, 61, 84, 85, 86, 87, 88, 89, 90, 93, 94, 95, 96, 97, 98, 99, 100 | Guarded amount-sign checks. `_validated_line_item` already requires finite nonnegative quantities/prices and exact signed product amounts, with producer credits negated. Positive producer amounts and negative consumer/fee amounts fail at that earlier public diagnostic. Zero-price and settlement cases cover accepted boundaries. |
| `_require_wellformed_line_items` 64, 103 | Equivalent initial lookup. Duplicate participant/type keys are refused before aggregation. A participant's first entry always uses zero; looking up None also returns zero because participant ids must be nonempty strings. |
| `_require_wellformed_line_items` 127, 128, 129, 130 | Guarded accumulation diagnostic. Individually quantized finite money amounts and the actual Python list-length limit keep the exponent of their sum well inside the ordinary Decimal exponent range. Energy-sum overflow remains separately reachable and tested. |
| `_require_wellformed_line_items` 141, 142, 143, 144 | Guarded arithmetic diagnostic. Both nonnegative quantity totals have already accumulated successfully. Their difference cannot exceed the larger magnitude. The tolerance depends on the finite actual list length. Exact-boundary and excess-gap tests exercise the public comparison. |
| `_require_wellformed_line_items` 158, 159, 160, 161 | Guarded bound calculation. Quantity mismatch already satisfies the finite per-line tolerance and the persisted tariff validator caps the internal price at CHF 10/kWh. Multiplication and addition remain finite in the ordinary context. |
| `prepare_invoice_snapshots` 173 | Equivalent sum start. Every grouped participant has at least one validated Decimal amount. Both starts produce the same sum, including zero-cost snapshots. |
| `prepare_invoice_snapshots` 181, 182, 183, 184, 199, 200, 201, 202 | Guarded snapshot arithmetic. Validated position cardinality, six-decimal product money, reconciled battery cost shares and the bounded residual constrain each participant's net. VAT is finite and capped at 100%. The normal-context net/cent/VAT operations cannot reach these extra refusals. The positive, zero, settlement and battery snapshot tests check those results. |

## Persisted residual quantization

`_require_rounding_adjustment` 52, 53, 54 and 55 are guarded on the
production path. `store/billing.py::approve_billing_period` reads persisted
line items in id order and passes them unchanged to the preparation seam.
`store/schema.py` declares their primary key as `SERIAL` and `amount_chf`
as `DECIMAL(12,6)`; the compatibility migration preserves those types.

Even allowing every signed integer primary-key value, there are at most
2^32 distinct rows. Each absolute amount is below 10^6 CHF. The sum of
absolute values is therefore below 4.295 x 10^15 CHF. At six decimal places,
it needs at most 22 significant digits. Every intermediate sum is within
that same absolute bound, so the ordinary Decimal precision of 28 can add,
negate and quantize this residual exactly. The exceptional diagnostic cannot
be reached by a supported persisted draft.

This is a production-schema guard, not a claim about arbitrary synthetic
lists accepted by the Python function or a database altered outside the
tracked schema. Revisit the classification if either column type, the caller
or the Decimal context changes. Independent review checked the schema,
compatibility migration and sole production caller before accepting this
classification.

No generated records were deleted, no cache reused for evidence, no status
rewritten and no equivalent waiver applied. The strict gate remains blocked.
