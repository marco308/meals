"""The reference deployment and the image's build context.

`docker-compose.yml` is what `make up` runs on other people's servers, and the
repo root is the build context of a public image, so a convenience added to
either for the sake of a laptop ships to all of them.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LOOPBACK = ("127.0.0.1:", "[::1]:")


def _services() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]


def test_postgres_is_published_on_loopback_only():
    """Docker writes its own firewall rules for a published port, so ufw never
    sees them: "5433:5432" put the database's superuser on every interface of
    any host that ran `make up`."""
    for port in _services()["db"].get("ports", []):
        host_ip = port.get("host_ip", "") + ":" if isinstance(port, dict) else str(port)
        assert host_ip.startswith(LOOPBACK), f"{port!r} publishes Postgres beyond this machine"


def test_the_api_and_mcp_are_published_on_loopback_unless_asked():
    """The same rule for the two HTTP ports (#261): a stranger's `make up` is
    usually behind a reverse proxy on the same box, and a port published on
    every interface is one ufw never sees. `BIND_ADDR=0.0.0.0` opens it."""
    for service in ("api", "mcp"):
        for port in _services()[service].get("ports", []):
            assert str(port).startswith("${BIND_ADDR:-127.0.0.1}:"), (
                f"{service}: {port!r} publishes beyond this machine"
            )


def test_the_database_password_is_one_setting():
    """Written once, as POSTGRES_PASSWORD: a literal anywhere would be a
    password nobody chose, and two spellings of it would disagree the first
    time somebody set their own, leaving the API unable to log in."""
    services = _services()
    password = "${POSTGRES_PASSWORD:-meals}"
    assert services["db"]["environment"]["POSTGRES_PASSWORD"] == password
    assert services["backup"]["environment"]["PGPASSWORD"] == password
    assert services["api"]["environment"]["DATABASE_URL"] == f"postgresql+asyncpg://meals:{password}@db:5432/meals"


def test_no_env_file_enters_the_build_context_at_any_depth():
    """A bare `.env` pattern matches the root alone, and mcp/, skill/ and web/
    are copied into the image whole."""
    patterns = {line.strip() for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()}
    assert "**/.env*" in patterns  # every spelling: .env.local, .env.production …
    assert "!**/.env.example" in patterns  # but the documented template may ship
    # And the key files a working tree accumulates (a build-mode deploy builds
    # from one): an App Store Connect .p8, a certificate, a private key.
    assert {"**/*.p8", "**/*.pem", "**/*.key"} <= patterns
