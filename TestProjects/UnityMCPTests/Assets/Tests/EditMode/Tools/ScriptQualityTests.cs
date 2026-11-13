using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.ScriptQuality;
using MCPForUnity.Editor.Services.SourceFreshness;
using MCPForUnity.Editor.Setup;
using MCPForUnity.Editor.Tools;
using MCPForUnityTests.Editor.Services;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.PackageManager;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class ScriptQualityTests
    {
        [TestCase("../escape.cs")]
        [TestCase("Assets/../escape.cs")]
        [TestCase("Assets/Resources/GameData/private.cs")]
        [TestCase("Assets/.env/example.cs")]
        [TestCase("Assets/Host~/example.cs")]
        [TestCase("Library/example.cs")]
        [TestCase("Assets/example.txt")]
        [TestCase("C:/example.cs")]
        public void SelectionRefusesUnadmittedPaths(string path)
        {
            Assert.Throws<ArgumentException>(() => ScriptSelection.Canonical(path));
        }

        [Test]
        public void ExplicitSelectionIsRequiredAndDuplicatesAreRefused()
        {
            Assert.Throws<ArgumentException>(() => ScriptSelection.Resolve(new JObject()));
            Assert.Throws<ArgumentException>(() => ScriptSelection.Resolve(new JObject { ["paths"] = new JArray("Assets/A.cs", "Assets\\A.cs") }));
            Assert.Throws<ArgumentException>(() =>
                ScriptSelection.Resolve(new JObject { ["paths"] = new JArray("Assets/A.cs"), ["change_set_id"] = new string('a', 32) })
            );
        }

        [Test]
        public void DeletedSelectionNeverBecomesAnEmptyCleanRun()
        {
            Assert.Throws<FileNotFoundException>(() =>
                ScriptSelection.Resolve(new JObject { ["paths"] = new JArray("Assets/Missing-" + Guid.NewGuid().ToString("N") + ".cs") })
            );
        }

        [Test]
        public void UnicodeBomAndMetadataAreReadWithoutModification()
        {
            string name = "MCPQuality_" + Guid.NewGuid().ToString("N") + "_한글.cs";
            string path = Path.Combine(Application.dataPath, name);
            byte[] bytes = new UTF8Encoding(true).GetPreamble().Concat(Encoding.UTF8.GetBytes("class Example {}\r\n")).ToArray();
            byte[] meta = Encoding.UTF8.GetBytes("fileFormatVersion: 2\nguid: " + new string('a', 32) + "\n");
            try
            {
                File.WriteAllBytes(path, bytes);
                File.WriteAllBytes(path + ".meta", meta);
                var selection = ScriptSelection.Resolve(new JObject { ["paths"] = new JArray("Assets/" + name) });
                Assert.That(selection.Paths, Is.EqualTo(new[] { "Assets/" + name }));
                Assert.That(selection.Files[0].Text, Is.EqualTo("class Example {}\r\n"));
                Assert.That(selection.Files[0].Guid, Is.EqualTo(new string('a', 32)));
                Assert.That(File.ReadAllBytes(path), Is.EqualTo(bytes));
                Assert.That(File.ReadAllBytes(path + ".meta"), Is.EqualTo(meta));
            }
            finally
            {
                File.Delete(path);
                File.Delete(path + ".meta");
            }
        }

        [UnityTest]
        public IEnumerator MissingImportedAssemblyContextIsExplicit()
        {
            string name = "MCPUnimported_" + Guid.NewGuid().ToString("N") + ".cs";
            string path = Path.Combine(Application.dataPath, name);
            File.WriteAllText(path, "class Unimported { this is broken }");
            try
            {
                var selection = ScriptSelection.Resolve(new JObject { ["paths"] = new JArray("Assets/" + name) });
                var token = new SourceSnapshot("project", "session", "sources", "context", true, Array.Empty<string>(), 1, 1);
                var capture = ScriptCompilationContext.CaptureAsync(selection, token, false, CancellationToken.None);
                while (!capture.IsCompleted)
                    yield return null;
                Assert.That(capture.IsFaulted, Is.True);
                Assert.That(capture.Exception.GetBaseException().Message, Does.Contain("source_not_in_imported_assembly_inventory"));
                Assert.That(File.ReadAllText(path), Is.EqualTo("class Unimported { this is broken }"));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Test]
        public void NativeBoundaryRejectsCallerOwnershipAndFormattingFields()
        {
            foreach (string field in new[] { "owner", "format", "apply_fixes" })
            {
                var response = JObject.FromObject(ManageScriptQuality.HandleCommand(new JObject { ["action"] = "capabilities", [field] = true }));
                Assert.That((bool?)response["success"], Is.False);
            }
        }

        [Test]
        public void ChangedSourceEvidenceCannotBePublishedAsComplete()
        {
            var before = new SourceSnapshot("project", "session", "before", "context", true, Array.Empty<string>(), 1, 1);
            var after = new SourceSnapshot("project", "session", "after", "context", true, Array.Empty<string>(), 1, 1);
            var result = new JObject { ["complete"] = true, ["coverage_gaps"] = new JArray() };
            ScriptDiagnosticJobs.BindSourceEvidence(result, before, after);
            Assert.That((bool)result["complete"], Is.False);
            Assert.That((bool)result["stale"], Is.True);
            Assert.That(result["coverage_gaps"].Values<string>(), Does.Contain("source_changed_during_analysis"));
            Assert.That((string)result["source_token"], Is.EqualTo(before.Token));
        }

        [Test]
        public async Task CancelDuringHostUpdateReleasesAnalysisSlot()
        {
            const string path = "Assets/Scripts/TestAsmdef/ScriptQuality/SelectedQualitySource.cs";
            const BindingFlags flags = BindingFlags.Static | BindingFlags.NonPublic;
            var captureGate = (SemaphoreSlim)typeof(SourceFreshnessService).GetField("CaptureGate", flags).GetValue(null);
            string cache = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Library", "MCPQualityUpdate-" + Guid.NewGuid().ToString("N")));
            string previousCache = ScriptAnalysisHost.CacheRootOverrideForTests;
            string first = Guid.NewGuid().ToString("N"),
                next = Guid.NewGuid().ToString("N");
            using var consent = new ScopedConsentTestState(ScriptAnalysisHost.ConsentName);
            var options = new JObject { ["paths"] = new JArray(path), ["timeout_seconds"] = 180 };
            ScriptAnalysisHost.CacheRootOverrideForTests = cache;
            try
            {
                var installed = await ScriptAnalysisHostInstaller.InstallAsync(CancellationToken.None);
                Assert.That((bool?)ScriptAnalysisHost.Capabilities()["available"], Is.True);
                var update = ScriptAnalysisHostInstaller.InstallAsync(CancellationToken.None);
                try
                {
                    string updateStage = Directory.GetDirectories(cache).Single(p => Path.GetFileName(p) != (string)installed["directory"]);
                    // Hold capture so only the installer can own a child at cancellation.
                    await captureGate.WaitAsync();
                    try
                    {
                        ScriptDiagnosticJobs.Start(first, options);
                        while (!update.IsCompleted && (!File.Exists(Path.Combine(updateStage, "global.json")) || ScriptAnalysisHost.Owner.ActiveProcessId == 0))
                            await Task.Delay(10);
                        Assert.That((string)ScriptDiagnosticJobs.Status(first, 1, null)["state"], Is.EqualTo("running"));
                        Assert.That(ScriptAnalysisHost.Owner.ActiveProcessId, Is.GreaterThan(0));
                        ScriptDiagnosticJobs.Cancel(first);
                        var status = await WaitForAnalysis(first);
                        Assert.That((string)status["state"], Is.EqualTo("canceled"), status.ToString());
                        Assert.That((bool)status["terminal"], Is.True);
                    }
                    finally
                    {
                        captureGate.Release();
                    }
                }
                finally
                {
                    await update;
                }
                Assert.That((string)update.Result["directory"], Is.Not.EqualTo((string)installed["directory"]));
                ScriptDiagnosticJobs.Start(next, options);
                var completed = await WaitForAnalysis(next);
                Assert.That((string)completed["state"], Is.EqualTo("completed"), completed.ToString());
                Assert.That(completed["result"]["rows"].Any(), Is.True);
            }
            finally
            {
                ScriptDiagnosticJobs.Cancel(first);
                ScriptDiagnosticJobs.Cancel(next);
                ScriptAnalysisHost.CacheRootOverrideForTests = previousCache;
                if (Directory.Exists(cache))
                    Directory.Delete(cache, true);
            }
        }

        private static async Task<JObject> WaitForAnalysis(string id)
        {
            var timeout = System.Diagnostics.Stopwatch.StartNew();
            while (true)
            {
                var status = ScriptDiagnosticJobs.Status(id, 1, null);
                if ((bool)status["terminal"] || (string)status["state"] == "unsettled" || timeout.Elapsed > TimeSpan.FromSeconds(180))
                    return status;
                await Task.Delay(20);
            }
        }

        [Test]
        public void ResultPagesAreBoundedAndCursorsCannotCrossJobsOrSources()
        {
            const BindingFlags flags = BindingFlags.Static | BindingFlags.Instance | BindingFlags.NonPublic;
            Type jobType = typeof(ScriptDiagnosticJobs).GetNestedType("Job", flags);
            var jobs = (IDictionary)typeof(ScriptDiagnosticJobs).GetField("Jobs", flags).GetValue(null);
            string id = Guid.NewGuid().ToString("N");
            object job = Activator.CreateInstance(jobType, true);
            var result = new JObject
            {
                ["source_token"] = new string('a', 64),
                ["diagnostics"] = new JArray(
                    Enumerable
                        .Range(0, 100)
                        .Select(i => new JObject
                        {
                            ["id"] = "UNT0008",
                            ["message"] = new string('x', 12000),
                            ["line"] = i + 1,
                        })
                ),
                ["context_diagnostics"] = new JArray(),
                ["providers"] = new JObject { ["compiler"] = "test-compiler" },
            };
            jobType.GetField("Id", flags).SetValue(job, id);
            jobType.GetField("State", flags).SetValue(job, "completed");
            jobType.GetField("Result", flags).SetValue(job, result);
            jobType.GetField("Terminal", flags).SetValue(job, true);
            jobs.Add(id, job);
            try
            {
                var page = ScriptDiagnosticJobs.Status(id, 500, null);
                Assert.That(Encoding.UTF8.GetByteCount(page.ToString(Formatting.None)), Is.LessThanOrEqualTo(512 * 1024));
                string cursor = (string)page["result"]["next_cursor"];
                Assert.That(cursor, Is.Not.Null);
                int count = ((JArray)page["result"]["rows"]).Count;
                Assert.That((int)ScriptDiagnosticJobs.Status(id, 500, cursor)["result"]["offset"], Is.EqualTo(count));
                var context = (JArray)result["context_diagnostics"];
                context.Add(new JObject { ["line"] = 101 });
                context.Add(new JObject { ["line"] = 102 });
                string CursorAt(int offset) => offset + "." + typeof(ScriptDiagnosticJobs).GetMethod("CursorHash", flags).Invoke(null, new[] { job, offset });
                var boundary = ScriptDiagnosticJobs.Status(id, 2, CursorAt(99))["result"];
                Assert.That((string)boundary["rows"][0]["scope"], Is.EqualTo("selected"));
                Assert.That((int)boundary["rows"][0]["diagnostic"]["line"], Is.EqualTo(100));
                Assert.That((string)boundary["rows"][1]["scope"], Is.EqualTo("context"));
                Assert.That((int)boundary["rows"][1]["diagnostic"]["line"], Is.EqualTo(101));
                boundary["rows"][1]["diagnostic"]["line"] = -1;
                boundary["providers"]["compiler"] = "modified";
                Assert.That((int)context[0]["line"], Is.EqualTo(101));
                Assert.That((string)result["providers"]["compiler"], Is.EqualTo("test-compiler"));

                Assert.Greater(ManagedAllocation.Count(() => GC.KeepAlive(new byte[32])), 0);
                string late = CursorAt(100);
                ScriptDiagnosticJobs.Status(id, 1, late);
                int baseline = ManagedAllocation.Count(() => ScriptDiagnosticJobs.Status(id, 1, late));
                for (int i = 0; i < 4096; i++)
                    context.Add(new JObject { ["line"] = i + 103 });
                late = CursorAt(4196);
                JObject large = null;
                int allocations = ManagedAllocation.Count(() => large = ScriptDiagnosticJobs.Status(id, 1, late));
                Assert.That(allocations, Is.LessThanOrEqualTo(baseline + 16), "An indexed one-row page should allocate independently of inventory size.");
                Assert.That((int)large["result"]["total_rows"], Is.EqualTo(4198));
                Assert.That((int)large["result"]["rows"][0]["diagnostic"]["line"], Is.EqualTo(4197));
                var last = ScriptDiagnosticJobs.Status(id, 1, (string)large["result"]["next_cursor"])["result"];
                Assert.That((int)last["rows"][0]["diagnostic"]["line"], Is.EqualTo(4198));
                Assert.That(last["next_cursor"].Type, Is.EqualTo(JTokenType.Null));
                Assert.That(JObject.Parse(last.ToString(Formatting.None))["next_cursor"].Type, Is.EqualTo(JTokenType.Null));
                jobType.GetField("Id", flags).SetValue(job, Guid.NewGuid().ToString("N"));
                Assert.Throws<ArgumentException>(() => ScriptDiagnosticJobs.Status(id, 100, cursor));
                jobType.GetField("Id", flags).SetValue(job, id);
                result["source_token"] = new string('b', 64);
                Assert.Throws<ArgumentException>(() => ScriptDiagnosticJobs.Status(id, 100, cursor));
                ((JArray)result["diagnostics"]).Clear();
                context.Clear();
                var empty = ScriptDiagnosticJobs.Status(id, 100, null)["result"];
                Assert.That(((JArray)empty["rows"]).Count, Is.Zero);
                Assert.That(empty["next_cursor"].Type, Is.EqualTo(JTokenType.Null));
                Assert.That(JObject.Parse(empty.ToString(Formatting.None))["next_cursor"].Type, Is.EqualTo(JTokenType.Null));
                result["providers"]["compiler"] = new string('x', 512 * 1024);
                Assert.Throws<InvalidOperationException>(() => ScriptDiagnosticJobs.Status(id, 100, null));
            }
            finally
            {
                jobs.Remove(id);
                ((CancellationTokenSource)jobType.GetField("Cancellation", flags).GetValue(job)).Dispose();
            }
        }

        [UnityTest]
        public IEnumerator NativeContextRunsPinnedDiagnosticsAndPreservesSelectedBytes()
        {
            const string path = "Assets/Scripts/TestAsmdef/ScriptQuality/SelectedQualitySource.cs";
            string physical = ScriptSelection.Physical(path);
            byte[] original = File.ReadAllBytes(physical),
                metadata = File.ReadAllBytes(physical + ".meta");
            var selection = ScriptSelection.Resolve(new JObject { ["paths"] = new JArray(path) });
            var snapshot = new SourceSnapshot("project", "session", "sources", "context", true, Array.Empty<string>(), 1, 1);
            var capture = ScriptCompilationContext.CaptureAsync(selection, snapshot, true, CancellationToken.None);
            while (!capture.IsCompleted)
                yield return null;
            Assert.That(capture.IsFaulted, Is.False, capture.Exception?.ToString());
            JObject request = capture.Result;
            Assert.That(request["selected_paths"].Values<string>(), Is.EqualTo(new[] { path }));
            Assert.That(request["assemblies"].Any(a => (string)a["name"] == "TestAsmdef"), Is.True);
            string package = PackageInfo.FindForAssembly(typeof(AnalysisHostProcess).Assembly).resolvedPath;
            string cache = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Library", "MCPQualityFixture-" + Guid.NewGuid().ToString("N")));
            using var owner = new AnalysisHostProcess();
            var setup = AnalysisHostInstallation.InstallAsync(Path.Combine(package, "Editor", "ScriptDiagnosticsHost~"), cache, owner, CancellationToken.None);
            while (!setup.IsCompleted)
                yield return null;
            Assert.That(setup.IsFaulted, Is.False, setup.Exception?.ToString());
            string host = AnalysisHostInstallation.ResolveVerifiedHost(cache);
            byte[] json = Encoding.UTF8.GetBytes(request.ToString(Formatting.None));
            File.WriteAllBytes(Path.Combine(cache, "request.json"), json);
            using var input = new MemoryStream();
            byte[] length = BitConverter.GetBytes(json.Length);
            input.Write(length, 0, length.Length);
            input.Write(json, 0, json.Length);
            input.Position = 0;
            var run = owner.RunAsync("dotnet", new[] { host }, Path.GetDirectoryName(host), input, true, CancellationToken.None);
            while (!run.IsCompleted)
                yield return null;
            Assert.That(run.IsFaulted, Is.False, run.Exception?.ToString());
            var result = JObject.Parse(Encoding.UTF8.GetString(run.Result));
            Assert.That(result["diagnostics"].Any(d => (string)d["id"] == "UNT0008" && (string)d["path"] == path), Is.True, result.ToString());
            Assert.That(result["context_diagnostics"].Any(d => (string)d["id"] == "UNT0008"), Is.True, result.ToString());
            Assert.That(result["diagnostics"].Concat(result["context_diagnostics"]).Any(d => (string)d["severity"] == "error"), Is.False, result.ToString());
            Assert.That(File.ReadAllBytes(physical), Is.EqualTo(original));
            Assert.That(File.ReadAllBytes(physical + ".meta"), Is.EqualTo(metadata));
        }

        [UnityTest]
        public IEnumerator ContextReadsUncompiledBytesAndFlagsMissingSourceMembership()
        {
            const string path = "Assets/Scripts/TestAsmdef/ScriptQuality/SelectedQualitySource.cs";
            string physical = ScriptSelection.Physical(path);
            string extra = Path.Combine(Path.GetDirectoryName(physical), "Unimported_" + Guid.NewGuid().ToString("N") + ".cs");
            byte[] original = File.ReadAllBytes(physical),
                metadata = File.ReadAllBytes(physical + ".meta");
            try
            {
                File.WriteAllText(physical, "class BrokenCurrentSource { this is not compilable }");
                File.WriteAllText(extra, "class UnimportedContext {}");
                var selection = ScriptSelection.Resolve(new JObject { ["paths"] = new JArray(path) });
                var snapshot = new SourceSnapshot("project", "session", "source", "context", true, Array.Empty<string>(), 1, 1);
                var capture = ScriptCompilationContext.CaptureAsync(selection, snapshot, false, CancellationToken.None);
                while (!capture.IsCompleted)
                    yield return null;
                Assert.That(capture.IsFaulted, Is.False, capture.Exception?.ToString());
                var assembly = capture.Result["assemblies"].Single(a => (string)a["name"] == "TestAsmdef");
                Assert.That((string)assembly["sources"].Single(s => (string)s["path"] == path)["text"], Does.Contain("this is not compilable"));
                Assert.That(assembly["coverage_gaps"].Values<string>().Any(g => g.StartsWith("unimported_context_source:", StringComparison.Ordinal)), Is.True);
                Assert.That(File.ReadAllBytes(physical + ".meta"), Is.EqualTo(metadata));
            }
            finally
            {
                File.WriteAllBytes(physical, original);
                File.Delete(extra);
            }
        }
    }
}
