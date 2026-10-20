# Client address forwarding

How the app decides which network address a request came from, what the
shipped Compose deployment enables, and how to recover a deployment that
must not trust a proxy.

## App default: direct connections

The app reads `OPENLEG_TRUSTED_PROXY_HOPS`. Unset, it defaults to `0`: every
request is treated as coming directly from the connecting peer, and
`X-Forwarded-For` headers are ignored. This is the safe default for an app
exposed without a reverse proxy.

Validation is strict at startup. Only `0` and `1` are accepted; an empty
value, whitespace, or anything else refuses to start the app.

## Compose default: one private Caddy hop

The shipped `docker-compose.yml` sets

    OPENLEG_TRUSTED_PROXY_HOPS=${OPENLEG_TRUSTED_PROXY_HOPS-1}

The `-` syntax without a colon means the default `1` applies only when the
variable is unset. An explicitly empty value still reaches the app and fails
startup.

In mode `1` the app trusts one proxy hop and takes the client address from
`X-Forwarded-For`. The shipped Caddyfile reverse-proxies `flask:5000` and
writes the actual client IP into that header, ignoring any
`X-Forwarded-For` an untrusted client sent.

Enable this mode only when both conditions hold:

- exactly one known Caddy instance is the last hop in front of the app;
- the Flask container is unreachable from untrusted clients. It publishes no
  ports and shares only the private Compose network with Caddy.

A CDN, a second proxy in front of Caddy, or an exposed backend is not
covered. The app accepts only 0 or 1, so a longer chain cannot be expressed.

## What ProxyFix does and does not do

The app uses the standard Werkzeug `ProxyFix` middleware with `x_for=1` and
no other header enabled: it does not trust `X-Forwarded-Proto`,
`X-Forwarded-Host`, `X-Forwarded-Port`, or `X-Forwarded-Prefix`. `ProxyFix`
rewrites `REMOTE_ADDR` from the header you tell it to trust; it does not
authenticate the peer that sent the header. The safety of mode `1` rests
entirely on the network position above.

## What does not change

Rate limiting keys on the resolved client address either way. Existing
rate limits, limiter storage and the process-local fallback when Redis
is unavailable stay unchanged.

## Evidence scope

`tests/test_proxy_client_identity.py` drives the real application factory.
In mode `0` a forged `X-Forwarded-For` is ignored and rate limits key on the
peer address; in mode `1` two clients behind one Caddy-shaped hop keep
separate rate-limit buckets. An isolated loopback run of Caddy 2.11.7 in
front of Gunicorn confirmed the split (mode `0`: 200/200/429/429 across two
clients; mode `1`: 200/200/429/200) and that Caddy replaced forged header
values with the real source addresses (127.0.0.2, 127.0.0.3). This is not a
claim about live production, TLS termination, or multi-worker deployments.
Parent issue 557 keeps the live-chain, traffic, and owner-acceptance work.

## Recovery: direct mode in Compose

If a deployment cannot guarantee the private-backend precondition, set

    OPENLEG_TRUSTED_PROXY_HOPS=0

in the deployment environment, not by editing the compose file, and
recreate the Flask service so it starts with the updated environment:

    docker compose up -d --force-recreate flask

A plain restart of the existing container keeps the old environment, so a
Compose environment change requires a recreate. Mode `0` restores direct
peer keying and the single shared rate-limit bucket. No image or database
restore is involved.

Production promotion of either mode is owned by private operations.

## References

- [Flask guide: running behind a proxy](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/)
- [Werkzeug ProxyFix middleware](https://werkzeug.palletsprojects.com/en/stable/middleware/proxy_fix/)
- [Caddy reverse_proxy directive](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy/)
