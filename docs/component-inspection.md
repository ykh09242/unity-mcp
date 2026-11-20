# Component inspection

For `manage_components`, an omitted `search_method` resolves a slash-containing
target as a hierarchy path or a literal name when only one interpretation matches.
If both match, specify `by_path` or `by_name`, or use an instance ID. Path segments
identify separate GameObjects; a leading `/` anchors the path at a root, while an
unanchored path can match a hierarchy suffix. An implicitly selected path must be
unique. Strict selection also requires explicitly selected paths to be unique.

The CLI exposes `--strict-selection` and `--component-instance-id` on `component
remove`, `component set` and `component modify`. Strict selection requires a unique
GameObject and component unless `--component-index` or `--component-instance-id`
selects the component. An index is zero-based within `GetComponents(requested type)`,
including derived types. Component IDs are nonzero signed Int32 values; negative
Unity IDs are valid. Index and component ID selectors cannot be combined. Omitted
strict and ID options preserve legacy first-match behavior. Native selection errors
retain candidate and identity details, and the CLI exits unsuccessfully.

```sh
unity-mcp component set "Player" BoxCollider isTrigger true --strict-selection
unity-mcp component modify "Player" BoxCollider --properties '{"size":[2,2,2]}' --component-instance-id -81840
unity-mcp component remove "Player" BoxCollider --strict-selection --component-index 1 --force
```

Component reads return fields and safe Unity properties by default. Reading a custom
component no longer evaluates its C# properties automatically. Values remain under
`properties`; custom getter names that were skipped appear in `skippedProperties`.
`include_properties=false` still requests component identity only, and pagination
keeps its existing behavior and limits.

Default nested-value serialization reads fields without calling custom getters,
JSON converters, serialization callbacks, `ShouldSerialize` methods, or custom
equality functions. Arrays, standard lists and dictionaries with supported scalar
keys keep their normal value representation. Binary arrays retain base64 wire
encoding and the existing byte budget. Standard JSON token containers are traversed
without dispatching virtual members on custom token subclasses; those subclasses
are inspected as fields. Custom collection implementations are
inspected as fields instead of executing their enumerators. Unity object references
remain references; renderer materials and mesh-filter meshes use shared references
and do not instantiate copies.

To deliberately evaluate user code, add `evaluate_getters=true` to a component URI:

```text
mcpforunity://scene/gameobject/42/component/MyNamespace.MyComponent?evaluate_getters=true
mcpforunity://scene/gameobject/42/components?page_size=10&evaluate_getters=true
```

The option also works with `component_index`, `component_instance_id` and
`strict_selection`. Omission and `false` keep safe inspection. Invalid values are
rejected before reaching user code. Native requests accept `evaluate_getters` or
`evaluateGetters`; conflicting aliases are rejected.

Explicit evaluation restores computed properties and existing JSON serialization
behavior, including custom collection enumeration. This executes synchronously on
the Editor thread. A getter that blocks or enters unsafe native code cannot be
interrupted by the response size limit or by a request observation timeout. Use
explicit evaluation for intentional diagnostics, rather than background polling.

The original C# `GetComponentData(Component, bool)` signature remains available
and now uses safe inspection. Callers that require computed properties can use
`GetComponentData(component, includeNonPublicSerializedFields: true, evaluateGetters: true)`.
Response size, complexity and pagination limits are unchanged; large ordinary
field arrays remain supported. Use matching server and Editor package versions
when relying on the safe default.

## Managed references after a type migration

Use `inspect_serialized_object` with the host's current instance ID,
`mode="value"` and the exact `property_path` of a `[SerializeReference]` field.
With values enabled, `managed_reference_status` reports `resolved`, `null`,
`missing_type`, or `unknown` when Unity cannot classify the reference.
`managed_reference_id` is a decimal string scoped to the host. An unresolved
type recorded by Unity includes its former assembly, namespace and class under
`missing_type`. When a field exposes no matching ID but its host has missing
types, `unknown` includes `host_missing_type_count` and up to four identities
in `host_missing_types_sample`. Those identities belong to the host. The
inspector leaves the retained serialized payload in place.

When the project supplies a compatible type migration, reopen the owned asset
and inspect it again using the new host instance ID. Verify the restored
concrete type and the original data. The EditMode fixture
`ManagedTypeMigration_DistinguishesNullAndMissingAndRecoversOwnedPayloadAfterReopen`
checks a class, namespace and assembly move declared with `MovedFrom`.
The licensed Windows `package` suite runs this fixture in the
`managed-reference-migration` profile.
