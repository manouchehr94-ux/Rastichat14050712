#!/usr/bin/env node
// Honest lint gate for the two Next.js dashboards: neither disables lint
// nor swallows a nonzero exit code with `|| true`. Instead it compares
// ESLint's real output against a committed baseline of KNOWN, pre-existing
// errors (files this PR never touched, per `git diff` against the base
// branch) and fails on anything that isn't in that baseline:
//
//   - a brand-new error anywhere                              -> FAIL
//   - any error (new OR pre-existing/baselined) in a file this
//     PR actually modified                                    -> FAIL
//   - a pre-existing, baselined error in a file this PR did
//     NOT touch                                                -> allowed
//     (tracked, visible, not silently hidden — see the summary
//     this script prints every run)
//
// Fixing a baselined error shrinks the real count but does NOT require
// editing the baseline file by hand — this script only ever treats the
// baseline as an upper bound (an entry no longer produced by ESLint is
// simply not reported), so legacy debt only ever goes down. Run with
// `--update-baseline` to regenerate the baseline file from ESLint's
// CURRENT output (only ever do this deliberately, after confirming any
// remaining baselined error is genuinely known/deferred, not to hide a
// new one).
//
// Usage (run from the app directory, e.g. apps/operator-dashboard):
//   node ../../scripts/lint-baseline-check.mjs [--update-baseline]
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, relative } from 'node:path';

const APP_DIR = process.cwd();
const BASELINE_PATH = resolve(APP_DIR, 'eslint-baseline.json');
const UPDATE = process.argv.includes('--update-baseline');

function run(cmd, args, opts = {}) {
  try {
    return execFileSync(cmd, args, { cwd: APP_DIR, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, ...opts });
  } catch (err) {
    // eslint exits non-zero when it finds errors — that's expected here,
    // the JSON on stdout is still what we want.
    if (err.stdout) return err.stdout;
    throw err;
  }
}

function currentErrors() {
  const raw = run('npx', ['eslint', '--format', 'json']);
  const results = JSON.parse(raw);
  const errors = [];
  for (const file of results) {
    const relPath = relative(APP_DIR, file.filePath).split('\\').join('/');
    for (const msg of file.messages) {
      if (msg.severity !== 2) continue; // errors only — warnings stay non-blocking, unchanged from before
      errors.push({ file: relPath, rule: msg.ruleId || '(no-rule)', line: msg.line, message: msg.message });
    }
  }
  return errors;
}

function loadBaseline() {
  if (!existsSync(BASELINE_PATH)) return [];
  return JSON.parse(readFileSync(BASELINE_PATH, 'utf8'));
}

function baselineKey(e) { return `${e.file}::${e.rule}::${e.line}`; }

function changedFiles() {
  // Best-effort: diff against the PR's base branch in CI (GITHUB_BASE_REF),
  // falling back to origin/main locally, falling back to "unknown" (treat
  // nothing as changed rather than crash — CI still catches new errors via
  // the baseline diff itself, just without the extra per-file zero-
  // tolerance layer) if neither is available, e.g. a shallow single-branch
  // checkout.
  const base = process.env.GITHUB_BASE_REF ? `origin/${process.env.GITHUB_BASE_REF}` : 'origin/main';
  try {
    run('git', ['fetch', '--depth=50', 'origin', base.replace('origin/', '')], { cwd: resolve(APP_DIR, '../..') });
  } catch { /* best-effort */ }
  try {
    const mergeBase = run('git', ['merge-base', base, 'HEAD'], { cwd: resolve(APP_DIR, '../..') }).trim();
    const diff = run('git', ['diff', '--name-only', mergeBase, 'HEAD'], { cwd: resolve(APP_DIR, '../..') });
    const appPrefix = relative(resolve(APP_DIR, '../..'), APP_DIR) + '/';
    return new Set(
      diff.split('\n').filter(Boolean)
        .filter((f) => f.startsWith(appPrefix))
        .map((f) => f.slice(appPrefix.length)),
    );
  } catch {
    return null; // unknown — skip the per-file zero-tolerance layer, baseline diff still applies
  }
}

const current = currentErrors();

if (UPDATE) {
  writeFileSync(BASELINE_PATH, JSON.stringify(current.map(({ file, rule, line }) => ({ file, rule, line })), null, 2) + '\n');
  console.log(`Wrote ${current.length} known finding(s) to ${BASELINE_PATH}`);
  process.exit(0);
}

const baseline = loadBaseline();
const baselineSet = new Set(baseline.map(baselineKey));
const changed = changedFiles();

const newErrors = [];
const blockedInChangedFiles = [];
const legacyAllowed = [];

for (const e of current) {
  const key = baselineKey(e);
  const isBaselined = baselineSet.has(key);
  const isChanged = changed ? changed.has(e.file) : false;
  if (isChanged) {
    blockedInChangedFiles.push(e);
  } else if (!isBaselined) {
    newErrors.push(e);
  } else {
    legacyAllowed.push(e);
  }
}

console.log(`ESLint errors: ${current.length} total, ${legacyAllowed.length} legacy/baselined (unchanged files), ${newErrors.length} new, ${blockedInChangedFiles.length} in PR-changed files.`);

if (newErrors.length > 0) {
  console.error('\nNew lint errors not in the baseline:');
  for (const e of newErrors) console.error(`  ${e.file}:${e.line}  ${e.rule}  ${e.message}`);
}
if (blockedInChangedFiles.length > 0) {
  console.error('\nLint errors in files this PR modified (must be fixed even if pre-existing):');
  for (const e of blockedInChangedFiles) console.error(`  ${e.file}:${e.line}  ${e.rule}  ${e.message}`);
}
if (changed === null) {
  console.log('(Could not determine changed files against the base branch — skipped the per-file zero-tolerance check; baseline-diff check above still applies.)');
}

if (newErrors.length > 0 || blockedInChangedFiles.length > 0) {
  console.error(`\nFAIL: ${newErrors.length + blockedInChangedFiles.length} blocking lint error(s). See eslint-baseline.json and this script's header comment for how the gate works.`);
  process.exit(1);
}

console.log('OK — no new or PR-touched-file lint errors.');
