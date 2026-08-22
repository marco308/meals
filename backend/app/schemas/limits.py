from pydantic import BaseModel, Field


class ResourceAllowanceOut(BaseModel):
    """What this household is allowed of one resource, and how much is spent."""

    resource: str = Field(description="The resource this allowance governs, e.g. 'recipes'.")
    limit: int | None = Field(
        description=(
            "How many are allowed, or null for unlimited. This is the number the household will "
            "actually meet: whichever of its tier's cap and this server's fair-use ceiling is lower."
        )
    )
    used: int | None = Field(
        description=(
            "How many exist now, or null where the number has no meaning: an unlimited allowance "
            "(nothing was counted), or one scoped to a single meal or plan rather than the household."
        )
    )
    remaining: int | None = Field(description="limit - used, floored at zero; null whenever either is null.")
    scope: str = Field(description="Where the allowance applies: 'per household', 'in one meal', 'a month'.")
    upgradable: bool = Field(
        description=(
            "Whether a larger tier on this server would raise this limit. False for a fair-use "
            "ceiling, for the largest tier, and for a server that sells nothing."
        )
    )


class LimitsOut(BaseModel):
    """Everything this server allows the calling household."""

    tier: str = Field(description="The tier that produced these numbers.")
    limited: bool = Field(
        description=(
            "Whether this deployment limits anything at all. False on a self-hosted server that has "
            "configured nothing, where every allowance below is unlimited."
        )
    )
    resources: list[ResourceAllowanceOut]
