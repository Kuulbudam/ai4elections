-- Phase 14: escalation and notifications.
-- A review decision "Escalate" (stored as 'escalated') raises an escalation. Its history is append-only: each raise,
-- send-up, resolve or comment is an escalation_events row; the current status and level come from those events.
-- Notifications are a per-user inbox; a person can only read their own and mark them read.

CREATE TABLE public.escalations (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id       uuid NOT NULL,
  project_id   uuid NOT NULL,
  source_kind  text NOT NULL CHECK (source_kind IN ('finding', 'field_report')),
  source_id    uuid NOT NULL,
  title        text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
  area         jsonb NOT NULL DEFAULT '{}'::jsonb,
  raised_by    uuid NOT NULL REFERENCES auth.users (id),
  reason       text NOT NULL CHECK (length(reason) BETWEEN 1 AND 4000),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id)
);
CREATE INDEX escalations_project_idx ON public.escalations (project_id, created_at DESC);
CREATE INDEX escalations_source_idx ON public.escalations (source_id);
CREATE INDEX escalations_org_idx ON public.escalations (org_id);

CREATE TABLE public.escalation_events (
  id             uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id         uuid NOT NULL,
  escalation_id  uuid NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('raised', 'sent_up', 'resolved', 'comment')),
  level          text NOT NULL CHECK (level IN ('ward', 'lga', 'state', 'admin')),
  actor_id       uuid NOT NULL REFERENCES auth.users (id),
  note           text NOT NULL CHECK (length(note) BETWEEN 1 AND 4000),
  created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (org_id, escalation_id) REFERENCES public.escalations (org_id, id)
);
CREATE INDEX escalation_events_escalation_idx ON public.escalation_events (escalation_id, created_at);
CREATE INDEX escalation_events_org_idx ON public.escalation_events (org_id);

-- One resolution per escalation: once resolved it stays resolved (a new issue is a new escalation).
CREATE UNIQUE INDEX escalation_events_one_resolution ON public.escalation_events (escalation_id) WHERE kind = 'resolved';

CREATE OR REPLACE FUNCTION app.escalation_event_allowed() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF NEW.kind <> 'raised' AND NOT EXISTS (
       SELECT 1 FROM public.escalation_events WHERE escalation_id = NEW.escalation_id AND kind = 'raised') THEN
    RAISE EXCEPTION 'an escalation must be raised first' USING ERRCODE = '23514';
  END IF;
  IF NEW.kind IN ('sent_up', 'resolved') AND EXISTS (
       SELECT 1 FROM public.escalation_events WHERE escalation_id = NEW.escalation_id AND kind = 'resolved') THEN
    RAISE EXCEPTION 'this escalation is already resolved' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER escalation_event_allowed BEFORE INSERT ON public.escalation_events
  FOR EACH ROW EXECUTE FUNCTION app.escalation_event_allowed();

CREATE TABLE public.notifications (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES public.organisations (id),
  user_id     uuid NOT NULL REFERENCES auth.users (id),
  kind        text NOT NULL CHECK (kind IN ('escalation_raised', 'escalation_sent_up', 'escalation_resolved', 'incident')),
  title       text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
  body        text NOT NULL DEFAULT '' CHECK (length(body) <= 2000),
  link        text NOT NULL CHECK (link ~ '^/[A-Za-z0-9/_?=&%-]*$'),
  created_at  timestamptz NOT NULL DEFAULT now(),
  read_at     timestamptz
);
CREATE INDEX notifications_user_idx ON public.notifications (user_id, created_at DESC);
CREATE INDEX notifications_org_idx ON public.notifications (org_id);

-- Only read_at may change, and only from empty to a time.
CREATE OR REPLACE FUNCTION app.notification_mark_read_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF (NEW.id, NEW.org_id, NEW.user_id, NEW.kind, NEW.title, NEW.body, NEW.link, NEW.created_at)
     IS DISTINCT FROM (OLD.id, OLD.org_id, OLD.user_id, OLD.kind, OLD.title, OLD.body, OLD.link, OLD.created_at)
     OR OLD.read_at IS NOT NULL OR NEW.read_at IS NULL THEN
    RAISE EXCEPTION 'notifications can only be marked as read' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER notification_mark_read_only BEFORE UPDATE ON public.notifications
  FOR EACH ROW EXECUTE FUNCTION app.notification_mark_read_only();

-- Escalations and their events are evidence: never edited or deleted, and audited.
CREATE TRIGGER append_only_escalations BEFORE UPDATE OR DELETE ON public.escalations
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER append_only_truncate_escalations BEFORE TRUNCATE ON public.escalations
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER audit_escalations AFTER INSERT ON public.escalations
  FOR EACH ROW EXECUTE FUNCTION app.audit_row();
CREATE TRIGGER append_only_escalation_events BEFORE UPDATE OR DELETE ON public.escalation_events
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER append_only_truncate_escalation_events BEFORE TRUNCATE ON public.escalation_events
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER audit_escalation_events AFTER INSERT ON public.escalation_events
  FOR EACH ROW EXECUTE FUNCTION app.audit_row();

-- Current state of each escalation, from its events.
CREATE OR REPLACE VIEW public.escalation_overview WITH (security_invoker = true) AS
SELECT e.id, e.org_id, e.project_id, e.source_kind, e.source_id, e.title, e.area, e.raised_by, e.reason, e.created_at,
       last.level AS current_level,
       CASE WHEN res.id IS NULL THEN 'open' ELSE 'resolved' END AS status,
       res.created_at AS resolved_at, res.actor_id AS resolved_by, res.note AS resolution,
       last.created_at AS updated_at
FROM public.escalations e
LEFT JOIN LATERAL (
  SELECT level, created_at FROM public.escalation_events ev
  WHERE ev.escalation_id = e.id AND ev.kind IN ('raised', 'sent_up', 'resolved')
  ORDER BY ev.created_at DESC LIMIT 1) last ON true
LEFT JOIN public.escalation_events res ON res.escalation_id = e.id AND res.kind = 'resolved';

ALTER TABLE public.escalations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.escalation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.escalations, public.escalation_events, public.escalation_overview TO authenticated;
GRANT SELECT, UPDATE (read_at) ON public.notifications TO authenticated;
GRANT ALL ON public.escalations, public.escalation_events, public.notifications TO service_role;
GRANT SELECT ON public.escalation_overview TO service_role;

CREATE POLICY escalations_member_read ON public.escalations FOR SELECT TO authenticated
  USING (org_id IN (SELECT app.my_org_ids()));
CREATE POLICY escalation_events_member_read ON public.escalation_events FOR SELECT TO authenticated
  USING (org_id IN (SELECT app.my_org_ids()));
CREATE POLICY notifications_own_read ON public.notifications FOR SELECT TO authenticated
  USING (user_id = (SELECT auth.uid()));
CREATE POLICY notifications_own_mark_read ON public.notifications FOR UPDATE TO authenticated
  USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY escalations_require_mfa ON public.escalations AS RESTRICTIVE FOR ALL TO authenticated
  USING ((SELECT app.is_aal2())) WITH CHECK ((SELECT app.is_aal2()));
CREATE POLICY escalation_events_require_mfa ON public.escalation_events AS RESTRICTIVE FOR ALL TO authenticated
  USING ((SELECT app.is_aal2())) WITH CHECK ((SELECT app.is_aal2()));
CREATE POLICY notifications_require_mfa ON public.notifications AS RESTRICTIVE FOR ALL TO authenticated
  USING ((SELECT app.is_aal2())) WITH CHECK ((SELECT app.is_aal2()));
