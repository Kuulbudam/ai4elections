-- Server-side support tables for the FastAPI service (Phase 2). Private schema `app`: not exposed
-- through the Data API and not granted to anon/authenticated.

-- Fixed-window rate limiting shared by all serverless instances.
CREATE TABLE app.rate_limits (
  key           text        NOT NULL,
  window_start  timestamptz NOT NULL,
  hits          integer     NOT NULL DEFAULT 0,
  PRIMARY KEY (key, window_start)
);

CREATE OR REPLACE FUNCTION app.rate_limit_hit(p_key text, p_limit integer, p_window_seconds integer)
RETURNS boolean LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE
  v_window timestamptz := to_timestamp(floor(extract(epoch FROM now()) / p_window_seconds) * p_window_seconds);
  v_hits   integer;
BEGIN
  INSERT INTO app.rate_limits (key, window_start, hits) VALUES (p_key, v_window, 1)
  ON CONFLICT (key, window_start) DO UPDATE SET hits = app.rate_limits.hits + 1
  RETURNING hits INTO v_hits;
  DELETE FROM app.rate_limits WHERE key = p_key AND window_start < v_window - interval '1 hour';
  RETURN v_hits <= p_limit;
END $$;

-- Idempotency-Key replay store (per user + key).
CREATE TABLE app.idempotency_keys (
  user_id       uuid        NOT NULL,
  key           text        NOT NULL,
  endpoint      text        NOT NULL,
  request_hash  text        NOT NULL,
  status_code   integer     NOT NULL,
  response      jsonb       NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

REVOKE ALL ON app.rate_limits, app.idempotency_keys FROM PUBLIC;
GRANT ALL ON app.rate_limits, app.idempotency_keys TO service_role;
REVOKE ALL ON FUNCTION app.rate_limit_hit(text, integer, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.rate_limit_hit(text, integer, integer) TO service_role;
