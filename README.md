# raphael-orgs

Organizations, memberships, invites, billing hierarchy

## API

- Prefix: `/v1/orgs`
- Port: `8082`
- Health: `GET /health`

## Events

_Published and consumed events documented in `openapi.yaml` and raphael-contracts._

## Development

```bash
uv sync
uv run uvicorn raphael_orgs.app:app --reload --port 8082
```

Part of the [Raphael Platform](https://github.com/hummingbird-labs) by HummingBird Labs.
