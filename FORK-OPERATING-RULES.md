# silocyber/hermes-agent — fork operating rules

This is a fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
Upstream is actively developed; assume conflicts are routine and design for them.

It exists so that Vigilus-specific changes to the Hermes runtime live somewhere
we control, in version control, rather than as uncommitted edits in a third
party's checkout. Hermes carries 12 of our 20 agent seats, so a change here is
a change to most of the estate.

## The one rule that is not negotiable

**Never push to `NousResearch/hermes-agent`.** It is not ours. If a change
genuinely belongs upstream, propose it upstream as a separate, deliberate
decision — do not smuggle it there via a sync job.

Every clone should enforce this mechanically rather than relying on care:

```bash
git remote set-url --push upstream DISABLED_never_push_to_upstream
```

The sync workflow does the same thing to its own checkout.

## Branches

| Branch | What it is | Who writes to it |
|---|---|---|
| `main` | A pure mirror of `upstream/main`. Carries none of our changes. | The sync workflow, fast-forward only |
| `custom` | Upstream plus our customizations, as a patch series | Humans, by merging a pull request |
| `sync/upstream-*` | Throwaway branches the sync workflow proposes from | The sync workflow |

`main` is a mirror and nothing else. If you commit to it, the sync workflow
will refuse to fast-forward and stop — by design. It will not force.

`custom` is what the runtime actually runs.

## Where customizations live

Prefer `plugins/model-providers/` and other plugin directories. Upstream treats
plugins as extension points, so changes there survive refactors that would
shred a patch to core.

Every customization must be justifiable in its own commit message, and the
message must say **why**, not just what. The next person to hit a conflict is
deciding whether to keep your change or drop it, and they will be reading that
message under time pressure with a red CI.

## Adding a customization

1. Branch from `custom`.
2. Make the change. Keep it as small and as plugin-local as it can be.
3. Open a pull request into `custom`. Upstream's own CI (`.github/workflows/ci.yaml`)
   runs on every pull request, so the fork's tests are upstream's tests.
4. A human merges. Nothing auto-merges here.

**Before you add one, check whether upstream has already solved it.** A
customization we do not need is not free — it is a rebase conflict every time
upstream touches that file, forever. This has already happened once: see
"Customizations" below.

## How the upstream sync runs

`.github/workflows/sync-from-upstream.yml`, daily at 06:00 UTC and on manual
dispatch. It does two separable things:

1. **Mirrors `main`.** Fast-forwards `main` to `upstream/main` with a plain,
   non-force push. If that cannot fast-forward, it fails loudly instead of
   forcing.
2. **Proposes a rebase of `custom`.** It rebases `custom` onto the refreshed
   `main` on a *new* throwaway branch and opens a pull request into `custom`.

**It never force-pushes, and it never writes to `custom`.** That is a hard
constraint, not a preference. A rebase that silently dropped a customization
would put us back exactly where we started, with the added danger of a branch
that looks maintained. Every change to `custom` arrives as a pull request a
human read.

Merge sync PRs with **Rebase and merge**, so `custom` stays a legible patch
series on top of upstream rather than an untraceable divergence.

### The survival check

A clean rebase is not the same as a correct one. Upstream can delete, move, or
rewrite the file we patch, and git will resolve that without any conflict at
all — leaving a branch that looks healthy and no longer does anything.

So before proposing anything, the workflow records the set of files `custom`
changes relative to upstream, and checks it again afterward:

- Set became empty → **the job fails.** No PR, no push.
- Set changed → the PR carries a warning; read it properly.

### When the sync conflicts

The workflow abandons the rebase (it never pushes a tree with conflict markers)
and opens a pull request titled `CONFLICTS, manual resolution required`,
listing both the conflicting files and the files we customize.

Resolve it by preserving our *behavior* on top of upstream's refactor. Do not
resolve by discarding our side, and do not resolve by reverting upstream.

And consider the third option, which is often the right one: if upstream has
fixed the same problem itself, **delete our customization** and say so in the
merge. That is a success, not a retreat.

## Keeping the local runtime in step

This is the part that is easy to skip and most likely to waste the whole
exercise.

The Hermes runtime executes from `~/.hermes/hermes-agent`. A fork that is
current while that directory is stale buys nothing. We have already made this
mistake once at Vigilus (VIGI-333): a correct fix merged and changed nothing,
because the process that was running executed copies nobody had updated.

After any sync PR merges, and before trusting any claim about what Hermes is
running:

```bash
scripts/hermes-local-sync.sh
```

It fast-forwards the runtime checkout to `origin/custom`, proves the working
tree matches the ref, and tells you whether a restart is needed. It refuses —
without changing anything — if the checkout is dirty, shallow, diverged, or
pointed at the wrong remote. It will not stash and it will not reset; losing
uncommitted local work is the specific failure this whole fork exists to end.

A restart is required. Updating the files under a running process changes
nothing about the process.

## Customizations

### `get_max_tokens` / per-model `max_output_tokens` — **not carried; upstream fixed it**

Recorded here because it is the reason this fork was created, and because
somebody will otherwise rediscover it.

We ran for weeks with a `CustomProfile.get_max_tokens` override that read
`model_overrides.<provider>.<model>.max_output_tokens`, because the `custom`
provider set `default_max_tokens=65536` — a floor added for bare-metal Ollama
endpoints, which truncate to `num_predict=128` without one. On a model with a
131k context window that floor reserved half the window for output that was
never produced, and real inputs failed.

Upstream removed it in `fd3565deec` (2026-09-07), for our reason stated in our
terms: *"An arbitrary client ceiling can exceed a local server's actual output
limit. The endpoint owns its generation default."* `custom` now sends no
`max_tokens` at all unless the caller sets one.

Upstream went further and deleted user-facing output caps generally, including
`model_overrides.*.*.max_output_tokens` — see
`website/docs/integrations/providers.md` and the regression test
`tests/gateway/test_output_caps_removed.py`, which exists to keep them deleted.

So the override is not on `custom`. Re-adding it would reintroduce the exact
mechanism upstream deliberately removed and would fight a test written to
prevent it. It is preserved on the `custom-max-output-tokens-override` branch
for the record.

The operational lesson is the one above: our checkout was three weeks stale, so
we were carrying a patch for a bug upstream had already fixed. Run
`scripts/hermes-local-sync.sh` before diagnosing anything.

## Operator setup

The sync workflow needs a repository secret named **`SYNC_PAT`**.

`GITHUB_TOKEN` is deliberately not used: pull requests opened with it do not
trigger other workflows, so upstream CI would never run on a sync PR and we
would be merging unreviewed, untested upstream code.

Required scope, classic personal access token:

- `repo` — push the proposal branch, open the pull request
- `workflow` — an upstream sync routinely changes files under
  `.github/workflows/`, and GitHub rejects such a push without this scope

Or, as a fine-grained token scoped to `silocyber/hermes-agent` only:
Contents **read and write**, Pull requests **read and write**, Workflows
**read and write**.

Set it with:

```bash
gh secret set SYNC_PAT --repo silocyber/hermes-agent
```

Until it is set, the workflow fails at checkout. That is the intended
behavior — a sync that cannot open a PR must not fall back to anything else.
