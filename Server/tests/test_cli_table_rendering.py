"""Table output preserves visible rows without formatting omitted values."""

import pytest

from cli.utils.output import format_as_table


class CountedValue:
    reads = 0

    def __init__(self, value):
        self.value = value

    def __str__(self):
        type(self).reads += 1
        return self.value


@pytest.mark.parametrize("shape", ["records", "rows", "values", "mapping"])
def test_large_tables_only_format_visible_values(shape):
    CountedValue.reads = 0
    values = [CountedValue(f"item-{index:04d}") for index in range(1000)]
    if shape == "records":
        data = [{"name": value, "enabled": False} for value in values]
    elif shape == "rows":
        data = [[value, 0] for value in values]
    elif shape == "mapping":
        data = {f"key-{index:04d}": value for index, value in enumerate(values)}
    else:
        data = values

    table = format_as_table(data)

    assert CountedValue.reads == 50
    assert len(table.splitlines()) == 53
    assert table.endswith("... (950 more rows)")
    for index in range(50):
        assert f"item-{index:04d}" in table
    assert "item-0050" not in table
    assert "item-0999" not in table


def test_visible_rows_determine_table_widths():
    rows = [{"name": "a"}] * 50 + [{"name": "a hidden long value that cannot be displayed"}]
    table = format_as_table(rows)
    assert table.splitlines()[0] == "name"
    assert table.splitlines()[1] == "----"
    assert table.endswith("... (1 more rows)")


@pytest.mark.parametrize("count", [0, 1, 50])
def test_small_tables_keep_all_rows_without_omission_notice(count):
    values = [{"name": f"item-{index:04d}", "number": 0} for index in range(count)]
    table = format_as_table({"success": True, "data": values}) if count else format_as_table(values)
    if not count:
        assert table == "(no data)"
        return
    assert "more rows" not in table
    assert len(table.splitlines()) == count + 2
    assert table.splitlines()[0].strip().endswith("number")
    assert table.splitlines()[2].strip().endswith("0")
