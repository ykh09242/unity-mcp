"""Declarative verification profiles and closed action inputs."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from models.script_quality import QualityId, QualityOptions, ScriptSelection

ProfileId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
Selectors = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=512)]], Field(max_length=128)
]


class StageDeadlines(QualityOptions):
    freshness: Annotated[int, Field(ge=1, le=300)] = 120
    analysis: Annotated[int, Field(ge=1, le=300)] = 120
    impact: Annotated[int, Field(ge=1, le=120)] = 30
    tests: Annotated[int, Field(ge=1, le=900)] = 300
    console: Annotated[int, Field(ge=1, le=60)] = 30
    final: Annotated[int, Field(ge=1, le=120)] = 30


class VerificationProfile(QualityOptions):
    schema_version: Literal[1] = 1
    refresh_policy: Literal["never", "if_needed"] = "if_needed"
    analysis_required: bool = True
    diagnostic_scope: Literal["all", "introduced"] = "all"
    fail_on: Annotated[list[Literal["error", "warning", "info"]], Field(max_length=3)] = Field(
        default_factory=lambda: ["error"]
    )
    impact: Literal["off", "if_baseline", "required"] = "if_baseline"
    test_mode: Literal["EditMode", "PlayMode"] | None = None
    test_names: Selectors = Field(default_factory=list)
    assembly_names: Selectors = Field(default_factory=list)
    category_names: Selectors = Field(default_factory=list)
    deadlines: StageDeadlines = Field(default_factory=StageDeadlines)

    @model_validator(mode="after")
    def consistent(self):
        if self.test_mode is None and (
            self.test_names or self.assembly_names or self.category_names
        ):
            raise ValueError("Test selectors require an explicit mode")
        if len(set(self.fail_on)) != len(self.fail_on):
            raise ValueError("Duplicate severity policy")
        if not self.analysis_required and (
            self.impact == "required" or self.diagnostic_scope == "introduced"
        ):
            raise ValueError("Required impact and diagnostic deltas require analysis")
        return self


class ProfileWrite(QualityOptions):
    profile_id: ProfileId
    profile: VerificationProfile
    expected_hash: Annotated[str, Field(pattern=r"^(missing|[a-f0-9]{64})$")]


class VerificationPrepare(ScriptSelection):
    profile_id: ProfileId = "script-check"
    capture_baseline: bool = False
    baseline_id: QualityId | None = None

    @model_validator(mode="after")
    def baseline_choice(self):
        if self.capture_baseline and self.baseline_id is not None:
            raise ValueError("Choose capture_baseline or baseline_id")
        return self


class VerificationStart(QualityOptions):
    preparation_id: QualityId


class VerificationRunOptions(QualityOptions):
    run_id: QualityId


class VerificationStatus(QualityOptions):
    run_id: QualityId | None = None
    preparation_id: QualityId | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.run_id is None) == (self.preparation_id is None):
            raise ValueError("Choose run_id or preparation_id")
        return self


VerificationAction = Literal[
    "profiles", "set_profile", "prepare", "run", "status", "cancel", "report"
]


def parse_verification_options(action, options):
    models = {
        "profiles": QualityOptions,
        "set_profile": ProfileWrite,
        "prepare": VerificationPrepare,
        "run": VerificationStart,
        "status": VerificationStatus,
        "cancel": VerificationRunOptions,
        "report": VerificationRunOptions,
    }
    if action not in models:
        raise ValueError("unsupported_verification_action")
    return models[action].model_validate({} if options is None else options)
