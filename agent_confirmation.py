# SPDX-License-Identifier: AGPL-3.0-or-later
"""Human authorization for the immutable intent of an agent VNB submission."""

import database as db
import operator_actor
import vnb_exchange


class BoundCases:
    """Check the fingerprint again at the real claim, before any business write."""

    def __init__(self, row):
        from store import vnb_exchange as cases

        self.cases = cases
        self.row = row

    def __getattr__(self, name):
        return getattr(self.cases, name)

    def _check(self, claim):
        if (
            claim.payload_fingerprint != self.row["payload_fingerprint"]
            or claim.adapter_key != self.row["adapter_key"]
        ):
            raise db.VnbSubmissionConflict("Confirmation changed")

    def _owned_case(self, row):
        if row.get("actor") != operator_actor.snapshot():
            raise db.VnbSubmissionConflict("Another submission already owns this case")
        return row

    def claim(self, claim):
        self._check(claim)
        return self._owned_case(self.cases.claim(claim))

    def claim_mutation(self, claim):
        self._check(claim)
        return self._owned_case(self.cases.claim_mutation(claim))


def confirm(community_id, request_id, building_id):
    with db.agent_confirmation(community_id, request_id, building_id) as row:
        if row["state"] != "awaiting_confirmation":
            return {"state": row["state"], "result": row["result"]}
        command_type, submit = (
            (vnb_exchange.FormationSubmission, vnb_exchange.submit_formation)
            if row["kind"] == "formation"
            else (
                vnb_exchange.ParticipantMutationSubmission,
                vnb_exchange.submit_membership_mutation,
            )
        )
        command = command_type(**{**row["command"], "actor_building_id": building_id})
        preview = submit(command, dry_run=True)
        if (
            preview.payload_fingerprint != row["payload_fingerprint"]
            or preview.adapter_key != row["adapter_key"]
            or preview.canonical_payload != row["canonical_payload"]
            or preview.recipient != row["preview"]["recipient"]
            or preview.intended_action != row["preview"]["intended_action"]
        ):
            raise db.VnbSubmissionConflict("Confirmation changed; preview again")
        with operator_actor.confirmed_by(row["actor"], building_id):
            outcome = submit(command, cases=BoundCases(row))
        row["result"] = {
            key: getattr(outcome, key) for key in ("state", "case_id", "event_id")
        }
        row["state"] = "confirmed"
        row["confirmed_by"] = building_id
        return {"state": row["state"], "result": row["result"]}
