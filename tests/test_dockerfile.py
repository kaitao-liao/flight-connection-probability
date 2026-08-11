from pathlib import Path


DATABASE_URL = (
    "https://github.com/kaitao-liao/flight-connection-probability/"
    "releases/download/v1-data/flights_production.duckdb"
)
DATABASE_SHA256 = "6d1b144fd7f7d7a7db742503b60019eb91bdc9c8a1336e9d2b0ff32c9d18776b"


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
