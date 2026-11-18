# Golden dataset hashes

One file per dataset version and seed: `<dataset_version>-seed<seed>.json`, containing
`{"dataset_hash": "<sha256>"}`.

The simulation admin service compares the hash of every completed load with the golden file
for its seed. A mismatch marks the site `FAILED` (simulator spec 7.1). Regenerate and review the
golden file whenever the generator or the dataset version changes.
