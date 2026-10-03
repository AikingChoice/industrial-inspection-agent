"""PostgreSQL event storage for production inspection requests.

The application intentionally does not create or mutate the schema at startup.
Apply the reviewed SQL migration before deploying the service.
"""
import hashlib
import json
from typing import Optional

from config import DATABASE_URL


class EventConflict(Exception):
    """An event ID was reused with a different payload or is still running."""


class EventUnavailable(Exception):
    """The event cannot be safely replayed or persisted."""


def _connect():
    if not DATABASE_URL:
        raise EventUnavailable("Inspection storage is not configured")
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(
        DATABASE_URL,
        connect_timeout=5,
        row_factory=dict_row,
        options="-c statement_timeout=5000 -c lock_timeout=3000",
    )


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def verify_storage() -> None:
    """Fail startup/readiness when PostgreSQL or the applied schema is absent."""
    try:
        with _connect() as connection:
            connection.execute("SELECT event_id FROM inspection_events LIMIT 0")
            migration = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 1"
            ).fetchone()
            if not migration:
                raise EventUnavailable("Required database migration is not recorded")
    except Exception as exc:
        raise EventUnavailable("Inspection storage or required migration is unavailable") from exc


def claim_event(event_id: str, payload: dict) -> Optional[dict]:
    """Claim a unique source event, or return its persisted result on replay."""
    canonical_payload = _canonical_json(payload)
    payload_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    try:
        with _connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO inspection_events (event_id, payload_sha256, device_id, status, request_payload)
                VALUES (%s, %s, %s, 'processing', %s::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                (event_id, payload_hash, payload["device_id"], canonical_payload),
            ).fetchone()
            if inserted:
                return None

            existing = connection.execute(
                """
                SELECT payload_sha256, status, response_payload
                FROM inspection_events
                WHERE event_id = %s
                """,
                (event_id,),
            ).fetchone()
            if not existing or existing["payload_sha256"] != payload_hash:
                raise EventConflict("Event ID has already been used with a different payload")
            if existing["status"] == "completed":
                return existing["response_payload"]
            if existing["status"] == "processing":
                raise EventConflict("Event is already being processed")
            raise EventUnavailable("Event previously failed and requires operator reconciliation")
    except (EventConflict, EventUnavailable):
        raise
    except Exception as exc:
        raise EventUnavailable("Could not claim inspection event") from exc


def complete_event(event_id: str, response: dict) -> None:
    """Persist the response before returning it to the caller."""
    payload = _canonical_json(response)
    try:
        with _connect() as connection:
            result = connection.execute(
                """
                UPDATE inspection_events
                SET status = 'completed', response_payload = %s::jsonb, completed_at = now()
                WHERE event_id = %s AND status = 'processing'
                """,
                (payload, event_id),
            )
            if result.rowcount != 1:
                raise EventUnavailable("Inspection event is not in a completable state")
    except EventUnavailable:
        raise
    except Exception as exc:
        raise EventUnavailable("Could not persist inspection result") from exc


def fail_event(event_id: str, error_code: str) -> None:
    """Record a non-sensitive failure code; never store exception contents."""
    try:
        with _connect() as connection:
            connection.execute(
                """
                UPDATE inspection_events
                SET status = 'failed', error_code = %s, completed_at = now()
                WHERE event_id = %s AND status = 'processing'
                """,
                (error_code[:64], event_id),
            )
    except Exception:
        # Keep the original request failure as the primary signal. Monitoring
        # should also alert on failed persistence/health checks.
        return
