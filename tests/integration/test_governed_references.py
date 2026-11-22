"""Every governed eval case's reference query runs on the reference dataset and returns rows
(slice-5-acceptance step 5): a case can never silently compare two empty answers."""

import pytest
from test_copilot_api import Site
from test_copilot_api import ready_site as ready_site  # the 14-day reference site, seed 42

from evals.cases import load_set
from palletwise.governed import guard
from palletwise.governed import tool as governed

pytestmark = pytest.mark.integration

CASES = [c for c in load_set("governed")[0] if c.gold.reference_sql]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_the_reference_query_returns_rows(ready_site: Site, case: object) -> None:
    sql = case.gold.reference_sql  # type: ignore[attr-defined]
    names, rows, truncated, _ = governed.run(
        guard.check(sql).sql, ready_site.tenant, ready_site.site, "test", None
    )
    assert rows and names and not truncated, case.id  # type: ignore[attr-defined]
