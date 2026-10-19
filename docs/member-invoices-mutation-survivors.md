# Member invoice mutation survivors

Issue #673, within #478. This record covers `member_invoices.py` in the
unchanged ten-file mutation scope, not the whole repository.

## Native evidence

The baseline is commit `eeee71e`: mutmut 3.7.0 generated 7,069 mutants,
killed 5,386 and retained 1,683 survivors, with zero uncovered mutants and
no other statuses. The strict score was 76.19%. The member invoice module
held 1,246 mutants: 864 killed and 382 survived.

The final test revision and complete native counts are recorded in the PR
validation record. Every run starts with an absent `mutants/` cache, uses the tracked
`pyproject.toml` unchanged, and runs `python -m pytest -q tests` before and
after the native mutation run. Evidence includes `evidence.json`, native
`results --all true`, native CI statistics and per-file metadata outside the
checkout. The ordinary focused command is:

```bash
python -m pytest tests/test_billing_member_invoices.py -q
```

## Behavior now checked

Public `list_view`, `detail_view` and PDF HTML rendering cover JSON-encoded
snapshots, precise refusal diagnostics, zero energy and tariffs, payment and
VAT boundaries, explicit half-up rounding, legacy and frozen rounding proofs,
battery position shape, correction links, lifecycle errors, overflow, and
visible populated and empty PDF sections. No billing behavior changed.

## Remaining classifications for review

These are semantic classifications, not native status overrides. All listed
mutants remain survivors and block the automated 100% strong gate.

IDs below mean `member_invoices.x_<function>__mutmut_<number>`; private
function names already begin with an underscore.

| Function and numbers | Classification and evidence |
| --- | --- |
| `_decimal_text` 4 | Equivalent exponent. Decimal `quantize` reads the exponent of its quantum, not its coefficient. `1.scaleb(-places)` and `2.scaleb(-places)` have the same exponent. Existing amount, kWh and rate assertions cover the public output. |
| `_decimal_text` 9, 16 | Equivalent finite formatting for this interface. Quantization fixes the exponent at -2 or -3; default formatting, `f` and `F` produce the same finite decimal text. Non-finite input is rejected earlier. |
| `_summary` 3, 9, 10, 11, 17, 20, 21, 22 | Guarded diagnostics. Both public read functions first run `_detail_from_invoice`, which validates the same provenance dictionary and period start before calling `_summary`. Corrupt provenance is refused before these messages can be observed. The malformed snapshot tests exercise those public refusals. |
| `_summary` 69, 75, 76, 77 | Guarded diagnostic. `_require_invoice_totals` already requires a finite gross amount before `_summary` repeats that check. The invalid-gross case fails at the earlier public diagnostic. |
| `_require_invoice_totals` 77 | Equivalent sum start. A nonempty rendered-items list is required and every amount is a Decimal. Adding the first Decimal to integer zero or Decimal zero yields the same Decimal sum. Zero and mixed charge/credit tests cover both cases. |
| `_rows_html` 12 | Equivalent HTML tag case. HTML tag names are case insensitive; cell text and structure are unchanged. The visible PDF text tests use HTMLParser rather than requiring source tag spelling. |
| `_rows_html` 15, 17, 20, 35, 37, 40 | Unreachable defaults for supported input. `_line_item_view` always emits `item_type_label` and `display_amount_chf`. PDF rendering accepts the exact validated detail dictionary; omitting those fields is outside that contract. The populated PDF test checks the actual values. |
| `render_pdf_html_string` 4, 6, 33, 35, 40, 42 | Unreachable defaults for supported input. `detail_view` always supplies `charges`, `credits` and `rounding_adjustments`, including empty lists. The populated and empty PDF cases exercise those values. |
| `render_pdf_html_string` 50, 52, 55, 59, 61, 64, 68, 70, 73, 77, 79, 82, 86, 88, 91, 95, 97, 100, 104, 106, 109, 113, 115, 118, 127, 129, 132, 136, 138, 141, 145, 147, 150, 154, 156, 159 | Unreachable scalar defaults for supported input. The detail read model always supplies invoice identity, issuer, period/date labels, VAT mode/rate, tariff, payment days and totals. Missing or corrupt snapshots fail before rendering. Public read-model and visible PDF assertions check these fields. |

These classifications do not remove generated records, narrow scope, or
convert a failed strict gate into a pass. They need substantive review before
any separate decision about equivalent-mutant policy.
