import re

with open("backend/app/agent/tools/builtin.py", "r") as f:
    content = f.read()

# Fix the broken strings
content = content.replace('"WHEN: Need to search the internet for current or general information.\n', '"WHEN: Need to search the internet for current or general information.\\n"\n')
content = content.replace('"AVOID: Only using for biomedical data if a specialized wrapper is better.\n', '"AVOID: Only using for biomedical data if a specialized wrapper is better.\\n"\n')
content = content.replace('"CRITICAL_ARGS: query.\n', '"CRITICAL_ARGS: query.\\n"\n')
content = content.replace('"RETURNS: web search result list.\n', '"RETURNS: web search result list.\\n"\n')
content = content.replace('"FAILS_IF: query is missing."', '"FAILS_IF: query is missing."\n')

with open("backend/app/agent/tools/builtin.py", "w") as f:
    f.write(content)
