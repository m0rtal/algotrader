CREATE TABLE IF NOT EXISTS instruments (
  ticker VARCHAR PRIMARY KEY,
  figi VARCHAR NOT NULL UNIQUE,
  class VARCHAR NOT NULL,
  name VARCHAR NOT NULL,
  currency VARCHAR NOT NULL,
  lot_size INTEGER NOT NULL,
  isin VARCHAR,
  sector VARCHAR,
  source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_instruments_class ON instruments(class);

CREATE TABLE IF NOT EXISTS pipeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phase VARCHAR NOT NULL,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP,
  rows_processed INTEGER DEFAULT 0,
  status VARCHAR NOT NULL DEFAULT 'ok',
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_started ON pipeline(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_phase ON pipeline(phase, started_at DESC);
