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
| `reconcile_prefab` | None | In a replacement Editor, compares the original operation's signed evidence with current assets and Addressables state. |
| `replan_prefab` | `previous_operation_id`, `expected_observation` | Creates a plan under a fresh `operation_id` after rechecking the reconciliation. A confirmed copy carries forward its original GUID. |

Preview, application, replanning and cancellation require edit permission. Status and reconciliation use read permission. The public operation ID belongs to the authenticated receipt owner, target project and server process. Remote credentials for different owners have separate scopes. Local authenticated clients share their installation's receipt principal.

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

A conflicting source or destination, replaced parent, changed prefab, changed settings revision or dirty destination group stops the edit. For a value mismatch, `data.conflict` contains `field`, `expected` and `observed`. A competing creation returns `destination_vacant`. An intervening address edit returns `settings_revision` and leaves the edited address in place. The group is saved only after the Editor verifies the added entry. A cancelled workflow retains already-created assets. Each guarded file and its metadata can total up to 16 MiB.

An active native call reports `running`. A lost observer after entry into native code reports `unknown` and requires inspection. A domain reload retains checkpoints under `SessionState`. Replacing the Editor removes write authority from the original plan. The Editor stores up to 64 unexpired signed evidence records for two hours under the project's `Library/MCPForUnity/Addressables/` directory. Replacing the receipt server or losing the journal makes automatic reconciliation unavailable. Command receipts remain available under their own retention limits. [Reload continuation](reload-continuation.md)

## After the Editor restarts

1. Target the reopened project with the same receipt owner, store and server. Send `{"action":"reconcile_prefab","operation_id":"73a0eadc63a9467aade513b903aae15e"}` with a fresh receipt key. Inspect `data.state`, `data.reconciliation`, the completed steps and the outstanding steps. The response includes the original and current Editor identities, the recorded and observed prefab GUID and digest, and persistent settings revisions.
2. When `replan_available` is true, review the outstanding work. To approve it, choose a fresh operation ID and submit the observed digest as shown below. Use the returned plan's new ID for each subsequent step. If creation already completed, the only remaining step is `register_prefab`.

```json
{
  "action": "replan_prefab",
  "operation_id": "b254a7e82b9a4df4bec1f788020d5a48",
  "previous_operation_id": "73a0eadc63a9467aade513b903aae15e",
  "expected_observation": "<64 lowercase hex characters from data.reconciliation.observation>"
}
```

An approved operation exclusively owns the remaining work. If `continuation_claimed` is true, use the new operation ID to inspect it. When reconciliation reports `registered`, the recorded entry already exists, even if a cancellation request arrived during registration. A conflict, completed cancellation or unconfirmed native invocation has no continuation. In particular, an occupied destination with a matching-looking prefab cannot establish which invocation created it. An `unknown` result includes the observed facts for manual inspection.

The `addressables-legacy` native profile covers a lost socket response, two simultaneously connected clients competing for one destination, a second client's conflicting address edit, domain reload and reopening the same project. The replacement phase checks both a confirmed copy and an imported asset for which the fixture restored the signed record captured during import. The XML for each phase records its independent verdict. [Native package shape runner](development/native-package-shapes.md)
