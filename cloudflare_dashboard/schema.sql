CREATE TABLE IF NOT EXISTS dashboard_snapshot (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  payload TEXT NOT NULL,
  received_at TEXT NOT NULL,
  device_updated_at TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ingest_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_bytes INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ingest_log_received_at
  ON ingest_log(received_at DESC);
