-- studio: single-writer schema.
-- Facts live in `takes`; decisions in `reviews`; audit in `events`; work in `jobs`.

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    style       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenes (
    project_id  TEXT NOT NULL,
    scene_id    TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    camera      TEXT NOT NULL DEFAULT '',
    props       TEXT NOT NULL DEFAULT '',
    light       TEXT NOT NULL DEFAULT '',
    plate_path  TEXT NOT NULL DEFAULT '',
    plate_sha   TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, scene_id)
);

CREATE TABLE IF NOT EXISTS characters (
    character_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    refs_json    TEXT NOT NULL DEFAULT '[]',
    primary_ref  TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shots (
    project_id   TEXT NOT NULL,
    shot_id      TEXT NOT NULL,
    ordinal      INTEGER NOT NULL,
    scene_id     TEXT NOT NULL DEFAULT '',
    cast_json    TEXT NOT NULL DEFAULT '[]',
    camera       TEXT NOT NULL DEFAULT '',
    prompt       TEXT NOT NULL DEFAULT '',
    dialogue     TEXT NOT NULL DEFAULT '',
    sound        TEXT NOT NULL DEFAULT '',
    transition   TEXT NOT NULL DEFAULT 'cut',
    anchor       TEXT NOT NULL DEFAULT '',
    first_frame  TEXT NOT NULL DEFAULT '',
    duration     REAL NOT NULL DEFAULT 5.0,
    fingerprint  TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (project_id, shot_id)
);

CREATE TABLE IF NOT EXISTS takes (
    project_id  TEXT NOT NULL,
    shot_id     TEXT NOT NULL,
    version     INTEGER NOT NULL,
    path        TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    elapsed_s   REAL NOT NULL DEFAULT 0,
    peak_mib    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, shot_id, version)
);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    shot_id     TEXT NOT NULL,
    version     INTEGER NOT NULL,
    verdict     TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS previews (
    project_id  TEXT NOT NULL,
    shot_id     TEXT NOT NULL,
    path        TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, shot_id)
);

CREATE TABLE IF NOT EXISTS asset_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,              -- scene | character
    owner_id    TEXT NOT NULL,              -- scene_id or character_id
    project_id  TEXT NOT NULL DEFAULT '',
    version     INTEGER NOT NULL,
    path        TEXT NOT NULL,
    sha         TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_asset_versions ON asset_versions(kind, owner_id, version DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    state       TEXT NOT NULL DEFAULT 'queued',
    error       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    started_at  TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    shot_id     TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shots_ord    ON shots(project_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_takes_shot   ON takes(project_id, shot_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_state   ON jobs(state, created_at);
CREATE INDEX IF NOT EXISTS idx_events_proj  ON events(project_id, id DESC);
