# Evidence

- Base commit: `0123456789abcdef0123456789abcdef01234567`
- Branch: `harness/fix-633-bundle-size`

## Baseline — untouched tree at BASE

### npm run lint — exit code 0 (PASS)

stdout (verbatim tail):

```text
lint ok
```

### npm run test:unit — exit code 1 (FAIL)

stdout (verbatim tail):

```text
1 failing: a flaky clock test, red before any change
```

## Post-change — the branch as packaged

### npm run lint — exit code 0 (PASS)

stdout (verbatim tail):

```text
lint ok
```

### npm run build — exit code 0 (PASS)

stdout (verbatim tail):

```text
built in 4.2s
```

### npm run test:unit — exit code 1 (FAIL)

stdout (verbatim tail):

```text
1 failing: a flaky clock test, red before any change
```
