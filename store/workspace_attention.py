# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded task aggregates for the LEG administration workspace."""

from datetime import date

import psycopg2

from store.billing import (
    _INVOICE_LIFECYCLE_STATE,
    _UNREADABLE_GROSS,
    BillingStoreError,
    count_assignable_statement_entries,
)

# Match billing_workspace.readiness_flags for the draft review affordance.
# Walk only objects, as the Python check does, and never cast invalid numbers.
_READY_DRAFT = """
    CASE WHEN jsonb_typeof(p.source_document_ids) = 'array'
         THEN jsonb_array_length(p.source_document_ids) > 0 ELSE FALSE END
    AND COALESCE(p.reconciliation->>'vnb_imbalance_release',
                 p.reconciliation->>'vnb_imbalance_tolerance_ratio') IS NULL
    AND NOT EXISTS (
        SELECT 1 FROM jsonb_each(
            CASE WHEN jsonb_typeof(p.reconciliation) = 'object'
                 THEN p.reconciliation ELSE '{}'::jsonb END
        ) f(key, value)
        WHERE key IN ('vnb_imbalance_kwh', 'vnb_imbalance_ratio')
          AND NOT COALESCE(
              CASE WHEN pg_input_is_valid(value #>> '{}', 'numeric')
                   THEN (value #>> '{}')::numeric = 0 ELSE FALSE END, FALSE
          )
    )
    AND (
        WITH RECURSIVE fields(key, value) AS (
            VALUES (''::text, p.reconciliation)
            UNION ALL
            SELECT f.key, f.value FROM fields
            CROSS JOIN LATERAL jsonb_each(
                CASE WHEN jsonb_typeof(fields.value) = 'object'
                     THEN fields.value ELSE '{}'::jsonb END
            ) f
        )
        SELECT COALESCE(bool_and(
            CASE WHEN pg_input_is_valid(value #>> '{}', 'numeric')
                 THEN (value #>> '{}')::numeric NOT IN ('NaN', 'Infinity', '-Infinity')
                      AND (COALESCE(jsonb_typeof(p.reconciliation->'precision_policy') = 'object', FALSE)
                           OR (value #>> '{}')::numeric = 0)
                 ELSE FALSE END
        ), FALSE)
        FROM fields
        WHERE right(key, 14) = 'difference_kwh'
          AND jsonb_typeof(value) <> 'object'
    )
"""


def get_workspace_attention(
    community_id: str,
    building_id: str,
    today: date,
    *,
    can_prepare: bool,
    can_approve: bool,
    require_dual_control: bool,
    can_manage_documents: bool,
    can_manage_members: bool,
) -> dict[str, dict]:
    """Return at most one count and one destination per task in this LEG."""
    import database

    try:
        with database.get_connection() as conn, conn.cursor() as cur:
            if can_prepare:
                # The task aggregates and the eligibility scan must see the same bookings.
                cur.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
            cur.execute(
                f"""
                WITH scope AS (
                    SELECT %(community)s AS community_id, %(actor)s AS actor,
                           %(today)s::date AS today
                ), draft_tasks AS (
                    SELECT p.id, p.period_start,
                           CASE
                               WHEN %(approve)s
                                 AND (NOT %(dual)s OR
                                      (NULLIF(p.prepared_by, '') IS NOT NULL AND p.prepared_by <> s.actor))
                                   THEN 'approve'
                               WHEN %(prepare)s
                                 AND COALESCE(p.prepared_by, '') IN ('', 'system')
                                   THEN 'prepare'
                           END AS task
                    FROM billing_periods p JOIN scope s USING (community_id)
                    WHERE (%(prepare)s OR %(approve)s) AND p.status = 'draft'
                      AND {_READY_DRAFT}
                ), periods AS (
                    SELECT task, count(*) AS count
                    FROM draft_tasks WHERE task IS NOT NULL GROUP BY task
                ), issued AS (
                    SELECT i.id, i.gross_chf, i.due_date,
                           {_INVOICE_LIFECYCLE_STATE} AS state
                    FROM invoices i JOIN scope s USING (community_id)
                    WHERE %(prepare)s AND i.status = 'issued'
                ), open_questions AS (
                    SELECT q.invoice_id, q.response_due_at, q.id
                    FROM invoice_queries q JOIN scope s USING (community_id)
                    JOIN invoices i ON i.id = q.invoice_id AND i.community_id = q.community_id
                    WHERE %(prepare)s AND q.status IN ('open', 'acknowledged')
                      AND i.status = 'issued'
                )
                SELECT p.task, p.count, oldest.id AS target, oldest.period_start AS at,
                       NULL::numeric AS amount, FALSE AS unreadable
                FROM periods p
                CROSS JOIN LATERAL (
                    SELECT id, period_start FROM draft_tasks WHERE task = p.task
                    ORDER BY period_start, id LIMIT 1
                ) oldest
                UNION ALL
                SELECT 'deliver', count(*), min(id), NULL, NULL, FALSE
                FROM issued WHERE state = 'issued'
                UNION ALL
                SELECT 'overdue', count(*), NULL, NULL,
                       sum(gross_chf) FILTER (WHERE NOT ({_UNREADABLE_GROSS})),
                       bool_or({_UNREADABLE_GROSS})
                FROM issued JOIN scope ON TRUE WHERE state = 'delivered' AND due_date < today
                UNION ALL
                SELECT 'next_payment', 0, NULL,
                       min(due_date)::timestamp AT TIME ZONE 'Europe/Zurich', NULL, FALSE
                FROM issued JOIN scope ON TRUE WHERE state = 'delivered' AND due_date >= today
                UNION ALL
                SELECT 'questions', count(*),
                       (SELECT invoice_id FROM open_questions
                        ORDER BY response_due_at NULLS LAST, id LIMIT 1),
                       min(response_due_at), NULL, FALSE
                FROM open_questions
                UNION ALL
                SELECT 'submission', count(*), NULL, NULL, NULL, FALSE
                FROM vnb_submission_cases JOIN scope USING (community_id)
                WHERE %(documents)s AND state = 'failed'
                UNION ALL
                SELECT 'mutation', count(*), NULL, NULL, NULL, FALSE
                FROM vnb_mutation_cases JOIN scope USING (community_id)
                WHERE %(members)s AND state = 'failed'
                """,
                {
                    "community": community_id,
                    "actor": building_id,
                    "today": today,
                    "prepare": can_prepare,
                    "approve": can_approve,
                    "dual": require_dual_control,
                    "documents": can_manage_documents,
                    "members": can_manage_members,
                },
            )
            tasks = {
                row["task"]: dict(row)
                for row in cur.fetchall()
                if row["count"] or row["at"]
            }
            if can_prepare and (
                count := count_assignable_statement_entries(conn, community_id)
            ):
                tasks["bank"] = {"count": count}
            return tasks
    except psycopg2.Error as error:
        raise BillingStoreError("Could not load workspace attention") from error
