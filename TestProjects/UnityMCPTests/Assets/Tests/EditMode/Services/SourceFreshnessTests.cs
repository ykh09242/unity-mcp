using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.SourceFreshness;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class SourceFreshnessTests
    {
        [TestCase("discovery")]
        [TestCase("root")]
        [TestCase("input")]
        [TestCase("environment")]
        public void ExcludedInputsAreRejectedBeforeAnyFileSystemAccess(string route)
        {
            // A purely synthetic filesystem: never create or inspect excluded paths on disk.
            string root = Path.GetFullPath("synthetic-source-root");
            string resources = Path.Combine(root, "Assets", "Resources");
            string excluded = Path.Combine(resources, "GameData");
            string allowed = Path.Combine(resources, "Allowed.cs");
            void Access(string path)
            {
                if (
                    path.StartsWith(excluded, StringComparison.OrdinalIgnoreCase)
                    || Path.GetFileName(path).StartsWith(".env", StringComparison.OrdinalIgnoreCase)
                )
                    Assert.Fail("Excluded input received a filesystem operation.");
            }
            var scope = new SourceScope(root, "session", "context");
            scope.Roots.Clear();
            scope.FileSystem = new SourceFileSystem
            {
                DirectoryExists = path =>
                {
                    Access(path);
                    return path == resources;
                },
                FileExists = path =>
                {
                    Access(path);
                    return false;
                },
                Attributes = path =>
                {
                    Access(path);
                    return FileAttributes.Normal;
                },
                Entries = path =>
                {
                    Access(path);
                    return new[] { excluded, allowed };
                },
                Open = path =>
                {
                    Access(path);
                    return new MemoryStream(new byte[] { 1 });
                },
            };
            if (route == "discovery")
                scope.Roots["Assets/Resources"] = resources;
            else if (route == "root")
                scope.Roots["alias"] = excluded;
            else
                scope.Inputs["alias"] = route == "input" ? Path.Combine(excluded, "Input.cs") : Path.Combine(root, ".env.local");
            var snapshot = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
            Assert.IsFalse(snapshot.Complete);
            CollectionAssert.Contains(snapshot.Reasons, "excluded_compilation_input");
            Assert.AreEqual(route == "discovery" ? 1 : 0, snapshot.FileCount);
        }

        [Test]
        public void ChangedOrIncompleteSourceCannotInheritAnEarlierCompilationFailure()
        {
            var ledger = new SourceFreshnessLedger("project", "session");
            ledger.BeginCompilation(Compilation().CompileId, Snapshot(), "domain");
            Assert.AreEqual("compile_failed", ledger.ObserveCompilation(Snapshot(), Compilation("failed")).Status);
            Assert.AreEqual("changed", ledger.ObserveCompilation(Snapshot("fixed"), Compilation("failed")).Status);
            Assert.AreEqual("incomplete", ledger.ObserveCompilation(Snapshot(complete: false), Compilation("failed")).Status);
            ledger.BeginCompilation(Compilation().CompileId, Snapshot("fixed"), "domain");
            Assert.AreEqual("observed_current", ledger.ObserveCompilation(Snapshot("fixed"), Compilation()).Status);
        }

        [Test]
        public void SourceObservationExposesTheCapturedUnityVersion()
        {
            var scope = SourceFreshnessService.CaptureScope();
            scope.Roots.Clear();
            scope.Inputs.Clear();
            scope.FileSystem = new SourceFileSystem { FileExists = _ => false };
            var snapshot = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
            var observation = new FreshnessObservation { Snapshot = snapshot };
            Assert.AreEqual(UnityEngine.Application.unityVersion, (string)observation.ToJson("p", "s")["unity_version"]);
        }

        [TestCase("all", "request")]
        [TestCase("scripts", "none")]
        public void CleanCompilationRefusesAnAmbiguousImportOrNonCompilationRequest(string scope, string compile)
        {
            var response = JObject.FromObject(
                RefreshUnity
                    .HandleCommand(
                        new JObject
                        {
                            ["scope"] = scope,
                            ["compile"] = compile,
                            ["force_recompile"] = true,
                        }
                    )
                    .GetAwaiter()
                    .GetResult()
            );
            Assert.IsFalse((bool)response["success"]);
            StringAssert.Contains("force_recompile", (string)response["error"]);
        }

        [Test]
        public void UnityCompilationContextDoesNotInvokeUnsupportedCompilerOptionGetters()
        {
            var scope = SourceFreshnessService.CaptureScope();
            StringAssert.Contains("LanguageVersion", scope.Context);
            StringAssert.DoesNotContain("EmitReferenceAssembly", scope.Context);
            CollectionAssert.DoesNotContain(scope.Reasons, "compiler_options_unavailable");
        }

        [Test]
        public void ContentAndMembershipChangesInvalidateTheSnapshotDespiteUnchangedTimestamps()
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-freshness-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "Assets"));
            try
            {
                string file = Path.Combine(root, "Assets/A.cs");
                File.WriteAllText(file, "class A { const int Value = 1; }");
                var scope = new SourceScope(root, "session", "context");
                var first = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
                var again = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
                Assert.IsTrue(first.Complete);
                Assert.AreEqual(first.Token, again.Token);
                var time = File.GetLastWriteTimeUtc(file);
                File.WriteAllText(file, "class A { const int Value = 2; }");
                File.SetLastWriteTimeUtc(file, time);
                var edited = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
                Assert.AreNotEqual(first.Token, edited.Token);
                File.Move(file, Path.Combine(root, "Assets/B.cs"));
                var renamed = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
                Assert.AreNotEqual(edited.Token, renamed.Token);
                File.Delete(Path.Combine(root, "Assets/B.cs"));
                var deleted = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
                Assert.AreNotEqual(renamed.Token, deleted.Token);
                File.WriteAllText(file, "class A { const int Value = 1; }");
                Assert.AreEqual(first.Token, (SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult()).Token);
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [TestCase("Assets/A.asmdef")]
        [TestCase("Assets/A.asmref")]
        [TestCase("Assets/csc.rsp")]
        [TestCase("Packages/manifest.json")]
        [TestCase("ProjectSettings/ProjectSettings.asset")]
        [TestCase(".editorconfig")]
        public void CompilerConfigurationAndPackageInputsParticipateInIdentity(string relative)
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-context-" + Guid.NewGuid().ToString("N"));
            string path = Path.Combine(root, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            try
            {
                File.WriteAllText(path, "one");
                var scope = new SourceScope(root, "session", "defines=A");
                var before = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
                File.WriteAllText(path, "two");
                var after = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
                Assert.AreNotEqual(before.Token, after.Token);
                var changedDefines = SourceSnapshotCollector
                    .CaptureAsync(new SourceScope(root, "session", "defines=B"), CancellationToken.None)
                    .GetAwaiter()
                    .GetResult();
                Assert.AreNotEqual(after.Token, changedDefines.Token);
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void IncompleteScanAndCancellationNeverProduceACompleteSnapshot()
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-bounds-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "Assets"));
            try
            {
                File.WriteAllText(Path.Combine(root, "Assets/A.cs"), new string('a', 100));
                var scope = new SourceScope(root, "session", "context") { MaxBytes = 16 };
                var snapshot = SourceSnapshotCollector.CaptureAsync(scope, CancellationToken.None).GetAwaiter().GetResult();
                Assert.IsFalse(snapshot.Complete);
                CollectionAssert.Contains(snapshot.Reasons, "input_limit");
                var cancellation = new CancellationToken(true);
                Assert.Throws<TaskCanceledException>(() => SourceSnapshotCollector.CaptureAsync(scope, cancellation).GetAwaiter().GetResult());
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void OutputIdentityReaderMatchesLoadedMetadataWithoutLoadingTheOutput()
        {
            var assembly = typeof(SourceFreshnessTests).Assembly;
            int count = AppDomain.CurrentDomain.GetAssemblies().Length;
            Assert.AreEqual(assembly.ManifestModule.ModuleVersionId.ToString("N"), AssemblyIdentity.ReadModuleId(assembly.Location));
            Assert.AreEqual(count, AppDomain.CurrentDomain.GetAssemblies().Length);
            Assert.IsNull(AssemblyIdentity.ReadModuleId("missing-output.dll"));
        }

        internal static SourceSnapshot Snapshot(string source = "source", bool complete = true) =>
            new SourceSnapshot("project", "session", source, "context", complete, complete ? Array.Empty<string>() : new[] { "input_limit" }, 1, 10);

        internal static CompileObservation Compilation(string outcome = "success", bool loaded = true) =>
            new CompileObservation
            {
                CompileId = "11111111111111111111111111111111",
                Generation = 1,
                Outcome = outcome,
                Complete = true,
                Attributed = true,
                Finished = true,
                AssemblyCount = 1,
                ReloadObserved = loaded,
                Assemblies = new[] { new AssemblyIdentity("A", "abc", loaded ? "abc" : "old") },
            };

        [Test]
        public void CurrentRequiresStableInputsAttributedCompilationAndMatchingLoadedOutputs()
        {
            var ledger = new SourceFreshnessLedger("project", "session");
            Assert.AreEqual("incomplete", ledger.ObserveSnapshot(Snapshot()).Status);
            ledger.BeginCompilation(Compilation().CompileId, Snapshot(), "domain");
            Assert.AreEqual("pending", ledger.Observation.Status);
            Assert.AreEqual("observed_current", ledger.ObserveCompilation(Snapshot(), Compilation()).Status);
            Assert.IsTrue(ledger.CheckAdmission(Snapshot().Token));
            Assert.IsFalse(ledger.CheckAdmission("foreign"));
            Assert.AreEqual("changed", ledger.ObserveSnapshot(Snapshot("changed")).Status);
            Assert.IsFalse(ledger.CheckAdmission(Snapshot().Token));
        }

        [TestCase("failed", true, true, "compile_failed")]
        [TestCase("success", false, true, "pending")]
        [TestCase("success", true, false, "incomplete")]
        [TestCase("unknown", true, true, "incomplete")]
        public void MissingCompileLoadOrScanEvidenceCannotCertifyCurrent(string outcome, bool loaded, bool complete, string expected)
        {
            var ledger = new SourceFreshnessLedger("project", "session");
            ledger.BeginCompilation(Compilation().CompileId, Snapshot(), "domain");
            Assert.AreEqual(expected, ledger.ObserveCompilation(Snapshot(complete: complete), Compilation(outcome, loaded)).Status);
        }

        [Test]
        public void CanceledBeeAndMismatchedLoadedOutputCannotReuseOldSuccess()
        {
            var ledger = new SourceFreshnessLedger("project", "session");
            ledger.BeginCompilation(Compilation().CompileId, Snapshot(), "domain");
            var canceled = Compilation();
            canceled.Complete = false;
            canceled.Reason = "compilation_canceled";
            Assert.AreEqual("incomplete", ledger.ObserveCompilation(Snapshot(), canceled).Status);
            var mismatched = Compilation();
            mismatched.Assemblies = new[] { new AssemblyIdentity("A", "new", "old") };
            Assert.AreEqual("incomplete", ledger.ObserveCompilation(Snapshot(), mismatched).Status);
        }

        [Test]
        public void NoOpCompilationNeedsPriorCurrentEvidenceAndCannotCreateAColdBaseline()
        {
            var ledger = new SourceFreshnessLedger("project", "session");
            ledger.BeginCompilation(Compilation().CompileId, Snapshot(), "domain");
            var noOp = Compilation("unknown");
            noOp.AssemblyCount = 0;
            noOp.Reason = "no_assembly_compilation";
            Assert.AreEqual("incomplete", ledger.ObserveCompilation(Snapshot(), noOp).Status);
            ledger.ObserveCompilation(Snapshot(), Compilation());
            ledger.BeginCompilation(Compilation().CompileId, Snapshot(), "domain");
            Assert.AreEqual("observed_current", ledger.ObserveCompilation(Snapshot(), noOp).Status);
            ledger.BeginCompilation(Compilation().CompileId, Snapshot("changed"), "domain");
            Assert.AreEqual("incomplete", ledger.ObserveCompilation(Snapshot("changed"), noOp).Status);
        }

        [Test]
        public void CompileInspectionDoesNotConfuseMissingSourceEvidenceWithReadiness()
        {
            var response = JObject.FromObject(
                GetCompileJob.HandleCommand(new JObject { ["compile_id"] = "ffffffffffffffffffffffffffffffff" }).GetAwaiter().GetResult()
            );
            var freshness = response["source_freshness"];
            Assert.IsNotNull(freshness, "Compilation inspection must separately report the source evidence, including its absence.");
            Assert.AreEqual("unity-mcp/source_freshness@1", (string)freshness["schema"]);
            Assert.AreNotEqual("observed_current", (string)freshness["status"]);
            Assert.AreEqual(false, (bool)freshness["source_revision_confirmed"]);
        }
    }
}
