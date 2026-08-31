# Frontend Build

Install the pinned frontend dependencies and rebuild the compiled stylesheet:

```bash
npm ci
npm run build:css
```

Commit `static/css/openleg.css` whenever the source utilities change. CI rebuilds
the stylesheet and rejects a differing generated file.
