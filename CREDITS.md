# Credits

YAMP is AGPL-3.0 (`LICENSE` in the repository), and it stands on a great deal of other people's
work. This page names it.

The two clients carry none of it. The iPhone app has no Swift package
dependencies at all (`ios/Meals/project.yml` declares none), and the web app has
no build step, no bundler and no CDN, just hand-written CSS and ES modules the
browser loads directly. Everything below therefore ships inside the server
image.

Nothing here is a compliance exercise. MIT, BSD, Apache-2.0 and the rest ask
that the copyright notice and the licence text travel with the software, and
they do: every package installs its own licence file into `site-packages`
inside the image, where `find` will turn up all of them. This page exists
because naming what you are built on is the decent thing to do.

Every deployment serves it at `/credits`, rendered from this file, the same way
`/privacy`, `/support` and `/terms` are.

## What the server is built on

The fifteen dependencies this project actually chose, in
`backend/pyproject.toml` and `mcp/pyproject.toml`.

| Package | Licence | What it does here |
| --- | --- | --- |
| [aiosmtplib](https://github.com/cole/aiosmtplib) | MIT | Sends the password-reset and dunning mail without blocking the event loop. |
| [aiosqlite](https://github.com/omnilib/aiosqlite) | MIT | The async SQLite driver: the default one-container database, and every test in this repo. |
| [alembic](https://alembic.sqlalchemy.org) | MIT | Schema migrations, applied on boot on both engines. |
| [asyncpg](https://github.com/MagicStack/asyncpg) | Apache-2.0 | The async Postgres driver the deployed stack runs on. |
| [bcrypt](https://github.com/pyca/bcrypt) | Apache-2.0 | Password hashing, and the hashing behind API tokens and invite codes. |
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/bs4/) | MIT | Finds the JSON-LD block in a recipe page. The backend never calls an LLM, so this is the whole of ingestion. |
| [fastapi](https://github.com/fastapi/fastapi) | MIT | The API: routing, dependency injection, and the OpenAPI schema `/docs` serves. |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | Fetching recipe pages, and how the mounted MCP server calls the API over loopback. |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | MIT | Renders this page, along with `/privacy`, `/support` and `/terms`. |
| [mcp](https://modelcontextprotocol.io) | MIT | The Model Context Protocol SDK the server at `/mcp` is built on. |
| [prometheus-client](https://github.com/prometheus/client_python) | Apache-2.0 AND BSD-2-Clause | The counters and histograms behind `/metrics`. |
| [pydantic](https://github.com/pydantic/pydantic) | MIT | Every request and response schema, and the validation that lets a 422 say something useful. |
| [pydantic-settings](https://github.com/pydantic/pydantic-settings) | MIT | Configuration from the environment, in one typed place. |
| [sqlalchemy](https://www.sqlalchemy.org) | MIT | The async ORM, and the only thing in the product that touches the database. |
| [uvicorn](https://uvicorn.dev) | BSD-3-Clause | The ASGI server that runs all of it. |

## What those bring with them

Nobody picked these directly. They are holding the thing up all the same.

| Package | Licence |
| --- | --- |
| [annotated-doc](https://github.com/fastapi/annotated-doc) | MIT |
| [annotated-types](https://github.com/annotated-types/annotated-types) | MIT |
| [anyio](https://github.com/agronholm/anyio) | MIT |
| [attrs](https://github.com/python-attrs/attrs) | MIT |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 |
| [cffi](https://github.com/python-cffi/cffi) | MIT-0 |
| [click](https://github.com/pallets/click) | BSD-3-Clause |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 OR BSD-3-Clause |
| [dnspython](https://www.dnspython.org) | ISC |
| [email-validator](https://github.com/JoshData/python-email-validator) | Unlicense |
| [greenlet](https://greenlet.readthedocs.io) | MIT AND PSF-2.0 |
| [h11](https://github.com/python-hyper/h11) | MIT |
| [httpcore](https://www.encode.io/httpcore/) | BSD-3-Clause |
| [httpcore2](https://github.com/pydantic/httpx2) | BSD-3-Clause |
| [httptools](https://github.com/MagicStack/httptools) | MIT |
| [httpx2](https://github.com/pydantic/httpx2) | BSD-3-Clause |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause |
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | MIT |
| [jsonschema-specifications](https://github.com/python-jsonschema/jsonschema-specifications) | MIT |
| [mako](https://www.makotemplates.org/) | MIT |
| [markupsafe](https://github.com/pallets/markupsafe) | BSD-3-Clause |
| [mcp-types](https://modelcontextprotocol.io) | MIT |
| [mdurl](https://github.com/executablebooks/mdurl) | MIT |
| [opentelemetry-api](https://github.com/open-telemetry/opentelemetry-python) | Apache-2.0 |
| [pycparser](https://github.com/eliben/pycparser) | BSD-3-Clause |
| [pydantic-core](https://github.com/pydantic/pydantic-core) | MIT |
| [pyjwt](https://github.com/jpadilla/pyjwt) | MIT |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 |
| [pyyaml](https://pyyaml.org/) | MIT |
| [referencing](https://github.com/python-jsonschema/referencing) | MIT |
| [rpds-py](https://github.com/crate-py/rpds) | MIT |
| [soupsieve](https://github.com/facelessuser/soupsieve) | MIT |
| [sse-starlette](https://github.com/sysid/sse-starlette) | BSD-3-Clause |
| [starlette](https://github.com/Kludex/starlette) | BSD-3-Clause |
| [truststore](https://github.com/sethmlarson/truststore) | MIT |
| [typing-extensions](https://github.com/python/typing_extensions) | PSF-2.0 |
| [typing-inspection](https://github.com/pydantic/typing-inspection) | MIT |
| [uvloop](https://github.com/MagicStack/uvloop) | MIT OR Apache-2.0 |
| [watchfiles](https://github.com/samuelcolvin/watchfiles) | MIT |
| [websockets](https://github.com/python-websockets/websockets) | BSD-3-Clause |

## Everything that isn't a Python package

The images are made of software too, and the backup sidecar is mostly other
people's programs wired together.

- [Python](https://www.python.org/) (PSF-2.0) and [Debian](https://www.debian.org/) (many licences, GPL-compatible), by way of the [uv](https://github.com/astral-sh/uv) base image (MIT OR Apache-2.0). `uv` also resolves and locks everything in the two tables above.
- [SQLite](https://www.sqlite.org/) (public domain), the default database of a one-container install, reached through `aiosqlite`.
- [PostgreSQL](https://www.postgresql.org/) (PostgreSQL Licence), the database the reference deployment runs, and the `pg_dump` and `pg_restore` the whole of `backup/` is built around.
- [Alpine Linux](https://alpinelinux.org/) (MIT), underneath the backup sidecar via `postgres:17-alpine`.
- [rclone](https://rclone.org/) (MIT) and [GnuPG](https://gnupg.org/) (GPL-3.0), which encrypt each nightly dump and push it off the node.
- [Docker](https://www.docker.com/) (Apache-2.0), which is how any of it runs anywhere.

The iPhone app is [SwiftUI](https://developer.apple.com/xcode/swiftui/),
Foundation and XCTest, all Apple's and all shipped with the OS. Its Xcode
project is generated by [XcodeGen](https://github.com/yonaskolb/XcodeGen) (MIT),
which builds the app without ever shipping inside it.

## Leaned on, never shipped

Not in the image, and the work would be worse without them:
[ruff](https://docs.astral.sh/ruff) (MIT),
[pytest](https://docs.pytest.org) with `pytest-asyncio` and `pytest-cov` (MIT
and Apache-2.0), [respx](https://lundberg.github.io/respx/) (BSD-3-Clause) and
[coverage.py](https://github.com/coveragepy/coveragepy) (Apache-2.0).

## How this page stays true

A hand-written list of dependencies is a list that rots, so this one is linted.
`backend/tests/unit/test_credits.py` resolves both `uv.lock` files down to the packages that actually install on
Linux, and fails when one of them ships uncredited or stays credited after it
has gone. Adding a dependency breaks CI until it is named here, which is the
same trick that keeps the export field list and the limits vocabulary honest.

Missing or wrong attribution here is a bug worth reporting, on the same footing
as any other, and `/support` says where to send it.
