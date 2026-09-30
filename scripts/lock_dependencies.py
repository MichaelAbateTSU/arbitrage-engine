"""Lock the installed, tested dependency graph using authoritative PyPI hashes.

This avoids downloading every platform wheel just to compute published hashes.
Run after resolving/installing a manifest change, then verify installation with
pip --require-hashes. Both Windows and Linux dependency markers are considered.
"""

import importlib.metadata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import tomllib
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

root = Path(__file__).resolve().parent.parent
manifest = tomllib.loads(
    (root / "backend" / "pyproject.toml").read_text(encoding="utf-8")
)


def resolve(requirements):
    pending = [Requirement(value) for value in requirements]
    included = {}
    seen = set()
    environments = [
        {
            **default_environment(),
            "python_version": "3.12",
            "python_full_version": "3.12.0",
        },
        {
            **default_environment(),
            "python_version": "3.12",
            "python_full_version": "3.12.0",
            "sys_platform": "linux",
            "os_name": "posix",
            "platform_system": "Linux",
            "platform_machine": "x86_64",
        },
    ]
    while pending:
        requirement = pending.pop()
        if requirement.marker and not any(
            requirement.marker.evaluate(env) for env in environments
        ):
            continue
        package = importlib.metadata.distribution(requirement.name)
        if package.version not in requirement.specifier:
            raise RuntimeError(
                f"Installed {requirement.name} does not satisfy manifest"
            )
        key = (package.metadata["Name"].lower(), tuple(sorted(requirement.extras)))
        if key in seen:
            continue
        seen.add(key)
        included[package.metadata["Name"].lower()] = package.version
        for text in package.requires or []:
            child = Requirement(text)
            extras = ["", *requirement.extras]
            if not child.marker or any(
                child.marker.evaluate({**env, "extra": extra})
                for env in environments
                for extra in extras
            ):
                child.marker = None
                pending.append(child)
    return included


def lock_item(item):
    name, version = item
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.get(f"https://pypi.org/pypi/{name}/{version}/json")
        response.raise_for_status()
        data = response.json()
    python = data["info"].get("requires_python")
    if python and not SpecifierSet(python).contains("3.12.0"):
        raise RuntimeError(f"{name}=={version} is not Python 3.12 compatible")
    hashes = sorted({entry["digests"]["sha256"] for entry in data["urls"]})
    if not hashes:
        raise RuntimeError(f"No authoritative hashes for {name}=={version}")
    return (
        name
        + "=="
        + version
        + " \\\n"
        + " \\\n".join("    --hash=sha256:" + value for value in hashes)
    )


runtime = manifest["project"]["dependencies"]
development = [*runtime, *manifest["project"]["optional-dependencies"]["dev"]]
for filename, requirements in (
    ("requirements.lock", runtime),
    ("requirements-dev.lock", development),
):
    graph = resolve(requirements)
    with ThreadPoolExecutor(max_workers=8) as pool:
        lines = list(pool.map(lock_item, sorted(graph.items())))
    target = root / "backend" / filename
    target.write_text(
        "# Generated from installed tested graph and official PyPI SHA256 metadata.\n"
        "# Regenerate: python scripts/lock_dependencies.py\n"
        + "\n\n".join(lines)
        + "\n",
        encoding="utf-8",
    )
    print(f"Locked {len(graph)} packages: {filename}")
