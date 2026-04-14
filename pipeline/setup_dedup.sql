-- dedup.db 初始化脚本
-- 用法: sqlite3 pipeline/dedup.db < pipeline/setup_dedup.sql

CREATE TABLE IF NOT EXISTS processed_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    source TEXT,
    processed_at TEXT DEFAULT (datetime('now')),
    type TEXT
);

CREATE INDEX IF NOT EXISTS idx_url_hash ON processed_urls(url_hash);

CREATE TABLE IF NOT EXISTS seen_urls (
    url_hash TEXT PRIMARY KEY,
    url TEXT,
    title TEXT,
    added_at TEXT
);
