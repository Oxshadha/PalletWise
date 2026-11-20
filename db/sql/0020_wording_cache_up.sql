-- Answer-wording cache (agent spec 14b, Slice 2 D4, step 7c). It sits after authorization and after
-- the tool has run on current data: a hit saves the small model's wording call, nothing else. The
-- key is a hash of tenant, site, tool, normalized arguments, the hash of what the model saw of the
-- result, the normalized question, prompt, semantic model, model, template, settings and cache
-- versions, locale and units; it carries no authorization fingerprint (D4). Only wording that
-- passed grounding is stored.

CREATE TABLE pw.wording_cache (
  tenant_id    uuid NOT NULL REFERENCES pw.tenant (id),
  site_id      uuid NOT NULL,
  key_hash     text NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
  tool         text NOT NULL,
  wording      text NOT NULL CHECK (length(wording) BETWEEN 1 AND 2000),
  created_at   timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  hits         integer NOT NULL DEFAULT 0 CHECK (hits >= 0),
  last_used_at timestamptz,
  PRIMARY KEY (tenant_id, key_hash),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);
CREATE INDEX wording_cache_expiry ON pw.wording_cache (tenant_id, expires_at);

ALTER TABLE pw.wording_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.wording_cache FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pw.wording_cache
  USING (tenant_id = (SELECT pw.current_tenant()))
  WITH CHECK (tenant_id = (SELECT pw.current_tenant()));

-- pw_cache: the wording cache only (AGENTS.md rule 7: its own role for its own write path).
GRANT USAGE ON SCHEMA pw TO pw_cache;
GRANT SELECT, INSERT, UPDATE, DELETE ON pw.wording_cache TO pw_cache;
GRANT EXECUTE ON FUNCTION pw.current_tenant() TO pw_cache;
