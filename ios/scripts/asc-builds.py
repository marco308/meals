"""Every build App Store Connect holds, and the version record's review state.

This answers step 1 of the ritual in ../CHANGELOG.md — "bump to one above the
highest build in App Store Connect" — which cannot be answered from the ledger,
because uploads have come from outside this repo before. `altool` has no
`--list-builds`, so ask the API. Run it as:

    set -a; . ios/.env; set +a
    uv run --with cryptography python ios/scripts/asc-builds.py

The ES256 JWT is hand-rolled on `cryptography` rather than pulling in PyJWT:
nothing else in the repo needs a JWT library, and this script is not shipped.
"""

import base64
import json
import os
import sys
import time
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

APP_ID = "6794266229"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token(key_id: str, issuer: str, key_path: str) -> str:
    with open(key_path, "rb") as handle:
        key = serialization.load_pem_private_key(handle.read(), password=None)
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    now = int(time.time())
    payload = {"iss": issuer, "iat": now, "exp": now + 600, "aud": "appstoreconnect-v1"}
    signing_input = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(payload).encode())}"
    der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{signing_input}.{b64(raw)}"


def get(path: str, jwt: str) -> dict:
    request = urllib.request.Request(
        f"https://api.appstoreconnect.apple.com{path}",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    key_id = os.environ["ASC_KEY_ID"]
    issuer = os.environ["ASC_ISSUER"]
    key_path = os.path.expanduser(f"~/.appstoreconnect/private_keys/AuthKey_{key_id}.p8")
    jwt = token(key_id, issuer, key_path)

    builds = get(
        f"/v1/builds?filter[app]={APP_ID}&limit=20&sort=-version"
        "&fields[builds]=version,uploadedDate,processingState,expired",
        jwt,
    )
    print("ASC build   uploaded                  state       expired")
    for build in builds["data"]:
        a = build["attributes"]
        print(
            f"{a['version']:<11} {a['uploadedDate']:<25} "
            f"{a['processingState']:<11} {a['expired']}"
        )

    versions = get(
        f"/v1/apps/{APP_ID}/appStoreVersions?limit=10"
        "&fields[appStoreVersions]=versionString,appStoreState,createdDate",
        jwt,
    )
    print("\nversion   state                        created")
    for version in versions["data"]:
        a = version["attributes"]
        print(f"{a['versionString']:<9} {a['appStoreState']:<28} {a['createdDate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
