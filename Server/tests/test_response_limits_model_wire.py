"""Bounds follow the installed MCP serializer without executing extension hooks."""

import json
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastmcp.tools.base import ToolResult
from fastmcp.resources.base import ResourceResult
from mcp.server.runner import _dump_result
from mcp_types import CallToolResult, TextContent
from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PrivateAttr,
    computed_field,
    field_serializer,
    model_serializer,
)

from models import response_limits as limits
from transport import response_limit_middleware as middleware


def wire_bytes(result):
    return json.dumps(
        _dump_result(result.to_mcp_result()), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


async def admit(raw, monkeypatch, budget=500):
    monkeypatch.setattr(middleware, "MAX_RESPONSE_BYTES", 32_768 + budget)
    result = ToolResult.from_mcp_result(raw)

    async def call_next(_context):
        return result

    return await middleware.ResponseLimitMiddleware().on_call_tool(None, call_next)


class ExtraResult(CallToolResult):
    model_config = ConfigDict(extra="allow")


@pytest.mark.asyncio
async def test_raw_extra_is_rejected_when_installed_sdk_wire_exceeds_budget(monkeypatch):
    # Given: extras live outside __dict__, but the SDK emits them.
    raw = ExtraResult(content=[], extension="x" * 10_000)
    assert len(wire_bytes(ToolResult.from_mcp_result(raw))) > 500
    # When: the actual middleware admits a raw MCP result.
    result = await admit(raw, monkeypatch)
    # Then: only the constant-sized tool error reaches SDK serialization.
    assert result.is_error
    assert len(wire_bytes(result)) <= 500


@pytest.mark.asyncio
async def test_raw_extra_and_aliases_survive_when_wire_fits_budget(monkeypatch):
    # Given: ordinary MCP metadata and small extension fields.
    raw = ExtraResult(
        content=[TextContent(text="ok", _meta={"kind": "plain"})],
        _meta={"request": 7},
        structured_content={"ok": True},
        extension="small",
    )
    expected = _dump_result(raw)
    # When: the bounded result traverses middleware and the real SDK dump.
    result = await admit(raw, monkeypatch)
    # Then: the exact emitted aliases, metadata and extras remain present.
    assert _dump_result(result.to_mcp_result()) == expected
    assert not result.is_error


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", ["field", "model", "dump"])
async def test_extension_serializers_are_rejected_without_invocation(monkeypatch, hook):
    # Given: a serializer whose allocation cannot be bounded by stored fields.
    calls = []

    class FieldResult(CallToolResult):
        extension: str = "small"

        @field_serializer("extension")
        def expand(self, _value):
            calls.append("field")
            return "x" * 10_000

    class ModelResult(CallToolResult):
        @model_serializer
        def expand(self):
            calls.append("model")
            return {"content": [], "extension": "x" * 10_000}

    class DumpResult(CallToolResult):
        def model_dump(self, **_kwargs):
            calls.append("dump")
            return {"content": [], "extension": "x" * 10_000}

    raw = {"field": FieldResult, "model": ModelResult, "dump": DumpResult}[hook](content=[])
    # When: the result is inspected before the SDK calls the hook.
    result = await admit(raw, monkeypatch)
    # Then: unsupported hooks never allocate or run a second time downstream.
    assert result.is_error
    assert calls == []
    assert len(wire_bytes(result)) <= 500
    assert calls == []


def test_model_alias_is_measured_when_it_exceeds_wire_budget():
    # Given: a short storage name and an oversized serialization alias.
    class Aliased(BaseModel):
        value: str = Field(serialization_alias="x" * 1000)

    value = Aliased(value="ok")
    # When / Then: the alias participates in admission before serialization.
    assert limits.response_size(value, max_bytes=500) is None


def test_excluded_field_is_ignored_when_only_emitted_model_fields_fit():
    # Given: a field that never appears in the SDK dump.
    class Excluded(BaseModel):
        visible: str = Field(serialization_alias="displayLabel")
        hidden: str = Field(exclude=True)

    value = Excluded(visible="ok", hidden="x" * 10_000)
    # When / Then: admission follows the emitted fields and aliases.
    assert limits.response_size(value, max_bytes=100) is not None


def test_model_extras_are_charged_against_retained_memory():
    # Given: small JSON with extra fields retained independently of __dict__.
    value = ExtraResult(content=[], extension="x" * 100)
    # When: the conservative charge is obtained.
    charge = limits.response_size(value)
    # Then: admission respects the exact retained boundary.
    assert charge is not None
    assert limits.response_size(value, max_retained=charge) == charge
    assert limits.response_size(value, max_retained=charge - 1) is None


def test_cyclic_model_extra_is_rejected_before_serialization():
    # Given: an extras graph that points back to the model itself.
    value = ExtraResult(content=[])
    value.__pydantic_extra__["cycle"] = value
    # When / Then: the bounded graph traversal refuses it.
    assert limits.response_size(value, max_depth=8) is None


def test_repeated_model_values_keep_node_bounds():
    # Given: aliases of one model still appear twice on the wire.
    class Small(BaseModel):
        value: str

    item = Small(value="ok")
    # When: a repeated graph is charged.
    charge = limits.response_size([item, item])
    # Then: repeated wire nodes consume independent traversal capacity.
    assert charge is not None
    assert limits.response_size([item, item], max_nodes=3) is None


def test_model_storage_inspection_does_not_double_count_wire_nodes():
    # Given: a valid MCP graph below the 100k emitted-node ceiling.
    value = CallToolResult(content=[], structured_content={"items": [0] * 50_000})
    # When / Then: retained storage inspection has its own bounded work budget.
    assert limits.response_size(value) is not None


def test_ancestor_storage_inspection_covers_nested_model_values():
    # Given: a nested model whose original storage was already inspected at root.
    class Metadata(BaseModel):
        items: list[int]

    value = CallToolResult(content=[], _meta={"value": Metadata(items=[0] * 50_000)})
    # When / Then: nested projections do not repeat retained traversal work.
    assert limits.response_size(value) is not None


def test_native_url_subclass_hook_is_rejected_without_string_conversion():
    # Given: a runtime URL subclass can override the native serializer's str call.
    calls = []

    class HookUrl(AnyUrl):
        def __str__(self):
            calls.append("url")
            return "x" * 10_000

    class UrlMeta(BaseModel):
        url: AnyUrl

    value = CallToolResult(content=[], _meta={"value": UrlMeta(url=HookUrl("file:///x"))})
    # When / Then: refusal precedes both storage inspection and wire serialization.
    assert limits.response_size(value, max_bytes=500) is None
    assert calls == []


@pytest.mark.parametrize("hook", ["annotated", "computed", "exclude_if", "json_encoder"])
@pytest.mark.filterwarnings("ignore::pydantic.PydanticDeprecatedSince20")
def test_schema_serialization_hooks_are_rejected_before_invocation(hook):
    # Given: Pydantic can register serialization hooks without field_serializer.
    calls = []

    def expand(value):
        calls.append(value)
        return "x" * 10_000

    class AnnotatedModel(BaseModel):
        value: Annotated[str, PlainSerializer(expand)]

    class ComputedModel(BaseModel):
        value: str

        @computed_field
        @property
        def expansion(self) -> str:
            return expand(self.value)

    class ExcludeModel(BaseModel):
        value: str = Field(exclude_if=expand)

    class EncoderModel(BaseModel):
        model_config = ConfigDict(json_encoders={str: expand})
        value: str

    model_type = {
        "annotated": AnnotatedModel,
        "computed": ComputedModel,
        "exclude_if": ExcludeModel,
        "json_encoder": EncoderModel,
    }[hook]
    # When / Then: the shared limiter refuses the hook before allocating its output.
    assert limits.response_size(model_type(value="small"), max_bytes=500) is None
    assert calls == []


@pytest.mark.asyncio
async def test_custom_core_schema_is_rejected_before_expanding_alias(monkeypatch):
    # Given: a custom core schema disagrees with the public field alias metadata.
    class CustomSchema(CallToolResult):
        @classmethod
        def __get_pydantic_core_schema__(cls, source, handler):
            schema = handler(source)
            schema["schema"]["fields"]["content"]["serialization_alias"] = "x" * 10_000
            return schema

    raw = CustomSchema(content=[])
    assert len(wire_bytes(ToolResult.from_mcp_result(raw))) > 500

    def refuse_dump(*_args, **_kwargs):
        raise AssertionError("custom schema must be rejected before model_dump")

    monkeypatch.setattr(BaseModel, "model_dump", refuse_dump)
    # When: preflight sees the custom schema hook.
    result = await admit(raw, monkeypatch)
    # Then: it refuses the result before creating the expanded projection.
    assert result.is_error


def test_custom_serializer_delegate_is_rejected_without_invocation(monkeypatch):
    # Given: replacing Pydantic's native serializer would invoke arbitrary code.
    calls = []

    class DelegateResult(CallToolResult):
        pass

    class Serializer:
        def to_python(self, *_args, **_kwargs):
            calls.append("delegate")
            return {"content": [], "extension": "x" * 10_000}

    value = DelegateResult(content=[])
    monkeypatch.setattr(DelegateResult, "__pydantic_serializer__", Serializer())
    # When / Then: the native serializer contract is checked before any dump.
    assert limits.response_size(value, max_bytes=500) is None
    assert calls == []


def test_excluded_model_fields_remain_charged_against_retained_budget():
    # Given: an excluded string fits memory but is larger than the wire ceiling.
    class Excluded(BaseModel):
        visible: str
        hidden: str = Field(exclude=True)

    value = Excluded(visible="ok", hidden="x" * 10_000)
    # When: both original storage and the emitted projection are inspected.
    charge = limits.response_size(value, max_bytes=100)
    # Then: exclusion preserves wire admission without hiding retained objects.
    assert charge is not None
    assert charge > len(value.hidden)
    assert limits.response_size(value, max_bytes=100, max_retained=charge) == charge
    assert limits.response_size(value, max_bytes=100, max_retained=charge - 1) is None


def test_private_model_storage_remains_charged_against_retained_budget():
    # Given: private fields remain in the original result but are absent from wire.
    class PrivateResult(CallToolResult):
        _stash: str = PrivateAttr(default="x" * 10_000)

    value = PrivateResult(content=[])
    # When: the shared limiter measures wire and original storage independently.
    charge = limits.response_size(value, max_bytes=100)
    # Then: private storage cannot evade the retained-memory ceiling.
    assert charge is not None
    assert charge > len(value._stash)
    assert limits.response_size(value, max_bytes=100, max_retained=8_000) is None


def test_private_storage_on_nested_model_is_charged_by_ancestor():
    # Given: the ancestor storage scan encounters a nested model's private value.
    class PrivateMetadata(BaseModel):
        visible: str
        _stash: str = PrivateAttr(default="x" * 10_000)

    value = CallToolResult(content=[], _meta={"value": PrivateMetadata(visible="ok")})
    # When / Then: nesting does not hide the retained private graph.
    assert limits.response_size(value, max_bytes=500) is not None
    assert limits.response_size(value, max_bytes=500, max_retained=8_000) is None


def test_opaque_private_storage_is_refused_without_conversion_hooks():
    # Given: an opaque private object has no safely bounded JSON storage graph.
    calls = []

    class Opaque:
        def __str__(self):
            calls.append("str")
            return "x" * 10_000

    class PrivateResult(CallToolResult):
        _stash = PrivateAttr(default_factory=Opaque)

    # When / Then: retention inspection refuses it without stringifying the object.
    assert limits.response_size(PrivateResult(content=[])) is None
    assert calls == []


@pytest.mark.asyncio
async def test_admitted_raw_result_is_detached_from_later_source_mutation(monkeypatch):
    # Given: a mutable extension that the caller can still retain.
    raw = ExtraResult(content=[], extension={"value": "original"})
    # When: middleware freezes the safe wire representation.
    result = await admit(raw, monkeypatch)
    raw.extension["value"] = "x" * 10_000
    # Then: the actual downstream SDK serializes the admitted projection.
    assert _dump_result(result.to_mcp_result())["extension"] == {"value": "original"}
    assert len(wire_bytes(result)) <= 500


@pytest.mark.asyncio
async def test_native_resource_url_and_aliases_keep_installed_sdk_representation(monkeypatch):
    # Given: ResourceResult uses native URL schemas and MCP mimeType/_meta aliases.
    monkeypatch.setattr(middleware, "MAX_RESPONSE_BYTES", 32_768 + 500)
    original = ResourceResult("hello", meta={"kind": "resource"})
    context = SimpleNamespace(message=SimpleNamespace(uri="resource://plain/item"))
    expected = _dump_result(original.to_mcp_result(context.message.uri))

    async def call_next(_context):
        return original

    # When: the real resource middleware applies the shared model limiter.
    result = await middleware.ResponseLimitMiddleware().on_read_resource(context, call_next)
    # Then: the standard resource model remains admissible and unchanged on wire.
    assert _dump_result(result.to_mcp_result(context.message.uri)) == expected
    assert (
        limits.response_size(original.to_mcp_result(context.message.uri), max_bytes=500) is not None
    )
