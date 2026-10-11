using System;
using System.Linq;
using System.Threading;
using UnityEditor;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    /// <summary>User-selected registry and baseline snapshot; no directory discovery or Python process.</summary>
    internal sealed class PlayScenarioSoakBaselineSelector : IDisposable
    {
        private readonly PlayScenarioImportWorker worker = new PlayScenarioImportWorker();
        private readonly TextField path;
        private readonly DropdownField names;
        private readonly Label state;
        private readonly Action<string> select;
        private readonly Button use;
        private PlayScenarioSoakBaselineRegistry.Entry[] entries = Array.Empty<PlayScenarioSoakBaselineRegistry.Entry>();
        private bool disposed;
        private PlayScenarioSoakBaselineRegistry.Entry selectedEntry;

        internal PlayScenarioSoakBaselineSelector(VisualElement parent, Action<string> select)
        {
            this.select = select;
            var section = new Foldout { text = "Named local baseline", name = "soakBaselineRegistry" };
            path = new TextField("Registry file or directory") { name = "soakBaselineRegistryPath", maxLength = 4096 };
            path.RegisterValueChangedCallback(OnPathChanged);
            section.Add(path);
            section.Add(
                new Button(() =>
                {
                    string chosen = EditorUtility.OpenFilePanel("Choose baseline registry", "", "json");
                    if (!string.IsNullOrEmpty(chosen))
                    {
                        path.value = chosen;
                        Load();
                    }
                })
                {
                    text = "Choose registry",
                    name = "chooseSoakBaselineRegistry",
                }
            );
            section.Add(new Button(Load) { text = "Load / refresh registry", name = "loadSoakBaselineRegistry" });
            names = new DropdownField("Registered baseline") { name = "soakRegisteredBaseline" };
            section.Add(names);
            use = new Button(Use) { text = "Use selected baseline", name = "useSoakRegisteredBaseline" };
            use.SetEnabled(false);
            section.Add(use);
            state = new Label("Load an explicitly selected local registry. Pin changes are managed by the offline CLI.") { name = "soakBaselineRegistryState" };
            state.AddToClassList("scenario-note");
            section.Add(state);
            section.userData = worker;
            parent.Add(section);
        }

        private void OnPathChanged(ChangeEvent<string> _)
        {
            worker.Cancel();
            if (selectedEntry != null)
                select("");
            selectedEntry = null;
            entries = Array.Empty<PlayScenarioSoakBaselineRegistry.Entry>();
            names.choices.Clear();
            names.SetValueWithoutNotify("");
            use.SetEnabled(false);
            state.text = "Registry source changed. Load the selected registry.";
        }

        private void Load()
        {
            if (disposed)
                return;
            string source = path.value;
            use.SetEnabled(false);
            state.text = "Registry import pending.";
            worker.RequestRead(
                "registry:" + PlayScenarioImportWorker.SourceKey(source),
                token => PlayScenarioSoakBaselineRegistry.Read(source, token),
                (value, error) =>
                {
                    if (error != null)
                    {
                        entries = Array.Empty<PlayScenarioSoakBaselineRegistry.Entry>();
                        names.choices.Clear();
                        names.SetValueWithoutNotify("");
                        state.text = "Registry import failed: " + error.Message;
                        return;
                    }
                    entries = (PlayScenarioSoakBaselineRegistry.Entry[])value;
                    names.choices = entries.Select(entry => entry.Name).ToList();
                    names.SetValueWithoutNotify(names.choices.FirstOrDefault() ?? "");
                    use.SetEnabled(entries.Length > 0);
                    state.text =
                        entries.Length
                        + " registered baseline(s). Pinned: "
                        + string.Join(", ", entries.Where(entry => entry.Pinned).Select(entry => entry.Name));
                }
            );
        }

        private void Use()
        {
            if (disposed)
                return;
            var entry = entries.FirstOrDefault(item => item.Name == names.value);
            if (entry == null)
                return;
            use.SetEnabled(false);
            state.text = "Revalidating registered evidence hashes.";
            worker.RequestRead(
                "baseline:" + entry.Name,
                token => PlayScenarioSoakBaselineRegistry.ValidateSnapshot(entry, token),
                (value, error) =>
                {
                    use.SetEnabled(true);
                    if (error != null)
                    {
                        state.text = "Baseline selection failed: " + error.Message;
                        return;
                    }
                    state.text = "Selected " + entry.Name + ". Import the baseline snapshot to compare.";
                    selectedEntry = entry;
                    select((string)value);
                }
            );
        }

        internal void ValidateSelected(string source, CancellationToken cancellation)
        {
            var entry = selectedEntry;
            if (entry != null && PlayScenarioImportWorker.SourceKey(source) == PlayScenarioImportWorker.SourceKey(entry.Snapshot))
                PlayScenarioSoakBaselineRegistry.ValidateSnapshot(entry, cancellation);
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            path.UnregisterValueChangedCallback(OnPathChanged);
            worker.Dispose();
            entries = Array.Empty<PlayScenarioSoakBaselineRegistry.Entry>();
            selectedEntry = null;
        }
    }
}
