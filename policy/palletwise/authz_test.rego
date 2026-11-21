package palletwise.authz_test

import data.palletwise.authz
import rego.v1

tenant := "t-1"

site := "s-1"

person(role) := {"sub": "u-1", "role": role, "tenant": tenant, "sites": [site]}

copilot := {"id": "copilot", "version": "1", "status": "ACTIVE", "scopes": ["inventory.read", "orders.read", "proposal.create"]}

request(role, scope) := {
	"user": person(role),
	"action": {"name": "x", "scope": scope},
	"resource": {"tenant": tenant, "site": site},
}

by_agent(role, scope, agent) := object.union(request(role, scope), {"agent": agent})

reasons(inp) := r if {
	r := authz.decision.reasons with input as inp
}

# ---------------------------------------------------------------- every row of the scope table

manager_scopes := {"inventory.read", "orders.read", "exception.acknowledge", "simulation.control", "proposal.create", "proposal.approve", "proposal.reject"}

admin_scopes := {
	"inventory.read", "orders.read", "admin.users.read", "admin.agents.manage",
	"admin.settings.manage", "admin.audit.read", "admin.usage.read", "simulation.control",
}

all_scopes := manager_scopes | admin_scopes

test_manager_gets_exactly_its_scopes if {
	every scope in manager_scopes {
		authz.allow with input as request("warehouse_manager", scope)
	}
	every scope in all_scopes - manager_scopes {
		reasons(request("warehouse_manager", scope)) == ["role_lacks_scope"]
	}
}

test_admin_gets_exactly_its_scopes if {
	every scope in admin_scopes {
		authz.allow with input as request("admin", scope)
	}
	every scope in all_scopes - admin_scopes {
		reasons(request("admin", scope)) == ["role_lacks_scope"]
	}
}

# ---------------------------------------------------------------- every deny reason

test_default_deny if {
	not authz.allow with input as {}
	reasons({}) == ["invalid_input"]
}

test_every_missing_field_denies if {
	full := by_agent("warehouse_manager", "inventory.read", copilot)
	every path in [["user", "sub"], ["user", "role"], ["user", "tenant"], ["user", "sites"], ["action", "scope"], ["resource", "tenant"], ["resource", "site"], ["agent", "status"], ["agent", "scopes"]] {
		broken := json.remove(full, [concat("/", path)])
		not authz.allow with input as broken
		"invalid_input" in reasons(broken)
	}
}

test_wrong_types_deny if {
	not authz.allow with input as object.union(request("admin", "inventory.read"), {"user": object.union(person("admin"), {"sites": site})})
	not authz.allow with input as object.union(request("admin", "inventory.read"), {"action": {"scope": ["inventory.read"]}})
}

test_unknown_role if {
	reasons(request("superuser", "inventory.read")) == ["unknown_role"]
}

test_other_tenant if {
	inp := object.union(request("warehouse_manager", "inventory.read"), {"resource": {"tenant": "t-2", "site": site}})
	reasons(inp) == ["tenant_mismatch"]
}

test_site_not_in_user_sites if {
	inp := object.union(request("warehouse_manager", "inventory.read"), {"resource": {"tenant": tenant, "site": "s-2"}})
	reasons(inp) == ["site_not_allowed"]
}

test_empty_site_list_means_no_access if {
	inp := object.union(request("warehouse_manager", "inventory.read"), {"user": object.union(person("warehouse_manager"), {"sites": []})})
	reasons(inp) == ["site_not_allowed"]
}

test_agent_allowed_when_both_grant if {
	authz.allow with input as by_agent("warehouse_manager", "inventory.read", copilot)
}

test_agent_suspended if {
	agent := object.union(copilot, {"status": "SUSPENDED"})
	reasons(by_agent("warehouse_manager", "inventory.read", agent)) == ["agent_not_active"]
}

test_agent_lacks_scope_the_user_has if {
	agent := object.union(copilot, {"scopes": ["orders.read"]})
	reasons(by_agent("warehouse_manager", "inventory.read", agent)) == ["agent_lacks_scope"]
}

test_user_lacks_scope_the_agent_has if {
	# Agent scopes only remove rights: an admin's copilot cannot create proposals.
	reasons(by_agent("admin", "proposal.create", copilot)) == ["role_lacks_scope"]
}

test_agent_never_approves_or_rejects_even_if_registry_lists_it if {
	agent := object.union(copilot, {"scopes": ["proposal.approve", "proposal.reject"]})
	reasons(by_agent("warehouse_manager", "proposal.approve", agent)) == ["agent_forbidden_scope"]
	reasons(by_agent("warehouse_manager", "proposal.reject", agent)) == ["agent_forbidden_scope"]
}

test_agent_never_administers_even_if_registry_lists_it if {
	agent := object.union(copilot, {"scopes": ["admin.settings.manage"]})
	reasons(by_agent("admin", "admin.settings.manage", agent)) == ["agent_forbidden_scope"]
}

test_person_approves_directly if {
	authz.allow with input as request("warehouse_manager", "proposal.approve")
}

test_decision_shape if {
	authz.decision == {"allow": true, "reasons": []} with input as request("admin", "inventory.read")
}
