using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.Components.ReferenceGraph
{
    internal sealed class McpReferenceGraphSection : IDisposable
    {
        private readonly VisualElement root,
            providers,
            configurationRows,
            summary;
        private readonly Label identity,
            status,
            storage,
            message,
            summaryDetail,
            readyCount,
            pendingCount,
            issueCount,
            diskCount,
            updated;
        private readonly DropdownField kind;
        private readonly TextField itemId,
            payload;
        private readonly IntegerField expectedVersion;
        private readonly Toggle reauthorize,
            sourceEnabled;
        private readonly Func<string, JObject, CancellationToken, Task<JObject>> send;
        private readonly Func<string> connectionIdentity;
        private readonly List<Button> buttons = new();
        private CancellationTokenSource pending;
        private string nextAfter,
            exportKind,
            exportPage,
            viewIdentity;
        private bool visible,
            disposed;

        internal McpReferenceGraphSection(
            VisualElement root,
            Func<string, JObject, CancellationToken, Task<JObject>> send = null,
            Func<string> connectionIdentity = null
        )
        {
            this.root = root;
            this.send = send ?? McpReferenceGraphClient.Send;
            this.connectionIdentity = connectionIdentity ?? McpReferenceGraphClient.Identity;
            root.AddToClassList("graph-section");
            var heading = Box(root, "graph-heading");
            Text(heading, "Reference Graph", "graph-title").AddToClassList("graph-title");
            Text(heading, "Asset connections, collection health and source access.").AddToClassList("graph-muted");
            var toolbar = Box(root, "graph-toolbar");
            AddButton(toolbar, "Refresh status", "graph-refresh", () => Run("overview", new JObject())).AddToClassList("graph-primary");
            updated = Text(toolbar, "Updates on request");
            updated.AddToClassList("graph-muted");
            identity = Text(root, "This Editor · current connection account", "graph-identity");
            identity.AddToClassList("graph-scope");
            summary = Box(root, "graph-summary");
            status = Text(summary, "Connect your graph", "graph-status");
            status.AddToClassList("graph-summary-title");
            summaryDetail = Text(summary, "Refresh to check collection progress and source access for this Editor.");
            AddButton(summary, "Retry collection", "graph-retry", () => Run("retry", new JObject()));
            var metrics = Box(root, "graph-metrics");
            readyCount = Metric(metrics, "Ready");
            pendingCount = Metric(metrics, "Pending");
            issueCount = Metric(metrics, "Issues");
            diskCount = Metric(metrics, "Database");
            message = Text(root, "", "graph-message");
            message.AddToClassList("hidden");
            Text(root, "Collection sources").AddToClassList("graph-section-heading");
            providers = Box(root, "graph-providers");
            providers.name = "graph-providers";
            Text(providers, "Source status will appear after the first refresh.").AddToClassList("graph-empty");
            Text(root, "Static analysis is partial. Reference evidence alone does not prove an asset safe to delete.").AddToClassList("graph-muted");
            var transfer = new Foldout
            {
                text = "Rules & source configuration",
                value = false,
                name = "graph-transfer",
            };
            transfer.AddToClassList("graph-foldout");
            root.Add(transfer);
            Text(transfer, "Read this account's settings, select an item to edit, or copy a page to transfer it.").AddToClassList("graph-muted");
            kind = new DropdownField("Configuration", new List<string> { "Wrapper rules", "External sources", "Collection policy" }, 0);
            transfer.Add(kind);
            var exportActions = Box(transfer, "graph-actions");
            AddButton(exportActions, "Load settings", "graph-export", () => Export(false));
            AddButton(exportActions, "Next page", "graph-next", () => Export(true));
            AddButton(
                exportActions,
                "Copy page",
                "graph-copy",
                () =>
                {
                    if (exportPage != null)
                        EditorGUIUtility.systemCopyBuffer = exportPage;
                    return Task.CompletedTask;
                }
            );
            configurationRows = Box(transfer, "graph-config-list");
            var editor = new Foldout
            {
                text = "Edit or import an item",
                value = false,
                name = "graph-item-editor",
            };
            transfer.Add(editor);
            Text(
                    editor,
                    "Select an existing item first. To create one, use a new ID and version 0. A changed destination version prevents overwriting someone else's update."
                )
                .AddToClassList("graph-muted");
            itemId = new TextField("Item ID");
            editor.Add(itemId);
            expectedVersion = new IntegerField("Destination version") { value = 0 };
            editor.Add(expectedVersion);
            payload = new TextField("Configuration JSON")
            {
                multiline = true,
                value = "{}",
                name = "graph-config",
            };
            payload.AddToClassList("graph-json");
            editor.Add(payload);
            sourceEnabled = new Toggle("Enable this external source") { value = false };
            editor.Add(sourceEnabled);
            reauthorize = new Toggle("Approve this source's read access for this account") { value = false };
            editor.Add(reauthorize);
            AddButton(editor, "Apply item", "graph-import", Import).AddToClassList("graph-primary");
            kind.RegisterValueChangedCallback(_ =>
            {
                nextAfter = exportKind = exportPage = null;
                configurationRows.Clear();
                itemId.value = "";
                payload.value = "{}";
                expectedVersion.value = 0;
                reauthorize.value = sourceEnabled.value = false;
                SourceFields();
                UpdateControls();
            });
            var maintenance = new Foldout
            {
                text = "Storage & maintenance",
                value = false,
                name = "graph-maintenance",
            };
            maintenance.AddToClassList("graph-foldout");
            root.Add(maintenance);
            storage = Text(maintenance, "Refresh to inspect storage and retained work.", "graph-storage");
            Text(
                    maintenance,
                    "Clear completed query scratch older than one day and abandoned staging in small batches. Active work, saved evidence and authored settings are preserved."
                )
                .AddToClassList("graph-muted");
            AddButton(
                maintenance,
                "Clear eligible scratch",
                "graph-cleanup",
                () => Run("cleanup", new JObject { ["before"] = DateTimeOffset.UtcNow.AddDays(-1).ToUnixTimeSeconds(), ["limit"] = 100 })
            );
            SourceFields();
            UpdateControls();
            EditorConfigurationCache.Instance.OnConfigurationChanged += ConfigurationChanged;
        }

        private string Kind =>
            kind.index == 1 ? "source"
            : kind.index == 2 ? "policy"
            : "rule";

        private void SourceFields()
        {
            sourceEnabled.EnableInClassList("hidden", Kind != "source");
            reauthorize.EnableInClassList("hidden", Kind != "source");
            itemId.EnableInClassList("hidden", Kind == "policy");
        }

        private static VisualElement Box(VisualElement parent, string className)
        {
            var element = new VisualElement();
            element.AddToClassList(className);
            parent.Add(element);
            return element;
        }

        private static Label Metric(VisualElement parent, string title)
        {
            var card = Box(parent, "graph-metric");
            var value = Text(card, "—");
            value.AddToClassList("graph-metric-value");
            Text(card, title).AddToClassList("graph-muted");
            return value;
        }

        private void UpdateControls()
        {
            bool idle = pending == null;
            foreach (var button in buttons)
                button.SetEnabled(idle && (button.name == "graph-refresh" || viewIdentity != null));
            root.Q<Button>("graph-next")?.SetEnabled(idle && nextAfter != null);
            root.Q<Button>("graph-copy")?.SetEnabled(idle && exportPage != null);
            foreach (var field in new VisualElement[] { kind, itemId, payload, expectedVersion, reauthorize, sourceEnabled, configurationRows })
                field.SetEnabled(idle);
        }

        private static Label Text(VisualElement parent, string text, string name = null)
        {
            var label = new Label(text) { name = name, enableRichText = false };
            label.AddToClassList("graph-note");
            parent.Add(label);
            return label;
        }

        private Button AddButton(VisualElement parent, string text, string name, Func<Task> operation)
        {
            var button = new Button(async () =>
            {
                try
                {
                    await operation();
                }
                catch (Exception)
                {
                    if (!disposed && visible)
                    {
                        message.RemoveFromClassList("hidden");
                        message.text = "Check the configuration fields and retry.";
                    }
                }
            })
            {
                text = text,
                name = name,
            };
            buttons.Add(button);
            parent.Add(button);
            return button;
        }

        private void ConfigurationChanged(string key)
        {
            if (
                key == nameof(EditorConfigurationCache.UseHttpTransport)
                || key == nameof(EditorConfigurationCache.HttpBaseUrl)
                || key == nameof(EditorConfigurationCache.HttpRemoteBaseUrl)
                || key == nameof(EditorConfigurationCache.HttpTransportScope)
                || key == "ApiKey"
                || key == "All"
            )
                Invalidate();
        }

        private void Invalidate()
        {
            pending?.Cancel();
            viewIdentity = null;
            identity.text = "Connection changed · refresh to verify this account";
            identity.tooltip = null;
            status.text = "Refresh required";
            summary.EnableInClassList("graph-attention", false);
            summary.EnableInClassList("graph-current", false);
            summaryDetail.text = "The previous view was cleared to keep accounts and Editors separate.";
            readyCount.text = pendingCount.text = issueCount.text = diskCount.text = "—";
            updated.text = "Updates on request";
            storage.text = message.text = "";
            providers.Clear();
            configurationRows.Clear();
            nextAfter = exportKind = exportPage = null;
            payload.value = "{}";
            itemId.value = "";
            expectedVersion.value = 0;
            reauthorize.value = sourceEnabled.value = false;
            UpdateControls();
        }

        internal void SetVisible(bool value)
        {
            if (disposed || visible == value)
                return;
            visible = value;
            if (!value)
                Invalidate();
        }

        private Task Export(bool next)
        {
            if (next && (nextAfter == null || exportKind != Kind))
            {
                message.text = "Read the first page, then continue while another page is available.";
                return Task.CompletedTask;
            }
            return Run(
                "export_config",
                new JObject
                {
                    ["kind"] = Kind,
                    ["after"] = next ? nextAfter : "",
                    ["limit"] = 20,
                }
            );
        }

        private Task Import()
        {
            var options = new JObject
            {
                ["kind"] = Kind,
                ["id"] = Kind == "policy" ? "policy" : itemId.value,
                ["config"] = JObject.Parse(payload.value),
                ["expected_version"] = expectedVersion.value,
            };
            if (Kind == "source")
            {
                options["reauthorize"] = reauthorize.value;
                options["enabled"] = sourceEnabled.value;
            }
            return Run("import_config", options);
        }

        internal async Task Run(string action, JObject options)
        {
            if (disposed || !visible || pending != null)
                return;
            using var cancellation = new CancellationTokenSource();
            pending = cancellation;
            UpdateControls();
            message.RemoveFromClassList("hidden");
            message.text = "Working… You can leave this tab; accepted server work may continue.";
            try
            {
                string before = connectionIdentity();
                if (action != "overview" && viewIdentity != before)
                {
                    Invalidate();
                    return;
                }
                JObject data = await send(action, options, cancellation.Token);
                if (disposed || !visible || cancellation.IsCancellationRequested)
                    return;
                if (before != connectionIdentity())
                {
                    Invalidate();
                    return;
                }
                viewIdentity = before;
                Render(data);
                updated.text = "Updated " + DateTime.Now.ToString("HH:mm:ss", CultureInfo.InvariantCulture);
                if (action == "export_config")
                {
                    exportKind = data.Value<string>("kind");
                    nextAfter = data.Value<string>("next_after");
                    exportPage = new JObject
                    {
                        ["schema_version"] = 1,
                        ["kind"] = exportKind,
                        ["items"] = data["items"].DeepClone(),
                        ["next_after"] = nextAfter,
                    }.ToString(Formatting.Indented);
                    configurationRows.Clear();
                    foreach (JObject item in (JArray)data["items"])
                    {
                        var row = new Button(() =>
                        {
                            itemId.value = item.Value<string>("id");
                            expectedVersion.value = item.Value<int>("version");
                            payload.value = item["config"].ToString(Formatting.Indented);
                            sourceEnabled.value = item.Value<bool?>("enabled") ?? false;
                            reauthorize.value = false;
                            root.Q<Foldout>("graph-item-editor").value = true;
                        })
                        {
                            text = item.Value<string>("id") + "   ·   v" + item.Value<int>("version") + "   ·   Edit",
                            enableRichText = false,
                        };
                        configurationRows.Add(row);
                    }
                    message.text =
                        nextAfter == null ? "Settings loaded. Select an item to edit." : "Settings loaded. More items are available on the next page.";
                }
                else if (action == "cleanup")
                    message.text =
                        "Removed "
                        + data["completed_queries_removed"]
                        + " completed queries and "
                        + data["abandoned_staging_removed"]
                        + " abandoned staging records. More: "
                        + data["more"]
                        + ". SQLite space is reusable.";
                else if (action == "import_config")
                {
                    expectedVersion.value = data.Value<int>("version");
                    reauthorize.value = false;
                    message.text = "Item updated. Refresh to inspect reconciliation progress.";
                }
                else
                    message.text =
                        action == "retry"
                            ? "Collection retry scheduled. Refresh to inspect progress."
                            : "Status loaded. Provider freshness is reported separately from native progress.";
                if (action == "overview")
                    message.AddToClassList("hidden");
            }
            catch (OperationCanceledException)
            {
                if (!disposed && visible)
                    message.text = "Request wait stopped. Refresh before repeating a change.";
            }
            catch (InvalidOperationException error)
            {
                if (!disposed && visible)
                    message.text = error.Message;
            }
            catch (Exception)
            {
                if (!disposed && visible)
                    message.text = "Graph request failed. Check the connection and refresh; no automatic retry was issued.";
            }
            finally
            {
                if (ReferenceEquals(pending, cancellation))
                    pending = null;
                if (!disposed)
                    UpdateControls();
            }
        }

        internal void Render(JObject data)
        {
            var label = data["identity"];
            if (label != null)
            {
                identity.text =
                    (label.Value<string>("mode") == "remote" ? "Remote account " : "Local account ")
                    + Short(label["principal"])
                    + "   ·   Editor "
                    + Short(label["instance_id"])
                    + "   ·   Project "
                    + Short(label["project_id"]);
                identity.tooltip = "Account: " + label["principal"] + "\nEditor: " + label["instance_id"] + "\nProject: " + label["project_id"];
            }
            var graph = data["graph_status"];
            long errors = graph?.Value<long?>("errors") ?? 0;
            string workerError = graph?.Value<string>("worker_error");
            bool failed = errors > 0 || !string.IsNullOrEmpty(workerError);
            bool current = graph?.Value<bool?>("native_current") == true;
            status.text =
                failed ? "Collection needs attention"
                : current ? "Editor references are up to date"
                : "Collecting references";
            summary.EnableInClassList("graph-attention", failed);
            summary.EnableInClassList("graph-current", !failed && current);
            summaryDetail.text =
                failed ? "Some evidence is incomplete. Review the source status below, fix access or compilation, then retry."
                : current ? "Native collection is current. External sources keep their own verification time."
                : "Collection continues in the background. Refresh to see the latest progress.";
            if (!string.IsNullOrEmpty(workerError))
                summaryDetail.text += "\n" + workerError;
            readyCount.text = Number(graph?["ready"]);
            pendingCount.text = Number(graph?["pending"]);
            issueCount.text = Number(graph?["errors"]);
            if (data["storage"] != null)
            {
                diskCount.text = Bytes(data["storage"]["allocated_bytes"]);
                storage.text =
                    "Database: "
                    + diskCount.text
                    + "   ·   Reusable space: "
                    + Bytes(data["storage"]["reusable_bytes"])
                    + "\nWAL files are excluded. Cleanup reuses space without blocking on a database shrink."
                    + "\nMemory caches: catalog "
                    + Presence(data["memory"]?["catalog_cached"])
                    + ", analysis "
                    + Presence(data["memory"]?["analysis_cached"])
                    + ". Heap bytes are not measured.";
                if (data["jobs"] is JObject jobs)
                    foreach (var job in jobs.Properties())
                    {
                        string title =
                            job.Name == "graph_queries" ? "Queries"
                            : job.Name == "graph_snapshots" ? "Snapshots"
                            : job.Name == "graph_mutations" ? "Mutation receipts"
                            : "Runtime captures";
                        var counts = ((JObject)job.Value)
                            .Properties()
                            .Select(p =>
                                (
                                    p.Name == "0" ? "active"
                                    : p.Name == "1" ? "completed"
                                    : p.Name.Replace('_', ' ')
                                )
                                + " "
                                + Number(p.Value)
                            );
                        storage.text += "\n" + title + ": " + string.Join(", ", counts);
                    }
            }
            providers.Clear();
            Provider(
                "Unity assets & scripts",
                current ? "Current" : "Collecting",
                "Revision " + graph?["revision"] + " · " + (graph?["progress"]?.Value<string>("phase") ?? "not started").Replace('_', ' '),
                false
            );
            if (graph?["providers"] is JObject providerRows)
                foreach (var provider in providerRows.Properties())
                {
                    var value = provider.Value;
                    string state = value.Value<string>("state");
                    long failures = value.Value<long?>("errors") ?? 0;
                    string error = value.Value<string>("error");
                    bool issue = failures > 0 || state == "failed" || !string.IsNullOrEmpty(error);
                    bool addressables = provider.Name == "addressables";
                    string title = addressables ? "Addressables" : "External source · " + provider.Name.Replace("external:", "");
                    string badge =
                        issue ? "Needs attention"
                        : state == "verified_at_scan" ? "Verified at scan"
                        : state == "available" ? "Available"
                        : "Not verified";
                    string detail = error;
                    if (addressables && value["states"] is JObject states)
                    {
                        detail = string.Join(" · ", states.Properties().Select(p => p.Name.Replace('_', ' ') + ": " + Number(p.Value)));
                        if (states.HasValues && !issue)
                            badge = states["available"] != null ? "Available" : "Partial coverage";
                    }
                    long? checkedAt = value.Value<long?>("last_verified_at");
                    if (checkedAt.HasValue && checkedAt > 0)
                        detail =
                            (string.IsNullOrEmpty(detail) ? "" : detail + "\n")
                            + "Last verified "
                            + DateTimeOffset.FromUnixTimeSeconds(checkedAt.Value).ToLocalTime().ToString("g", CultureInfo.CurrentCulture);
                    Provider(
                        title,
                        badge,
                        string.IsNullOrEmpty(detail)
                            ? (
                                badge == "Available"
                                    ? "Catalog information is available for reference analysis."
                                    : "Refresh or retry collection to check this source."
                            )
                            : detail,
                        issue
                    );
                }
        }

        private void Provider(string title, string badge, string detail, bool issue)
        {
            var card = Box(providers, "graph-provider");
            var heading = Box(card, "graph-provider-heading");
            Text(heading, title).AddToClassList("graph-provider-title");
            var state = Text(heading, badge);
            state.AddToClassList("graph-badge");
            state.EnableInClassList("graph-badge-warning", issue);
            Text(card, detail).AddToClassList("graph-muted");
        }

        private static string Short(JToken value)
        {
            string text = value?.ToString() ?? "unknown";
            return text.Length <= 24 ? text : text.Substring(0, 12) + "…";
        }

        private static string Number(JToken value) => (value?.Value<long>() ?? 0).ToString("N0", CultureInfo.CurrentCulture);

        private static string Presence(JToken value) => value?.Value<bool>() == true ? "loaded" : "not retained";

        private static string Bytes(JToken value)
        {
            double bytes = value?.Value<double>() ?? 0;
            return bytes >= 1073741824 ? (bytes / 1073741824).ToString("0.0", CultureInfo.CurrentCulture) + " GiB"
                : bytes >= 1048576 ? (bytes / 1048576).ToString("0.0", CultureInfo.CurrentCulture) + " MiB"
                : (bytes / 1024).ToString("0.0", CultureInfo.CurrentCulture) + " KiB";
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            visible = false;
            pending?.Cancel();
            EditorConfigurationCache.Instance.OnConfigurationChanged -= ConfigurationChanged;
            buttons.Clear();
            root.Clear();
        }
    }
}
