"""Per-server resource ownership shared by composition, routes and lifespan cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.custom_tool_service import CustomToolService
    from services.reference_graph.service import ReferenceGraphService
    from transport.catalog_subscriptions import CatalogSubscriptions
    from transport.execution_receipts import ExecutionReceipts
    from transport.scene_load_inputs import SceneLoadInputs
    from transport.session_state_store import SessionStateStore


@dataclass(slots=True)
class ServerRuntime:
    """One authoritative store for the resources installed on a server instance.

    Transport-wide owners (the plugin hub and lazy legacy pool) retain their
    existing generation-fenced lifecycle. They are not copied into this context.
    """

    session_state: SessionStateStore | None = None
    scene_inputs: SceneLoadInputs | None = None
    reference_graph: ReferenceGraphService | None = None
    execution_receipts: ExecutionReceipts | None = None
    catalog_subscriptions: CatalogSubscriptions | None = None
    custom_tools: CustomToolService | None = None


class ServerRuntimeOwner:
    """Compatibility attributes used by existing installers and SDK integrations.

    Every attribute delegates to the instance's runtime; there is no parallel
    resource store and replacing an owner stays visible to routes and cleanup.
    """

    runtime: ServerRuntime

    @property
    def _unity_session_state_store(self) -> SessionStateStore | None:
        return self.runtime.session_state

    @_unity_session_state_store.setter
    def _unity_session_state_store(self, value: SessionStateStore | None) -> None:
        self.runtime.session_state = value

    @property
    def _scene_load_inputs(self) -> SceneLoadInputs | None:
        return self.runtime.scene_inputs

    @_scene_load_inputs.setter
    def _scene_load_inputs(self, value: SceneLoadInputs | None) -> None:
        self.runtime.scene_inputs = value

    @property
    def _reference_graph_service(self) -> ReferenceGraphService | None:
        return self.runtime.reference_graph

    @_reference_graph_service.setter
    def _reference_graph_service(self, value: ReferenceGraphService | None) -> None:
        self.runtime.reference_graph = value

    @property
    def _execution_receipts(self) -> ExecutionReceipts | None:
        return self.runtime.execution_receipts

    @_execution_receipts.setter
    def _execution_receipts(self, value: ExecutionReceipts | None) -> None:
        self.runtime.execution_receipts = value

    @property
    def _catalog_subscriptions(self) -> CatalogSubscriptions | None:
        return self.runtime.catalog_subscriptions

    @_catalog_subscriptions.setter
    def _catalog_subscriptions(self, value: CatalogSubscriptions | None) -> None:
        self.runtime.catalog_subscriptions = value
