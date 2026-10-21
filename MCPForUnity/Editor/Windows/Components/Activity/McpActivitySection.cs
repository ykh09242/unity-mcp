using System;
using System.Collections.Generic;
using System.IO;
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
            AddText("Tests, builds and Play scenarios. Client wait timeouts do not stop these jobs.", "activity-scope");
            empty = AddText("No retained activity in these job owners.", "activity-empty");
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
                var rows = capture();
                CaptureCount++;
                try
                {
                    foreach (var row in rows)
                    {
                        var key = (row.Owner, row.Id);
                        if (!retained.Add(key))
                            continue;
                        if (!views.TryGetValue(key, out var view))
                        {
                            view = new RowView(row.Key, cancel, evidence);
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
                    bool showEmpty = ordered.Count == 0;
                    if (showEmpty != emptyShown)
                    {
                        empty.style.display = showEmpty ? DisplayStyle.Flex : DisplayStyle.None;
                        emptyShown = showEmpty;
                    }
                    observed = current;
                }
                finally
                {
                    retained.Clear();
                    removed.Clear();
                    ordered.Clear();
                }
            }
            foreach (var view in views.Values)
                if (view.Active)
                    view.UpdateElapsed(now);
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

            internal RowView(string key, Action<OperationActivityRow> onCancel, Action<OperationActivityRow> onEvidence)
            {
                Root.name = "activity-" + key;
                Root.AddToClassList("activity-card");
                foreach (var label in new[] { title, state, detail, cancellation, elapsed })
                {
                    label.enableRichText = false;
                    label.AddToClassList("activity-note");
                    Root.Add(label);
                }
                title.AddToClassList("activity-title");
                var actions = new VisualElement();
                actions.AddToClassList("activity-actions");
                Root.Add(actions);
                cancel = new Button(() => onCancel(row)) { text = "Request cancellation", name = "activity-cancel" };
                evidence = new Button(() => onEvidence(row)) { text = "View evidence / status", name = "activity-evidence" };
                actions.Add(evidence);
                actions.Add(cancel);
            }

            internal void Update(OperationActivityRow value, long now)
            {
                if (!initialized || row.Operation != value.Operation || row.Id != value.Id)
                    title.text = value.Operation + " · " + value.Id;
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
        private string text;

        internal static void Show(OperationActivityRow row)
        {
            object result;
            try
            {
                result = OperationActivity.Evidence(row);
            }
            catch
            {
                result = new { observation = "Evidence is unavailable; inspect the existing job owner." };
            }
            ShowResult("Operation evidence", result);
        }

        internal static void ShowResult(string title, object result)
        {
            var window = CreateInstance<OperationEvidenceWindow>();
            window.titleContent = new GUIContent(title);
            window.text = JsonConvert.SerializeObject(result ?? new { observation = "Job no longer retained." }, Formatting.Indented);
            window.minSize = new Vector2(360, 260);
            window.Show();
        }

        private void CreateGUI()
        {
            rootVisualElement.Add(new Button(() => EditorGUIUtility.systemCopyBuffer = text) { text = "Copy evidence" });
            var scroll = new ScrollView();
            scroll.style.flexGrow = 1;
            rootVisualElement.Add(scroll);
            scroll.Add(
                new TextField
                {
                    value = text,
                    multiline = true,
                    isReadOnly = true,
                }
            );
        }

        private void OnDisable()
        {
            text = null;
            rootVisualElement.Clear();
        }
    }
}
