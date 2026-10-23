using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.Components.Activity
{
    internal sealed class McpActivitySection : IDisposable
    {
        private readonly VisualElement root,
            list;
        private readonly Label empty;
        private readonly Dictionary<(string owner, string id), RowView> views = new();
        private readonly HashSet<(string owner, string id)> retained = new();
        private readonly List<(string owner, string id)> removed = new();
        private readonly List<RowView> ordered = new();
        private const int PageSize = 25;
        private readonly DropdownField stateFilter,
            ownerFilter;
        private readonly Label pageLabel,
            selectedSummary;
        private readonly Button previous,
            next,
            selectedEvidence;
        private List<OperationActivityRow> snapshot = new();
        private readonly List<OperationActivityRow> matching = new();
        private int page;
        private string selectedOwner,
            selectedId;
        private bool emptyShown = true;
        private readonly Func<List<OperationActivityRow>> capture;
        private readonly Func<long> revision;
        private readonly Action<OperationActivityRow> cancel,
            evidence;
        private readonly IVisualElementScheduledItem tick;
        private long observed = -1;
        private bool visible,
            disposed;
        internal int CaptureCount { get; private set; }
        internal event Action DiagnosticsRequested;

        internal Newtonsoft.Json.Linq.JObject CaptureDiagnosticSelection() => McpDiagnosticBundle.OperationSummary(Selected());

        internal McpActivitySection(
            VisualElement root,
            Func<List<OperationActivityRow>> capture = null,
            Func<long> revision = null,
            Action<OperationActivityRow> cancel = null,
            Action<OperationActivityRow> evidence = null
        )
        {
            this.root = root;
            this.capture = capture ?? OperationActivity.Capture;
            this.revision = revision ?? (() => OperationActivity.Revision);
            this.cancel = cancel ?? RequestCancellation;
            this.evidence = evidence ?? OperationEvidenceWindow.Show;
            root.AddToClassList("activity-section");
            var title = new Label("Activity in this Editor") { name = "activity-heading" };
            title.AddToClassList("section-title");
            root.Add(title);
            AddText("Project: " + Path.GetFileName(Path.GetDirectoryName(Application.dataPath)), "activity-project");
            AddText("Tests, builds, Play scenarios, packages and asset generation. Client wait timeouts do not stop these jobs.", "activity-scope");
            var filters = new VisualElement();
            filters.AddToClassList("activity-filters");
            root.Add(filters);
            stateFilter = new DropdownField("State", new List<string> { "All states", "Active", "Failed", "Completed" }, 0) { name = "activity-state-filter" };
            ownerFilter = new DropdownField("Owner", new List<string> { "All owners" }, 0) { name = "activity-owner-filter" };
            filters.Add(stateFilter);
            filters.Add(ownerFilter);
            stateFilter.RegisterValueChangedCallback(_ =>
            {
                page = 0;
                RenderRows();
            });
            ownerFilter.RegisterValueChangedCallback(_ =>
            {
                page = 0;
                RenderRows();
            });
            selectedSummary = AddText("Select a job to inspect its current state. Raw evidence is loaded only on request.", "activity-selected-summary");
            var selectionActions = new VisualElement();
            selectionActions.AddToClassList("activity-actions");
            root.Add(selectionActions);
            selectionActions.Add(new Button(() => DiagnosticsRequested?.Invoke()) { text = "Diagnostic bundle", name = "activity-diagnostics" });
            selectedEvidence = new Button(() =>
            {
                var row = Selected();
                if (row != null)
                    this.evidence(row);
            })
            {
                text = "Open raw evidence",
                name = "activity-selected-evidence",
            };
            selectedEvidence.SetEnabled(false);
            selectionActions.Add(selectedEvidence);
            selectionActions.Add(
                new Button(() => EditorGUIUtility.systemCopyBuffer = selectedSummary.text) { text = "Copy summary", name = "activity-copy-summary" }
            );
            var paging = new VisualElement();
            paging.AddToClassList("activity-actions");
            root.Add(paging);
            previous = new Button(() =>
            {
                page--;
                RenderRows();
            })
            {
                text = "Previous",
                name = "activity-previous",
            };
            next = new Button(() =>
            {
                page++;
                RenderRows();
            })
            {
                text = "Next",
                name = "activity-next",
            };
            pageLabel = new Label { name = "activity-page" };
            paging.Add(previous);
            paging.Add(pageLabel);
            paging.Add(next);
            empty = AddText("No recent operations. Run a test, build, Play scenario or generation task to see progress here.", "activity-empty");
            list = new VisualElement { name = "activity-rows" };
            root.Add(list);
            AddText(
                "Rows follow each owner's retention. Build rows reset on domain reload; retained reports remain available through report_history. A missing row is not proof of cancellation.",
                "activity-retention"
            );
            tick = root.schedule.Execute(Tick).Every(1000);
            tick.Pause();
        }

        private Label AddText(string text, string name)
        {
            var label = new Label(text) { name = name, enableRichText = false };
            label.AddToClassList("activity-note");
            root.Add(label);
            return label;
        }

        internal void SetVisible(bool value)
        {
            if (disposed || visible == value)
                return;
            visible = value;
            if (value)
            {
                observed = -1;
                Tick();
                tick.Resume();
            }
            else
                tick.Pause();
        }

        internal void Tick()
        {
            if (!visible || disposed)
                return;
            long current = revision();
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            if (current != observed)
            {
                snapshot = capture() ?? new List<OperationActivityRow>();
                CaptureCount++;
                observed = current;
                var owners = snapshot
                    .Select(row => row.Owner)
                    .Where(owner => !string.IsNullOrEmpty(owner))
                    .Distinct()
                    .OrderBy(owner => owner, StringComparer.Ordinal)
                    .ToList();
                if (ownerFilter.value != "All owners" && !owners.Contains(ownerFilter.value))
                    owners.Add(ownerFilter.value);
                owners.Insert(0, "All owners");
                if (!ownerFilter.choices.SequenceEqual(owners))
                    ownerFilter.choices = owners;
                RenderRows();
            }
            foreach (var view in views.Values)
                if (view.Active)
                    view.UpdateElapsed(now);
        }

        private bool Matches(OperationActivityRow row)
        {
            if (ownerFilter.value != "All owners" && row.Owner != ownerFilter.value)
                return false;
            switch (stateFilter.value)
            {
                case "Active":
                    return row.Active;
                case "Completed":
                    return !row.Active;
                case "Failed":
                    return row.State == "failed" || row.State == "error" || row.State == "interrupted" || row.State == "timed_out";
                default:
                    return true;
            }
        }

        private OperationActivityRow Selected() => snapshot.FirstOrDefault(row => row.Owner == selectedOwner && row.Id == selectedId);

        private void Select(OperationActivityRow row)
        {
            selectedOwner = row.Owner;
            selectedId = row.Id;
            RenderSelection();
        }

        private void RenderSelection()
        {
            var selected = Selected();
            selectedEvidence.SetEnabled(selected?.EvidenceId != null);
            if (selected != null)
                selectedSummary.text =
                    selected.Operation
                    + " · "
                    + selected.Owner
                    + " · "
                    + selected.Id
                    + "\nState: "
                    + selected.State
                    + " · Phase: "
                    + selected.Phase
                    + "\n"
                    + selected.Detail
                    + "\n"
                    + selected.Cancellation;
            else if (selectedId != null)
                selectedSummary.text = "Selected operation is no longer retained. Inspect its existing owner or saved reports; no work was restarted.";
            foreach (var pair in views)
                pair.Value.Root.EnableInClassList("activity-selected", pair.Key.owner == selectedOwner && pair.Key.id == selectedId);
        }

        private void RenderRows()
        {
            if (disposed)
                return;
            matching.Clear();
            foreach (var row in snapshot)
                if (Matches(row))
                    matching.Add(row);
            page = Math.Max(0, Math.Min(page, Math.Max(0, (matching.Count - 1) / PageSize)));
            int first = page * PageSize,
                last = Math.Min(first + PageSize, matching.Count);
            pageLabel.text = (matching.Count == 0 ? "0" : (first + 1) + "–" + last) + " of " + matching.Count + " matching · " + snapshot.Count + " retained";
            previous.SetEnabled(page > 0);
            next.SetEnabled(last < matching.Count);
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            try
            {
                for (int i = first; i < last; i++)
                {
                    var row = matching[i];
                    var key = (row.Owner, row.Id);
                    if (!retained.Add(key))
                        continue;
                    if (!views.TryGetValue(key, out var view))
                    {
                        view = new RowView(row.Key, cancel, evidence, Select);
                        views.Add(key, view);
                        list.Add(view.Root);
                    }
                    view.Update(row, now);
                    ordered.Add(view);
                }
                foreach (var pair in views)
                    if (!retained.Contains(pair.Key))
                    {
                        pair.Value.Root.RemoveFromHierarchy();
                        removed.Add(pair.Key);
                    }
                foreach (var key in removed)
                    views.Remove(key);
                for (int i = 0; i < ordered.Count; i++)
                    if (list[i] != ordered[i].Root)
                        ordered[i].Root.PlaceBehind(list[i]);
                bool showEmpty = matching.Count == 0;
                if (showEmpty != emptyShown)
                {
                    empty.style.display = showEmpty ? DisplayStyle.Flex : DisplayStyle.None;
                    emptyShown = showEmpty;
                }
                empty.text =
                    snapshot.Count == 0
                        ? "No recent operations. Run a test, build, Play scenario or generation task to see progress here."
                        : "No jobs match these filters. Change the filters to inspect other retained jobs.";
                RenderSelection();
            }
            finally
            {
                retained.Clear();
                removed.Clear();
                ordered.Clear();
            }
        }

        private static void RequestCancellation(OperationActivityRow row)
        {
            if (
                EditorUtility.DisplayDialog(
                    "Request cancellation",
                    "Request cancellation for " + row.Id + "? The owner determines when native work has stopped.",
                    "Request cancellation",
                    "Keep running"
                )
            )
                OperationEvidenceWindow.ShowResult("Cancellation request", OperationActivity.Cancel(row));
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            visible = false;
            tick.Pause();
            DiagnosticsRequested = null;
            snapshot = new List<OperationActivityRow>();
            matching.Clear();
            views.Clear();
            retained.Clear();
            removed.Clear();
            ordered.Clear();
            root.Clear();
        }

        private sealed class RowView
        {
            internal readonly VisualElement Root = new();
            private readonly Label title = new(),
                identifier = new(),
                state = new(),
                detail = new(),
                cancellation = new(),
                elapsed = new();
            private readonly Button cancel,
                evidence;
            private readonly OperationActivityRow row = new();
            private bool initialized;
            internal bool Active => row.Active;
            private long lastSecond = long.MinValue;

            internal RowView(string key, Action<OperationActivityRow> onCancel, Action<OperationActivityRow> onEvidence, Action<OperationActivityRow> onSelect)
            {
                Root.name = "activity-" + key;
                Root.AddToClassList("activity-card");
                foreach (var label in new[] { title, state, identifier, detail, cancellation, elapsed })
                {
                    label.enableRichText = false;
                    label.AddToClassList("activity-note");
                    Root.Add(label);
                }
                title.AddToClassList("activity-title");
                state.AddToClassList("activity-state");
                identifier.AddToClassList("activity-id");
                var actions = new VisualElement();
                actions.AddToClassList("activity-actions");
                Root.Add(actions);
                cancel = new Button(() => onCancel(row)) { text = "Request cancellation", name = "activity-cancel" };
                evidence = new Button(() => onEvidence(row)) { text = "View evidence / status", name = "activity-evidence" };
                actions.Add(new Button(() => onSelect(row)) { text = "Inspect", name = "activity-select" });
                actions.Add(evidence);
                actions.Add(cancel);
            }

            internal void Update(OperationActivityRow value, long now)
            {
                if (!initialized || row.Operation != value.Operation)
                    title.text = value.Operation;
                if (!initialized || row.Id != value.Id)
                    identifier.text = "ID: " + value.Id;
                if (!initialized || row.Active != value.Active)
                    Root.EnableInClassList("activity-active", value.Active);
                if (!initialized || row.State != value.State || row.Phase != value.Phase)
                    state.text = "State: " + value.State + " · Phase: " + value.Phase;
                if (!initialized || row.Detail != value.Detail)
                    detail.text = value.Detail;
                if (!initialized || row.Cancellation != value.Cancellation)
                    cancellation.text = value.Cancellation;
                if (!initialized || row.CanCancel != value.CanCancel)
                    cancel.SetEnabled(value.CanCancel);
                if (!initialized || row.CanCancel != value.CanCancel || row.Cancellation != value.Cancellation)
                    cancel.tooltip = value.CanCancel ? "Ask the existing operation owner to cancel this job" : value.Cancellation;
                if (!initialized || (row.EvidenceId != null) != (value.EvidenceId != null))
                    evidence.SetEnabled(value.EvidenceId != null);
                bool elapsedChanged = !initialized || row.Started != value.Started || row.Finished != value.Finished || row.Active != value.Active;
                row.Owner = value.Owner;
                row.Id = value.Id;
                row.Operation = value.Operation;
                row.State = value.State;
                row.Phase = value.Phase;
                row.Detail = value.Detail;
                row.Cancellation = value.Cancellation;
                row.EvidenceId = value.EvidenceId;
                row.CanCancel = value.CanCancel;
                row.Active = value.Active;
                row.Started = value.Started;
                row.Finished = value.Finished;
                initialized = true;
                if (elapsedChanged)
                    UpdateElapsed(now);
            }

            internal void UpdateElapsed(long now)
            {
                long seconds = row.Started <= 0 ? -1 : Math.Max(0, ((row.Active ? now : row.Finished ?? row.Started) - row.Started) / 1000);
                if (seconds == lastSecond)
                    return;
                lastSecond = seconds;
                elapsed.text = seconds < 0 ? "Elapsed time unavailable" : "Elapsed: " + TimeSpan.FromSeconds(seconds).ToString("g");
            }
        }
    }

    internal sealed class OperationEvidenceWindow : EditorWindow
    {
        [SerializeField]
        private string owner,
            operationId,
            evidenceId,
            projectScope;
        private string text;
        internal Func<OperationActivityRow, object> resolve = null;

        internal void SetOperation(OperationActivityRow row)
        {
            owner = row.Owner;
            operationId = row.Id;
            evidenceId = row.EvidenceId;
            projectScope = Application.dataPath;
            text = null;
        }

        internal static void Show(OperationActivityRow row)
        {
            var window = CreateInstance<OperationEvidenceWindow>();
            window.titleContent = new GUIContent("Operation evidence");
            window.SetOperation(row);
            window.minSize = new Vector2(360, 260);
            window.Show();
        }

        internal static void ShowResult(string title, object result)
        {
            var window = CreateInstance<OperationEvidenceWindow>();
            window.titleContent = new GUIContent(title);
            window.text = JsonConvert.SerializeObject(result ?? new { observation = "Job no longer retained." }, Formatting.Indented);
            window.minSize = new Vector2(360, 260);
            window.Show();
        }

        private string ReadEvidence()
        {
            if (projectScope != Application.dataPath)
                return "Evidence belongs to a different project. Open the operation from this project's Activity page.";
            try
            {
                var row = new OperationActivityRow
                {
                    Owner = owner,
                    Id = operationId,
                    EvidenceId = evidenceId,
                };
                object result = (resolve ?? OperationActivity.Evidence)(row);
                return JsonConvert.SerializeObject(result ?? new { observation = "Job no longer retained; evidence is unavailable." }, Formatting.Indented);
            }
            catch
            {
                return "Evidence is unavailable. Inspect the existing operation owner or saved reports; no work was restarted.";
            }
        }

        private void CreateGUI()
        {
            rootVisualElement.Clear();
            if (!string.IsNullOrEmpty(owner))
                text = ReadEvidence();
            text ??= "This temporary result is unavailable after script reload. Inspect the existing operation from Activity.";
            var actions = new VisualElement();
            actions.style.flexDirection = FlexDirection.Row;
            actions.style.flexWrap = Wrap.Wrap;
            rootVisualElement.Add(actions);
            var refresh = new Button(() =>
            {
                text = ReadEvidence();
                rootVisualElement.Q<TextField>("operation-evidence-text").SetValueWithoutNotify(text);
            })
            {
                text = "Refresh evidence",
            };
            refresh.SetEnabled(!string.IsNullOrEmpty(owner));
            actions.Add(refresh);
            actions.Add(new Button(() => EditorGUIUtility.systemCopyBuffer = text) { text = "Copy raw evidence" });
            var scroll = new ScrollView();
            scroll.style.flexGrow = 1;
            rootVisualElement.Add(scroll);
            scroll.Add(
                new TextField
                {
                    name = "operation-evidence-text",
                    value = text,
                    multiline = true,
                    isReadOnly = true,
                }
            );
        }

        private void OnDisable()
        {
            text = null;
            resolve = null;
            rootVisualElement.Clear();
        }
    }
}
