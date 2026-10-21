"""Stable native bundle-inspection success/failure variants without wire changes."""

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, TypeAdapter


class AssetBundlePage(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    source: StrictStr
    bundleName: StrictStr | None
    recursive: StrictBool
    entries: list[StrictStr]
    totalEntries: StrictInt
    pageSize: StrictInt
    pageNumber: StrictInt
    maxPageSize: StrictInt
    hasMore: StrictBool


class AssetBundleSuccess(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    success: Literal[True]
    data: AssetBundlePage
    message: StrictStr | None = None


class CapabilityFailure(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    success: Literal[False]
    message: StrictStr | None = None
    error: StrictStr | None = None
    code: StrictStr | StrictInt | None = None
    data: Any = None


BUNDLE_RESULT = TypeAdapter(
    Annotated[AssetBundleSuccess | CapabilityFailure, Field(discriminator="success")]
)


def validate_bundle_result(value: Any) -> None:
    # Pydantic's Literal discriminator alone can accept integer bool equivalents.
    if not isinstance(value, dict) or type(value.get("success")) is not bool:
        raise ValueError("Bundle inspection returned an invalid success discriminator.")
    BUNDLE_RESULT.validate_python(value, strict=True)
