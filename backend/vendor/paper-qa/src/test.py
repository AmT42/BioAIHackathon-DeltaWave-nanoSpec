import asyncio, httpx
from types import SimpleNamespace
from paperqa.clients.open_access_resolver import OpenAccessResolver

async def main():
    client = httpx.AsyncClient()
    resolver = OpenAccessResolver(client)
    settings = SimpleNamespace(agent=SimpleNamespace(http_timeout_s=30.0))
    try:
        result = await resolver._fetch_pdf_headless("https://doi.org/10.1111/tri.13783", settings=settings)
        print("RESULT:", result)
    finally:
        await client.aclose()

asyncio.run(main())