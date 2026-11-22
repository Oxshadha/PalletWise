# 0003. WMS connectors: one contract, declarative adapters

## Context

PalletWise reads every warehouse through the connector contract (simulator spec 2): ordered,
idempotent canonical records applied by the connector worker as `pw_ingest`. Warehouse systems
differ in every detail that matters for that: endpoints, authentication, paging, names, units and
status codes. Writing one adapter class per WMS makes each new warehouse a software project.

## Decision

1. **One interface, `WmsConnector`** (`palletwise.connectors.base`): `pull(cursor, limit)` returns
   canonical records oldest first with an opaque cursor; `health()` reports each stream. The
   records use exactly the record types and payload fields that `pw.apply_record` accepts.
2. **A registry of connector kinds** (`palletwise.connectors.build`): a configuration names its
   kind (`rest` first; change data capture and file drops register the same way).
3. **The REST connector is declarative.** One YAML mapping per WMS
   (`palletwise/connectors/mappings/`) states:
   - the base URL and the authentication, with the credential read from an environment variable;
   - the paging style (cursor or page number) and where the next cursor is;
   - per stream: the endpoint, where the items are, the item's id and change time, the parameter
     for incremental reads, and each canonical field's source path, type, code table and unit
     scale.
4. **Resilience in the connector, not the mapping:** rate limits and server errors are retried
   with backoff, honouring `Retry-After`; each stream keeps its own position in the cursor, so a
   restart resumes where it stopped.

## Consequences

- A WMS with a REST API plugs in with a mapping file; the semantic layer, the copilot, the map and
  the inbox stay as they are, because they only ever see canonical records.
- The WMS credential lives in the environment of the connector process only.
- Mappings are reviewed like code: they decide what the warehouse means to PalletWise.
- The sample mapping (`acme-wms.yaml`) and its tests show a different API (other names, grams,
  cubic centimetres, short status codes) becoming the same records the simulator produces.

## Status

Accepted by the product owner, 2026-11-22.
