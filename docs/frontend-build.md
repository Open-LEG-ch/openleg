# Frontend Build

Product pages extend `templates/product_base.html` and load the shared
`partials/tailwind_brand.html` partial. Never load the Tailwind CDN.

Tailwind CSS and its separate CLI are both pinned to 4.3.3. The CLI reads
`static/css/tailwind.css` and writes the tracked, compiled
`static/css/openleg.css`. Theme tokens live in the source CSS. Explicit source
paths scan `templates/` and `static/js/`; tests and documentation are excluded.
Install the pinned dependencies and rebuild it with:

```bash
npm ci
npm run build:css
```

Commit `static/css/openleg.css` whenever source utilities change. CI rebuilds the
stylesheet and rejects a differing generated file.

Tailwind v4 requires Safari 16.4+, Chrome 111+ or Firefox 128+. See the
[upstream upgrade guide](https://tailwindcss.com/docs/upgrade-guide).
