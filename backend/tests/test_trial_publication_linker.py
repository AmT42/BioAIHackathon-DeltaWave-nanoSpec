from __future__ import annotations

from dataclasses import replace

from app.config import get_settings
from app.agent.tools.sources.trials import build_trial_tools


class FakeHttp:
    def get_json(self, *, url, params=None, headers=None):
        if "esearch.fcgi" in url:
            return ({"esearchresult": {"idlist": []}}, {})
        if "api.openalex.org/works" in url:
            return ({"results": []}, {})
        raise AssertionError(f"Unhandled URL {url}")


def _tool(specs, name: str):
    return next(spec for spec in specs if spec.name == name)


def test_trial_publication_linker_flags_completed_without_publications() -> None:
    settings = replace(get_settings(), openalex_api_key="test-key")
    tools = build_trial_tools(settings, FakeHttp())

    output = _tool(tools, "trial_publication_linker").handler(
        {
            "ids": ["NCT01234567"],
            "mode": "balanced",
            "trials": [
                {
                    "nct_id": "NCT01234567",
                    "overall_status": "COMPLETED",
                    "has_results": False,
                }
            ],
        },
        None,
    )

    link = output["data"]["links"][0]
    assert link["nct_id"] == "NCT01234567"
    assert link["flag"] == "completed_but_unpublished_possible"
