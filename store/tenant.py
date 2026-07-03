# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tenant repository.

A Tenant maps a territory ``<territory>.openleg.ch`` to its white-label config.
Resolves the connection seam via ``database.get_connection`` at call time.
"""

import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def get_tenant_by_territory(territory: str) -> Optional[Dict]:
    """Get tenant config by territory slug."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM white_label_configs
                    WHERE territory = %s AND active = TRUE
                """,
                    (territory,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting tenant {territory}: {e}")
        return None


def get_all_active_tenants() -> List[Dict]:
    """Get all active tenant configs."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT territory, utility_name, primary_color, contact_email, active, config
                    FROM white_label_configs
                    WHERE active = TRUE
                    ORDER BY territory
                """)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting active tenants: {e}")
        return []


def upsert_tenant(territory: str, config: Dict) -> bool:
    """Insert or update a tenant config."""
    try:
        import json

        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO white_label_configs (
                        territory, utility_name, primary_color, secondary_color,
                        contact_email, contact_phone, legal_entity, dso_contact,
                        active, config
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (territory) DO UPDATE SET
                        utility_name = EXCLUDED.utility_name,
                        primary_color = EXCLUDED.primary_color,
                        secondary_color = EXCLUDED.secondary_color,
                        contact_email = EXCLUDED.contact_email,
                        contact_phone = EXCLUDED.contact_phone,
                        legal_entity = EXCLUDED.legal_entity,
                        dso_contact = EXCLUDED.dso_contact,
                        active = EXCLUDED.active,
                        config = EXCLUDED.config,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        territory,
                        config.get("utility_name", ""),
                        config.get("primary_color", "#c7021a"),
                        config.get("secondary_color", "#4338ca"),
                        config.get("contact_email", ""),
                        config.get("contact_phone", ""),
                        config.get("legal_entity", ""),
                        config.get("dso_contact", ""),
                        config.get("active", True),
                        json.dumps(
                            {
                                k: v
                                for k, v in config.items()
                                if k
                                not in (
                                    "utility_name",
                                    "primary_color",
                                    "secondary_color",
                                    "contact_email",
                                    "contact_phone",
                                    "legal_entity",
                                    "dso_contact",
                                    "active",
                                    "territory",
                                )
                            }
                        ),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error upserting tenant {territory}: {e}")
        return False


def seed_default_tenant() -> bool:
    """Seed the default Zurich tenant if it doesn't exist."""
    from tenant import DEFAULT_TENANT

    existing = get_tenant_by_territory("zurich")
    if existing:
        return True
    return upsert_tenant("zurich", DEFAULT_TENANT)
