using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.Components.ReferenceGraph
{
    internal sealed class McpReferenceExplorer
    {
        private const int PageSize = 25;
        private readonly VisualElement root,
            rows,
            fields;
        private readonly ObjectField asset,
            target;
        private readonly DropdownField view,
            direction;
        private readonly Label status,
            explanation;
        private readonly Foldout details;
        private readonly Button next,
            release;
        private readonly Func<string, JObject, Task> request;
        private readonly List<Button> rowButtons = new();
        private string cursor,
            queryId,
            pageAction,
            pageStatus;
        private JObject pageOptions;
        private int pageNumber;
        private bool available,
            waiting;

        internal static bool Handles(string action) =>
            action == "query" || action == "explain" || action == "impact" || action == "path" || action == "release_query";

        internal McpReferenceExplorer(VisualElement parent, Func<string, JObject, Task> request)
        {
            this.request = request;
            root = new VisualElement { name = "graph-explorer" };
            root.AddToClassList("graph-explorer");
            parent.Add(root);
            Note(root, "Explore an asset").AddToClassList("graph-section-heading");
            Note(root, "Choose an asset, then request a page. Selection changes never scan the project or load the whole graph.");
            fields = new VisualElement();
            root.Add(fields);
            asset = AssetField(fields, "Asset", "graph-explorer-asset");
            fields.Add(new Button(() => asset.value = Selection.activeObject) { text = "Use selected asset", name = "graph-explorer-selection" });
            view = new DropdownField("View", new List<string> { "Direct references", "Impact", "Path" }, 0) { name = "graph-explorer-view" };
            direction = new DropdownField("Direction", new List<string> { "Incoming", "Outgoing" }, 0) { name = "graph-explorer-direction" };
            fields.Add(view);
            fields.Add(direction);
            target = AssetField(fields, "Path target", "graph-explorer-target");
            target.style.display = DisplayStyle.None;
            asset.RegisterValueChangedCallback(_ => ResetPage());
            target.RegisterValueChangedCallback(_ => ResetPage());
            direction.RegisterValueChangedCallback(_ => ResetPage());
            view.RegisterValueChangedCallback(_ =>
            {
                target.style.display = view.value == "Path" ? DisplayStyle.Flex : DisplayStyle.None;
                ResetPage();
            });
            var actions = new VisualElement();
            actions.AddToClassList("graph-actions");
            fields.Add(actions);
            actions.Add(new Button(async () => await Search()) { text = "Explore / restart", name = "graph-explorer-search" });
            next = new Button(async () => await Continue()) { text = "Next page", name = "graph-explorer-next" };
            actions.Add(next);
            release = new Button(async () => await Release()) { text = "Release query", name = "graph-explorer-release" };
            actions.Add(release);
            status = Note(root, "Refresh graph status to verify the current account, then choose an asset.", "graph-explorer-status");
            rows = new VisualElement { name = "graph-explorer-rows" };
            root.Add(rows);
            details = new Foldout { text = "Reference explanation", value = false };
            root.Add(details);
            explanation = Note(details, "Select Explain on a reference to read its recorded evidence.", "graph-explorer-explanation");
            SetAvailable(false);
        }

        private static ObjectField AssetField(VisualElement parent, string label, string name)
        {
            var field = new ObjectField(label)
            {
                name = name,
                objectType = typeof(UnityEngine.Object),
                allowSceneObjects = false,
            };
            parent.Add(field);
            return field;
        }

        private static Label Note(VisualElement parent, string text, string name = null)
        {
            var label = new Label(text) { name = name, enableRichText = false };
            label.AddToClassList("graph-note");
            parent.Add(label);
            return label;
        }

        private static string Node(UnityEngine.Object value)
        {
            string path = AssetDatabase.GetAssetPath(value);
            string guid = string.IsNullOrEmpty(path) ? "" : AssetDatabase.AssetPathToGUID(path);
            return string.IsNullOrEmpty(guid) ? null : "asset:" + guid;
        }

        private async Task Search()
        {
            if (!available)
                return;
            if (queryId != null)
            {
                await Release();
                if (queryId != null || !available)
                    return;
            }
            string node = Node(asset.value),
                destination = Node(target.value);
            if (node == null || (view.value == "Path" && destination == null))
            {
                status.text = "Choose a saved project asset" + (view.value == "Path" ? " and a path target." : ".");
                return;
            }
            ResetPage();
            pageAction =
                view.value == "Impact" ? "impact"
                : view.value == "Path" ? "path"
                : "query";
            pageOptions = new JObject
            {
                ["node"] = node,
                ["direction"] = direction.value == "Incoming" ? "incoming" : "outgoing",
                ["limit"] = PageSize,
            };
            if (pageAction == "path")
                pageOptions["target"] = destination;
            await request(pageAction, (JObject)pageOptions.DeepClone());
        }

        private Task Continue()
        {
            if (!available || cursor == null || pageOptions == null)
                return Task.CompletedTask;
            var options = (JObject)pageOptions.DeepClone();
            options["cursor"] = cursor;
            return request(pageAction, options);
        }

        private Task Release() => !available || queryId == null ? Task.CompletedTask : request("release_query", new JObject { ["id"] = queryId });

        private void ResetPage()
        {
            cursor = pageAction = null;
            pageOptions = null;
            pageNumber = 0;
            rows.Clear();
            rowButtons.Clear();
            explanation.text = "Select Explain on a reference to read its recorded evidence.";
            details.value = false;
            status.text = "Ready to explore. No references have been queried for this selection.";
            SetAvailable(available);
        }

        internal void Clear()
        {
            waiting = false;
            queryId = null;
            asset.SetValueWithoutNotify(null);
            target.SetValueWithoutNotify(null);
            ResetPage();
        }

        internal void SetAvailable(bool value)
        {
            available = value;
            fields.SetEnabled(value);
            next.SetEnabled(value && cursor != null && pageOptions != null);
            release.SetEnabled(value && queryId != null);
            foreach (var button in rowButtons)
                button.SetEnabled(value);
        }

        internal void Waiting()
        {
            waiting = true;
            status.text = "Waiting for reference evidence. Stop waiting remains available above; server work may continue.";
        }

        internal void StopWaiting()
        {
            if (!waiting)
                return;
            waiting = false;
            status.text = "Request wait stopped. Refresh before repeating a query; accepted server work may continue.";
        }

        internal void Failed(string message)
        {
            waiting = false;
            cursor = null;
            rows.Clear();
            rowButtons.Clear();
            explanation.text = "";
            status.text = message;
            SetAvailable(available);
        }

        internal void Receive(string action, JObject data)
        {
            waiting = false;
            if (action == "release_query")
            {
                queryId = null;
                ResetPage();
                status.text = "Query released. Choose Explore to start again.";
                return;
            }
            var edges = data["edges"] as JArray ?? new JArray();
            if (action == "explain")
            {
                explanation.text =
                    edges.Count == 0
                        ? "No retained evidence is available for this reference. Refresh the query."
                        : string.Join("\n\n", edges.OfType<JObject>().Select(edge => Describe(edge, true)));
                details.value = true;
                status.text = (pageStatus ?? "") + "\nExplanation loaded from recorded evidence.";
                return;
            }
            cursor = data.Value<string>("next_cursor");
            queryId = data.Value<string>("query_id") ?? queryId;
            pageNumber++;
            rows.Clear();
            rowButtons.Clear();
            foreach (JObject edge in edges)
            {
                string id = edge.Value<string>("id");
                var row = new VisualElement { name = "graph-edge-" + id };
                row.AddToClassList("graph-edge");
                rows.Add(row);
                Note(row, Describe(edge, false));
                if (!string.IsNullOrEmpty(id))
                {
                    var button = new Button(async () =>
                    {
                        if (available)
                            await request("explain", new JObject { ["id"] = id });
                    })
                    {
                        text = "Explain reference",
                        name = "graph-explorer-explain-" + id,
                    };
                    row.Add(button);
                    rowButtons.Add(button);
                }
            }
            string route =
                action == "path"
                    ? (
                        data.Value<bool?>("path_found") == true ? " Path found; pages follow target to start."
                        : cursor != null ? " Path search continues."
                        : " No path was recorded."
                    )
                    : "";
            status.text =
                $"Page {pageNumber} · {edges.Count} references · revision {data["revision"] ?? data["graph_status"]?["revision"]}. "
                + (cursor != null ? "More work is available on the next page." : "End of this query.")
                + route
                + "\nCoverage is partial. "
                + (data["graph_status"]?.Value<bool?>("native_current") == true ? "Native collection is current." : "Native collection is still updating.")
                + " External sources have separate verification times below. No result proves an asset safe to delete.";
            pageStatus = status.text;
            SetAvailable(available);
        }

        private static string Describe(JObject edge, bool detailed)
        {
            string text =
                (edge.Value<string>("source_path") ?? edge.Value<string>("owner") ?? "Unknown source")
                + " → "
                + (edge.Value<string>("target_path") ?? edge.Value<string>("target") ?? "Unknown target")
                + "\nSource: "
                + edge["provider"]
                + " · Kind: "
                + edge["kind"]
                + " · Confidence: "
                + (edge.Value<string>("confidence") ?? "unknown")
                + " · "
                + (edge.Value<bool?>("stale") == true ? "Stale evidence" : "Recorded evidence")
                + " · Target: "
                + (
                    edge.Value<bool?>("target_exists") == true ? "present"
                    : edge.Value<bool?>("target_exists") == false ? "missing"
                    : "unknown"
                );
            if (detailed)
                text +=
                    "\nEvidence: "
                    + edge["evidence"]
                    + "\nSource line: "
                    + edge["line"]
                    + " · Pointer: "
                    + edge["source_pointer"]
                    + "\nPublished path: "
                    + edge["provenance"]?["path"]
                    + "\nSub-object: "
                    + edge["subobject"]
                    + " · File ID: "
                    + edge["target_file_id"];
            return text;
        }
    }
}
