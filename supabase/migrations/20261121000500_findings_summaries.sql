-- Phase 19: AI summary at the end of Findings.
-- The server counts the findings; the AI writes a few sentences from those counts. Each summary is kept with the
-- counts it was written from (so anyone can see what it was based on) and whether every number in it was checked.
-- Append-only and audited, like every other AI output on the platform.

CREATE TABLE public.findings_summaries (
  id            uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id        uuid NOT NULL,
  project_id    uuid NOT NULL,
  created_by    uuid NOT NULL REFERENCES auth.users (id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  language      text NOT NULL CHECK (language IN ('en', 'ha', 'yo', 'ig', 'pcm')),
  facts         jsonb NOT NULL,
  facts_sha256  text NOT NULL CHECK (facts_sha256 ~ '^[a-f0-9]{64}$'),
  summary       text NOT NULL CHECK (length(summary) BETWEEN 1 AND 4000),
  grounded      boolean NOT NULL,
  note          text CHECK (note IS NULL OR note IN ('ungrounded', 'wording', 'empty')),
  model         text,
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id)
);
CREATE INDEX findings_summaries_project_idx ON public.findings_summaries (project_id, language, created_at DESC);
CREATE INDEX findings_summaries_org_idx ON public.findings_summaries (org_id);

CREATE TRIGGER append_only_findings_summaries BEFORE UPDATE OR DELETE ON public.findings_summaries
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER append_only_truncate_findings_summaries BEFORE TRUNCATE ON public.findings_summaries
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER audit_findings_summaries AFTER INSERT ON public.findings_summaries
  FOR EACH ROW EXECUTE FUNCTION app.audit_row();

ALTER TABLE public.findings_summaries ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.findings_summaries TO authenticated;
GRANT ALL ON public.findings_summaries TO service_role;
CREATE POLICY findings_summaries_read ON public.findings_summaries FOR SELECT TO authenticated
  USING (app.has_role(org_id, ARRAY['admin', 'analyst', 'researcher', 'viewer', 'coordinator']));
CREATE POLICY findings_summaries_require_mfa ON public.findings_summaries AS RESTRICTIVE FOR ALL TO authenticated
  USING ((SELECT app.is_aal2())) WITH CHECK ((SELECT app.is_aal2()));
