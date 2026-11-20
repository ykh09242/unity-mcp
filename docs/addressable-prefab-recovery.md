# Recover a prefab copy and Addressables registration

`manage_addressables` can create a prefab from an existing prefab template and register the resulting asset GUID in a selected group. Unity assigns the new GUID. The entry address is the destination asset path. Both steps target the same Editor process and imported project. The workflow requires a compatible Addressables provider, an existing writable group, and an execution-receipt store. [Execution receipts](execution-receipts-ledger.md)

Choose a fresh lowercase UUID without hyphens as `operation_id`. Keep it through the workflow. Each command gets its own receipt idempotency key, including each fresh status observation. Send MCP calls with the explicit `unity_instance` and matching full project hash in the receipt metadata. For local CLI calls, use `receipt execute` with the same store and explicit full target. [CLI receipt commands](../website/docs/guides/cli.md#recover-a-command-after-response-loss)

| Action | Inputs beyond `operation_id` | Result |
| --- | --- | --- |
| `preview_prefab` | `source_prefab`, vacant `prefab_path`, `settings_path`, `group_guid` | Returns the destination and group asset paths, the captured settings revision, and the dependency on the creation result. Requires an existing destination parent. |
| `create_prefab` | None | Copies the template, reads the imported prefab GUID and checkpoints its revision. Repeating this step observes the original creation. |
| `prefab_status` | None | Returns the process identity, state, confirmed GUID, completed steps and outstanding steps. |
| `register_prefab` | None | Registers the confirmed GUID, checks the entry and saves the selected group. Repeating a settled registration observes that entry. |
| `cancel_prefab` | None | Ends remaining work. A native call already in progress can settle after the request. |

Preview, application and cancellation require edit permission. Status uses read permission. The public operation ID belongs to the authenticated receipt owner, target project and server process. Remote credentials for different owners have separate scopes. Local authenticated clients share their installation's receipt principal.

For example, create request files with a common operation ID and the selected project's actual paths and group GUID. Supply the preview file to a targeted CLI command such as:

```text
unity-mcp --instance <full-project-hash> receipt execute --store-id <store-id> --idempotency-key prefab-preview-request-0001 manage_addressables @preview.json
```

```json
{
  "action": "preview_prefab",
  "operation_id": "73a0eadc63a9467aade513b903aae15e",
  "source_prefab": "Assets/Templates/Item.prefab",
  "prefab_path": "Assets/Items/NewItem.prefab",
  "settings_path": "Assets/AddressableAssetsData/AddressableAssetSettings.asset",
  "group_guid": "40d51690071540a3a3400c4aa75cc714"
}
```

Submit `{"action":"create_prefab","operation_id":"73a0eadc63a9467aade513b903aae15e"}` with a new receipt key. If delivery breaks, reconnect to the original target and request `prefab_status`. When `state` is `created`, `completed_steps` contains `create_prefab` and `outstanding_steps` contains `register_prefab`. Submit that action with the same operation ID and its own receipt key. A `registered` observation includes both completed steps and the GUID that links them.

A conflicting source or destination, replaced parent, changed prefab, changed settings revision or dirty destination group stops the edit. `conflict` includes the affected revision or field. The group is saved only after the Editor verifies the added entry. A cancelled workflow retains already-created assets. Each guarded file and its metadata can total up to 16 MiB.

An active native call reports `running`. A lost observer after entry into native code reports `unknown` and requires inspection. A domain reload retains checkpoints under `SessionState`. Replacing the Editor or server process makes the old continuation unavailable. Up to 64 unexpired operations are retained per Editor for two hours. Command receipts remain available under their own retention limits. [Reload continuation](reload-continuation.md)

The `addressables-legacy` native profile closes an authenticated socket after prefab import, reconnects a second client, and checks the GUID, asset count and saved entry. Separate phases reload the domain and reopen the project in a replacement Editor. The XML for each phase records its independent verdict. [Native package shape runner](development/native-package-shapes.md)
