"""Opt-in first-file priority while retaining stock xdist loadfile work groups."""

from collections.abc import Generator, Sequence
from dataclasses import dataclass

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ci-first-file",
        metavar="COLLECTED_PATH",
        help="Dispatch this exact collected relative file first with loadfile workers",
    )


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("ci_first_file") or hasattr(config, "workerinput"):
        return
    if (
        config.getoption("dist", default="no") != "loadfile"
        or not config.getoption("numprocesses", default=0)
        or config.getoption("loadscopereorder", default=True)
        or config.getoption("collectonly")
    ):
        raise pytest.UsageError(
            "--ci-first-file requires active workers, --dist=loadfile and "
            "explicit --no-loadscope-reorder"
        )
    config.pluginmanager.register(FirstFileValidation(config.getoption("ci_first_file")))


@dataclass(frozen=True, slots=True)
class FirstFileValidation:
    """Validate worker inventories on the controller before initial dispatch."""

    selected: str

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, ids: Sequence[str]) -> None:
        if self.selected not in {nodeid.split("::", 1)[0] for nodeid in ids}:
            raise pytest.UsageError(
                f"--ci-first-file={self.selected} does not match a selected collected file; "
                "use its exact root-relative nodeid path"
            )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> Generator[None, list, list]:
    # The outer wrapper applies priority after existing collection/deselection hooks.
    result = yield
    selected = config.getoption("ci_first_file")
    if selected:
        groups: dict[str, list[pytest.Item]] = {}
        for item in items:
            groups.setdefault(item.nodeid.split("::", 1)[0], []).append(item)
        if selected not in groups:
            # Worker UsageErrors lose their diagnostic in xdist's shutdown path.
            # The controller's public collection hook reports this before dispatch.
            return result
        # xdist 3.8's stock loadfile policy is a stable descending case-count sort.
        # Disabling its own reorder lets our leading file survive unchanged; all
        # remaining file groups retain that policy and their existing testcase order.
        ordered = sorted(groups, key=lambda name: -len(groups[name]))
        ordered.remove(selected)
        items[:] = [item for name in [selected, *ordered] for item in groups[name]]
    return result
