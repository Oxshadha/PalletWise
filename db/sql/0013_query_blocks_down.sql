-- Reverses 0013: the view returns to its 0011 shape (a view cannot drop a column).
DROP FUNCTION IF EXISTS sem.last_move(uuid, timestamptz);
DROP FUNCTION IF EXISTS sem.last_pick_by_bin(uuid, timestamptz);
DROP VIEW sem.demand_event;
CREATE VIEW sem.demand_event AS
SELECT site_id, product_id, occurred_at,
       CASE WHEN movement_type = 'PICK' THEN qty ELSE -qty END AS qty
  FROM sem.movement
 WHERE NOT order_cancelled
   AND (movement_type = 'PICK' OR (movement_type = 'TRANSFER' AND reason_code = 'PICK_REVERSAL'));
REVOKE ALL ON sem.demand_event FROM PUBLIC;
GRANT SELECT ON sem.demand_event TO pw_agent_read;
