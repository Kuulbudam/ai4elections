-- Phase 5: findings dashboard, review, notes, claims.

-- One row per finding with its current review status (latest decision; history stays in
-- finding_reviews) and note / claim counts. security_invoker: the caller's RLS applies, including
-- membership and two-step verification on every base table.
CREATE OR REPLACE VIEW public.finding_overview WITH (security_invoker = true) AS
SELECT f.id, f.org_id, f.project_id, f.dataset_version_id, f.analytical_run_id, f.category, f.indicator,
       f.rule_id, f.raw_row_id, f.pu_code, f.state, f.lga, f.ward, f.severity, f.confidence, f.title,
       f.statistics, f.peer_group, f.created_at,
       coalesce(r.decision, 'unreviewed') AS review_status,
       r.created_at AS reviewed_at,
       (SELECT count(*) FROM public.investigation_notes n WHERE n.finding_id = f.id) AS note_count,
       (SELECT count(*) FROM public.claims c WHERE c.finding_id = f.id) AS claim_count
FROM public.findings f
LEFT JOIN LATERAL (
  SELECT fr.decision, fr.created_at FROM public.finding_reviews fr
  WHERE fr.finding_id = f.id ORDER BY fr.created_at DESC, fr.id DESC LIMIT 1
) r ON true;

GRANT SELECT ON public.finding_overview TO authenticated, service_role;

CREATE INDEX IF NOT EXISTS finding_reviews_finding_idx ON public.finding_reviews (finding_id, created_at DESC);
CREATE INDEX IF NOT EXISTS investigation_notes_finding_idx ON public.investigation_notes (finding_id, created_at);
CREATE INDEX IF NOT EXISTS claims_finding_idx ON public.claims (finding_id);
CREATE INDEX IF NOT EXISTS claims_project_idx ON public.claims (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS claim_evidence_claim_idx ON public.claim_evidence (claim_id, created_at);
CREATE INDEX IF NOT EXISTS audit_events_entity_idx ON public.audit_events (org_id, entity_id);
CREATE INDEX IF NOT EXISTS findings_geo_idx ON public.findings (analytical_run_id, state, lga, ward);

-- A claim may only become a verified fact when at least one evidence item is attached
-- (the separate-person and reason rules are table CHECKs). Enforced for every writer.
CREATE OR REPLACE FUNCTION app.claim_requires_evidence() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF NEW.status = 'verified_fact' AND OLD.status IS DISTINCT FROM 'verified_fact'
     AND NOT EXISTS (SELECT 1 FROM public.claim_evidence e WHERE e.claim_id = NEW.id AND e.org_id = NEW.org_id) THEN
    RAISE EXCEPTION 'evidence_required: attach independent evidence before verifying a claim'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER claim_requires_evidence BEFORE UPDATE ON public.claims
  FOR EACH ROW EXECUTE FUNCTION app.claim_requires_evidence();

-- A claim attached to a finding must be in the finding's project.
CREATE OR REPLACE FUNCTION app.claim_finding_project() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF NEW.finding_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM public.findings f WHERE f.id = NEW.finding_id AND f.project_id = NEW.project_id) THEN
    RAISE EXCEPTION 'mismatch: the finding is not in this project' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER claim_finding_project BEFORE INSERT ON public.claims
  FOR EACH ROW EXECUTE FUNCTION app.claim_finding_project();
