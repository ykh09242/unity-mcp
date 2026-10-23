# Component inspection

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
