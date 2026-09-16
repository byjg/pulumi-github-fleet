"""
Declare GitHub repositories, secrets, workflow permissions and branch protection
rulesets from a configuration dictionary.

    from ghfleet import apply, load_config

    apply(load_config("config.yml"))

`apply` takes the configuration as a plain dictionary, so it does not care whether it
came from YAML, JSON or was built in code. The two conventions it would otherwise
impose -- where the token of an owner comes from, and where the value of a secret
comes from -- are its keyword arguments.
"""

from .config import (
    BRANCH_PROTECTION_KEYS,
    DEFAULT_BRANCH_PROTECTION_RULES,
    REPOSITORY_SETTINGS_KEYS,
    branch_protection_of,
    load_config,
    repositories_of,
    repository_settings_of,
    resolve_list_references,
    ruleset_name_of,
)
from .resources import (
    apply,
    get_owner_plan,
    secret_value_from_environment,
    token_from_environment,
)

__all__ = [
    "BRANCH_PROTECTION_KEYS",
    "DEFAULT_BRANCH_PROTECTION_RULES",
    "REPOSITORY_SETTINGS_KEYS",
    "apply",
    "branch_protection_of",
    "get_owner_plan",
    "load_config",
    "repositories_of",
    "repository_settings_of",
    "resolve_list_references",
    "ruleset_name_of",
    "secret_value_from_environment",
    "token_from_environment",
]
