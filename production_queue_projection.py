from __future__ import annotations

from durable_job_queue import DurableJobDatabase

_DEAD_LETTER_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS durable_queue_dead_letter_projection
AFTER UPDATE OF status ON durable_queue
WHEN NEW.status = 'dead_letter'
BEGIN
    UPDATE conversion_jobs
    SET payload_json = json_set(
            payload_json,
            '$.status', 'failed',
            '$.message', 'Konwersja wyczerpała dozwolone próby i wymaga interwencji.',
            '$.error', NEW.last_error,
            '$.error_code', 'durable_worker_dead_letter',
            '$.runtime_queue', json_patch(
                COALESCE(json_extract(payload_json, '$.runtime_queue'), '{}'),
                json_object(
                    'provider', 'sqlite-worker',
                    'status', 'dead_letter',
                    'attempt', NEW.attempt,
                    'max_attempts', NEW.max_attempts,
                    'last_error', NEW.last_error
                )
            ),
            '$.updated_at', NEW.updated_at
        ),
        updated_at = NEW.updated_at
    WHERE job_id = NEW.job_id;
END;
"""


def install_queue_state_projection(database: DurableJobDatabase) -> None:
    """Keep terminal queue state and public job state in one SQLite transaction."""

    with database.connect() as connection:
        connection.executescript(_DEAD_LETTER_TRIGGER)
