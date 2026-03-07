# HEARTBEAT.md

Check these on each heartbeat (2-4x daily):

1. get_stale_outreach --days_threshold 7: any municipalities that need follow-up?
2. get_stuck_formations --days 7: any communities stuck in formation?
3. get_decisions: any pending CEO decisions waiting?

If stale outreach found: draft follow-up, queue for approval.
If stuck formations found: send_telegram alert to CEO.
If nothing actionable: reply HEARTBEAT_OK.

## Weekly: Pilot candidate scouting (Mondays)

Score 10 municipalities for next case study page. Criteria from SOUL.md "Next pilot candidates" section. Report top 3 via Telegram with structured analysis. Target regions without a case study yet: Zurich, Bern, Ostschweiz, Zentralschweiz.
