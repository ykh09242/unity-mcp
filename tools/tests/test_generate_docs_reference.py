from typing import Annotated, Union

from pydantic import BeforeValidator, Field

from tools.generate_docs_reference import _annotation_description, _render_type


def test_pep604_and_typing_unions_render_bare_collections_consistently():
    cases = [
        (list | None, Union[list, None], "list[Any] | None"),
        (dict | bool | None, Union[dict, bool, None], "dict[Any] | bool | None"),
        (None | tuple | str, Union[None, tuple, str], "tuple[Any] | str | None"),
    ]
    for modern, legacy, expected in cases:
        assert _render_type(modern) == expected
        assert _render_type(legacy) == expected


def test_union_rendering_preserves_nested_generic_types():
    modern = list[dict[str, int | None]] | None
    legacy = Union[list[dict[str, Union[int, None]]], None]
    expected = "list[dict[str, int | None]] | None"
    assert _render_type(modern) == expected
    assert _render_type(legacy) == expected


def test_union_description_preserves_nested_annotated_metadata():
    described = Annotated[str, "Nested description"]
    modern = Annotated[described | None, 7]
    legacy = Annotated[Union[described, None], 7]
    assert _annotation_description(modern) == "Nested description"
    assert _annotation_description(legacy) == "Nested description"


def test_unannotated_unions_have_no_description():
    assert _annotation_description(str | None) is None
    assert _annotation_description(Union[str, None]) is None


def test_field_description_survives_validator_metadata_and_optional_union():
    described = Annotated[int | None, Field(description="Component index"), BeforeValidator(lambda value: value)]
    assert _annotation_description(described) == "Component index"
    assert _annotation_description(Annotated[described | str, 7]) == "Component index"
    assert _render_type(described) == "int | None"


def test_existing_string_description_precedes_field_metadata():
    described = Annotated[int, Field(description="Field description"), "Existing description"]
    assert _annotation_description(described) == "Existing description"
    assert _annotation_description(Annotated[int, Field(), 7]) is None
