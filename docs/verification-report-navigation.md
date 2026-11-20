# Open a PR dependency report in Unity

Attach the JSON Lines comparison produced by `verification-candidate dependency-diff`
to the PR. Download it and choose **Window > MCP for Unity > Review Dependency Report**
in the Unity project. The **Review dependency report** button in Reference Explorer
opens the same window. Click **Open dependency-diff JSONL** to list the report items,
then select the baseline or candidate entry for the checkout you want to inspect.

Each selectable item carries the full evidence commit and the owner's asset GUID.
A retargeted reference also carries the serialized owner's local file ID, field
path and source digest. Selecting the item pings the asset or component, shows
the exact property path and displays the referenced object in a read-only field.
The Editor resolves the GUID at its current project path. An asset move with
unchanged bytes or duplicate hierarchy names does not change that lookup.
When a field belongs to an unopened scene the Editor opens it additively.
If Unity's field path cannot be expressed unambiguously, the report offers the
exact owner by local file ID and labels that entry **Owner only**. Field paths
support array indices, Unicode names and serialized auto-property backing fields.

If the checkout differs, the window shows the expected commit and current HEAD.
Open the project at the evidence commit to follow the locator. A missing object,
unavailable property, changed source bytes or an unsaved edit affecting an
indexed field produces a diagnostic. Existing open scenes are never saved or
replaced by the report viewer. Legacy reports without locators can be exported
again from the retained baseline and candidate checkpoints.

The source check compares the physical file to the committed digest. For text
Unity YAML that Git stored with LF, it also accepts an otherwise identical
CRLF checkout.
