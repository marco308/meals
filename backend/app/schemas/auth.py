import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)  # bcrypt operates on the first 72 bytes
    display_name: str = Field(min_length=1, max_length=200)
    # Both optional, so a client written against the pre-Q19 API still registers
    # (into a household of its own — see the endpoint docstring).
    invite_code: str | None = Field(
        default=None,
        max_length=64,
        description="A household invite code from POST /auth/invites. Omit to start a new, empty household.",
    )
    household_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Names the new household. Ignored when invite_code is given. Defaults to 'Home'.",
    )


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)  # bcrypt operates on the first 72 bytes


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    created_at: datetime
    # Which household this account is in. Added with Q19: once a server holds
    # more than one, "did my invite land me in the right place?" is a question
    # the client has to be able to answer.
    household_id: uuid.UUID
    household_name: str | None = None
    # Q23: which member leads this household. A client compares it with `id` to
    # decide whether to offer the invite and remove controls at all, rather than
    # offering them and letting the server refuse.
    household_lead_user_id: uuid.UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def _pull_household_name(cls, data: object) -> object:
        """Flatten `user.household.name` onto the schema. Iterating
        `model_fields` rather than listing them keeps this correct as fields are
        added."""
        household = getattr(data, "household", None)
        if household is None:
            return data
        flattened = {name: getattr(data, name) for name in cls.model_fields if hasattr(data, name)}
        flattened["household_name"] = household.name
        flattened["household_lead_user_id"] = household.lead_user_id
        return flattened


class AuthOut(BaseModel):
    token: str
    token_type: str = "bearer"
    user: UserOut


class TokenCreateIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class TokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    label: str | None
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


class TokenCreatedOut(TokenOut):
    token: str  # the plaintext PAT — shown exactly once


class AccountDeleteIn(BaseModel):
    # In the body, never a query parameter — a password in a URL ends up in
    # access logs and proxy caches.
    password: str = Field(description="Your current password, to confirm the deletion.")


class AccountDeletedOut(BaseModel):
    household_deleted: bool
    detail: str


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    code: str = Field(min_length=1, max_length=64, description="The code from the reset email.")
    new_password: str = Field(min_length=8, max_length=72)  # bcrypt operates on the first 72 bytes


class AcceptedOut(BaseModel):
    detail: str


class InviteCreateIn(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=90)


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_by_user_id: uuid.UUID | None


class InviteCreatedOut(InviteOut):
    code: str  # the plaintext invite code — shown exactly once


class HouseholdMemberOut(BaseModel):
    """One person in the household, as the other members see them.

    Emails are included deliberately: everyone here already shares a recipe
    library, a plan and a shopping list, so an address is not the secret in the
    room — and "which of these two accounts is my partner's" needs answering.
    """

    id: uuid.UUID
    display_name: str
    email: str
    created_at: datetime
    is_lead: bool
    # Who admitted them, from the invite they redeemed. None for the person who
    # started the household, and None once their inviter deletes their account
    # (the reference is SET NULL, decision Q20).
    invited_by_user_id: uuid.UUID | None = None


class HouseholdOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    lead_user_id: uuid.UUID | None
    members: list[HouseholdMemberOut]


class HouseholdUpdateIn(BaseModel):
    """Both fields are optional, but sending neither is a mistake worth naming
    rather than a no-op worth pretending succeeded."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    lead_user_id: uuid.UUID | None = Field(
        default=None,
        description="Hand the lead to another member of this household. They must already be in it.",
    )

    @model_validator(mode="after")
    def _something_to_do(self) -> "HouseholdUpdateIn":
        if self.name is None and self.lead_user_id is None:
            raise ValueError("send a name, a lead_user_id, or both — this request changes nothing")
        return self


class MemberRemovedOut(BaseModel):
    """`you_left` is the difference between "they are gone" and "you are". When
    it is true the caller's own household has changed under them, and their
    next read of anything will be of somewhere else."""

    removed_user_id: uuid.UUID
    you_left: bool
    detail: str


class InviteRedeemIn(BaseModel):
    code: str = Field(min_length=1, max_length=64, description="The invite code, as it was given to you.")
    force: bool = Field(
        default=False,
        description=(
            "Required when you are the only member of your current household: it holds recipes and "
            "history that nobody will be able to reach once you leave, and this is you saying they may go."
        ),
    )
