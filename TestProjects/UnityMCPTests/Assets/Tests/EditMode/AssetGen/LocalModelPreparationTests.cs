using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
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
}
