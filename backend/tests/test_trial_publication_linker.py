from __future__ import annotations

from dataclasses import replace

from app.config import get_settings
from app.agent.tools.sources.trials import build_trial_tools


class FakeHttp:
    def get_json(self, *, url, params=None, headers=None):
        term = str((params or {}).get("term") or "")
        if "esearch.fcgi" in url:
            if "NCT09999999" in term:
                raise Exception("simulated upstream failure")
            return ({"esearchresult": {"idlist": []}}, {})
        if "api.openalex.org/works" in url:
            return ({"results": []}, {})
        raise AssertionError(f"Unhandled URL {url}")


def _tool(specs, name: str):
    return next(spec for spec in specs if spec.name == name)


def test_trial_publication_linker_flags_possible_unpublished_completed_trials() -> None:
    settings = replace(get_settings(), openalex_api_key="test-key")

    class Http:
        def get_json(self, *, url, params=None, headers=None):
            if "esearch.fcgi" in url:
                return ({"esearchresult": {"idlist": []}}, {})
            if "api.openalex.org/works" in url:
                return ({"results": []}, {})
            raise AssertionError(f"Unhandled URL {url}")

    tools = build_trial_tools(settings, Http())

    output = _tool(tools, "trial_publication_linker").handler(
        {
            "nct_ids": ["NCT01234567"],
            "trials": [
                {
                    "nct_id": "NCT01234567",
                    "overall_status": "COMPLETED",
                    "completion_date": "2020-01-01",
                    "has_results": False,
                }
            ],
            "evidence_age_days": 365,
        },
        None,
    )

    link = output["data"]["links"][0]
    assert link["nct_id"] == "NCT01234567"
    assert link["flag"] == "possible_unpublished_completed_trial"


def test_trial_publication_linker_returns_partial_results_on_per_nct_failure() -> None:
    settings = replace(get_settings(), openalex_api_key=None)

    class PartialFailHttp:
        def get_json(self, *, url, params=None, headers=None):
            term = str((params or {}).get("term") or "")
            if "esearch.fcgi" in url:
                if "NCT09999999" in term and "[si]" in term:
                    from app.agent.tools.errors import ToolExecutionError

                    raise ToolExecutionError(code="UPSTREAM_ERROR", message="boom")
                return ({"esearchresult": {"idlist": ["12345"] if "NCT01234567" in term else []}}, {})
            raise AssertionError(f"Unhandled URL {url}")

    tools = build_trial_tools(settings, PartialFailHttp())
    output = _tool(tools, "trial_publication_linker").handler(
        {
            "nct_ids": ["NCT01234567", "NCT09999999"],
            "trials": [
                {
                    "nct_id": "NCT01234567",
                    "overall_status": "COMPLETED",
                    "completion_date": "2024-01-01",
                    "has_results": False,
                },
                {
                    "nct_id": "NCT09999999",
                    "overall_status": "COMPLETED",
                    "completion_date": "2020-01-01",
                    "has_results": False,
                },
            ],
            "evidence_age_days": 365,
        },
        None,
    )

    assert len(output["data"]["links"]) == 2
    second = next(item for item in output["data"]["links"] if item["nct_id"] == "NCT09999999")
    assert second["warnings"]
    assert any("NCT09999999" in warn for warn in output["warnings"])
