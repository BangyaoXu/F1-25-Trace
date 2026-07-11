"""SQLite storage. Samples are stored per lap as a zlib-compressed,
column-oriented JSON blob — small on disk and served to the frontend
with a single decompress."""

import json
import sqlite3
import zlib

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL,
    started_at TEXT NOT NULL,
    packet_format INTEGER,
    game_year INTEGER,
    track_id INTEGER,
    track_name TEXT,
    session_type INTEGER,
    session_type_name TEXT,
    weather INTEGER,
    air_temp INTEGER,
    track_temp INTEGER,
    track_length INTEGER
);
CREATE TABLE IF NOT EXISTS laps (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    car_role TEXT NOT NULL,          -- player | pb_ghost | rival
    car_index INTEGER,
    lap_num INTEGER,
    lap_time_ms INTEGER,
    s1_ms INTEGER, s2_ms INTEGER, s3_ms INTEGER,
    valid INTEGER NOT NULL DEFAULT 1,
    tyre_visual INTEGER,
    top_speed INTEGER,
    n_samples INTEGER,
    created_at TEXT NOT NULL,
    samples BLOB
);
CREATE INDEX IF NOT EXISTS idx_laps_session ON laps(session_id);
"""


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def pack_samples(columns):
    return zlib.compress(json.dumps(columns, separators=(",", ":")).encode(), 6)


def unpack_samples(blob):
    return json.loads(zlib.decompress(blob).decode())
