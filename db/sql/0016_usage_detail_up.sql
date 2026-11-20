-- Usage detail per model call for the balanced gate (ADR 0001): which stage of the answer the call
-- served, and whether its tokens are an upper bound because the provider never reported usage
-- (a call cancelled or timed out after it was sent).

ALTER TABLE pw.model_usage
  ADD COLUMN stage text NOT NULL DEFAULT 'UNKNOWN'
    CHECK (stage IN ('TOOL_CHOICE', 'ANSWER_TEXT', 'WORDING', 'CLASSIFIER', 'UNKNOWN')),
  ADD COLUMN usage_estimated boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT model_usage_outcome CHECK (outcome IN ('OK', 'ERROR', 'CANCELLED'));

COMMENT ON COLUMN pw.model_usage.stage IS
  'TOOL_CHOICE: an agent call that chose tools; ANSWER_TEXT: an agent call that wrote the answer; '
  'WORDING: path A wording; CLASSIFIER: the input classifier; UNKNOWN: rows before ADR 0001.';
COMMENT ON COLUMN pw.model_usage.usage_estimated IS
  'True when the provider reported no usage and the tokens are the upper bound (input estimate plus '
  'the output cap), so cost is overstated, never understated.';
