# Favicon install guide — stacked books

## Files in this folder

| File | Purpose |
|------|---------|
| `favicon.svg` | Full-detail mark (shelf + three spines, one leaning). Scalable, used by modern browsers. |
| `favicon-small.svg` | Bold small-size variant (three chunky upright spines, wider gaps). Source for the tiny rasters. |
| `favicon.ico` | Multi-resolution legacy icon (16 / 32 / 48), built from the small variant. |
| `favicon-16.png`, `favicon-32.png`, `favicon-48.png` | Small variant, individual sizes. |
| `apple-touch-icon.png` | 180×180, full detail. iOS home-screen icon. |
| `icon-192.png`, `icon-512.png` | Full detail. PWA / Android install icons. |

## 1. Add to your `<head>`

Drop the files at your site root and add:

```html
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
```

Ordering matters: the `.ico` (bold variant) covers low-DPI 16px tabs, while
SVG-capable browsers prefer `favicon.svg`, which renders crisp at any DPI.

## 2. PWA manifest icons

Reference the large PNGs in your web app manifest:

```json
"icons": [
  { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
  { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
]
```

## Notes

- The brand color `#DC2626` is baked into every asset, so the icons render as
  a solid red tile and will not adapt to dark tab bars (intentional).
- If you want a transparent-background mark that inverts with the browser
  theme instead, regenerate from a transparent variant of the SVGs.
