package palletwise.authz_test

import data.palletwise.authz
import rego.v1

test_denies_by_default if {
	not authz.allow with input as {"user": {"role": "warehouse_manager"}, "action": {"scope": "inventory.read"}}
}

test_denies_empty_input if {
	not authz.allow with input as {}
}
