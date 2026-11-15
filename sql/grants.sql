-- The sweep job's service account logs in with IAM auth (deploy/deploy.sh
-- creates the IAM_BASED user). It writes findings and closes runs; nothing else.
DO $$ BEGIN
    CREATE ROLE zombie_writer NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA public TO zombie_writer;
GRANT SELECT, INSERT, UPDATE ON runs TO zombie_writer;
GRANT SELECT, INSERT, DELETE ON findings TO zombie_writer;
GRANT zombie_writer TO "zombie-sweep-job@comglitn.iam";

-- The agent's service account reads only (MCP Toolbox also sets readOnly).
GRANT zombie_reader TO "zombie-sweep-agent@comglitn.iam";
