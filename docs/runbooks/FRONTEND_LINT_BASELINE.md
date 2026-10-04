# Frontend lint baseline

`apps/operator-dashboard` and `apps/platform-dashboard` both carry
pre-existing ESLint errors (mostly `@typescript-eslint/no-explicit-any` in
older test/API-client files) that predate the `pr-checks.yml` CI workflow —
this workflow is the first time lint has ever been enforced in CI for
either app. Disabling the rule or `|| true`-ing the whole gate would hide
*new* regressions along with the old ones, so instead:

- `npm run lint` — the real ESLint output, informational in CI (`|| true`),
  never silently "passing" a nonzero result.
- `npm run lint:baseline` — the actual blocking gate
  (`scripts/lint-baseline-check.mjs`). Fails on:
  - any error not already listed in that app's `eslint-baseline.json`
    (a brand-new error, anywhere);
  - any error at all — baselined or not — in a file this PR modified
    (diffed against the PR's base branch). Touching a file means it must
    be lint-clean, not just "no worse than before."
  - A pre-existing, baselined error in a file the PR did **not** touch is
    allowed and printed as "legacy/baselined" in the step's output.

## Current baseline size (as of this PR)

| App | Baselined errors | Rule(s) |
|---|---|---|
| `apps/operator-dashboard` | 28 | `@typescript-eslint/no-explicit-any` (test files + `src/lib/api.ts`) |
| `apps/platform-dashboard` | 38 | `@typescript-eslint/no-explicit-any` (test files + `src/lib/api.ts`) |

(A 39th platform-dashboard error, a genuine `react-hooks/set-state-in-effect`
bug in `src/app/inbox/page.tsx`, was fixed rather than baselined — see that
file's `useEffect`.)

Both numbers only ever go down over time: fixing a baselined error doesn't
require touching `eslint-baseline.json` (the checker only treats it as an
upper bound), and a future contributor who eliminates one is welcome to
regenerate the file to reflect it —
`npm run lint:baseline:update` inside the app directory — as a deliberate,
reviewed change, never as a side effect of unrelated work.
