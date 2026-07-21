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
