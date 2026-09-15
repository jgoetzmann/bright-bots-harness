# Diagnosis

`scripts/check-bundle-size.js:42` globs `dist/*.js` and never sees `dist/assets/*.mjs`, so the
measured total is wrong on every esm build.
