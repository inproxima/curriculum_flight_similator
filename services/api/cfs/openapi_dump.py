import json
import sys

from cfs.main import app

path = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
with open(path, "w") as f:
    json.dump(app.openapi(), f, indent=1, sort_keys=True)
print(f"wrote {path}")
