-- Data assistant (founder requirement, 19 Nov): every question and answer is kept, append-only.
CREATE TABLE public.assistant_queries (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id      uuid NOT NULL,
  project_id  uuid NOT NULL,
  user_id     uuid NOT NULL REFERENCES auth.users (id),
  question    text NOT NULL CHECK (length(btrim(question)) BETWEEN 1 AND 2000),
  answer      text NOT NULL,
  tools       jsonb NOT NULL DEFAULT '[]'::jsonb,
  citations   jsonb NOT NULL DEFAULT '[]'::jsonb,
  grounded    boolean NOT NULL,
  model       text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id)
);
CREATE INDEX assistant_queries_project_idx ON public.assistant_queries (project_id, created_at DESC);

ALTER TABLE public.assistant_queries ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.assistant_queries TO authenticated;
GRANT ALL ON public.assistant_queries TO service_role;
-- Admins and analysts of the organisation may read the log (the assistant is for them only).
CREATE POLICY assistant_queries_read ON public.assistant_queries FOR SELECT TO authenticated
  USING (app.has_role(org_id, ARRAY['admin', 'analyst']));
CREATE POLICY assistant_queries_require_mfa ON public.assistant_queries AS RESTRICTIVE FOR ALL TO authenticated
  USING (app.is_aal2()) WITH CHECK (app.is_aal2());

CREATE TRIGGER append_only_assistant_queries BEFORE UPDATE OR DELETE ON public.assistant_queries
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER append_only_truncate_assistant_queries BEFORE TRUNCATE ON public.assistant_queries
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();
