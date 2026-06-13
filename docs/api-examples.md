# API Examples

The public API is available without an API key. Examples use
`https://openleg.ch`; replace the host when testing locally.

## List Municipalities

```bash
curl "https://openleg.ch/api/v1/municipalities?kanton=ZH&limit=5"
```

Use this to discover BFS numbers and public municipality profile fields.

## Municipality Detail

```bash
curl "https://openleg.ch/api/v1/municipalities/261"
```

Returns one municipality profile where public data is available.

## Tariffs

```bash
curl "https://openleg.ch/api/v1/tariffs/261?year=2026"
```

Returns public ElCom tariff rows for the municipality and year.

## LEG Potential

```bash
curl "https://openleg.ch/api/v1/leg-potential/261?year=2026&grid_reduction_pct=40&participants=10"
```

Returns an estimated public LEG value calculation. Treat it as planning support,
not a binding tariff offer.

## Search

```bash
curl "https://openleg.ch/api/v1/search?q=Zürich&limit=5"
```

Use search before linking users to a municipality route.

## Documentation

```bash
curl "https://openleg.ch/api/v1/docs"
```

For browser usage, open `/api/v1/docs`.
