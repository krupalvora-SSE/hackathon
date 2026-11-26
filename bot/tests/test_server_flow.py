import asyncio

import server


def test_flow_outline_lists_sections_with_titled_states():
    out = asyncio.run(server.flow_outline())
    assert out["title"] and out["version"] and out["sections"]
    ids = [s for sec in out["sections"] for s in sec["states"]]
    assert "OFFER_PRIMARY" in ids and len(ids) == len(set(ids))      # every step in exactly one section
    assert all(out["states"][i]["title"] for i in ids)
    assert out["states"]["PRE_CALL"]["type"] == "system"
