-- Exceptions API (Slice 3 step 3, slice-3-acceptance 2.4, CWM 11.3). Runs as pw_owner.
--
-- Reading: the exception tables through `sem` views for pw_agent_read, the role every site read
-- of the API and the copilot uses. Writing: pw_app gains one function, acknowledge_exception(),
-- and no table privilege.

CREATE VIEW sem.exception AS
SELECT x.id AS exception_id, x.site_id, x.exception_type, x.subject_key, x.product_id, x.zone_id,
       x.order_id, x.status, x.critical, x.score, x.score_components, x.settings_version_id,
       x.first_detected_at, x.last_evaluated_at, x.acknowledged_by, x.acknowledged_at
  FROM pw.exception_record x;

CREATE VIEW sem.exception_bin AS
SELECT b.exception_id, b.site_id, b.bin_id FROM pw.exception_bin b;

CREATE VIEW sem.exception_order AS
SELECT o.exception_id, o.site_id, o.order_id, o.order_no FROM pw.exception_order o;

-- Acknowledges one exception for the Manager `p_subject` (FR-EXC-04, 2.4 rule 1). The status
-- becomes ACKNOWLEDGED; the score and the record stay, and the engine keeps re-evaluating it.
-- Recorded at the site clock, with an ACKNOWLEDGED event in the history. Idempotent by state:
-- acknowledging an acknowledged record changes nothing. Outcomes: 'acknowledged', 'unchanged',
-- 'resolved' (nothing to acknowledge) or 'not_found' (no such record at this site for the
-- caller's tenant: row-level security applies to this function too).
CREATE FUNCTION pw.acknowledge_exception(p_site uuid, p_exception uuid, p_subject text)
RETURNS TABLE (outcome text, acknowledged_by text, acknowledged_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  r pw.exception_record;
  v_now timestamptz;
BEGIN
  IF coalesce(p_subject, '') = '' THEN
    RAISE EXCEPTION 'an acknowledgement needs the user' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO r FROM pw.exception_record
   WHERE id = p_exception AND site_id = p_site FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found', NULL::text, NULL::timestamptz;
    RETURN;
  END IF;
  IF r.status = 'RESOLVED' THEN
    RETURN QUERY SELECT 'resolved', r.acknowledged_by, r.acknowledged_at;
    RETURN;
  END IF;
  IF r.status = 'ACKNOWLEDGED' THEN
    RETURN QUERY SELECT 'unchanged', r.acknowledged_by, r.acknowledged_at;
    RETURN;
  END IF;
  SELECT c.sim_time INTO STRICT v_now FROM pw.site_clock c WHERE c.site_id = p_site;
  UPDATE pw.exception_record
     SET status = 'ACKNOWLEDGED', acknowledged_by = p_subject, acknowledged_at = v_now,
         row_version = row_version + 1
   WHERE id = r.id;
  INSERT INTO pw.exception_evaluation (tenant_id, site_id, exception_id, evaluated_at, event, status,
                                       score, score_components, settings_version_id)
  VALUES (r.tenant_id, r.site_id, r.id, v_now, 'ACKNOWLEDGED', 'ACKNOWLEDGED', r.score,
          r.score_components, r.settings_version_id);
  RETURN QUERY SELECT 'acknowledged', p_subject, v_now;
END $$;

REVOKE ALL ON sem.exception, sem.exception_bin, sem.exception_order FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pw.acknowledge_exception(uuid, uuid, text) FROM PUBLIC;
GRANT SELECT ON sem.exception, sem.exception_bin, sem.exception_order TO pw_agent_read;
GRANT EXECUTE ON FUNCTION pw.acknowledge_exception(uuid, uuid, text) TO pw_app;
