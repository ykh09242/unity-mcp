using System;
using System.Globalization;
using System.Linq;
using System.Threading;
using Newtonsoft.Json.Linq;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    internal sealed class PlayScenarioSoakComparisonEditor : IDisposable
    {
        private const int PageSize = 25;
        private readonly Foldout section;
        private readonly Slot baseline;
        private readonly Slot candidate;
        private readonly VisualElement summary;
        private readonly VisualElement rows;
        private readonly Label state;
        private readonly Label range;
        private readonly Label pageLabel;
        private readonly Button previous;
        private readonly Button next;
        private readonly DropdownField metric;
        private readonly PlayScenarioSoakComparisonChart chart;
        private PlayScenarioSoakComparison comparison;
        private int page;
        private bool disposed;

        private sealed class Slot : IDisposable
        {
            internal readonly PlayScenarioImportWorker Worker = new PlayScenarioImportWorker();
            internal readonly TextField Path;
            internal readonly Label State;
            internal PlayScenarioSoakComparisonSnapshot Snapshot;
            internal bool Fresh;
            private string source = "";
            private Action changed;
            private readonly Func<string, CancellationToken, PlayScenarioSoakComparisonSnapshot> read;
            private bool disposed;

            internal Slot(
                VisualElement parent,
                string side,
                Func<string, string> choose,
                Action changed,
                Func<string, CancellationToken, PlayScenarioSoakComparisonSnapshot> read
            )
            {
                this.changed = changed;
                this.read = read;
                Path = new TextField(side + " source") { name = "soak" + side + "Path", maxLength = 4096 };
                Path.RegisterValueChangedCallback(OnPathChanged);
                parent.Add(Path);
                State = Note("Select " + side.ToLowerInvariant() + " soak.json or client-soak.json.");
                State.name = "soak" + side + "State";
                State.displayTooltipWhenElided = false;
                State.AddToClassList("scenario-soak-source-state");
                parent.Add(State);
                var buttons = new VisualElement();
                buttons.AddToClassList("scenario-row");
                buttons.Add(
                    new Button(() =>
                    {
                        if (disposed)
                            return;
                        string file = choose(side);
                        if (string.IsNullOrEmpty(file))
                            return;
                        Path.value = file;
                        Import();
                    })
                    {
                        text = "Choose " + side.ToLowerInvariant(),
                        name = "chooseSoak" + side,
                    }
                );
                buttons.Add(new Button(Import) { text = "Import / refresh", name = "importSoak" + side });
                parent.Add(buttons);
                Path.userData = Worker;
            }

            private void OnPathChanged(ChangeEvent<string> change)
            {
                string key = PlayScenarioImportWorker.SourceKey(change.newValue);
                if (source == key)
                    return;
                source = key;
                Path.tooltip = change.newValue;
                State.tooltip = change.newValue;
                Worker.Cancel();
                Snapshot = null;
                Fresh = false;
                State.text = "Selected source changed. Import to display this source.";
                changed?.Invoke();
            }

            private void Import()
            {
                if (disposed)
                    return;
                string selected = Path.value;
                var reader = read;
                if (!Worker.RequestRead(source, token => reader(selected, token), Complete))
                    return;
                Fresh = false;
                State.text = Snapshot == null ? "Import pending. No snapshot displayed." : "Refresh pending; same-source snapshot is stale.";
                changed?.Invoke();
            }

            private void Complete(object result, Exception error)
            {
                if (error == null)
                {
                    Snapshot = (PlayScenarioSoakComparisonSnapshot)result;
                    Fresh = true;
                    State.text = "Fresh manual snapshot for the selected source.";
                    State.tooltip = Snapshot.Source;
                }
                else
                {
                    Fresh = false;
                    string detail = error.Message.Length > 512 ? error.Message.Substring(0, 512) + "..." : error.Message;
                    State.text = (Snapshot == null ? "Import failed. No snapshot displayed. " : "Refresh failed; same-source snapshot is stale. ") + detail;
                    State.tooltip = detail + "\n" + Path.value;
                }
                changed?.Invoke();
            }

            public void Dispose()
            {
                if (disposed)
                    return;
                disposed = true;
                Worker.Dispose();
                Path.UnregisterValueChangedCallback(OnPathChanged);
                Path.userData = null;
                changed = null;
                Snapshot = null;
            }
        }

        internal PlayScenarioSoakComparisonEditor(
            VisualElement parent,
            Func<string, string> choose,
            Func<string, CancellationToken, PlayScenarioSoakComparisonSnapshot> read = null
        )
        {
            section = new Foldout { text = "Compare saved Player soaks", name = "playerSoakComparison" };
            section.AddToClassList("scenario-soak-comparison");
            var content = new ScrollView { name = "soakComparisonScroll" };
            content.AddToClassList("scenario-soak-comparison-scroll");
            section.Add(content);
            content.Add(Note("Saved native snapshot diagnostics. Natural process exit is unverified here; this view makes no CI budget or leak verdict."));
            baseline = new Slot(content, "Baseline", choose, Refresh, read ?? PlayScenarioSoakComparisonSnapshot.Read);
            candidate = new Slot(content, "Candidate", choose, Refresh, read ?? PlayScenarioSoakComparisonSnapshot.Read);
            state = Note("Import baseline and candidate sources to compare saved measurements.");
            state.name = "soakComparisonState";
            content.Add(state);
            summary = new VisualElement { name = "soakComparisonSummary" };
            var metadata = new Foldout { text = "Saved identity and compatibility", name = "soakComparisonMetadata" };
            var metadataScroll = new ScrollView { name = "soakComparisonMetadataScroll" };
            metadataScroll.AddToClassList("scenario-soak-metadata-scroll");
            metadataScroll.Add(summary);
            metadata.Add(metadataScroll);
            metric = new DropdownField("Chart measurement", PlayScenarioSoakComparisonSnapshot.Metrics.ToList(), 0) { name = "soakComparisonMetric" };
            metric.RegisterValueChangedCallback(OnMetricChanged);
            content.Add(metric);
            content.Add(Note("Blue: baseline. Orange: candidate. Shaded: warmup. Missing/error measurements break each line; no interpolation across gaps."));
            chart = new PlayScenarioSoakComparisonChart();
            content.Add(chart);
            range = Note("No saved measurements.");
            range.name = "soakComparisonRange";
            content.Add(range);
            content.Add(metadata);
            var paging = new VisualElement();
            paging.AddToClassList("scenario-row");
            previous = new Button(() =>
            {
                page--;
                RenderRows();
            })
            {
                text = "Previous",
                name = "previousSoakComparisonPage",
            };
            next = new Button(() =>
            {
                page++;
                RenderRows();
            })
            {
                text = "Next",
                name = "nextSoakComparisonPage",
            };
            pageLabel = Note("");
            pageLabel.name = "soakComparisonPage";
            paging.Add(previous);
            paging.Add(pageLabel);
            paging.Add(next);
            content.Add(paging);
            rows = new VisualElement { name = "soakComparisonRows" };
            content.Add(rows);
            section.RegisterCallback<DetachFromPanelEvent>(OnDetached);
            parent.Add(section);
            Refresh();
        }

        private void OnMetricChanged(ChangeEvent<string> _)
        {
            page = 0;
            Render();
        }

        private void Refresh()
        {
            if (disposed)
                return;
            page = 0;
            comparison =
                baseline.Snapshot != null && candidate.Snapshot != null ? PlayScenarioSoakComparison.Create(baseline.Snapshot, candidate.Snapshot) : null;
            Render();
        }

        private void Render()
        {
            summary.Clear();
            if (comparison == null)
                state.text = "Import both selected sources. No two-run comparison is displayed.";
            else
            {
                bool stale = !baseline.Fresh || !candidate.Fresh;
                state.text =
                    stale ? "Comparison is stale while a selected source is pending or failed; cross-run deltas are hidden."
                    : comparison.AllowDeltas ? "Native snapshot comparison; process exit unverified. Raw paired deltas only; no budget verdict."
                    : "Native snapshots have incompatible or incomplete evidence. Cross-run deltas are hidden.";
                if (comparison.Unknown.Count > 0)
                    state.text += " Compatibility metadata is unknown; inspect saved identity details.";
                foreach (var slot in new[] { baseline, candidate })
                {
                    JObject identity = slot.Snapshot.Evidence.Summary ?? slot.Snapshot.Evidence.ClientSummary;
                    string side = slot == baseline ? "Baseline" : "Candidate";
                    summary.Add(Note(side + " selected source: " + slot.Snapshot.Source));
                    foreach (string field in new[] { "job_id", "process_id", "definition_hash", "build_id", "build_source_revision", "payload_hash" })
                        summary.Add(
                            Note(
                                side
                                    + " "
                                    + field
                                    + ": "
                                    + (identity?[field] == null || identity[field].Type == JTokenType.Null ? "unknown" : identity[field].ToString())
                            )
                        );
                    summary.Add(Note(side + " native evidence: " + slot.Snapshot.Evidence.Completeness));
                    summary.Add(
                        Note(
                            side
                                + " Unity "
                                + (slot.Snapshot.UnityVersion ?? "unknown")
                                + "; package "
                                + (slot.Snapshot.PackageVersion ?? "unknown")
                                + "; warmup first "
                                + slot.Snapshot.Warmup
                                + (slot.Snapshot.MetricsKnown ? " iterations." : " iterations (configuration unavailable).")
                        )
                    );
                    foreach (string field in PlayScenarioSoakComparisonSnapshot.EnvironmentFields)
                        summary.Add(Note(side + " environment." + field + ": " + Display(slot.Snapshot.Environment?[field])));
                    summary.Add(Note(side + " " + metric.value + ": " + PlayScenarioSoakComparison.Counts(slot.Snapshot, metric.value)));
                }
                foreach (string difference in comparison.Differences)
                    summary.Add(Note("Incompatible: " + difference));
                if (comparison.Unknown.Count > 0)
                    summary.Add(
                        Note("Unknown compatibility metadata: " + string.Join(", ", comparison.Unknown) + ". Unknown values do not establish compatibility.")
                    );
                int intervalCount = comparison.Intervals(metric.value).Count();
                summary.Add(
                    Note(
                        "Paired postwarmup contiguous intervals: "
                            + intervalCount
                            + ". Endpoint changes describe individual intervals; no slope is fitted across gaps."
                    )
                );
                if (!stale && comparison.AllowDeltas)
                    foreach (string interval in comparison.Intervals(metric.value).Take(16))
                        summary.Add(Note(interval));
                if (intervalCount > 16)
                    summary.Add(Note("Additional interval details omitted: " + (intervalCount - 16) + "; all samples remain available in pages below."));
            }
            chart.SetData(comparison, metric.value);
            range.text = chart.RangeText;
            if (comparison != null)
                range.text +=
                    "\n"
                    + comparison.PairingText(metric.value)
                    + " Shaded warmup: baseline first "
                    + comparison.Baseline.Warmup
                    + "; candidate first "
                    + comparison.Candidate.Warmup
                    + " iterations.";
            RenderRows();
        }

        private static string Display(JToken token) => token == null || token.Type == JTokenType.Null ? "unknown" : (string)token;

        private void RenderRows()
        {
            rows.Clear();
            int total = comparison == null ? 0 : Math.Max(comparison.Baseline.Planned, comparison.Candidate.Planned);
            int pages = Math.Max(1, (total + PageSize - 1) / PageSize);
            page = Math.Max(0, Math.Min(page, pages - 1));
            previous.SetEnabled(page > 0);
            next.SetEnabled(page + 1 < pages);
            pageLabel.text = "Page " + (page + 1) + " / " + pages + "; " + total + " saved/planned intervals";
            bool deltas = baseline.Fresh && candidate.Fresh && comparison?.AllowDeltas == true;
            for (int iteration = page * PageSize + 1; iteration <= Math.Min(total, (page + 1) * PageSize); iteration++)
            {
                string left = iteration <= baseline.Snapshot.Planned ? baseline.Snapshot.Series[metric.value][iteration - 1].Display : "unpaired";
                string right = iteration <= candidate.Snapshot.Planned ? candidate.Snapshot.Series[metric.value][iteration - 1].Display : "unpaired";
                var row = Note(
                    "#"
                        + iteration.ToString(CultureInfo.InvariantCulture)
                        + "; baseline "
                        + left
                        + "; candidate "
                        + right
                        + "; candidate − baseline "
                        + (deltas ? comparison.Delta(metric.value, iteration) : "unavailable")
                );
                row.name = "soakComparisonIteration_" + iteration;
                row.AddToClassList("scenario-result-row");
                rows.Add(row);
            }
        }

        private void OnDetached(DetachFromPanelEvent _) => Dispose();

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            baseline.Dispose();
            candidate.Dispose();
            chart.Dispose();
            comparison = null;
            metric.UnregisterValueChangedCallback(OnMetricChanged);
            section.UnregisterCallback<DetachFromPanelEvent>(OnDetached);
        }

        private static Label Note(string text)
        {
            var label = new Label(text);
            label.AddToClassList("scenario-note");
            return label;
        }
    }
}
