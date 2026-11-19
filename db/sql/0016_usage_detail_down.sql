ALTER TABLE pw.model_usage
  DROP CONSTRAINT IF EXISTS model_usage_outcome,
  DROP COLUMN IF EXISTS usage_estimated,
  DROP COLUMN IF EXISTS stage;
