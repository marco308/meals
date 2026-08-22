# Alexa shopping list → YAMP

A [Node-RED](https://nodered.org) flow that drains an Alexa shopping list into
the YAMP shopping list, so "Alexa, add apples to my shopping list" ends up on
the list you actually shop from.

Import [`alexa-shopping-list.json`](alexa-shopping-list.json).

Nothing here is YAMP-specific plumbing you couldn't write yourself against
`POST /shopping-list/items`. What the flow is really worth reading for is the
three failure modes it avoids, below.

## Requirements

- Node-RED with
  [`node-red-contrib-alexa-remote2-applestrudel`](https://www.npmjs.com/package/node-red-contrib-alexa-remote2-applestrudel),
  and an `alexa-remote-account` config node you have already authenticated.
- A YAMP API token: `POST /auth/tokens` with `{"label": "node-red-alexa"}`.
  Items land on the shopping list of whichever household that token belongs to.

Set two environment variables on the Node-RED process:

| Variable | Required | Meaning |
|---|---|---|
| `YAMP_TOKEN` | yes | the API token from `POST /auth/tokens` |
| `YAMP_URL` | no | base URL of your server; the flow falls back to a hard-coded default, so set this |

## After importing

The exported nodes refer to two config nodes **by id**, because a flow export
should not carry your Alexa credentials:

- `164fbc8f988a33b2` — the `alexa-remote-account`. Open both
  `alexa-remote-list` nodes and the `alexa-remote-event` node and point them at
  your own account node.
- `0212c54d6797edaa` — a Home Assistant button entity used as a manual
  "sync now" trigger. It needs
  `node-red-contrib-home-assistant-websocket`. If you don't run Home Assistant,
  delete that node; the other three triggers are enough.

The flow also assumes the Alexa list alias `SHOP`. Change it in the
`get SHOP items` node if you sync a different list.

## How it works

```
inject every 40s ─┐
ws-todo-change ───┤
HA button ────────┼──> get SHOP items ──> drop completed ──> split ──> parse ──> POST /shopping-list/items
inject manual ────┘                                                                   │
                                                                    2xx? ──yes──> removeItem (Alexa)
                                                                      └──no───> debug "YAMP add failed"
```

Three things are deliberate, and each one is a bug I hit in the version this
replaced:

- **Removal is gated on a 2xx.** The obvious wiring adds to the target and
  removes from Alexa in parallel, which quietly destroys items whenever the
  write fails. Here the Alexa list is a queue: anything rejected, or added while
  the API is unreachable, stays on the list and is retried on the next poll.
- **Removal goes through the list API** (`removeItem`, by item id and version),
  not by speaking "remove X from my shopping list" at an Echo. No device
  dependency, no delay, and nothing to mis-hear. If the item changes between the
  fetch and the delete the version no longer matches, the delete fails, and the
  next poll retries with a fresh version.
- **Every add carries an idempotency key** — a UUIDv5 derived from the Alexa
  item id, sent as `id`. The poll and the push event can pick up the same item
  at the same instant; YAMP returns 200 and the existing line instead of adding
  it twice. This relies on YAMP's client-supplied-id contract, the same one the
  iOS offline queue uses.

## Parsing

The parse function is a small JS mirror of YAMP's own ingest parser. It is
deliberately conservative: YAMP rejects imperial and spoon units with a 422, and
a 422 would wedge that item on the Alexa list forever, so those are converted
here with the same factors YAMP's `INGEST_CONVERSIONS` uses and anything
ambiguous is sent as a bare name with no quantity at all.

| Alexa | ingredient | quantity |
|---|---|---|
| `apples` | apple | |
| `two apples` | apple | 2 item |
| `a dozen eggs` | egg | 12 item |
| `a pint of milk` | milk | 568 ml |
| `500g mince` | mince | 500 g |
| `12 oz of steak` | steak | 336 g |
| `4 tins of chopped tomatoes` | chopped tomatoes | 4 tin |
| `2 x 400g tins of chopped tomatoes` | chopped tomatoes | 800 g |
| `one and a half litres of water` | water | 1500 ml |
| `3 red peppers` | red pepper | 3 item |
| `half a cucumber` | cucumber | |
| `some cheddar cheese` | cheddar cheese | |

Names go over as spoken; YAMP folds and singularises them, so `apples` and
`apple` resolve to one ingredient. Unknown foods get the ❓ aisle until you tag
them once with `PATCH /ingredients`.

## If nothing resolves from inside the container

Symptom: the HTTP node fails with `EAI_AGAIN`, but `getent hosts` and
`dns.resolve4` both work fine, so the DNS looks healthy.

Cause: if your API's domain is served by a split-horizon resolver (Tailscale
MagicDNS, a VPN nameserver, some corporate setups) that answers **REFUSED**
rather than NODATA for AAAA, then musl — the libc in Alpine-based images,
including the official Node-RED one — fails the entire `getaddrinfo` call. glibc
on the host shrugs and falls back to the A record, which is why it works
everywhere except inside the container. Node-RED's `http request` node uses core
`https`, so it takes the failing path.

Fix by pinning the name past DNS:

```yaml
    extra_hosts:
      - "yamp.example.com:10.0.0.5"
```

## Deliberately not done

The parsing belongs server-side, as an additive endpoint that reuses
`parse_ingredient_line`. The flow would then drop its parser and change one URL,
and Siri Shortcuts, the MCP `add_to_shopping_list` tool and the web quick-add
would all get the same behaviour. It lives here for now because this needs no
deploy.
