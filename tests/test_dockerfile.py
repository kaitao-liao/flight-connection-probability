import ast
from pathlib import Path


DATABASE_URL = (
    "https://github.com/kaitao-liao/flight-connection-probability/"
    "releases/download/v1-data/flights_production.duckdb"
)
DATABASE_SHA256 = "6d1b144fd7f7d7a7db742503b60019eb91bdc9c8a1336e9d2b0ff32c9d18776b"
PACKAGE = Path("backend/flight_connection")


def serving_module_files() -> set[Path]:
    """Follow package-local imports from the production API entry point."""
    pending = [PACKAGE / "api.py"]
    discovered = {PACKAGE / "__init__.py"}
    while pending:
        module = pending.pop()
        if module in discovered:
            continue
        discovered.add(module)
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                dependency = PACKAGE / f"{node.module}.py"
                if dependency.is_file() and dependency not in discovered:
                    pending.append(dependency)
    return discovered


def test_dockerfile_downloads_and_verifies_versioned_database_asset():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert DATABASE_URL in dockerfile
    assert DATABASE_SHA256 in dockerfile
    assert "curl --fail --location" in dockerfile
    assert "sha256sum --check --strict" in dockerfile
    assert "COPY --from=production-database /flights_production.duckdb" in dockerfile
    assert "COPY data/production/flights_production.duckdb" not in dockerfile


def test_local_database_is_not_added_to_docker_build_context():
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    assert "!data/production/flights_production.duckdb" not in dockerignore
    assert dockerignore.splitlines()[0] == "**"


def test_production_image_includes_timezone_validation_runtime():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    requirements = Path("requirements-runtime.txt").read_text(encoding="utf-8")
    assert "COPY backend/flight_connection/timezone_validation.py" in dockerfile
    assert "airportsdata==20260803" in requirements
    assert "tzdata==2025.2" in requirements


def test_production_image_copies_every_local_serving_module():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    for module in serving_module_files():
        source = module.as_posix()
        assert f"COPY {source} ./{source}" in dockerfile, f"Dockerfile omits {source}"
