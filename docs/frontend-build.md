# Frontend Build

Product pages extend `templates/product_base.html` and load the shared
`partials/tailwind_brand.html` partial. Never load the Tailwind CDN.

Tailwind reads `static/css/tailwind.css` and writes the tracked,
compiled `static/css/openleg.css`. Install the pinned dependencies and rebuild it
with:

```bash
npm ci
npm run build:css
```

Commit `static/css/openleg.css` whenever source utilities change. CI rebuilds the
stylesheet and rejects a differing generated file.
