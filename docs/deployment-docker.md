# Docker + Nginx + FastAPI Deployment

This deployment shape is for a centralized cloud backend.

## Topology

- `backend`: FastAPI service running with `uvicorn`
- `nginx`: reverse proxy exposing port `80`
- `mysql`: not bundled here; expected to be a managed/cloud MySQL instance

## Files

- `Dockerfile.backend`: backend image build definition
- `docker-compose.prod.yml`: production compose stack
- `deploy/nginx/trade-relay.conf`: Nginx reverse proxy config
- `.env.production.example`: required environment variables template

## Deploy Steps

1. Copy `.env.production.example` to `.env.production` and fill in real values.
2. Ensure the cloud MySQL instance allows inbound traffic from this server.
3. Start the stack:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

4. Verify health:

```bash
curl http://127.0.0.1/health
```

## Production Notes

- `TRADE_RELAY_JWT_SECRET` must be replaced with a long random secret.
- `TRADE_RELAY_ENCRYPTION_KEY` must be set before storing encrypted Binance API keys in DB.
- TLS is not handled in this sample. In production, terminate HTTPS with either:
  - host-level Nginx/Caddy
  - a cloud load balancer
  - an extended Nginx container setup with certificates

## Desktop Client Backend URL

The client now supports a configurable backend base URL.

- Electron runtime: set `TRADE_RELAY_API_BASE_URL=https://api.example.com`
- Browser/Vite fallback: set `VITE_API_BASE_URL=https://api.example.com` before build

If neither is set, the app falls back to `http://127.0.0.1:8000`.