import json
import sys

path, needle = sys.argv[1], sys.argv[2]
hits = []
with open(path, encoding="utf-8") as handle:
    for line in handle:
        if needle not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") == "tool_use":
                command = (part.get("input") or {}).get("command", "")
                if needle in command:
                    hits.append(command)
for command in hits[-2:]:
    print(command)
    print("-----")
