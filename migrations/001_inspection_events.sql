BEGIN;

CREATE TABLE schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE inspection_events (
    event_id varchar(128) PRIMARY KEY,
    payload_sha256 char(64) NOT NULL,
    device_id varchar(128) NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    request_payload jsonb NOT NULL,
    response_payload jsonb,
    error_code varchar(64),
    received_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK (
        (status = 'completed' AND response_payload IS NOT NULL AND completed_at IS NOT NULL)
        OR (status IN ('processing', 'failed'))
    )
);

CREATE INDEX inspection_events_device_received_idx
    ON inspection_events (device_id, received_at DESC);

CREATE INDEX inspection_events_status_received_idx
    ON inspection_events (status, received_at);

INSERT INTO schema_migrations (version) VALUES (1);

COMMIT;
