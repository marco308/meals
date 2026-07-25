"""Premium-vs-budget tagging (decision Q17).

Some ingredients repay the extra money (olive oil, parmesan, chocolate) and
some really don't (plain flour, tinned tomatoes for a long-cooked sauce). The
household's verdict lives on the ingredient, so it shows up at the moment it
matters — standing in the aisle deciding which one to put in the trolley.

There is no guessing table here on purpose: unlike an aisle, "is the posh one
worth it" is a household's own taste and budget, so a tier only ever gets set
by the user or their AI. The vocabulary is part of the published skill.
"""

# tier, label, badge — the badge is what shopping-list renderings show.
VALUE_TIERS: list[tuple[str, str, str]] = [
    ("premium", "Worth paying up for", "⭐"),
    ("budget", "Own-brand is fine", "💷"),
    ("any", "No strong opinion", ""),
]

DEFAULT_VALUE_TIER = "any"
VALUE_TIER_NAMES = [tier for tier, _, _ in VALUE_TIERS]
_LABELS = {tier: label for tier, label, _ in VALUE_TIERS}
_BADGES = {tier: badge for tier, _, badge in VALUE_TIERS}

# Reused verbatim in 4xx bodies and tool descriptions — an agent that reads it
# should know what to send next without another round-trip.
VALUE_TIER_HINT = "valid tiers: " + ", ".join(f"'{tier}' ({label.lower()})" for tier, label, _ in VALUE_TIERS)


def is_valid_value_tier(tier: str) -> bool:
    return tier in _LABELS


def value_tier_label(tier: str) -> str:
    return _LABELS.get(tier, _LABELS[DEFAULT_VALUE_TIER])


def value_tier_badge(tier: str) -> str:
    return _BADGES.get(tier, "")
