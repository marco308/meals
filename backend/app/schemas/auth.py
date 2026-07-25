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
