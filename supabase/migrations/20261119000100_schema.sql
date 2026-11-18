-- AI4Elections schema v1 (Phase 1).
-- Every tenant-owned table carries org_id NOT NULL and UNIQUE (org_id, id); children reference
-- parents with composite foreign keys (org_id, parent_id) so a row can never point into another
-- organisation. Security (grants, RLS) is in 20261119000300_security.sql.

CREATE SCHEMA IF NOT EXISTS app;          -- private helpers; not exposed through the Data API
REVOKE ALL ON SCHEMA app FROM PUBLIC;

-- ---------------------------------------------------------------- tenancy

CREATE TABLE public.organisations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 120),
  created_by  uuid NOT NULL REFERENCES auth.users (id),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.profiles (
  id            uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  email         text,
  display_name  text CHECK (display_name IS NULL OR length(display_name) <= 120),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.memberships (
  id          uuid NOT NULL DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organisations (id),
  user_id     uuid NOT NULL REFERENCES auth.users (id),
  role        text NOT NULL CHECK (role IN ('admin', 'analyst', 'researcher', 'viewer')),
  created_by  uuid REFERENCES auth.users (id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id),
  UNIQUE (id)
);
CREATE INDEX memberships_user_idx ON public.memberships (user_id);

-- ---------------------------------------------------------------- projects and data

CREATE TABLE public.projects (
  id              uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id          uuid NOT NULL REFERENCES public.organisations (id),
  election_name   text NOT NULL CHECK (length(btrim(election_name)) BETWEEN 2 AND 200),
  election_year   integer NOT NULL CHECK (election_year BETWEEN 1900 AND 2100),
  country         text NOT NULL CHECK (length(btrim(country)) BETWEEN 2 AND 100),
  election_level  text NOT NULL CHECK (election_level IN
                    ('presidential', 'governorship', 'senate', 'house_of_representatives',
                     'state_assembly', 'local_government', 'other')),
  description     text CHECK (description IS NULL OR length(description) <= 4000),
  archived_at     timestamptz,
  created_by      uuid NOT NULL REFERENCES auth.users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id)
);

CREATE TABLE public.data_sources (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id       uuid NOT NULL,
  project_id   uuid NOT NULL,
  source_type  text NOT NULL CHECK (source_type IN ('synthetic', 'official_publication', 'observer_collation', 'other')),
  description  text NOT NULL CHECK (length(description) BETWEEN 1 AND 2000),
  created_by   uuid NOT NULL REFERENCES auth.users (id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id)
);

CREATE TABLE public.datasets (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id      uuid NOT NULL,
  project_id  uuid NOT NULL,
  name        text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
  created_by  uuid NOT NULL REFERENCES auth.users (id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, project_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id)
);

CREATE TABLE public.dataset_versions (
  id                       uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id                   uuid NOT NULL,
  project_id               uuid NOT NULL,
  dataset_id               uuid NOT NULL,
  data_source_id           uuid NOT NULL,
  version_number           integer NOT NULL CHECK (version_number >= 1),
  status                   text NOT NULL DEFAULT 'awaiting_upload' CHECK (status IN
                             ('awaiting_upload', 'uploaded', 'ingesting', 'ingested', 'ingest_failed')),
  storage_path             text NOT NULL,
  original_filename        text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  content_type             text NOT NULL,
  declared_size_bytes      bigint NOT NULL CHECK (declared_size_bytes BETWEEN 1 AND 26214400),
  size_bytes               bigint CHECK (size_bytes IS NULL OR size_bytes BETWEEN 1 AND 26214400),
  sha256                   text CHECK (sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'),
  duplicate_of_version_id  uuid,
  row_count                integer CHECK (row_count IS NULL OR row_count >= 0),
  header                   jsonb,
  mapping                  jsonb,
  mapping_saved_at         timestamptz,
  uploaded_by              uuid NOT NULL REFERENCES auth.users (id),
  created_at               timestamptz NOT NULL DEFAULT now(),
  finalized_at             timestamptz,
  archived_at              timestamptz,
  archived_by              uuid REFERENCES auth.users (id),
  UNIQUE (org_id, id),
  UNIQUE (dataset_id, version_number),
  UNIQUE (storage_path),
  FOREIGN KEY (org_id, project_id, dataset_id) REFERENCES public.datasets (org_id, project_id, id),
  FOREIGN KEY (org_id, data_source_id) REFERENCES public.data_sources (org_id, id),
  FOREIGN KEY (org_id, duplicate_of_version_id) REFERENCES public.dataset_versions (org_id, id),
  CHECK (storage_path = org_id::text || '/' || project_id::text || '/' || dataset_id::text || '/' || id::text || '.csv')
);

CREATE TABLE public.raw_rows (
  id                  uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id              uuid NOT NULL,
  dataset_version_id  uuid NOT NULL,
  source_row_number   integer NOT NULL CHECK (source_row_number >= 1),
  cells               jsonb NOT NULL,
  UNIQUE (org_id, id),
  UNIQUE (dataset_version_id, source_row_number),
  FOREIGN KEY (org_id, dataset_version_id) REFERENCES public.dataset_versions (org_id, id)
);

CREATE TABLE public.result_records (
  id                  uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id              uuid NOT NULL,
  dataset_version_id  uuid NOT NULL,
  raw_row_id          uuid NOT NULL,
  schema_version      text NOT NULL,
  pu_code             text,
  state               text,
  lga                 text,
  ward                text,
  registered_voters   integer,
  accredited_voters   integer,
  valid_votes         integer,
  rejected_votes      integer,
  total_cast          integer,
  party_votes         jsonb NOT NULL DEFAULT '{}'::jsonb,
  latitude            double precision,
  longitude           double precision,
  row_status          text NOT NULL DEFAULT 'eligible' CHECK (row_status IN
                        ('eligible', 'eligible_with_warnings', 'quarantined_from_specific_analysis')),
  excluded_from       text[] NOT NULL DEFAULT '{}',
  exclusion_reasons   jsonb NOT NULL DEFAULT '[]'::jsonb,
  UNIQUE (org_id, id),
  UNIQUE (dataset_version_id, raw_row_id),
  FOREIGN KEY (org_id, dataset_version_id) REFERENCES public.dataset_versions (org_id, id),
  FOREIGN KEY (org_id, raw_row_id) REFERENCES public.raw_rows (org_id, id)
);
CREATE INDEX result_records_geo_idx ON public.result_records (dataset_version_id, state, lga, ward);

CREATE TABLE public.mapping_templates (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES public.organisations (id),
  name        text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  mapping     jsonb NOT NULL,
  created_by  uuid NOT NULL REFERENCES auth.users (id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, name)
);

CREATE TABLE public.admin_units (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id      uuid NOT NULL,
  project_id  uuid NOT NULL,
  state       text NOT NULL,
  lga         text NOT NULL,
  ward        text NOT NULL,
  created_by  uuid NOT NULL REFERENCES auth.users (id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (project_id, state, lga, ward),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id)
);

-- Global, versioned rule catalogue (not tenant data). Seeded in Phase 3.
CREATE TABLE public.validation_rules (
  rule_id     text NOT NULL CHECK (rule_id ~ '^V[0-9]{3}$'),
  version     text NOT NULL,
  definition  jsonb NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (rule_id, version)
);

-- ---------------------------------------------------------------- jobs and analysis

CREATE TABLE public.processing_jobs (
  id                 uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id             uuid NOT NULL,
  project_id         uuid NOT NULL,
  job_type           text NOT NULL CHECK (job_type IN ('ingest', 'validate', 'analyse')),
  target_id          uuid NOT NULL,
  input_fingerprint  text NOT NULL,
  payload            jsonb NOT NULL DEFAULT '{}'::jsonb,
  status             text NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  progress           real NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
  attempt_count      integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  max_attempts       integer NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
  created_by         uuid NOT NULL REFERENCES auth.users (id),
  idempotency_key    text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  started_at         timestamptz,
  finished_at        timestamptz,
  heartbeat_at       timestamptz,
  worker_id          text,
  error_code         text,
  retryable          boolean,
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  FOREIGN KEY (org_id, target_id) REFERENCES public.dataset_versions (org_id, id)
);
CREATE UNIQUE INDEX processing_jobs_live_unique ON public.processing_jobs (job_type, target_id, input_fingerprint)
  WHERE status IN ('queued', 'running');
CREATE INDEX processing_jobs_queued_idx ON public.processing_jobs (created_at) WHERE status = 'queued';

CREATE TABLE public.analytical_runs (
  id                  uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id              uuid NOT NULL,
  project_id          uuid NOT NULL,
  dataset_version_id  uuid NOT NULL,
  job_id              uuid NOT NULL,
  engine_version      text NOT NULL,
  method_version      text NOT NULL,
  rule_versions       jsonb NOT NULL DEFAULT '{}'::jsonb,
  parameters          jsonb NOT NULL DEFAULT '{}'::jsonb,
  status              text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'complete', 'failed')),
  content_hash        text CHECK (content_hash IS NULL OR content_hash ~ '^[a-f0-9]{64}$'),
  started_at          timestamptz NOT NULL DEFAULT now(),
  completed_at        timestamptz,
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  FOREIGN KEY (org_id, dataset_version_id) REFERENCES public.dataset_versions (org_id, id),
  FOREIGN KEY (org_id, job_id) REFERENCES public.processing_jobs (org_id, id),
  CHECK ((status = 'complete') = (completed_at IS NOT NULL AND content_hash IS NOT NULL))
);

CREATE TABLE public.validation_issues (
  id                  uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id              uuid NOT NULL,
  dataset_version_id  uuid NOT NULL,
  job_id              uuid NOT NULL,
  raw_row_id          uuid,
  rule_id             text NOT NULL,
  rule_version        text NOT NULL,
  severity            text NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
  source_row_number   integer,
  source_column       text,
  original_value      text,
  message             text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, dataset_version_id) REFERENCES public.dataset_versions (org_id, id),
  FOREIGN KEY (org_id, job_id) REFERENCES public.processing_jobs (org_id, id),
  FOREIGN KEY (org_id, raw_row_id) REFERENCES public.raw_rows (org_id, id)
);
CREATE INDEX validation_issues_version_idx ON public.validation_issues (dataset_version_id, rule_id);

CREATE TABLE public.findings (
  id                  uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id              uuid NOT NULL,
  project_id          uuid NOT NULL,
  dataset_version_id  uuid NOT NULL,
  analytical_run_id   uuid,
  category            text NOT NULL CHECK (category IN ('observation', 'data_quality_issue', 'statistical_anomaly')),
  indicator           text CHECK (indicator IS NULL OR indicator IN ('turnout_rate', 'leading_party_share')),
  rule_id             text,
  raw_row_id          uuid,
  pu_code             text,
  state               text,
  lga                 text,
  ward                text,
  severity            text NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'very_high')),
  confidence          text NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
  title               text NOT NULL,
  explanation         text NOT NULL,
  limitations         text NOT NULL,
  peer_group          jsonb,
  statistics          jsonb,
  exclusions          jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence            jsonb NOT NULL DEFAULT '[]'::jsonb,
  provenance          jsonb NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  FOREIGN KEY (org_id, dataset_version_id) REFERENCES public.dataset_versions (org_id, id),
  FOREIGN KEY (org_id, analytical_run_id) REFERENCES public.analytical_runs (org_id, id),
  FOREIGN KEY (org_id, raw_row_id) REFERENCES public.raw_rows (org_id, id),
  CHECK (title !~* 'fraud|rigged' AND explanation !~* 'fraud|rigged')
);
CREATE INDEX findings_project_idx ON public.findings (project_id, category, severity);

CREATE TABLE public.finding_reviews (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id       uuid NOT NULL,
  finding_id   uuid NOT NULL,
  decision     text NOT NULL CHECK (decision IN
                 ('acknowledged', 'needs_more_evidence', 'explained_by_data_error', 'explained_by_context', 'escalated')),
  reason       text NOT NULL CHECK (length(btrim(reason)) BETWEEN 10 AND 4000),
  reviewer_id  uuid NOT NULL REFERENCES auth.users (id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, finding_id) REFERENCES public.findings (org_id, id)
);
CREATE INDEX finding_reviews_finding_idx ON public.finding_reviews (finding_id, created_at);

CREATE TABLE public.investigation_notes (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id      uuid NOT NULL,
  finding_id  uuid NOT NULL,
  body        text NOT NULL CHECK (length(btrim(body)) BETWEEN 1 AND 4000),
  author_id   uuid NOT NULL REFERENCES auth.users (id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, finding_id) REFERENCES public.findings (org_id, id)
);

CREATE TABLE public.claims (
  id                   uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id               uuid NOT NULL,
  project_id           uuid NOT NULL,
  finding_id           uuid,
  statement            text NOT NULL CHECK (length(btrim(statement)) BETWEEN 10 AND 4000),
  status               text NOT NULL DEFAULT 'unverified_allegation'
                         CHECK (status IN ('unverified_allegation', 'verified_fact')),
  created_by           uuid NOT NULL REFERENCES auth.users (id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  verified_by          uuid REFERENCES auth.users (id),
  verified_at          timestamptz,
  verification_reason  text,
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  FOREIGN KEY (org_id, finding_id) REFERENCES public.findings (org_id, id),
  CHECK (
    (status = 'unverified_allegation' AND verified_by IS NULL AND verified_at IS NULL)
    OR (status = 'verified_fact' AND verified_by IS NOT NULL AND verified_by <> created_by
        AND verified_at IS NOT NULL AND length(btrim(coalesce(verification_reason, ''))) >= 10)
  )
);

CREATE TABLE public.claim_evidence (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id       uuid NOT NULL,
  claim_id     uuid NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('document_reference', 'link', 'file')),
  description  text NOT NULL CHECK (length(btrim(description)) BETWEEN 1 AND 2000),
  url          text CHECK (url IS NULL OR url ~* '^https?://'),
  file_sha256  text CHECK (file_sha256 IS NULL OR file_sha256 ~ '^[a-f0-9]{64}$'),
  added_by     uuid NOT NULL REFERENCES auth.users (id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, claim_id) REFERENCES public.claims (org_id, id),
  CHECK (kind <> 'link' OR url IS NOT NULL),
  CHECK (kind <> 'file' OR file_sha256 IS NOT NULL)
);

CREATE TABLE public.reports (
  id                  uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id              uuid NOT NULL,
  project_id          uuid NOT NULL,
  analytical_run_id   uuid NOT NULL,
  dataset_version_id  uuid NOT NULL,
  title               text,
  body                jsonb NOT NULL,
  content_hash        text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  audit_event_id      uuid,
  audit_event_hash    text,
  generated_by        uuid NOT NULL REFERENCES auth.users (id),
  generated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  FOREIGN KEY (org_id, analytical_run_id) REFERENCES public.analytical_runs (org_id, id),
  FOREIGN KEY (org_id, dataset_version_id) REFERENCES public.dataset_versions (org_id, id)
);
