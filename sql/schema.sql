-- Zombie Sweep for Google Cloud: findings store on AlloyDB Express (PostgreSQL 18).

CREATE TABLE IF NOT EXISTS runs (
    run_id        text PRIMARY KEY,           -- Workflows execution id
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'RUNNING',
    projects      text[] NOT NULL DEFAULT '{}',
    briefing      text                         -- written by the agent step
);

CREATE TABLE IF NOT EXISTS findings (
    run_id         text NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    source         text NOT NULL CHECK (source IN ('zombiescan', 'recommender')),
    project        text NOT NULL,
    location       text NOT NULL DEFAULT 'global',
    check_id       text NOT NULL,              -- zombiescan check, or Recommender ID
    resource       text NOT NULL,              -- full resource name
    resource_type  text,
    monthly_cost   numeric(12, 4) NOT NULL DEFAULT 0,
    approximate    boolean NOT NULL DEFAULT false,
    reason         text,
    PRIMARY KEY (run_id, source, project, check_id, resource)
);

CREATE INDEX IF NOT EXISTS findings_resource ON findings (resource);
CREATE INDEX IF NOT EXISTS findings_run_cost ON findings (run_id, monthly_cost DESC);

-- Read-only role for the agent's SQL tools (MCP Toolbox). Login is granted
-- separately; this role only reads.
DO $$ BEGIN
    CREATE ROLE zombie_reader NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA public TO zombie_reader;
GRANT SELECT ON runs, findings TO zombie_reader;
