'use strict';

// Behavior tests for bin/commit-hours-audit. Every case drives the public CLI
// against throwaway repositories under os.tmpdir(): git config is isolated and
// the clock is fixed with COMMIT_HOURS_AUDIT_NOW. Requires git-filter-repo.
//
//   node --test ai/agents/opencode/skills/commit-hours-audit/test/

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CLI = path.resolve(__dirname, '..', 'bin', 'commit-hours-audit');
const SATURDAY_NOW = '2026-10-31T12:00:00+01:00';
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'cha-test-'));

function env(extra = {}) {
  return {
    PATH: process.env.PATH,
    HOME: scratch,
    TMPDIR: os.tmpdir(),
    LANG: 'C',
    GIT_CONFIG_GLOBAL: '/dev/null',
    GIT_CONFIG_NOSYSTEM: '1',
    GIT_AUTHOR_NAME: 'Tester',
    GIT_AUTHOR_EMAIL: 'tester@example.com',
    GIT_COMMITTER_NAME: 'Tester',
    GIT_COMMITTER_EMAIL: 'tester@example.com',
    COMMIT_HOURS_AUDIT_NOW: SATURDAY_NOW,
    ...extra,
  };
}

function run(cmd, args, cwd, extra) {
  const res = spawnSync(cmd, args, { cwd, env: env(extra), encoding: 'utf8', timeout: 120000 });
  if (res.error) throw res.error;
  return res;
}

function git(cwd, ...args) {
  const res = run('git', args, cwd);
  if (res.status !== 0) throw new Error(`git ${args.join(' ')} failed: ${res.stderr}`);
  return res.stdout.trim();
}

function cli(cwd, args, extra) {
  return run(process.execPath, [CLI, ...args], cwd, extra);
}

function cliJson(cwd, args, extra) {
  const res = cli(cwd, [...args, '--json'], extra);
  assert.equal(res.status, 0, res.stderr);
  return JSON.parse(res.stdout);
}

function commit(cwd, message, author, committer = author, files = {}) {
  for (const [file, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(cwd, file)), { recursive: true });
    fs.writeFileSync(path.join(cwd, file), content);
  }
  git(cwd, 'add', '-A');
  const res = run('git', ['commit', '-q', '--allow-empty', '-m', message], cwd, {
    GIT_AUTHOR_DATE: author,
    GIT_COMMITTER_DATE: committer,
  });
  if (res.status !== 0) throw new Error(`commit ${message} failed: ${res.stderr}`);
  return git(cwd, 'rev-parse', 'HEAD');
}

function refsSnapshot(repo) {
  return git(repo, 'for-each-ref', '--format=%(objectname) %(refname)');
}

function rawDates(repo, sha) {
  const [author, committer] = git(repo, 'log', '-1', '--format=%ad%n%cd', '--date=raw', sha).split('\n');
  return { author, committer };
}

// Fixture: merges (one resolved by hand), two commits in the same author
// second, a committer-only violator plus a clean commit sharing its author
// second, refs sharing commits, a violator only on another branch, a clean
// child right after a violator, an annotated tag, a backup tag, published
// branches, a remote-only ref and a commit citing an id in message and docs.
let template;
let ids;

function buildFixture(root) {
  const repo = path.join(root, 'repo');
  git(root, 'init', '-q', '--bare', 'remote.git');
  git(root, 'init', '-q', '-b', 'main', 'repo');
  const out = {};
  out.root = commit(repo, 'root', '2026-10-05T08:00:00+02:00', undefined, { 'shared.txt': 'base\n' });
  git(repo, 'branch', 'feat');
  git(repo, 'branch', 'side');
  out.a = commit(repo, 'a', '2026-10-05T10:41:00+02:00', undefined, { 'a.txt': 'a\n' });
  out.b = commit(repo, 'b', '2026-10-05T10:41:00+02:00', undefined, { 'b.txt': 'b\n' });
  git(repo, 'branch', 'other');
  git(repo, 'tag', 'backup/pre-datefix-old');
  out.x = commit(repo, 'x committer-only', '2026-10-03T12:00:00+02:00', '2026-10-05T15:00:00+02:00', { 'x.txt': 'x\n' });
  out.y = commit(repo, 'y same author second', '2026-10-03T12:00:00+02:00', '2026-10-05T20:00:00+02:00', { 'y.txt': 'y\n' });
  out.p = commit(repo, 'p', '2026-10-06T18:50:00+02:00', undefined, { 'shared.txt': 'main\n' });
  out.child = commit(repo, 'child', '2026-10-06T19:26:00+02:00', undefined, { 'child.txt': 'child\n' });
  git(repo, 'checkout', '-q', 'feat');
  out.f1 = commit(repo, 'f1', '2026-10-06T11:00:00+02:00', undefined, { 'shared.txt': 'feat\n' });
  git(repo, 'checkout', '-q', 'other');
  out.o1 = commit(repo, 'o1 only on other', '2026-10-07T10:00:00+02:00', undefined, { 'o1.txt': 'o1\n' });
  git(repo, 'checkout', '-q', 'side');
  out.s1 = commit(repo, 's1 clean side', '2026-10-10T21:00:00+02:00', undefined, { 's1.txt': 's1\n' });
  git(repo, 'checkout', '-q', 'main');
  const mergeDate = { GIT_AUTHOR_DATE: '2026-10-06T20:00:00+02:00', GIT_COMMITTER_DATE: '2026-10-06T20:00:00+02:00' };
  run('git', ['merge', '-q', '--no-ff', 'feat', '-m', 'merge feat'], repo, mergeDate);
  fs.writeFileSync(path.join(repo, 'shared.txt'), 'resolved by hand\n');
  git(repo, 'add', 'shared.txt');
  const merged = run('git', ['commit', '-q', '--no-edit'], repo, mergeDate);
  if (merged.status !== 0) throw new Error(`merge commit failed: ${merged.stderr}`);
  out.merge = git(repo, 'rev-parse', 'HEAD');
  run('git', ['tag', '-a', 'v1', '-m', 'release v1'], repo, { GIT_COMMITTER_DATE: '2026-10-06T20:30:00+02:00' });
  const a7 = out.a.slice(0, 7);
  out.cite = commit(repo, `docs: cites ${a7} and backup/x-${a7}`, '2026-10-10T21:00:00+02:00', undefined, {
    'docs/notes.md': `See ${a7} for the fix.\nBranch backup/x-${a7} keeps the old state.\nData lives in file-${a7}.json.\n`,
    'test/fixtures/sample.txt': `${a7}\n`,
  });
  git(repo, 'remote', 'add', 'origin', '../remote.git');
  git(repo, 'push', '-q', 'origin', 'main', 'other', 'feat', 'feat:feat-old');
  git(repo, 'fetch', '-q', 'origin');
  return out;
}

function freshRepo() {
  const root = fs.mkdtempSync(path.join(scratch, 'case-'));
  fs.cpSync(template, root, { recursive: true });
  return path.join(root, 'repo');
}

function smallRepo(name, commits) {
  const root = fs.mkdtempSync(path.join(scratch, `${name}-`));
  git(root, 'init', '-q', '-b', 'main', name);
  const repo = path.join(root, name);
  const shas = commits.map(([message, author, committer]) => commit(repo, message, author, committer, { [`${message}.txt`]: `${message}\n` }));
  return { repo, shas };
}

function planOf(repo, args = [], extra) {
  const json = cliJson(repo, args, extra);
  assert.ok(json.planDir, 'expected a plan');
  const plan = JSON.parse(fs.readFileSync(path.join(json.planDir, 'plan.json'), 'utf8'));
  return { json, plan, dir: json.planDir };
}

function applied(repo, extra) {
  const { json, plan, dir } = planOf(repo);
  const res = cli(repo, ['apply', dir], extra);
  assert.equal(res.status, 0, `apply failed:\n${res.stdout}\n${res.stderr}`);
  return { json, plan, dir, res };
}

function newIdOf(repo, subject, ref = 'main') {
  const line = git(repo, 'log', '--format=%H %s', ref).split('\n').find((l) => l.slice(41) === subject);
  assert.ok(line, `no commit "${subject}" in ${ref}`);
  return line.slice(0, 40);
}

before(() => {
  template = fs.mkdtempSync(path.join(scratch, 'template-'));
  ids = buildFixture(template);
});

after(() => fs.rmSync(scratch, { recursive: true, force: true }));

// T1 ------------------------------------------------------------------------

test('audit reports full 40-hex ids in JSON and 7-char ids in the table', () => {
  const repo = freshRepo();
  const json = cliJson(repo, []);
  for (const v of json.violations) assert.match(v.sha, /^[0-9a-f]{40}$/);
  const direct = json.violations.filter((v) => !v.pulledIn).map((v) => v.sha).sort();
  assert.deepEqual(direct, [ids.a, ids.b, ids.x, ids.p, ids.f1].sort());
  const x = json.violations.find((v) => v.sha === ids.x);
  assert.equal(x.authorInside, false);
  assert.equal(x.committerInside, true);

  const text = cli(repo, []).stdout;
  assert.ok(text.split('\n').some((l) => l.startsWith(`${ids.a.slice(0, 7)} `)), text);
});

// T2 ------------------------------------------------------------------------

test('plan lists every ref that will change, which are published, and the push commands', () => {
  const repo = freshRepo();
  const json = cliJson(repo, []);
  assert.deepEqual(json.rewriteRefs.map((r) => r.ref).sort(), ['refs/heads/feat', 'refs/heads/main', 'refs/heads/other', 'refs/tags/v1']);
  assert.deepEqual(json.remoteOnlyRefs.map((r) => r.ref), ['refs/remotes/origin/feat-old']);
  const main = json.rewriteRefs.find((r) => r.ref === 'refs/heads/main');
  assert.deepEqual(main.published.map((p) => p.tracking), ['refs/remotes/origin/main']);
  assert.equal(json.rewriteRefs.find((r) => r.ref === 'refs/tags/v1').published, null);
  const originMain = git(repo, 'rev-parse', 'refs/remotes/origin/main');
  assert.ok(json.pushCommands.includes(`git push --force-with-lease=main:${originMain} origin main:main`), json.pushCommands.join('\n'));

  const o1 = json.violations.find((v) => v.sha === ids.o1);
  assert.equal(o1.pulledIn, true);
  const text = cli(repo, []).stdout;
  assert.match(text, /refs\/heads\/other/);
  assert.match(text, /refs\/remotes\/origin\/feat-old/);
  assert.ok(!/refs\/heads\/side|backup\/pre-datefix-old/.test(text), text);
});

// T3 ------------------------------------------------------------------------

test('plan is keyed by commit id: a clean commit sharing an author second is left alone', () => {
  const repo = freshRepo();
  const { plan, dir } = planOf(repo);
  const planned = plan.commits.map((c) => c.sha);
  assert.ok(planned.includes(ids.x));
  assert.ok(!planned.includes(ids.y));
  const callback = fs.readFileSync(path.join(dir, 'callback.py'), 'utf8');
  assert.ok(callback.includes(ids.x) && !callback.includes(ids.y));
  const x = plan.commits.find((c) => c.sha === ids.x);
  assert.equal(x.author.new, null, 'only the violating field moves');
  assert.notEqual(x.committer.new, null);
  const root = path.dirname(dir);
  for (const legacy of ['apply.sh', 'amend.sh', 'dates.map']) assert.ok(!fs.existsSync(path.join(root, legacy)), legacy);
});

// T4 ------------------------------------------------------------------------

test('proposals never date a parent after a child that keeps its date', () => {
  const repo = freshRepo();
  const { plan } = planOf(repo);
  const p = plan.commits.find((c) => c.sha === ids.p);
  assert.equal(p.committer.new, rawDates(repo, ids.child).committer, 'p must not move past its clean child (19:26)');

  const log = git(repo, 'log', '--format=%H %P %at %ct', 'main', 'other', 'feat').split('\n').map((l) => l.split(' '));
  const orig = new Map(log.map(([sha, ...rest]) => [sha, { parents: rest.slice(0, -2).filter(Boolean), author: Number(rest.at(-2)), committer: Number(rest.at(-1)) }]));
  const final = new Map([...orig].map(([sha, c]) => [sha, { ...c }]));
  for (const c of plan.commits) {
    if (c.author.new) final.get(c.sha).author = Number(c.author.new.split(' ')[0]);
    if (c.committer.new) final.get(c.sha).committer = Number(c.committer.new.split(' ')[0]);
  }
  for (const [sha, c] of orig) {
    for (const parent of c.parents) {
      for (const field of ['author', 'committer']) {
        if (c[field] < orig.get(parent)[field]) continue; // inversion already present
        assert.ok(final.get(sha)[field] >= final.get(parent)[field], `${field} of ${sha.slice(0, 7)} precedes ${parent.slice(0, 7)}`);
      }
    }
  }
});

test('an impossible order is announced instead of hidden', () => {
  const { repo, shas } = smallRepo('tight', [
    ['parent', '2026-10-27T08:00:00+01:00'],
    ['violator', '2026-10-27T10:00:00+01:00'],
  ]);
  const { json, plan } = planOf(repo, [], { COMMIT_HOURS_AUDIT_NOW: '2026-10-27T15:00:00+01:00' });
  assert.ok(json.warnings.some((w) => /moved back 1 day/.test(w)), json.warnings.join('\n'));
  assert.ok(json.warnings.some((w) => w.includes(`cannot keep commit order for ${shas[1].slice(0, 7)}`)), json.warnings.join('\n'));
  assert.equal(plan.commits.find((c) => c.sha === shas[1]).inversion, true);
});

// T5 ------------------------------------------------------------------------

test('apply refuses unsafe states and leaves every ref untouched', () => {
  const cases = [
    ['dirty tracked file', (repo) => fs.appendFileSync(path.join(repo, 'a.txt'), 'dirty\n'), /uncommitted changes/],
    ['branch checked out elsewhere', (repo) => git(repo, 'worktree', 'add', '-q', path.join(path.dirname(repo), 'wt'), 'other'), /checked out in another worktree/],
    ['refs moved since the plan', (repo) => git(repo, 'branch', 'extra', 'main~1'), /changed since the plan/],
    ['operation in progress', (repo) => fs.writeFileSync(path.join(repo, '.git', 'MERGE_HEAD'), `${ids.a}\n`), /in progress/],
  ];
  for (const [name, breakIt, message] of cases) {
    const repo = freshRepo();
    const { dir } = planOf(repo);
    breakIt(repo);
    const refs = refsSnapshot(repo);
    const res = cli(repo, ['apply', dir]);
    assert.notEqual(res.status, 0, name);
    assert.match(res.stderr, message, `${name}: ${res.stderr}`);
    assert.equal(refsSnapshot(repo), refs, name);
  }
});

// T6 ------------------------------------------------------------------------

test('apply keeps merges, rewrites shared commits identically in every ref, and protects the rest', () => {
  const repo = freshRepo();
  const merges = git(repo, 'rev-list', '--merges', '--count', 'main');
  const count = git(repo, 'rev-list', '--count', 'main', 'other', 'feat');
  const tree = git(repo, 'rev-parse', 'main^{tree}');
  const originMain = git(repo, 'rev-parse', 'refs/remotes/origin/main');
  const yDates = rawDates(repo, ids.y);
  const { dir, res } = applied(repo);

  assert.equal(git(repo, 'rev-list', '--merges', '--count', 'main'), merges);
  assert.equal(git(repo, 'rev-list', '--count', 'main', 'other', 'feat'), count);
  assert.equal(git(repo, 'rev-parse', 'main^{tree}'), tree);
  assert.equal(git(repo, 'show', 'main~1:shared.txt'), 'resolved by hand');
  const newB = newIdOf(repo, 'b');
  assert.notEqual(newB, ids.b);
  assert.equal(git(repo, 'rev-parse', 'other~1'), newB, 'other shares the rewritten b');
  assert.equal(git(repo, 'cat-file', '-t', 'v1'), 'tag');
  assert.equal(git(repo, 'rev-parse', 'v1^{commit}'), git(repo, 'rev-parse', 'main~1'));
  assert.equal(git(repo, 'rev-parse', 'backup/pre-datefix-old'), ids.b);
  assert.equal(git(repo, 'rev-parse', 'side'), ids.s1);
  assert.equal(git(repo, 'remote'), 'origin');
  assert.equal(git(repo, 'rev-parse', 'refs/remotes/origin/main'), originMain);
  assert.deepEqual(rawDates(repo, newIdOf(repo, 'y same author second')), yDates);
  assert.equal(run('git', ['bundle', 'verify', path.join(dir, 'backup.bundle')], repo).status, 0);
  assert.match(res.stdout, new RegExp(`git push --force-with-lease=main:${originMain} origin main:main`));
  assert.match(res.stdout, /verify: OK/);
});

// T7 ------------------------------------------------------------------------

test('a date changed outside the plan fails verification and apply restores the refs', () => {
  const repo = freshRepo();
  const { dir } = planOf(repo);
  const refs = refsSnapshot(repo);
  const res = cli(repo, ['apply', dir], {
    COMMIT_HOURS_AUDIT_TEST_EXTRA_CALLBACK: 'if commit.message.startswith(b"child"):\n    commit.committer_date = b"1791300000 +0200"',
  });
  assert.notEqual(res.status, 0);
  assert.match(res.stderr, /committer date/);
  assert.match(res.stderr, /restored/);
  assert.equal(refsSnapshot(repo), refs);
});

test('verify rejects a flattened merge even when the tip tree is unchanged', () => {
  const repo = freshRepo();
  const { dir } = applied(repo);
  const tree = git(repo, 'rev-parse', 'main^{tree}');
  const flatMerge = git(repo, 'commit-tree', 'main~1^{tree}', '-p', 'main~1^1', '-m', 'merge feat');
  const flatTip = git(repo, 'commit-tree', 'main^{tree}', '-p', flatMerge, '-m', git(repo, 'log', '-1', '--format=%B', 'main'));
  git(repo, 'update-ref', 'refs/heads/main', flatTip);
  assert.equal(git(repo, 'rev-parse', 'main^{tree}'), tree, 'the old tip-tree check would pass');
  assert.equal(git(repo, 'rev-list', '--merges', '--count', 'main'), '0');
  const res = cli(repo, ['verify', dir]);
  assert.equal(res.status, 1);
  assert.match(res.stdout, /verify: FAILED/);
  assert.match(res.stdout, /refs\/heads\/main/);
});

// T8 ------------------------------------------------------------------------

test('apply remaps ids cited in messages and keeps the old-to-new commit map', () => {
  const repo = freshRepo();
  const { dir } = applied(repo);
  const newA = newIdOf(repo, 'a');
  const subject = git(repo, 'log', '-1', '--format=%s', 'main');
  assert.equal(subject, `docs: cites ${newA.slice(0, 7)} and backup/x-${newA.slice(0, 7)}`);
  const map = fs.readFileSync(path.join(dir, 'commit-map'), 'utf8');
  assert.ok(map.includes(`${ids.a} ${newA}`), map);
  const report = cliJson(repo, ['verify', dir]);
  assert.equal(report.ok, true, JSON.stringify(report.errors));
  assert.ok(report.remapped.length >= 2);
});

// T9 ------------------------------------------------------------------------

test('remap-docs previews plain citations, skips names and fixtures, and writes only on request', () => {
  const repo = freshRepo();
  const { dir } = applied(repo);
  const a7 = ids.a.slice(0, 7);
  const newA7 = newIdOf(repo, 'a').slice(0, 7);
  const notes = path.join(repo, 'docs', 'notes.md');
  const before = fs.readFileSync(notes, 'utf8');

  const preview = cliJson(repo, ['remap-docs', dir]);
  assert.deepEqual(preview.replacements, [{ file: 'docs/notes.md', line: 1, old: a7, new: newA7 }]);
  const skipped = preview.skipped.map((s) => `${s.file}:${s.line}:${s.reason}`).sort();
  assert.deepEqual(skipped, ['docs/notes.md:2:embedded in a name', 'docs/notes.md:3:embedded in a name', 'test/fixtures/sample.txt:1:fixture or lockfile']);
  assert.equal(fs.readFileSync(notes, 'utf8'), before);

  const head = git(repo, 'rev-parse', 'HEAD');
  const write = cli(repo, ['remap-docs', dir, '--write']);
  assert.equal(write.status, 0, write.stderr);
  assert.equal(fs.readFileSync(notes, 'utf8'), before.replace(`See ${a7}`, `See ${newA7}`));
  assert.equal(fs.readFileSync(path.join(repo, 'test', 'fixtures', 'sample.txt'), 'utf8'), `${a7}\n`);
  assert.equal(git(repo, 'rev-parse', 'HEAD'), head, 'remap-docs never commits');
});

// T10 -----------------------------------------------------------------------

test('re-audit after apply is clean even when run on a weekday', () => {
  const repo = freshRepo();
  applied(repo, { COMMIT_HOURS_AUDIT_NOW: '2026-10-28T11:00:00+01:00' });
  const json = cliJson(repo, ['--all'], { COMMIT_HOURS_AUDIT_NOW: '2026-10-28T11:00:00+01:00' });
  assert.equal(json.violations.length, 0, JSON.stringify(json.violations));
});

// T11 -----------------------------------------------------------------------

test('apply archives a stale filter-repo run instead of prompting', () => {
  const repo = freshRepo();
  const stale = path.join(repo, '.git', 'filter-repo');
  fs.mkdirSync(stale);
  fs.writeFileSync(path.join(stale, 'already_ran'), '');
  const twoDaysAgo = new Date(Date.now() - 2 * 86400 * 1000);
  fs.utimesSync(path.join(stale, 'already_ran'), twoDaysAgo, twoDaysAgo);
  const { dir } = applied(repo);
  assert.ok(fs.existsSync(path.join(dir, 'prior-filter-repo', 'already_ran')));
});

// T12 -----------------------------------------------------------------------

test('existing audit behavior stays the same', () => {
  const { repo: clean } = smallRepo('clean', [['weekend', '2026-10-03T12:00:00+02:00']]);
  const res = cli(clean, []);
  assert.equal(res.status, 0);
  assert.equal(res.stdout, '✔ clean: 0 commits inside the working-hours rule (1-4:09:00-19:00,5:08:00-16:00) across 1 commits.\n');

  const bad = cli(clean, ['--rule', 'bogus']);
  assert.equal(bad.status, 1);
  assert.equal(bad.stderr, 'commit-hours-audit: invalid --rule segment: "bogus" (expected e.g. 1-4:09:00-19:00)\n');

  const unknown = cli(clean, ['--nope']);
  assert.equal(unknown.status, 1);
  assert.equal(unknown.stderr, 'commit-hours-audit: unknown argument: --nope (try --help)\n');

  const outside = cli(scratch, []);
  assert.equal(outside.status, 1);
  assert.equal(outside.stderr, 'commit-hours-audit: not inside a git repository\n');

  const repo = freshRepo();
  const ranged = cliJson(repo, ['--range', `${ids.b}..main`]);
  const direct = ranged.violations.filter((v) => !v.pulledIn).map((v) => v.sha).sort();
  assert.deepEqual(direct, [ids.x, ids.p, ids.f1].sort());
  assert.ok(!ranged.violations.some((v) => v.sha === ids.a || v.sha === ids.b), 'commits before the range stay out');
});

test('morning slot never proposes the first working minute', () => {
  const { repo, shas } = smallRepo('morning', [
    ['weekend', '2026-10-04T12:00:00+02:00'],
    ['early', '2026-10-05T10:00:00+02:00'],
    ['late', '2026-10-05T18:00:00+02:00'],
  ]);
  const json = cliJson(repo, ['--slot', 'morning']);
  const late = json.violations.find((v) => v.sha === shas[2]);
  assert.equal(late.proposed, '2026-10-05T08:59:59+02:00');
});

function signedCommit(repo, message, parent, ts) {
  const tree = git(repo, 'rev-parse', `${parent}^{tree}`);
  const body = `tree ${tree}\nparent ${parent}\nauthor Tester <tester@example.com> ${ts} +0200\n` +
    `committer Tester <tester@example.com> ${ts} +0200\n` +
    'gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEEfakefakefake\n -----END PGP SIGNATURE-----\n' +
    `\n${message}\n`;
  const res = spawnSync('git', ['hash-object', '-w', '-t', 'commit', '--stdin'], { cwd: repo, env: env(), input: body, encoding: 'utf8' });
  assert.equal(res.status, 0, res.stderr);
  const sha = res.stdout.trim();
  git(repo, 'update-ref', 'refs/heads/main', sha);
  git(repo, 'reset', '-q', '--hard');
  return sha;
}

test('only history after a planned commit is rewritten, so earlier signed commits keep their id', () => {
  const { repo, shas } = smallRepo('signed', [['base', '2026-10-04T12:00:00+02:00']]);
  const before = signedCommit(repo, 'signed before', shas[0], 1791108000); // Sun 2026-10-04 14:00
  const violator = commit(repo, 'violator', '2026-10-05T10:00:00+02:00', undefined, { 'v.txt': 'v\n' });
  signedCommit(repo, 'signed after', violator, 1791230400); // Mon 2026-10-05 22:00
  const { json, dir } = planOf(repo);
  assert.ok(json.warnings.some((w) => /1 signed commit\(s\) will lose their signature/.test(w)), json.warnings.join('\n'));
  const res = cli(repo, ['apply', dir]);
  assert.equal(res.status, 0, res.stderr);
  assert.equal(git(repo, 'rev-parse', 'main~2'), before, 'the signed commit before the violator is untouched');
  assert.match(git(repo, 'cat-file', 'commit', 'main~2'), /^gpgsig /m);
  assert.notEqual(git(repo, 'rev-parse', 'main~1'), violator);
});

test('apply stops when git-filter-repo is missing', () => {
  const gitDir = path.dirname(run('sh', ['-c', 'command -v git'], scratch).stdout.trim());
  const probe = run('git', ['filter-repo', '--version'], scratch, { PATH: gitDir });
  if (probe.status === 0) return; // installed system-wide: nothing to prove here
  const repo = freshRepo();
  const { dir } = planOf(repo);
  const refs = refsSnapshot(repo);
  const res = cli(repo, ['apply', dir], { PATH: gitDir });
  assert.equal(res.status, 1);
  assert.match(res.stderr, /git-filter-repo is required/);
  assert.equal(refsSnapshot(repo), refs);
});
