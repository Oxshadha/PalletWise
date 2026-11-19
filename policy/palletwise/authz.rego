# Authorization policy (identity spec 6, agent spec 7).
#
# The role to scope map is policy data (data.json); the API sends only the verified role.
#
# `allow` is built only from positive conditions that must all hold, so a missing or
# malformed field can never produce an allow. `deny` explains a refusal for the audit log;
# `allow` additionally requires that no deny reason fired.
package palletwise.authz

import rego.v1

default allow := false

allow if {
	valid_input
	input.action.scope in data.palletwise.role_scopes[input.user.role]
	input.resource.tenant == input.user.tenant
	input.resource.site in input.user.sites
	agent_permits
	count(deny) == 0
}

decision := {"allow": allow, "reasons": sort([r | some r in deny])}

valid_input if {
	is_string(input.user.sub)
	is_string(input.user.role)
	is_string(input.user.tenant)
	is_array(input.user.sites)
	is_string(input.action.scope)
	is_string(input.resource.tenant)
	is_string(input.resource.site)
	agent_shape_valid
}

agent_acts if "agent" in object.keys(input)

agent_shape_valid if not agent_acts

agent_shape_valid if {
	is_string(input.agent.status)
	is_array(input.agent.scopes)
}

# A person acting directly needs no agent rules.
agent_permits if not agent_acts

agent_permits if {
	agent_acts
	input.agent.status == "ACTIVE"
	input.action.scope in input.agent.scopes
	not agent_forbidden
}

# Separation of duties: the copilot never approves, rejects or administers, whatever the
# registry says (identity spec 6, rule 4).
agent_forbidden if input.action.scope in data.palletwise.agent_forbidden_scopes

agent_forbidden if {
	some prefix in data.palletwise.agent_forbidden_scope_prefixes
	startswith(input.action.scope, prefix)
}

# ---------------------------------------------------------------- reasons (for the audit)

deny contains "invalid_input" if not valid_input

deny contains "unknown_role" if {
	valid_input
	not data.palletwise.role_scopes[input.user.role]
}

deny contains "role_lacks_scope" if {
	valid_input
	data.palletwise.role_scopes[input.user.role]
	not input.action.scope in data.palletwise.role_scopes[input.user.role]
}

deny contains "tenant_mismatch" if {
	valid_input
	input.resource.tenant != input.user.tenant
}

deny contains "site_not_allowed" if {
	valid_input
	not input.resource.site in input.user.sites
}

deny contains "agent_not_active" if {
	valid_input
	agent_acts
	input.agent.status != "ACTIVE"
}

deny contains "agent_lacks_scope" if {
	valid_input
	agent_acts
	not input.action.scope in input.agent.scopes
}

deny contains "agent_forbidden_scope" if {
	valid_input
	agent_acts
	agent_forbidden
}
