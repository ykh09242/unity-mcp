using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.AssetGen;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.AssetGen
{
    [Parallelizable(ParallelScope.None)]
    public class LocalModelPreparationTests
    {
        private string project;
        private string assetsRoot;
        private string root;
        private string sourceRoot;
        private string outputRoot;
        private string privateRoot;
        private string[] previousPrivateDirectories;

        [SetUp]
        public void SetUp()
        {
            assetsRoot = Application.dataPath;
            project = Path.GetDirectoryName(assetsRoot);
            root = "Assets/__local_model_preparation_" + Guid.NewGuid().ToString("N");
            sourceRoot = Path.Combine(project, root, "Sources");
            outputRoot = root + "/Output";
            Directory.CreateDirectory(sourceRoot);
            privateRoot = Path.Combine(project, "Temp", "MCPForUnity", "LocalModels");
            previousPrivateDirectories = PrivateDirectories();
            AssetDiskBudget.FreeBytesForTests = _ => 1024 * 1024;
            AssetDiskBudget.HeadroomBytesForTests = 1;
        }

        [TearDown]
        public void TearDown()
        {
            LocalAssetCopy.OpenReadForTests = null;
            LocalAssetCopy.DeleteForTests = null;
            AssetDiskBudget.FreeBytesForTests = null;
            AssetDiskBudget.HeadroomBytesForTests = null;
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project), "a completed preparation must not leak disk credit");
            CollectionAssert.AreEquivalent(previousPrivateDirectories, PrivateDirectories(), "only shared temporary ancestors may remain");
            if (AssetDatabase.IsValidFolder(root))
                AssetDatabase.DeleteAsset(root);
            string absolute = Path.GetFullPath(Path.Combine(project, root));
            Assert.IsTrue(absolute.StartsWith(Path.GetFullPath(assetsRoot) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            if (Directory.Exists(absolute))
                Directory.Delete(absolute, true);
            if (File.Exists(absolute + ".meta"))
                File.Delete(absolute + ".meta");
        }

        private string[] PrivateDirectories() => Directory.Exists(privateRoot) ? Directory.GetDirectories(privateRoot) : Array.Empty<string>();

        private string Write(string name, string content)
        {
            string path = Path.Combine(sourceRoot, name);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, content);
            return path;
        }

        private JObject Parameters(string source) =>
            new()
            {
                ["sourcePath"] = source,
                ["outputFolder"] = outputRoot,
                ["name"] = "prepared",
            };

        [UnityTest]
        public IEnumerator CancellationWhileReaderIsBlockedWaitsForWorkerAndNeverPublishes()
        {
            // Given a small source whose filesystem reader pauses on the worker.
            string source = Write("model.fbx", "owned fixture bytes");
            byte[] original = File.ReadAllBytes(source);
            int editorThread = Thread.CurrentThread.ManagedThreadId;
            int readerThread = editorThread;
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            using var cancellation = new CancellationTokenSource();
            BlockingReader reader = null;
            LocalAssetCopy.OpenReadForTests = _ =>
            {
                readerThread = Thread.CurrentThread.ManagedThreadId;
                reader = new BlockingReader(original, entered, release);
                return reader;
            };
            Task<object> operation = ImportModelFile.HandleCommand(Parameters(source), cancellation.Token);
            var deadline = Stopwatch.StartNew();
            while (!entered.IsSet && !operation.IsCompleted && deadline.Elapsed < TimeSpan.FromSeconds(10))
                yield return null;

            // When cancellation arrives during a blocking OS read, ownership cannot be released yet.
            bool reachedReader = entered.IsSet;
            cancellation.Cancel();
            yield return null;
            bool pendingUntilReaderStops = !operation.IsCompleted;
            long heldReservation = AssetDiskBudget.ReservedBytesForTests(project);
            bool publishedEarly = Directory.Exists(Path.Combine(project, outputRoot));
            release.Set();
            deadline.Restart();
            while (!operation.IsCompleted && deadline.Elapsed < TimeSpan.FromSeconds(10))
                yield return null;

            // Then the worker closes the stream, removes only its temporary copy, and cancels.
            Assert.IsTrue(reachedReader, operation.Exception?.ToString());
            Assert.AreNotEqual(editorThread, readerThread, "byte copying must not run on the Editor thread");
            Assert.IsTrue(pendingUntilReaderStops, "cancellation must not detach the active worker");
            Assert.AreEqual(original.Length, heldReservation);
            Assert.IsFalse(publishedEarly);
            Assert.IsTrue(operation.IsCanceled, operation.Exception?.ToString());
            Assert.IsTrue(reader.Closed);
            Assert.AreEqual(1, reader.Reads, "a canceled read must not be followed by another read");
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
            Assert.IsFalse(Directory.Exists(Path.Combine(project, outputRoot)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(outputRoot + "/prepared.fbx", AssetPathToGUIDOptions.OnlyExistingAssets));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(source));
        }

        [UnityTest]
        public IEnumerator QueuedCancellationDoesNotOpenAnotherSourceOrAcquireItsReservation()
        {
            string source = Write("queued.fbx", "small bytes");
            byte[] bytes = File.ReadAllBytes(source);
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            using var firstCancellation = new CancellationTokenSource();
            using var queuedCancellation = new CancellationTokenSource();
            int opens = 0;
            LocalAssetCopy.OpenReadForTests = _ =>
            {
                Interlocked.Increment(ref opens);
                return new BlockingReader(bytes, entered, release);
            };
            Task<object> first = ImportModelFile.HandleCommand(Parameters(source), firstCancellation.Token);
            var deadline = Stopwatch.StartNew();
            while (!entered.IsSet && !first.IsCompleted && deadline.Elapsed < TimeSpan.FromSeconds(10))
                yield return null;
            Task<object> queued = ImportModelFile.HandleCommand(Parameters(source), queuedCancellation.Token);
            queuedCancellation.Cancel();
            deadline.Restart();
            while (!queued.IsCompleted && deadline.Elapsed < TimeSpan.FromSeconds(10))
                yield return null;
            bool queuedCanceled = queued.IsCanceled;
            int openedBeforeRelease = opens;
            long reservedBeforeRelease = AssetDiskBudget.ReservedBytesForTests(project);
            firstCancellation.Cancel();
            release.Set();
            deadline.Restart();
            while (!first.IsCompleted && deadline.Elapsed < TimeSpan.FromSeconds(10))
                yield return null;
            Assert.IsTrue(queuedCanceled, queued.Exception?.ToString());
            Assert.AreEqual(1, openedBeforeRelease);
            Assert.AreEqual(bytes.Length, reservedBeforeRelease);
            Assert.IsTrue(first.IsCanceled, first.Exception?.ToString());
            Assert.IsFalse(Directory.Exists(Path.Combine(project, outputRoot)));
        }

        [Test]
        public void CanceledPlanPropagatesCancellationWithoutAllocatingOutput()
        {
            string source = Write("model.obj", "o model\nv 0 0 0\n");
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            Assert.Throws<OperationCanceledException>(() => ModelDependencyBundle.Plan(source, cancellationToken: cancellation.Token, assetsRoot: assetsRoot));
            Assert.IsFalse(Directory.Exists(Path.Combine(project, outputRoot)));
            CollectionAssert.AreEquivalent(previousPrivateDirectories, PrivateDirectories());
        }

        [Test]
        public void PreparedSnapshotKeepsByteCreditWithoutHoldingTheCopySlot()
        {
            string source = Write("model.fbx", "owned bytes");
            using LocalModelPreparation prepared = LocalModelPreparation.PrepareAsync(source, assetsRoot, CancellationToken.None).GetAwaiter().GetResult();
            long bytes = new FileInfo(source).Length;
            Assert.AreEqual(bytes, AssetDiskBudget.ReservedBytesForTests(project));
            // A synchronous Editor caller must not deadlock behind a completed worker
            // whose publication continuation is waiting for that same Editor thread.
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            using (AssetDiskBudget.ReserveExtraction(project, 1, deadline.Token))
                Assert.AreEqual(bytes + 1, AssetDiskBudget.ReservedBytesForTests(project));
            Assert.AreEqual(bytes, AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void CancellationAfterPreparationPreventsPublicationAndReleasesOwnedSnapshot()
        {
            string source = Write("model.fbx", "owned bytes");
            using var cancellation = new CancellationTokenSource();
            using (LocalModelPreparation prepared = LocalModelPreparation.PrepareAsync(source, assetsRoot, cancellation.Token).GetAwaiter().GetResult())
            {
                Assert.AreEqual(new FileInfo(source).Length, AssetDiskBudget.ReservedBytesForTests(project));
                cancellation.Cancel();
                Assert.Throws<OperationCanceledException>(() => prepared.Publish(outputRoot, "model"));
            }
            Assert.IsFalse(Directory.Exists(Path.Combine(project, outputRoot)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(outputRoot + "/model.fbx", AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.AreEqual("owned bytes", File.ReadAllText(source));
        }

        [Test]
        public void UncommittedBundlePublicationRollsBackMovedFilesAndOwnedFolders()
        {
            string source = Write("model.gltf", "{\"asset\":{\"version\":\"2.0\"},\"buffers\":[{\"uri\":\"data/mesh.bin\",\"byteLength\":4}]}");
            string buffer = Write("data/mesh.bin", "data");
            using var cancellation = new CancellationTokenSource();
            string destination;
            using (LocalModelPreparation prepared = LocalModelPreparation.PrepareAsync(source, assetsRoot, cancellation.Token).GetAwaiter().GetResult())
            {
                BundleStageResult published = prepared.Publish(outputRoot, "model");
                destination = published.MainAssetPath;
                Assert.IsTrue(File.Exists(destination));
                Assert.IsEmpty(
                    AssetDatabase.AssetPathToGUID(destination, AssetPathToGUIDOptions.OnlyExistingAssets),
                    "publication must not start native model import"
                );
                cancellation.Cancel();
                Assert.Throws<OperationCanceledException>(() => prepared.Commit());
            }
            Assert.IsFalse(File.Exists(destination));
            Assert.IsFalse(Directory.Exists(Path.Combine(project, outputRoot)));
            Assert.IsTrue(File.Exists(source));
            Assert.AreEqual("data", File.ReadAllText(buffer));
        }

        [Test]
        public void ChangedMetadataIsRejectedBeforePublicationAndOriginalSourceIsPreserved()
        {
            string source = Write("model.obj", "o model\nv 0 0 0\n");
            byte[] original = File.ReadAllBytes(source);
            byte[] changed = (byte[])original.Clone();
            changed[2] = (byte)'x';
            LocalAssetCopy.OpenReadForTests = _ => new MemoryStream(changed, false);
            BundleImportException failure = Assert.Throws<BundleImportException>(() =>
                LocalModelPreparation.Prepare(source, assetsRoot, CancellationToken.None)
            );
            Assert.AreEqual("dependency_source_changed", (string)failure.Evidence["error_code"]);
            Assert.IsFalse(Directory.Exists(Path.Combine(project, outputRoot)));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(source));
        }

        [Test]
        public void FailedCancellationCleanupRetainsCreditUntilTheOwnedFileIsRemoved()
        {
            string source = Write("model.fbx", "owned bytes");
            using var cancellation = new CancellationTokenSource();
            string survivor = null;
            LocalAssetCopy.OpenReadForTests = _ => new CancelingReader(File.ReadAllBytes(source), cancellation);
            LocalAssetCopy.DeleteForTests = path =>
            {
                survivor = path;
                throw new IOException("injected owned-file lock");
            };
            Assert.Throws<OperationCanceledException>(() => LocalModelPreparation.Prepare(source, assetsRoot, cancellation.Token));
            Assert.IsNotNull(survivor);
            Assert.IsTrue(File.Exists(survivor));
            Assert.AreEqual(new FileInfo(source).Length, AssetDiskBudget.ReservedBytesForTests(project));
            LocalAssetCopy.DeleteForTests = null;
            File.Delete(survivor);
            using (AssetDiskBudget.ReserveExtraction(project, 1, CancellationToken.None))
                Assert.AreEqual(1, AssetDiskBudget.ReservedBytesForTests(project), "admission reclaims only confirmed removed output");
            string ownedDirectory = Path.GetDirectoryName(survivor);
            Assert.IsTrue(PrivateDirectories().Except(previousPrivateDirectories).Contains(ownedDirectory));
            Directory.Delete(ownedDirectory, false);
            Assert.AreEqual("owned bytes", File.ReadAllText(source));
        }

        [UnityTest]
        public IEnumerator CooperativeHandlerImportsOnEditorThreadAndPreservesAnExistingDestination()
        {
            string source = Write("triangle.obj", "o Triangle\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n");
            Directory.CreateDirectory(Path.Combine(project, outputRoot));
            string existing = Path.Combine(project, outputRoot, "prepared.obj");
            string existingContents = File.ReadAllText(source);
            Task<object> operation = ImportModelFile.HandleCommand(Parameters(source), CancellationToken.None);
            File.WriteAllText(existing, existingContents); // Appears while the worker prepares its snapshot.
            var deadline = Stopwatch.StartNew();
            while (!operation.IsCompleted && deadline.Elapsed < TimeSpan.FromSeconds(10))
                yield return null;
            Assert.IsTrue(operation.IsCompleted && !operation.IsFaulted, operation.Exception?.ToString());
            JObject response = JObject.FromObject(operation.Result);
            Assert.IsTrue((bool)response["success"], response.ToString());
            string result = (string)response["data"]["asset_path"];
            Assert.AreEqual(outputRoot + "/prepared_1.obj", result);
            Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(result));
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(result)?.GetComponentInChildren<MeshFilter>(true)?.sharedMesh);
            Assert.AreEqual(existingContents, File.ReadAllText(existing));
            Assert.IsTrue(File.Exists(source));
        }

        [UnityTest]
        public IEnumerator CancellationAfterCommitRetainsPathsThroughTransportJournal() => CanceledPublishedModel(false);

        [UnityTest]
        public IEnumerator CancellationDuringNativeImportRetainsPathsThroughTransportJournal() => CanceledPublishedModel(true);

        [Test]
        public void CanceledArchiveReceiptOmitsCandidateInventoryAndRetainsPublishedPaths()
        {
            // Given retained bytes and a valid, large archive-selection inventory.
            string source = Write("retained/model.obj", "o retained\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n");
            string path = root + "/Sources/retained/model.obj";
            var candidates = new JArray(Enumerable.Range(0, 200).Select(index => new string('x', 64) + index + ".obj"));
            var archive = new JObject
            {
                ["archive_path"] = outputRoot + "/models.zip",
                ["selected_asset_path"] = path,
                ["extraction_root"] = root + "/Sources/retained",
                ["extraction"] = "complete",
                ["candidate_count"] = candidates.Count,
                ["candidates"] = candidates,
                ["candidates_complete"] = true,
            };
            var job = new AssetGenJob
            {
                AssetPath = path,
                ModelImportStage = "registered",
                ArchiveSelection = archive,
            };
            var committed = new BundleStageResult
            {
                MainAssetPath = outputRoot + "/models.zip",
                Evidence = new JObject { ["stage"] = "staged" },
            };
            string stored = null;
            var journal = new CommandResumeJournal(() => stored, value => stored = value, () => 1000);
            var request = new JObject
            {
                ["version"] = 1,
                ["invocation_id"] = Guid.NewGuid().ToString("N"),
                ["editor_session_id"] = journal.SessionId,
                ["deadline_unix_ms"] = 10000,
                ["action"] = "new",
            };
            var admission = journal.Admit(request, "import_model_file", new JObject());
            Assert.IsTrue(journal.TryStart(admission.Ticket));
            request["action"] = "cancel";
            journal.Admit(request, "import_model_file", new JObject());
            // When the real cancellation receipt is checkpointed at the existing bounded journal.
            JObject result = JObject.FromObject(ImportModelFile.CancellationReceipt(job, committed));
            journal.Complete(admission.Ticket, new JObject { ["status"] = "success", ["result"] = result });
            journal.Release(admission.Ticket);
            journal = new CommandResumeJournal(() => stored, value => stored = value, () => 1000);
            request["action"] = "resume";
            var resumed = journal.Admit(request, "import_model_file", new JObject());
            // Then inventory size cannot erase the inspectable saved output and cancellation fence.
            Assert.AreEqual("canceled", resumed.State);
            Assert.IsNotNull(resumed.Payload);
            JToken data = resumed.Payload["result"]["data"];
            Assert.AreEqual(path, data.Value<string>("asset_path"));
            Assert.AreEqual("registered", data.Value<string>("model_import_stage"));
            Assert.IsTrue(data.Value<bool>("source_saved"));
            Assert.AreEqual(archive["extraction_root"], data["archive_selection"]["extraction_root"]);
            Assert.AreEqual(200, data["archive_selection"].Value<int>("candidate_count"));
            Assert.IsNull(data["archive_selection"]["candidates"]);
            Assert.IsTrue(data["archive_selection"].Value<bool>("candidates_omitted"));
            Assert.AreEqual(200, ((JArray)archive["candidates"]).Count, "Receipt compaction must not alter the import job's evidence.");
            Assert.AreEqual("o retained\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", File.ReadAllText(source));
        }

        private IEnumerator CanceledPublishedModel(bool duringImport)
        {
            // Given real model bytes, a collision, and a resumable transport invocation.
            string source = Write("triangle.obj", "o Triangle\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n");
            byte[] original = File.ReadAllBytes(source);
            Directory.CreateDirectory(Path.Combine(project, outputRoot));
            string collision = Path.Combine(project, outputRoot, "prepared.obj");
            File.WriteAllText(collision, "existing owned output");
            const string commandName = "__local_model_cancel_receipt";
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            object previous = handlers[commandName];
            handlers[commandName] = HandlerInfo.Cooperative(commandName, ImportModelFile.HandleCommand);
            string stored = null;
            var journal = new CommandResumeJournal(() => stored, value => stored = value, () => 1000);
            var parameters = Parameters(source);
            parameters["targetSize"] = 0;
            var request = new JObject
            {
                ["version"] = 1,
                ["invocation_id"] = Guid.NewGuid().ToString("N"),
                ["editor_session_id"] = journal.SessionId,
                ["deadline_unix_ms"] = 10000,
                ["action"] = "new",
            };
            var admission = journal.Admit(request, commandName, parameters);
            using var cancellation = new CancellationTokenSource();
            journal.BindCancellation(admission.Ticket, cancellation.Cancel);
            var hook = typeof(ImportModelFile).GetField("AfterCommitForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(hook);
            bool reachedBoundary = false;
            long reservationAtCancel = 0;
            void Cancel()
            {
                reachedBoundary = true;
                reservationAtCancel = AssetDiskBudget.ReservedBytesForTests(project);
                request["action"] = "cancel";
                Assert.AreEqual("canceled", journal.Admit(request, commandName, parameters).State);
            }
            if (duringImport)
                ModelCancellationPostprocessor.Observe = imported =>
                {
                    if (!reachedBoundary && imported.Contains(outputRoot + "/prepared_1.obj"))
                        Cancel();
                };
            else
                hook.SetValue(null, (Action)(() => Cancel()));
            TransportCommandOperation operation = null;
            try
            {
                // When cancellation arrives only after bytes are committed (or inside native import).
                operation = TransportCommandDispatcher.ExecuteCommandAsync(
                    new Command
                    {
                        type = commandName,
                        @params = parameters,
                        ResumeTicket = admission.Ticket,
                    },
                    cancellation.Token
                );
                var deadline = Stopwatch.StartNew();
                while (!operation.Completion.IsCompleted && deadline.Elapsed < TimeSpan.FromSeconds(15))
                    yield return null;
                // Then settlement retains a canceled fence and a reloadable publication receipt.
                Assert.IsTrue(operation.Completion.IsCompleted);
                Assert.IsTrue(reachedBoundary);
                Assert.Greater(reservationAtCancel, 0, "Cancellation must not release the active preparation.");
                Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
                journal.Release(admission.Ticket);
                journal = new CommandResumeJournal(() => stored, value => stored = value, () => 1000);
                request["action"] = "resume";
                var resumed = journal.Admit(request, commandName, parameters);
                Assert.AreEqual("canceled", resumed.State);
                Assert.IsNull(resumed.Ticket, "The retained result must not schedule a duplicate import.");
                JObject envelope = JObject.Parse(resumed.Response(null).ToJson());
                JToken result = envelope["payload"]["result"];
                Assert.IsNotNull(result, envelope.ToString());
                Assert.IsFalse(result.Value<bool>("success"));
                JToken data = result["data"];
                Assert.IsTrue(data.Value<bool>("source_saved"));
                Assert.AreEqual("canceled", data.Value<string>("execution_status"));
                Assert.AreEqual(outputRoot + "/prepared_1.obj", data.Value<string>("asset_path"));
                Assert.AreEqual(duringImport ? "usable" : "saved", data.Value<string>("model_import_stage"));
                Assert.AreEqual("inspect_then_reimport_retained", data.Value<string>("recovery_action"));
                Assert.IsFalse(data.Value<bool>("retry_safe"));
                CollectionAssert.AreEqual(original, File.ReadAllBytes(Path.Combine(project, data.Value<string>("asset_path"))));
                CollectionAssert.AreEqual(original, File.ReadAllBytes(source));
                Assert.AreEqual("existing owned output", File.ReadAllText(collision));
                Assert.AreEqual(1, Directory.GetFiles(Path.Combine(project, outputRoot), "prepared_*.obj").Length);
            }
            finally
            {
                hook.SetValue(null, null);
                ModelCancellationPostprocessor.Observe = null;
                if (previous == null)
                    handlers.Remove(commandName);
                else
                    handlers[commandName] = previous;
                if (operation == null || operation.Completion.IsCompleted)
                    journal.Release(admission.Ticket);
            }
        }

        private sealed class BlockingReader : MemoryStream
        {
            private readonly ManualResetEventSlim entered;
            private readonly ManualResetEventSlim release;
            internal bool Closed;
            internal int Reads;

            internal BlockingReader(byte[] bytes, ManualResetEventSlim entered, ManualResetEventSlim release)
                : base(bytes, false)
            {
                this.entered = entered;
                this.release = release;
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                Reads++;
                entered.Set();
                if (!release.Wait(TimeSpan.FromSeconds(15)))
                    throw new IOException("test reader was not released");
                return base.Read(buffer, offset, count);
            }

            protected override void Dispose(bool disposing)
            {
                Closed = true;
                base.Dispose(disposing);
            }
        }

        private sealed class CancelingReader : MemoryStream
        {
            private readonly CancellationTokenSource cancellation;

            internal CancelingReader(byte[] bytes, CancellationTokenSource cancellation)
                : base(bytes, false) => this.cancellation = cancellation;

            public override int Read(byte[] buffer, int offset, int count)
            {
                int read = base.Read(buffer, offset, count);
                cancellation.Cancel();
                return read;
            }
        }
    }

    internal sealed class ModelCancellationPostprocessor : AssetPostprocessor
    {
        internal static Action<string[]> Observe;

        private static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom) => Observe?.Invoke(imported);
    }
}
