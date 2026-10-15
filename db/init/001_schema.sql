-- İlk hafta sabitlenen şema. Değişiklik gerekirse ekibe duyurulur.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS buildings (
    building_id   INT PRIMARY KEY,
    primary_use   TEXT,
    square_feet   DOUBLE PRECISION,
    year_built    INT
);

CREATE TABLE IF NOT EXISTS readings (
    building_id   INT NOT NULL,
    ts            TIMESTAMPTZ NOT NULL,
    kwh           DOUBLE PRECISION,
    air_temp      DOUBLE PRECISION,
    is_weekend    BOOLEAN,
    PRIMARY KEY (building_id, ts)
);
SELECT create_hypertable('readings', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS anomalies (
    id            BIGSERIAL PRIMARY KEY,
    building_id   INT NOT NULL,
    ts_start      TIMESTAMPTZ NOT NULL,
    ts_end        TIMESTAMPTZ NOT NULL,
    score         DOUBLE PRECISION,
    source        TEXT CHECK (source IN ('rule', 'ml_sup', 'ml_unsup')),
    rule_type     TEXT,
    excess_kwh    DOUBLE PRECISION,
    est_cost_tl   DOUBLE PRECISION,
    explanation   TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_anomalies_building_ts ON anomalies (building_id, ts_start);
