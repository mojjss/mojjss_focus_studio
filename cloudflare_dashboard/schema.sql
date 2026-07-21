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

-- v5.5: cloud-owned timer state. The row with id=1 is the current timer.
CREATE TABLE IF NOT EXISTS cloud_timer (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  session_id TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'idle',
  task TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'Research',
  mode TEXT NOT NULL DEFAULT 'Focus',
  counts_toward_focus INTEGER NOT NULL DEFAULT 1,
  duration_seconds INTEGER NOT NULL DEFAULT 1500,
  elapsed_seconds INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  running_since TEXT,
  updated_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'cloud',
  timezone TEXT NOT NULL DEFAULT 'UTC'
);

CREATE TABLE IF NOT EXISTS cloud_sessions (
  id TEXT PRIMARY KEY,
  task TEXT NOT NULL,
  category TEXT NOT NULL,
  mode TEXT NOT NULL,
  counts_toward_focus INTEGER NOT NULL DEFAULT 1,
  started_at_utc TEXT NOT NULL,
  ended_at_utc TEXT NOT NULL,
  local_date TEXT NOT NULL,
  planned_minutes INTEGER NOT NULL DEFAULT 0,
  minutes INTEGER NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'cloud',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cloud_sessions_date
  ON cloud_sessions(local_date DESC, ended_at_utc DESC);

CREATE TABLE IF NOT EXISTS cloud_schedule (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  start TEXT NOT NULL,
  end TEXT NOT NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'General',
  notes TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  deleted INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'cloud'
);

CREATE INDEX IF NOT EXISTS idx_cloud_schedule_date
  ON cloud_schedule(date, start);

CREATE TABLE IF NOT EXISTS desktop_sync_status (
  device_id TEXT PRIMARY KEY,
  last_seen_at TEXT NOT NULL,
  app_version TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT ''
);

-- Focus Studio remote photo mode (run once in the same D1 database)
CREATE TABLE IF NOT EXISTS camera_requests (
    request_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    proof TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    message TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_camera_requests_status_time
ON camera_requests(status, requested_at);

CREATE TABLE IF NOT EXISTS camera_frames (
    request_id TEXT PRIMARY KEY,
    image_base64 TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(request_id) REFERENCES camera_requests(request_id) ON DELETE CASCADE
);

