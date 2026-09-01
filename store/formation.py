# SPDX-License-Identifier: AGPL-3.0-or-later
"""Formation repository.

Owns the LEG formation persistence: communities, community members, and the
consent-gated neighbour search. Formation rules and status assembly live in
`formation_wizard`.
"""

import logging
import uuid

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def _track_event(event_type, building_id=None, data=None):
    import database

    database.track_event(event_type, building_id, data)


def create_community_record(
    name: str, admin_building_id: str, distribution_model: str, description: str
) -> str | None:
    """Insert a community and its admin member; return the community id."""
    try:
        community_id = str(uuid.uuid4())

        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO communities (
                        community_id, name, admin_building_id, distribution_model,
                        description, status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                    (
                        community_id,
                        name,
                        admin_building_id,
                        distribution_model,
                        description,
                        "interested",
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO community_members (
                        community_id, building_id, role, status, joined_at
                    ) VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                    (community_id, admin_building_id, "admin", "confirmed"),
                )

                logger.info(
                    f"[FORMATION] Created community {community_id} by {admin_building_id}"
                )

                return community_id
    except Exception as e:
        logger.error(f"[FORMATION] Error creating community: {e}")
        return None


def insert_invited_member(community_id: str, building_id: str, invited_by: str) -> bool:
    """Insert an invited member unless the building already belongs to it."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM community_members
                    WHERE community_id = %s AND building_id = %s
                """,
                    (community_id, building_id),
                )

                if cur.fetchone():
                    logger.warning(
                        f"[FORMATION] Building {building_id} already in community {community_id}"
                    )
                    return False

                cur.execute(
                    """
                    INSERT INTO community_members (
                        community_id, building_id, role, status, invited_by, joined_at
                    ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                    (community_id, building_id, "member", "invited", invited_by),
                )

                _track_event(
                    "member_invited",
                    building_id,
                    {"community_id": community_id, "invited_by": invited_by},
                )

                logger.info(
                    f"[FORMATION] Invited {building_id} to community {community_id}"
                )
                return True
    except Exception as e:
        logger.error(f"[FORMATION] Error inviting member: {e}")
        return False


def confirm_invited_member(community_id: str, building_id: str) -> bool:
    """Confirm an invited membership; False when no open invitation exists."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE community_members
                    SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP
                    WHERE community_id = %s AND building_id = %s AND status = 'invited'
                """,
                (community_id, building_id),
            )

            if cur.rowcount > 0:
                _track_event(
                    "member_confirmed", building_id, {"community_id": community_id}
                )
                logger.info(
                    f"[FORMATION] {building_id} confirmed membership in {community_id}"
                )
                return True
            return False
    except Exception as e:
        logger.error(f"[FORMATION] Error confirming membership: {e}")
        return False


def count_confirmed_members(community_id: str) -> int | None:
    """Count confirmed members; None when the count cannot be read."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) as count FROM community_members
                    WHERE community_id = %s AND status = 'confirmed'
                """,
                    (community_id,),
                )
                return cur.fetchone()["count"]
    except Exception as e:
        logger.error(f"[FORMATION] Error counting confirmed members: {e}")
        return None


def mark_formation_started(community_id: str) -> bool:
    """Set the formation_started status and timestamp."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE communities
                    SET status = %s, formation_started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE community_id = %s
                """,
                    ("formation_started", community_id),
                )
                _track_event("formation_started", None, {"community_id": community_id})
                logger.info(
                    f"[FORMATION] Started formation for community {community_id}"
                )
                return True
    except Exception as e:
        logger.error(f"[FORMATION] Error starting formation: {e}")
        return False


def submit_community_to_dso(community_id: str) -> bool:
    """Move a signatures-pending community to dso_submitted."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE communities
                    SET status = %s, dso_submitted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE community_id = %s AND status = %s
                """,
                    ("dso_submitted", community_id, "signatures_pending"),
                )

                if cur.rowcount > 0:
                    _track_event("dso_submitted", None, {"community_id": community_id})
                    logger.info(
                        f"[FORMATION] Submitted DSO notification for community {community_id}"
                    )
                    return True
                return False
    except Exception as e:
        logger.error(f"[FORMATION] Error submitting to DSO: {e}")
        return False


def fetch_community_with_members(community_id: str) -> dict | None:
    """Read one community row with its aggregated members."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT 
                        c.*,
                        array_agg(
                            jsonb_build_object(
                                'building_id', cm.building_id,
                                'role', cm.role,
                                'status', cm.status,
                                'email', b.email,
                                'address', b.address,
                                'confirmed_at', cm.confirmed_at
                            ) ORDER BY cm.joined_at
                        ) FILTER (WHERE cm.building_id IS NOT NULL) as members
                    FROM communities c
                    LEFT JOIN community_members cm ON c.community_id = cm.community_id
                    LEFT JOIN buildings b ON cm.building_id = b.building_id
                    WHERE c.community_id = %s
                    GROUP BY c.community_id
                """,
                (community_id,),
            )
            return cur.fetchone()
    except Exception as e:
        logger.error(f"[FORMATION] Error getting community status: {e}")
        return None


def fetch_user_communities(building_id: str) -> list[dict] | None:
    """Read the communities one building belongs to."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 
                        c.community_id,
                        c.name,
                        c.status,
                        c.distribution_model,
                        cm.role,
                        cm.status as member_status,
                        (SELECT COUNT(*) FROM community_members WHERE community_id = c.community_id) as member_count
                    FROM communities c
                    JOIN community_members cm ON c.community_id = cm.community_id
                    WHERE cm.building_id = %s
                    ORDER BY c.created_at DESC
                """,
                    (building_id,),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[FORMATION] Error getting user communities: {e}")
        return None


def fetch_nearby_consenting_neighbours(
    building_id: str, radius_meters: int
) -> list[dict] | None:
    """Read consenting, verified neighbours near one building.

    Returns None when the building itself has no location; the consent gate is
    part of the query, not the caller's responsibility.
    """
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT lat, lon FROM buildings WHERE building_id = %s
                """,
                (building_id,),
            )

            user = cur.fetchone()
            if not user:
                return None

            cur.execute(
                """
                    SELECT 
                        b.building_id,
                        b.address,
                        b.email,
                        b.lat,
                        b.lon,
                        (6371000 * acos(
                            cos(radians(%s)) * cos(radians(b.lat)) *
                            cos(radians(b.lon) - radians(%s)) +
                            sin(radians(%s)) * sin(radians(b.lat))
                        )) as distance
                    FROM buildings b
                    INNER JOIN consents c ON b.building_id = c.building_id
                    WHERE b.verified = TRUE
                    AND c.share_with_neighbors = TRUE
                    AND b.building_id != %s
                    AND NOT EXISTS (
                        SELECT 1 FROM community_members cm
                        WHERE cm.building_id = b.building_id
                        AND cm.status IN ('confirmed', 'invited')
                    )
                    HAVING distance <= %s
                    ORDER BY distance
                """,
                (user["lat"], user["lon"], user["lat"], building_id, radius_meters),
            )

            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[FORMATION] Error getting formable clusters: {e}")
        return None
