import re

with open("backend/app/agent/tools/builtin.py", "r") as f:
    content = f.read()

# Let's replace the tool_web_search_mock function
new_func = """
import httpx
from bs4 import BeautifulSoup

def tool_web_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if not query:
        raise ValueError("'query' is required")
        
    url = "https://lite.duckduckgo.com/lite/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    data = {"q": query}
    try:
        resp = httpx.post(url, headers=headers, data=data, timeout=10.0)
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for tr in soup.find_all("tr"):
            td = tr.find("td", class_="result-snippet")
            if td:
                snippet = td.get_text(" ", strip=True)
                prev_tr = tr.find_previous_sibling("tr")
                a_tag = prev_tr.find("a", class_="result-link") if prev_tr else None
                if not a_tag:
                    prev_prev_tr = prev_tr.find_previous_sibling("tr") if prev_tr else None
                    a_tag = prev_prev_tr.find("a", class_="result-link") if prev_prev_tr else None
                if a_tag:
                    results.append({
                        "title": a_tag.get_text(strip=True),
                        "url": a_tag.get("href"),
                        "snippet": snippet
                    })
    except Exception as e:
        results = [{"title": "Error", "url": "", "snippet": str(e)}]

    return make_tool_output(
        source="builtin",
        summary=f"Performed web search for {query}.",
        data={
            "query": query,
            "results": results[:5],
        },
        ids=[],
        citations=[],
        ctx=ctx,
    )
"""

content = re.sub(
    r'def tool_web_search_mock\(payload.*?return make_tool_output\([^)]+\)',
    new_func.strip(),
    content,
    flags=re.DOTALL
)

# Now replace the ToolSpec
new_spec = """
        ToolSpec(
            name="web_search",
            description=(
                "WHEN: Need to search the internet for current or general information.\\n"
                "AVOID: Only using for biomedical data if a specialized wrapper is better.\\n"
                "CRITICAL_ARGS: query.\\n"
                "RETURNS: web search result list.\\n"
                "FAILS_IF: query is missing."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=tool_web_search,
            source="builtin",
        ),
"""

content = re.sub(
    r'ToolSpec\(\s*name="web_search_mock".*?source="builtin",\s*\),',
    new_spec.strip(),
    content,
    flags=re.DOTALL
)

with open("backend/app/agent/tools/builtin.py", "w") as f:
    f.write(content)

