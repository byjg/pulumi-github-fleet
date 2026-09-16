"""Reading and validating the configuration."""

import yaml

# Repository properties this program is able to manage: every one of them is both
# readable from the `github.get_repository` data source and writable on the
# `github.Repository` resource. `repository_settings` in config.yml accepts exactly
# these keys, and whatever it leaves out is fed back from the data source so Pulumi
# never overwrites it with a provider default.
#
# Left out on purpose: `name` (set from the config), `default_branch` (deprecated on
# the resource, use `BranchDefault`), `private` (superseded by `visibility`), `fork`
# (a creation-time input), and `has_downloads` (deprecated on the provider, which
# warns once per repository on every run).
#
# `pages` is deliberately absent as well, and that absence is what disables GitHub
# Pages: the provider calls DisablePages when the property is empty. The documentation
# is served from Cloudflare (opensource.byjg.com), so no repository here should have a
# Pages site -- the one exception is the owner's user site, handled in __main__.py.
# It could not be declared anyway -- the data source returns a list while the resource
# takes a single object.
#
# `allow_forking` is also out. It only configures private forking of *organization*
# owned private repositories, and even there it depends on an organization-level
# policy; on a public repository it means nothing. The provider documents the correct
# use as leaving it unset, and only sends it when the value changes on a non-public
# repository -- declaring it made GitHub answer 422 on byjg/boletimdeurna, a private
# repository of a user account, where the setting does not exist at all.
REPOSITORY_SETTINGS_KEYS = (
    "allow_auto_merge",
    "allow_merge_commit",
    "allow_rebase_merge",
    "allow_squash_merge",
    "allow_update_branch",
    "archived",
    "delete_branch_on_merge",
    "description",
    "has_discussions",
    "has_issues",
    "has_projects",
    "has_wiki",
    "homepage_url",
    "is_template",
    "merge_commit_message",
    "merge_commit_title",
    "squash_merge_commit_message",
    "squash_merge_commit_title",
    "topics",
    "visibility",
)


# Keys accepted inside a `branch_protection` block. `rules`, `bypass_actors` and
# `ref_name` are handed to the provider as they are written, so every rule the
# provider supports is available from config.yml without any code here knowing it.
BRANCH_PROTECTION_KEYS = frozenset(
    {
        "enabled",
        "enforcement",
        "name",
        "target",
        "ref_name",
        "rules",
        "bypass_actors",
        "bypass_actor_ids",
    }
)

# The policy applied when a `branch_protection` block does not carry its own `rules`.
# It is a default, not a rule of the program: write `rules` in config.yml to replace it.
DEFAULT_BRANCH_PROTECTION_RULES = {
    "pull_request": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": True,
        "require_code_owner_review": False,
        "require_last_push_approval": False,
        "required_review_thread_resolution": False,
        "allowed_merge_methods": ["merge", "squash", "rebase"],
    },
    "deletion": True,
    "non_fast_forward": True,
    "required_linear_history": False,
    "required_signatures": False,
}


def load_config(path):
    """Load a configuration file in the format this package expects."""
    with open(path) as file:
        return yaml.load(file, Loader=yaml.FullLoader)


def resolve_list_references(list_items, lists_config):
    """
    Resolve list references to actual repository names.

    Args:
        list_items: List that can contain either repository names or list references
        lists_config: Dictionary of named lists from the config

    Returns:
        List of actual repository names
    """
    resolved = []
    for item in list_items:
        # Check if this item is a reference to a named list
        if item in lists_config:
            # It's a list reference, expand it
            resolved.extend(lists_config[item])
        else:
            # It's a direct repository name
            resolved.append(item)
    return resolved


def repositories_of(entry, config):
    """Every repository name a `repository` entry covers, lists resolved."""
    resolved_from_lists = resolve_list_references(entry.get("list", []), config.get("lists", {}))
    return resolved_from_lists + entry.get("repositories", [])


def repository_settings_of(entry):
    """The `repository_settings` block of an entry, rejecting keys GitHub would ignore."""
    settings = entry.get("repository_settings", {})
    unknown = sorted(set(settings) - set(REPOSITORY_SETTINGS_KEYS))
    if unknown:
        raise Exception(
            f"Unknown repository_settings in '{entry.get('name', 'unnamed')}': "
            f"{', '.join(unknown)}. Aborting."
        )
    return settings


def branch_protection_of(entry):
    """The `branch_protection` block of an entry, rejecting keys the program ignores."""
    config = entry.get("branch_protection", {})
    unknown = sorted(set(config) - BRANCH_PROTECTION_KEYS)
    if unknown:
        raise Exception(
            f"Unknown branch_protection in '{entry.get('name', 'unnamed')}': "
            f"{', '.join(unknown)}. Aborting."
        )
    return config


def ruleset_name_of(branch_protection, default_branch):
    """The ruleset name, which import_rulesets.py has to match to adopt an existing one."""
    return branch_protection.get("name", f"Protect {default_branch}")
