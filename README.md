---
sidebar_key: pulumi-github-fleet
tags: [python, devops]
---

# ghfleet

[![Build](https://github.com/byjg/pulumi-github-fleet/actions/workflows/build.yml/badge.svg)](https://github.com/byjg/pulumi-github-fleet/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/ghfleet.svg)](https://pypi.org/project/ghfleet/)

Declare a fleet of GitHub repositories from one configuration file, with Pulumi:
repository settings, Actions secrets, workflow permissions and branch protection
rulesets.

It exists because the interesting part of managing dozens of repositories is not the
API calls, it is answering "which repositories get this, and what is it exactly?" in a
file you can read, review and diff.

## Install

```bash
pip install ghfleet     # or: uv add ghfleet
```

## Use

```python
import pulumi

from ghfleet import apply, load_config

apply(load_config("config.yml"))

pulumi.export("summary", "GitHub repositories configured successfully")
```

That is the whole Pulumi program. `apply` takes the configuration as a plain
dictionary, so it does not care whether it came from YAML, JSON or was built in code.

### The two conventions are yours to change

`apply` would otherwise impose where a token and a secret value come from. Both are
keyword arguments, with the obvious behaviour as the default:

| Argument | Default |
|---|---|
| `token_for(owner)` | reads `GITHUB_ACCESS_TOKEN_<owner>` from the environment, raising when unset |
| `secret_value(secret)` | the entry's own `value`, else the environment variable named after the secret |

```python
apply(
    load_config("config.yml"),
    token_for=lambda owner: vault.read(f"github/{owner}").token,
    secret_value=lambda secret: vault.read(secret["name"]).value,
)
```

## The configuration

```yaml
lists:
  services:
    - api-gateway
    - billing
    - notifications

repository:
  - name: services
    owner: acme
    list:
      - services
    repositories:
      - one-off-repo
    repository_settings:
      allow_update_branch: true
    workflow_permissions:
      default: write
      can_approve_pull_request_reviews: false
    branch_protection:
      enabled: true
      bypass_actor_ids: [5]
    secrets:
      - name: REGISTRY_USER
        value: acme
      - name: REGISTRY_TOKEN
```

- **`lists`** — named lists of repository names, so a repository set is written once and
  referenced by several entries.
- **`repository`** — the entries. Each one names an `owner`, the repositories it covers
  (through `list`, `repositories`, or both), and what they get. A repository named by
  more than one entry receives the union.

### `repository_settings`

Declare **only** the settings that must be guaranteed. The accepted keys are in
`REPOSITORY_SETTINGS_KEYS`; an unknown one aborts the run.

A repository that declares this block becomes a `github.Repository` resource, adopted
into the state on the first run that declares it — no import step. The resource covers
the *whole* repository, so everything the block does not declare (description, topics,
visibility, the other merge options) is read back from the `github.get_repository` data
source and handed to the resource unchanged. Two consequences:

- `pulumi preview` shows a diff only for the keys you declared, which is what makes it
  a drift report;
- an undeclared property is *not* enforced: change the description on GitHub and the
  next run simply follows it.

The resource is declared with `retain_on_delete`, so neither `pulumi destroy` nor
removing a line from the configuration deletes a repository. Its secrets and ruleset
*are* removed — only the repository is retained.

`pages` is not an accepted key, and that absence is meaningful: the provider reads it
as "no Pages site" and disables Pages on any repository that still has one. The one
exception is the owner's user site, the repository named `<owner>.github.io`, which is
recognised by name and left alone (see "GitHub says no", below).

### `branch_protection`

```yaml
branch_protection:
  enabled: true
  enforcement: active          # active | evaluate | disabled
  bypass_actor_ids: [5]        # 2 = Triage, 3 = Write, 4 = Maintain, 5 = Admin
  rules:
    pull_request:
      required_approving_review_count: 2
      require_code_owner_review: true
      allowed_merge_methods: [squash]
    required_status_checks:
      strict_required_status_checks_policy: true
      required_checks:
        - context: "Build"
    deletion: true
    non_fast_forward: true
```

`rules`, `ref_name` and `bypass_actors` are handed to the provider **exactly as
written** — the resource accepts plain dictionaries — so every rule
`github.RepositoryRuleset` supports is reachable from the configuration without this
package knowing about it. The keys are the provider's, in snake_case.

Left out, `rules` falls back to `DEFAULT_BRANCH_PROTECTION_RULES`: one approving
review, dismiss stale reviews on push, all three merge methods, no branch deletion and
no force push. It is a default, not a rule of the package — writing `rules` replaces it
entirely rather than merging into it.

Other keys: `name` (default `Protect <default branch>`), `target` (`branch` or `tag`),
`ref_name` (`{includes, excludes}`, default the default branch), and `bypass_actors`
for anything that is not a repository role. An unknown key aborts the run: on a branch
protection block, a silent typo means a rule that is not enforced.

Note that rulesets on **private** repositories require a paid plan. On the free plan
GitHub answers 403, so the ruleset is skipped with a warning instead of failing the
run.

### `secrets`

```yaml
secrets:
  - name: REGISTRY_USER
    value: acme          # in the file, for values that are not secret
  - name: REGISTRY_TOKEN # no value: taken from the environment
```

The run aborts before declaring anything if a secret has no value anywhere. That check
is not politeness: an empty value would overwrite the real secret with an empty string
in every repository that declares it.

### Sharing blocks between entries

Entries repeat themselves. Use YAML anchors — a feature of the file format, so nothing
here has to implement it:

```yaml
x-defaults:
  branch_protection: &protected
    enabled: true
    bypass_actor_ids: [5]

repository:
  - name: services
    branch_protection: *protected

  - name: legacy
    branch_protection:
      <<: *protected            # inherit, then override
      enforcement: "disabled"
```

Precedence stays local and explicit: a key written next to `<<:` wins.

## GitHub says no

Two settings answer 422 whatever you send, and both cost a failed run to discover.

**`allow_forking` on a private repository of a user account.** The repository-level
forking policy only exists for private repositories owned by an *organization*, and
even there an organization owner has to allow forking of private repositories first.
On a public repository the setting means nothing. It is therefore not among the
accepted keys, which is also what the provider recommends — its schema says to "leave
unset for the default behaviour". The restriction is not in the REST reference; it is
in the [forking policy guide](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/managing-the-forking-policy-for-your-repository).

**Deactivating GitHub Pages on the owner's user site.** The repository named
`<owner>.github.io` cannot have its Pages site unpublished:

```
DELETE /repos/<owner>/<owner>.github.io/pages: 422 Deactivating GitHub pages
for this repository is not allowed
```

This one is documented nowhere — neither the REST reference nor the "Unpublishing a
GitHub Pages site" guide mentions it, and that guide draws no distinction between a
user site and a project site. It is only what the API does, so the package recognises
the repository by name and keeps `pages` out of its diff.

## What this is not

An ordinary Python package, not a Pulumi component: it declares its resources into the
stack that calls it, and can only be consumed from Python.

## License

MIT. See [LICENSE](LICENSE).
