-- One key per resource, so the two sources can be compared: (project, kind, name).
-- zombiescan stores a short name and its own resource_type; Recommender stores a
-- full resource name such as //compute.googleapis.com/projects/p/zones/z/instances/w3.

CREATE OR REPLACE VIEW findings_keyed AS
SELECT f.*,
       CASE
         WHEN f.source = 'zombiescan' THEN f.resource_type
         WHEN f.resource LIKE '//sqladmin.googleapis.com/%' OR f.resource LIKE '//cloudsql.googleapis.com/%' THEN 'sql-instance'
         WHEN f.resource LIKE '//container.googleapis.com/%' THEN 'gke-cluster'
         ELSE CASE split_part(f.resource, '/', -2)
                WHEN 'instances'    THEN 'compute-instance'
                WHEN 'disks'        THEN 'compute-disk'
                WHEN 'addresses'    THEN 'compute-address'
                WHEN 'images'       THEN 'compute-image'
                WHEN 'reservations' THEN 'compute-reservation'
                ELSE split_part(f.resource, '/', -2)
              END
       END AS kind,
       CASE WHEN f.source = 'zombiescan' THEN split_part(f.resource, '/', -1)
            ELSE split_part(f.resource, '/', -1) END AS name
FROM findings f;

-- Each finding with whether the other source flagged the same resource.
CREATE OR REPLACE VIEW findings_compared AS
SELECT k.*,
       EXISTS (SELECT 1 FROM findings_keyed o
               WHERE o.run_id = k.run_id AND o.source <> k.source
                 AND o.project = k.project AND o.kind = k.kind AND o.name = k.name) AS found_by_both
FROM findings_keyed k;

GRANT SELECT ON findings_keyed, findings_compared TO zombie_reader;
