-- Project-owned schema; existing public tables are left untouched.
CREATE SCHEMA IF NOT EXISTS money_app;

CREATE TABLE IF NOT EXISTS money_app.stocks (
    ticker TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS money_app.daily_prices (
    ticker TEXT NOT NULL REFERENCES money_app.stocks(ticker),
    trade_date DATE NOT NULL,
    open DOUBLE PRECISION NOT NULL CHECK (open > 0),
    high DOUBLE PRECISION NOT NULL CHECK (high > 0),
    low DOUBLE PRECISION NOT NULL CHECK (low > 0),
    close DOUBLE PRECISION NOT NULL CHECK (close > 0),
    volume BIGINT NOT NULL CHECK (volume >= 0),
    source TEXT NOT NULL CHECK (source = 'yfinance'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, trade_date)
);

-- Track the downloaded date range so a 1-year download cannot serve a 5-year chart.
CREATE TABLE IF NOT EXISTS money_app.price_downloads (
    ticker TEXT NOT NULL REFERENCES money_app.stocks(ticker),
    history_period TEXT NOT NULL,
    first_date DATE NOT NULL,
    last_date DATE NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 30),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, history_period)
);

CREATE TABLE IF NOT EXISTS money_app.institutional_flows (
    ticker TEXT NOT NULL REFERENCES money_app.stocks(ticker),
    trade_date DATE NOT NULL,
    foreign_net BIGINT NOT NULL,
    trust_net BIGINT NOT NULL,
    dealer_net BIGINT NOT NULL,
    total_net BIGINT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('TWSE', 'TPEx')),
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (total_net = foreign_net + trust_net + dealer_net),
    PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE IF NOT EXISTS money_app.score_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker TEXT NOT NULL REFERENCES money_app.stocks(ticker),
    price_date DATE NOT NULL,
    model_version TEXT NOT NULL,
    score INTEGER NOT NULL,
    score_max INTEGER NOT NULL CHECK (score_max > 0),
    input_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (score >= 0 AND score <= score_max),
    UNIQUE (ticker, model_version, input_hash)
);
CREATE INDEX IF NOT EXISTS score_snapshots_stock_date
    ON money_app.score_snapshots (ticker, recorded_at DESC);

-- Retain each observed revision. report_date is a table date, NOT a company
-- announcement timestamp; first_seen_at is the earliest time our app knew it.
CREATE TABLE IF NOT EXISTS money_app.monthly_revenues (
    ticker TEXT NOT NULL REFERENCES money_app.stocks(ticker),
    revenue_month DATE NOT NULL CHECK (EXTRACT(DAY FROM revenue_month) = 1),
    report_date DATE NOT NULL,
    industry TEXT NOT NULL,
    revenue_thousand NUMERIC NOT NULL,
    previous_month_thousand NUMERIC,
    previous_year_thousand NUMERIC,
    yoy_pct DOUBLE PRECISION,
    mom_pct DOUBLE PRECISION,
    cumulative_yoy_pct DOUBLE PRECISION,
    source TEXT NOT NULL CHECK (source IN ('TWSE', 'TPEx')),
    input_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, revenue_month, input_hash)
);
CREATE INDEX IF NOT EXISTS monthly_revenue_industry
    ON money_app.monthly_revenues (industry, revenue_month);

CREATE TABLE IF NOT EXISTS money_app.score_outcomes (
    snapshot_id BIGINT NOT NULL REFERENCES money_app.score_snapshots(id),
    horizon INTEGER NOT NULL CHECK (horizon IN (5, 20, 60)),
    calculation_version TEXT NOT NULL,
    entry_date DATE NOT NULL,
    end_date DATE NOT NULL,
    return_pct DOUBLE PRECISION NOT NULL,
    lowest_close_return_pct DOUBLE PRECISION NOT NULL,
    price_source TEXT NOT NULL CHECK (price_source = 'yfinance'),
    input_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_id, horizon, calculation_version)
);

CREATE TABLE IF NOT EXISTS money_app.market_index_closes (
    symbol TEXT NOT NULL CHECK (symbol = 'TPEX'),
    trade_date DATE NOT NULL,
    close DOUBLE PRECISION NOT NULL CHECK (close > 0),
    source TEXT NOT NULL CHECK (source = 'TPEx'),
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS money_app.watchlist (
    ticker TEXT PRIMARY KEY REFERENCES money_app.stocks(ticker),
    added_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
