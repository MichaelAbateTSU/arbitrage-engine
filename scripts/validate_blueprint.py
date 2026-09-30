from pathlib import Path

import httpx
import jsonschema
import yaml

root = Path(__file__).resolve().parent.parent
response = httpx.get(
    "https://render.com/schema/render.yaml.json", timeout=20, follow_redirects=True
)
response.raise_for_status()
schema = response.json()
blueprint = yaml.safe_load((root / "render.yaml").read_text(encoding="utf-8"))
try:
    jsonschema.validate(blueprint, schema)
except jsonschema.ValidationError as exc:
    raise SystemExit(
        f"Blueprint invalid at {list(exc.absolute_path)}: {exc.message}"
    ) from None
assert blueprint["previews"]["generation"] == "off"
for group in blueprint["envVarGroups"]:
    settings = {x["key"]: x.get("value") for x in group["envVars"]}
    assert settings["TRADING_MODE"] == "paper"
    assert settings["LIVE_TRADING_ENABLED"] == "false"
print("PASSED Render JSON schema and paper-only invariants")
