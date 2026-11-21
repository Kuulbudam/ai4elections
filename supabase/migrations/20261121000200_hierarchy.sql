-- Phase 15: hierarchy. Two new roles and area assignments.
--   coordinator  - responsible for a state, LGA or ward: receives escalations for that area, resolves or sends up.
--   field_agent  - an observer at one or more polling units: sends field reports (Phase 16) for their units only.
-- Escalations go PU agent -> ward -> LGA -> state coordinator -> admins (first level that has someone assigned).

ALTER TABLE public.memberships DROP CONSTRAINT IF EXISTS memberships_role_check;
ALTER TABLE public.memberships ADD CONSTRAINT memberships_role_check
  CHECK (role IN ('admin', 'analyst', 'researcher', 'viewer', 'coordinator', 'field_agent'));

CREATE TABLE public.area_assignments (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES public.organisations (id),
  user_id     uuid NOT NULL REFERENCES auth.users (id),
  level       text NOT NULL CHECK (level IN ('state', 'lga', 'ward', 'polling_unit')),
  state       text NOT NULL CHECK (length(btrim(state)) BETWEEN 1 AND 120),
  lga         text CHECK (lga IS NULL OR length(btrim(lga)) BETWEEN 1 AND 120),
  ward        text CHECK (ward IS NULL OR length(btrim(ward)) BETWEEN 1 AND 120),
  pu_code     text CHECK (pu_code IS NULL OR length(btrim(pu_code)) BETWEEN 1 AND 60),
  created_by  uuid NOT NULL REFERENCES auth.users (id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  -- each level names exactly the parts it needs
  CHECK (
    (level = 'state' AND lga IS NULL AND ward IS NULL AND pu_code IS NULL) OR
    (level = 'lga' AND lga IS NOT NULL AND ward IS NULL AND pu_code IS NULL) OR
    (level = 'ward' AND lga IS NOT NULL AND ward IS NOT NULL AND pu_code IS NULL) OR
    (level = 'polling_unit' AND lga IS NOT NULL AND ward IS NOT NULL AND pu_code IS NOT NULL)
  )
);
CREATE UNIQUE INDEX area_assignments_unique ON public.area_assignments
  (org_id, user_id, level, lower(state), lower(coalesce(lga, '')), lower(coalesce(ward, '')), lower(coalesce(pu_code, '')));
CREATE INDEX area_assignments_org_level_idx ON public.area_assignments (org_id, level);
CREATE INDEX area_assignments_user_idx ON public.area_assignments (user_id);

-- A coordinator holds state / LGA / ward areas; a field agent holds polling units.
CREATE OR REPLACE FUNCTION app.assignment_matches_role() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE v_role text;
BEGIN
  SELECT role INTO v_role FROM public.memberships WHERE org_id = NEW.org_id AND user_id = NEW.user_id;
  IF v_role IS NULL THEN
    RAISE EXCEPTION 'this person is not a member of the organisation' USING ERRCODE = '23514';
  END IF;
  IF NEW.level = 'polling_unit' AND v_role <> 'field_agent' THEN
    RAISE EXCEPTION 'only field agents are assigned to polling units' USING ERRCODE = '23514';
  END IF;
  IF NEW.level <> 'polling_unit' AND v_role <> 'coordinator' THEN
    RAISE EXCEPTION 'only coordinators are assigned to a state, LGA or ward' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER assignment_matches_role BEFORE INSERT ON public.area_assignments
  FOR EACH ROW EXECUTE FUNCTION app.assignment_matches_role();

-- Assignments can be removed (people move) but not edited; both are audited.
CREATE OR REPLACE FUNCTION app.forbid_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  RAISE EXCEPTION 'rows in %.% cannot be edited; remove and add again', TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = '42501';
END $$;
CREATE TRIGGER area_assignments_no_update BEFORE UPDATE ON public.area_assignments
  FOR EACH ROW EXECUTE FUNCTION app.forbid_update();
CREATE TRIGGER audit_area_assignments AFTER INSERT OR DELETE ON public.area_assignments
  FOR EACH ROW EXECUTE FUNCTION app.audit_row();

-- Removing someone from the organisation also removes their areas.
CREATE OR REPLACE FUNCTION app.drop_assignments_with_membership() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF TG_OP = 'DELETE' OR NEW.role IS DISTINCT FROM OLD.role THEN
    DELETE FROM public.area_assignments WHERE org_id = OLD.org_id AND user_id = OLD.user_id;
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER memberships_drop_assignments AFTER UPDATE OF role OR DELETE ON public.memberships
  FOR EACH ROW EXECUTE FUNCTION app.drop_assignments_with_membership();

ALTER TABLE public.area_assignments ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, DELETE ON public.area_assignments TO authenticated;
GRANT ALL ON public.area_assignments TO service_role;
CREATE POLICY area_assignments_member_read ON public.area_assignments FOR SELECT TO authenticated
  USING (org_id IN (SELECT app.my_org_ids()));
CREATE POLICY area_assignments_admin_insert ON public.area_assignments FOR INSERT TO authenticated
  WITH CHECK (app.has_role(org_id, ARRAY['admin']) AND created_by = (SELECT auth.uid()));
CREATE POLICY area_assignments_admin_delete ON public.area_assignments FOR DELETE TO authenticated
  USING (app.has_role(org_id, ARRAY['admin']));
CREATE POLICY area_assignments_require_mfa ON public.area_assignments AS RESTRICTIVE FOR ALL TO authenticated
  USING ((SELECT app.is_aal2())) WITH CHECK ((SELECT app.is_aal2()));
