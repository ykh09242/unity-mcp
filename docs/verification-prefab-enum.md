# Compare the meaning of an existing serialized prefab enum

Capture the same prefab in each revision after Unity imports and compiles that revision. Keep the project-relative prefab path, component type and direct field name fixed. Select an Int32 enum on a component stored directly in the saved text prefab. Add `--enum-file-id` when several components of the same type occur in the prefab. Use a new output directory outside the project for each capture.

```sh
"<Unity>" -batchmode -nographics -quit -projectPath "<project>" \
  -executeMethod MCPForUnity.Editor.Tools.VerificationEnumValue.CaptureFromCommandLine \
  --enum-prefab Assets/Encounters/Boss.prefab --enum-component Game.Encounter \
  --enum-field State --enum-output "<baseline>"
unity-mcp verification-candidate enum-value-diff "<baseline>" "<reordered>" --repaired "<fixed>"
```

Each capture retains the prefab and component script with their metadata, the file ID, field, imported integer, enum type, assembly and compiled member-to-integer map. The comparison locates the selected YAML component by file ID, checks its script GUID and field value, and matches the raw asset digests across captures. The observed member names come from the compiled mappings. A removed value or multiple aliases leave the unique-meaning conclusion unresolved. Binary prefabs, nested serialized properties, inherited prefab overrides and enum types with other underlying storage need a different capture.

The native control saves an original prefab with `State: 1` (`Combat`), then reorders the enum so integer 1 becomes `Reward`. It assigns explicit values in the new declaration order (`Idle = 0, Reward = 2, Combat = 1`) and reopens the identical prefab in a third Editor process. The runner requires three successful imports and matching retained bytes before it reports the original meaning restored.

```sh
python TestProjects/UnityMCPTests/Verification/run_enum_value_control.py --editor "<Unity.exe>" --project "<disposable project>" --output "<fresh output>"
unity-mcp verification-candidate enum-value-diff "<output>/before/capture" "<output>/after/capture" --repaired "<output>/repaired/capture"
```

Use a licensed Unity Editor and a prepared copy of UnityMCPTests under `.unity-ci` or `.tmp`. The output includes each Editor process log, raw capture and `comparison.json`.
