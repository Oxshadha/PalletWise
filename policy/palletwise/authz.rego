# Authorization policy (agent spec 7). Slice 0: deny by default; rules arrive in Slice 1.
package palletwise.authz

import rego.v1

default allow := false
