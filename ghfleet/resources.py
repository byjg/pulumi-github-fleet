"""Turning a configuration into Pulumi resources."""

import json
import os
import urllib.error
import urllib.request

import pulumi
import pulumi_github as github

from .config import (
    DEFAULT_BRANCH_PROTECTION_RULES,
    REPOSITORY_SETTINGS_KEYS,
    branch_protection_of,
    repositories_of,
    repository_settings_of,
    ruleset_name_of,
)


def github_api_get(path, token):
    """GET a GitHub REST API path and return the parsed JSON, or None on any HTTP error."""
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.URLError:
        return None


def get_owner_plan(owner, token):
    """
    Return the GitHub plan name for an owner ("free", "pro", "team", ...), or None
    if it cannot be determined.

    The plan lives in a different place depending on the owner type:
      - Organization: GET /orgs/{owner}  (token needs the admin:org scope)
      - User account: GET /user          (token needs the read:user scope)
    """
    account = github_api_get(f"/users/{owner}", token)
    if account is None:
        return None

    if account.get("type") == "Organization":
        data = github_api_get(f"/orgs/{owner}", token)
    else:
        # /user is the *authenticated* user, which is only the plan we want
        # when the token actually belongs to this owner.
        data = github_api_get("/user", token)
        if data is not None and data.get("login") != owner:
            return None

    return ((data or {}).get("plan") or {}).get("name")


def token_from_environment(owner):
    """The token for an owner, from GITHUB_ACCESS_TOKEN_<owner>."""
    variable = f"GITHUB_ACCESS_TOKEN_{owner.replace('-', '_')}"
    token = os.environ.get(variable)
    if token is None:
        raise Exception(f"{variable} is not set. Aborting.")
    return token


def secret_value_from_environment(secret):
    """Value from the configuration, else from the environment variable named after it."""
    value = secret.get("value")
    return value if value is not None else os.environ.get(secret["name"])


def apply(config, *, token_for=token_from_environment, secret_value=secret_value_from_environment):
    """
    Declare every resource the configuration describes.

    Args:
        config: The configuration, as `load_config` returns it.
        token_for: Called with an owner, returns its GitHub token. Defaults to reading
            GITHUB_ACCESS_TOKEN_<owner> from the environment, raising when it is unset.
        secret_value: Called with a `secrets` entry, returns the value to write.
            Defaults to the entry's own `value`, else the environment variable named
            after the secret.
    """
    # Create GitHub providers once per unique owner
    providers = {}
    owner_plans = {}
    for repository in config.get("repository", []):
        owner = repository["owner"]
        if owner not in providers:
            access_token = token_for(owner)
            providers[owner] = github.Provider(f"github-{owner}", token=access_token, owner=owner)
            owner_plans[owner] = get_owner_plan(owner, access_token)

    # Build a comprehensive map of all repositories and their configurations
    # This prevents duplicate resource creation if a repo appears in multiple configs
    repo_configs = {}  # Key: "owner/repo_name", Value: merged config

    for repository in config.get("repository", []):
        owner = repository["owner"]

        repo_list = repositories_of(repository, config)

        # Get settings for this configuration
        workflow_permissions = repository.get("workflow_permissions", {})
        branch_protection_config = branch_protection_of(repository)
        secrets_config = repository.get("secrets", [])
        repository_settings_config = repository_settings_of(repository)

        # Process each repository and merge configs if it appears multiple times
        for repo_name_item in repo_list:
            full_name = f"{owner}/{repo_name_item}"

            if full_name not in repo_configs:
                # First time seeing this repo
                repo_configs[full_name] = {
                    "owner": owner,
                    "repo_name": repo_name_item,
                    "workflow_permissions": workflow_permissions,
                    "branch_protection": branch_protection_config,
                    "secrets": list(secrets_config),  # Copy the list
                    "repository_settings": dict(repository_settings_config),  # Copy the dict
                }
            else:
                # Repo already seen, merge configurations
                existing = repo_configs[full_name]

                # Merge workflow permissions (use most permissive)
                if workflow_permissions:
                    existing["workflow_permissions"] = workflow_permissions

                # Merge branch protection (enable if any config enables it)
                if branch_protection_config.get("enabled", False):
                    existing["branch_protection"] = branch_protection_config

                # Merge repository settings (a later entry wins on the same key)
                existing["repository_settings"].update(repository_settings_config)

                # Merge secrets (add new ones, avoid duplicates)
                existing_secret_names = {s["name"] for s in existing["secrets"]}
                for secret in secrets_config:
                    if secret["name"] not in existing_secret_names:
                        existing["secrets"].append(secret)

    # Abort before declaring any secret: one without a value would overwrite the
    # real secret with an empty string in every repository that declares it.
    missing_secrets = sorted(
        {
            secret["name"]
            for repo_config in repo_configs.values()
            for secret in repo_config["secrets"]
            if not secret_value(secret)
        }
    )
    if missing_secrets:
        raise Exception(
            f"Secrets without a value: {', '.join(missing_secrets)}. Load env.txt. Aborting."
        )

    # Now process each unique repository once with merged configuration
    for full_name, repo_config in repo_configs.items():
        owner = repo_config["owner"]
        repo_name_item = repo_config["repo_name"]
        workflow_permissions = repo_config["workflow_permissions"]
        branch_protection_config = repo_config["branch_protection"]
        secrets_config = repo_config["secrets"]
        repository_settings = repo_config["repository_settings"]

        provider = providers[owner]
        resource_prefix = full_name.replace("/", "-").replace("_", "-")

        # 1) Get the repository data source
        repo_data = github.get_repository(
            full_name=full_name, opts=pulumi.InvokeOptions(provider=provider)
        )

        # 2) Repository settings
        # The provider only exposes these through the whole repository resource. The
        # repositories already exist, so `import_` adopts each one on the run that first
        # declares it -- no import script, and a repository added to config.yml later is
        # adopted the same way. Pulumi skips the import once the resource is in the state.
        # Every property config.yml does not declare is fed back from the data source: the
        # desired state then equals reality and `pulumi preview` shows a diff only for what
        # was actually declared, instead of resetting descriptions and topics to a default.
        if repository_settings:
            baseline = {key: getattr(repo_data, key) for key in REPOSITORY_SETTINGS_KEYS}
            # The absence of `pages` disables GitHub Pages, which is what this program
            # wants everywhere -- except on the owner's user site, the repository named
            # <owner>.github.io, where GitHub answers 422 "Deactivating GitHub pages for
            # this repository is not allowed". That restriction is not in the REST
            # documentation; it is what the API does.
            is_user_site = repo_name_item.lower() == f"{owner.lower()}.github.io"
            github.Repository(
                f"{resource_prefix}-repository",
                name=repo_name_item,
                **{**baseline, **repository_settings},
                opts=pulumi.ResourceOptions(
                    provider=provider,
                    import_=repo_name_item,
                    ignore_changes=["pages"] if is_user_site else [],
                    # A repository is never ours to delete: `pulumi destroy` drops it from
                    # the state and leaves it on GitHub.
                    retain_on_delete=True,
                ),
            )

        # 3) Set workflow permissions
        if workflow_permissions:
            default_permission = workflow_permissions.get("default", "read")
            can_approve_pr = workflow_permissions.get("can_approve_pull_request_reviews", False)

            # Set GITHUB_TOKEN default permissions
            github.WorkflowRepositoryPermissions(
                f"{resource_prefix}-workflow-permissions",
                repository=repo_name_item,
                default_workflow_permissions=default_permission,
                can_approve_pull_request_reviews=can_approve_pr,
                opts=pulumi.ResourceOptions(provider=provider),
            )

        # 4) Create/Update secrets
        for secret in secrets_config:
            secret_name = secret["name"]
            github.ActionsSecret(
                f"{resource_prefix}-secret-{secret_name.lower().replace('_', '-')}",
                repository=repo_name_item,
                secret_name=secret_name,
                plaintext_value=secret_value(secret),
                opts=pulumi.ResourceOptions(provider=provider),
            )

        # 5) Create/Update branch protection ruleset
        # Rulesets on *private* repos require a paid plan; GitHub answers 403 on the free
        # plan. Public repos support them on every plan.
        owner_plan = owner_plans.get(owner)
        rulesets_supported = repo_data.visibility != "private" or (
            owner_plan is not None and owner_plan != "free"
        )

        if branch_protection_config.get("enabled", False) and not rulesets_supported:
            if owner_plan is None:
                reason = (
                    "could not determine the plan for this owner "
                    "(the token needs read:user for user accounts, admin:org for organizations)"
                )
            else:
                reason = f"private repository on the '{owner_plan}' plan does not support rulesets"
            pulumi.log.warn(f"Skipping branch protection ruleset for {full_name}: {reason}")
        elif branch_protection_config.get("enabled", False):
            # Get the default branch from the data source
            default_branch = repo_data.default_branch

            # `rules`, `ref_name` and `bypass_actors` go to the provider exactly as they are
            # written in config.yml -- the resource accepts plain dictionaries -- so every
            # rule the provider supports is reachable without this program knowing about it.
            # `bypass_actor_ids` stays as the shorthand for repository roles:
            # 2 = Triage, 3 = Write, 4 = Maintain, 5 = Admin.
            bypass_actors = branch_protection_config.get("bypass_actors")
            if bypass_actors is None:
                bypass_actors = [
                    {"actor_id": actor_id, "actor_type": "RepositoryRole", "bypass_mode": "always"}
                    for actor_id in branch_protection_config.get("bypass_actor_ids", [5])
                ]

            github.RepositoryRuleset(
                f"{resource_prefix}-branch-protection",
                name=ruleset_name_of(branch_protection_config, default_branch),
                repository=repo_name_item,
                target=branch_protection_config.get("target", "branch"),
                enforcement=branch_protection_config.get("enforcement", "active"),
                bypass_actors=bypass_actors,
                conditions={
                    "ref_name": branch_protection_config.get(
                        "ref_name",
                        {"includes": [f"refs/heads/{default_branch}"], "excludes": []},
                    )
                },
                rules=branch_protection_config.get("rules", DEFAULT_BRANCH_PROTECTION_RULES),
                opts=pulumi.ResourceOptions(provider=provider),
            )
