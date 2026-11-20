# Find the layer supplying a serialized prefab property

Call the focused `prefab_overrides` tool with `action="trace_property"`, a scene GameObject in the active scene, and an exact serialized leaf path. Supply the component's current instance ID in `object_id` when the field belongs to a component. The following example uses the Light component ID from a lamp instance.

```json
{"action":"trace_property","target":"Room/Lamp","object_id":315,"property_path":"m_Intensity"}
```

`layers[0]` represents the scene object. Each subsequent layer is its immediate source, ending at the layer that defines that object. Each row returns the observed value and its `override`, `inherited` or `defined` status. `originIndex` selects the layer supplying the current value. For a scene instance of variant B derived from variant A, the ordinary order is scene, B, A and base prefab. Trace individual leaves of compound fields because their origins can differ. Missing properties, unsupported value types and chains over 64 layers return an error.

The trace reads live serialized objects and imported prefab assets. To check persistence, save only the intended scene, reopen that scene, resolve fresh instance IDs and trace the same paths again. Compare the values, source asset paths and override states. The Edit Mode test `MCPForUnityTests.Editor.Tools.ManagePrefabsStageTests.TraceProperty_PreservesVariantOriginsAfterSceneReopen` exercises all four possible origins, a property overridden at every layer and a separate dirty scene whose saved bytes and unsaved edit must survive. [Immediate prefab sources](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/PrefabUtility.GetCorrespondingObjectFromSource.html) · [Serialized property override state](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/SerializedProperty-prefabOverride.html)
