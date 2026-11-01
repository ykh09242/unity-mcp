using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
using Newtonsoft.Json;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnity.Tests.EditMode.AssetGen
{
    public class AssetGenSavedValidationTests
    {
        private const int LargeBytes = 4 * 1024 * 1024;
        private const BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic;
        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
        private static readonly Type RunnerType = typeof(AssetGenJobManager).GetNestedType("Runner", BindingFlags.NonPublic);
        private readonly ManualResetEventSlim entered = new();
        private readonly ManualResetEventSlim release = new();
        private string folder;
        private string folderGuid;
        private object runner;
        private Task validationTask;
        private double now;
        private int imports;
        private int workerThread;
        private bool holdIgnoresCancellation;

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            AssetGenJobManager.TransportOverrideForTests = new FakeHttpTransport
            {
                Handler = _ => throw new AssertionException("Saved validation must not contact a provider."),
            };
            now = 100;
            AssetGenJobManager.ClockForTests = () => Volatile.Read(ref now);
            AssetGenJobManager.TimeoutSeconds = 600;
            folder = "Assets/__McpSavedValidation_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            Assert.IsNotEmpty(folderGuid);
            entered.Reset();
            release.Reset();
            runner = null;
            validationTask = null;
            imports = 0;
            workerThread = 0;
            holdIgnoresCancellation = false;
            AssetGenJobManager.ImportOverrideForTests = Import;
        }

        [TearDown]
        public void TearDown()
        {
            release.Set();
            AssetGenJobManager.ResetForTests();
            typeof(ImportFingerprintCache).GetField("FingerprintReadForTests", Static)?.SetValue(null, null);
            if (validationTask != null)
                Assert.IsTrue(SpinWait.SpinUntil(() => validationTask.IsCompleted, 5000), "Owned validation did not stop.");
            Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(folder), "Cleanup must retain foreign folders.");
            AssetDatabase.DeleteAsset(folder);
        }

        private static void Set(object owner, string field, object value) => RunnerType.GetField(field, Instance).SetValue(owner, value);

        private static object Get(object owner, string field) => RunnerType.GetField(field, Instance)?.GetValue(owner);

        private static object FindRunner(string id) => ((IDictionary)typeof(AssetGenJobManager).GetField("Runners", Static).GetValue(null))[id];

        private static void Phase(object owner, string value)
        {
            FieldInfo field = RunnerType.GetField("Phase", Instance);
            field.SetValue(owner, Enum.Parse(field.FieldType, value));
        }

        private void InstallBarrier()
        {
            // Reflection lets the baseline control compile and fail at the actual synchronous
            // manager handoff rather than failing compilation because the new seam is absent.
            typeof(AssetGenJobManager).GetField("ValidationChunkForTests", Static)?.SetValue(null, (Action<long, CancellationToken>)PauseChunk);
        }

        private void ClearBarrier() => typeof(AssetGenJobManager).GetField("ValidationChunkForTests", Static)?.SetValue(null, null);

        private void PauseChunk(long read, CancellationToken token)
        {
            if (entered.IsSet)
                return;
            workerThread = Thread.CurrentThread.ManagedThreadId;
            entered.Set();
            if (holdIgnoresCancellation)
            {
                if (!release.Wait(5000))
                    throw new TimeoutException("Fixture did not release validation.");
            }
            else if (!release.Wait(5000, token))
                throw new TimeoutException("Fixture did not release validation.");
        }

        private AssetGenJob Import(AssetGenJob job, string path)
        {
            imports++;
            Assert.AreEqual(false, Get(runner, "AutoRefreshSuppressed") ?? false, "Refresh ownership must be released at importer admission.");
            job.AssetPath = path;
            return job;
        }

        private static byte[] Wave(int size)
        {
            var bytes = new byte[size];
            using var stream = new MemoryStream(bytes);
            using var writer = new BinaryWriter(stream);
            writer.Write(System.Text.Encoding.ASCII.GetBytes("RIFF"));
            writer.Write(size - 8);
            writer.Write(System.Text.Encoding.ASCII.GetBytes("WAVEfmt "));
            writer.Write(16);
            writer.Write((short)1);
            writer.Write((short)1);
            writer.Write(8000);
            writer.Write(16000);
            writer.Write((short)2);
            writer.Write((short)16);
            writer.Write(System.Text.Encoding.ASCII.GetBytes("data"));
            writer.Write(size - 44);
            return bytes;
        }

        private static string Hash(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private AssetGenJob StartDownload(int bytes = LargeBytes)
        {
            var job = new AssetGenJob
            {
                JobId = Guid.NewGuid().ToString("N"),
                Kind = "audio",
                Provider = "fal",
                State = AssetGenJobState.Running,
            };
            runner = Activator.CreateInstance(RunnerType, true);
            Set(runner, "Job", job);
            Set(runner, "Ext", "wav");
            Set(runner, "Subfolder", "Audio");
            Set(runner, "Name", job.JobId);
            Set(runner, "OutputFolder", folder);
            Set(runner, "ImportFn", (Func<AssetGenJob, string, AssetGenJob>)Import);
            Phase(runner, "AwaitDownload");
            typeof(AssetGenJobManager).GetMethod("Register", Static).Invoke(null, new[] { job, runner });
            Set(
                runner,
                "DownloadTask",
                Task.FromResult(
                    new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = Wave(bytes),
                    }
                )
            );
            return job;
        }

        private AssetGenJobCheckpoint SavedCheckpoint(bool registered = false)
        {
            byte[] bytes = Wave(LargeBytes);
            var cp = new AssetGenJobCheckpoint
            {
                JobId = Guid.NewGuid().ToString("N"),
                Kind = "audio",
                Provider = "fal",
                Phase = "saved",
                StartedAt = 100,
                Deadline = 700,
                OutputFolder = folder,
                Name = "owned",
                Extension = "wav",
                Subfolder = "Audio",
                SavedPath = folder + "/owned.wav",
                SavedLength = bytes.Length,
                SavedHash = Hash(bytes),
            };
            File.WriteAllBytes(AssetGenPaths.ToAbsolute(cp.SavedPath), bytes);
            if (registered)
                AssetDatabase.ImportAsset(cp.SavedPath, ImportAssetOptions.ForceSynchronousImport);
            string meta = AssetGenPaths.ToAbsolute(cp.SavedPath + ".meta");
            cp.MetadataPresent = File.Exists(meta);
            cp.MetadataLength = cp.MetadataPresent ? new FileInfo(meta).Length : 0;
            cp.MetadataHash = cp.MetadataPresent ? Hash(File.ReadAllBytes(meta)) : null;
            cp.RegisteredGuid = AssetDatabase.AssetPathToGUID(cp.SavedPath);
            if (string.IsNullOrEmpty(cp.RegisteredGuid))
                cp.RegisteredGuid = null;
            return cp;
        }

        private AssetGenJob Restore(AssetGenJobCheckpoint cp)
        {
            var saved = new AssetGenJob
            {
                JobId = cp.JobId,
                Kind = cp.Kind,
                Provider = cp.Provider,
                State = AssetGenJobState.Importing,
                Format = cp.Extension,
            };
            SessionState.SetString("MCPForUnity.AssetGen.Job." + cp.JobId, AssetGenJobCheckpoint.Project(saved, cp).ToString(Formatting.None));
            SessionState.SetString("MCPForUnity.AssetGen.JobIndex", cp.JobId);
            AssetGenJobManager.RestoreJobs();
            runner = FindRunner(cp.JobId);
            return AssetGenJobManager.GetJob(cp.JobId);
        }

        private static bool Terminal(AssetGenJob job) =>
            job.State == AssetGenJobState.Done || job.State == AssetGenJobState.Failed || job.State == AssetGenJobState.Canceled;

        private IEnumerator Until(Func<bool> condition, string failure)
        {
            double end = EditorApplication.timeSinceStartup + 10;
            while (!condition())
            {
                Assert.Less(EditorApplication.timeSinceStartup, end, failure);
                yield return null;
            }
        }

        private void StartRecoveredValidation(AssetGenJob job)
        {
            AssetGenJobManager.TryAdvanceForTests(job.JobId); // Reconnect, without hashing.
            AssetGenJobManager.TryAdvanceForTests(job.JobId); // Dispatch owned validation.
            validationTask = Get(runner, "ValidationTask") as Task;
            Assert.IsNotNull(validationTask, "The actual saved-file manager handoff must yield before hashing a large artifact.");
            Assert.AreEqual(0, imports);
        }

        [UnityTest]
        public IEnumerator LargeManagerHandoff_HeartbeatsContinueAndCancelStopsValidation()
        {
            InstallBarrier();
            int mainThread = Thread.CurrentThread.ManagedThreadId;
            int heartbeats = 0;
            void Beat() => heartbeats++;
            EditorApplication.update += Beat;
            try
            {
                AssetGenJob job = StartDownload();
                yield return Until(() => entered.IsSet || Terminal(job), "Manager did not reach validation.");
                Assert.IsTrue(entered.IsSet, "Large publication imported without the asynchronous validation handoff.");
                Assert.AreNotEqual(mainThread, workerThread);
                validationTask = Get(runner, "ValidationTask") as Task;
                Assert.IsNotNull(validationTask);
                Assert.IsFalse(validationTask.IsCompleted);
                Assert.AreEqual(true, Get(runner, "AutoRefreshSuppressed"));
                int before = heartbeats;
                yield return Until(() => heartbeats >= before + 2, "Editor updates stalled during validation.");
                Assert.AreEqual(0, imports);
                Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
                Assert.AreEqual(false, Get(runner, "AutoRefreshSuppressed"));
                yield return Until(() => validationTask.IsCompleted, "Canceled validation retained a file handle.");
                Assert.AreEqual(AssetGenJobState.Canceled, job.State);
                Assert.AreEqual(0, imports);
                using var writable = new FileStream(AssetGenPaths.ToAbsolute(job.AssetPath), FileMode.Open, FileAccess.ReadWrite, FileShare.None);
                Console.WriteLine(
                    $"SAVED_VALIDATION heartbeat_delta={heartbeats - before} worker_thread={workerThread} main_thread={mainThread} imports={imports}"
                );
            }
            finally
            {
                EditorApplication.update -= Beat;
            }
        }

        [Test]
        public void WorkerDeadline_StopsBetweenChunksWithoutAnEditorTick()
        {
            InstallBarrier();
            AssetGenJob job = Restore(SavedCheckpoint());
            StartRecoveredValidation(job);
            Assert.IsTrue(entered.Wait(5000), "Worker did not reach its first chunk.");
            Volatile.Write(ref now, 701);
            release.Set();
            Assert.IsTrue(SpinWait.SpinUntil(() => validationTask.IsCompleted, 5000));
            Assert.IsTrue(validationTask.IsFaulted, "The worker itself must enforce the original deadline.");
            Assert.AreEqual(
                "job_timeout",
                validationTask.Exception.GetBaseException().GetType().GetField("Code", Instance).GetValue(validationTask.Exception.GetBaseException())
            );
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual("job_timeout", job.RecoveryCode);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(0, imports);
        }

        [Test]
        public void MainThreadDeadline_ReleasesRefreshAndDefersTokenDisposalUntilWorkerStops()
        {
            InstallBarrier();
            holdIgnoresCancellation = true;
            AssetGenJob job = StartDownload();
            Assert.IsTrue(
                SpinWait.SpinUntil(
                    () =>
                    {
                        AssetGenJobManager.TryAdvanceForTests(job.JobId);
                        return entered.IsSet || Terminal(job);
                    },
                    5000
                )
            );
            Assert.IsTrue(entered.IsSet, "Manager never dispatched saved validation.");
            validationTask = Get(runner, "ValidationTask") as Task;
            var source = (CancellationTokenSource)Get(runner, "Cts");
            Volatile.Write(ref now, 701);
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual("job_timeout", job.RecoveryCode);
            Assert.AreEqual(false, Get(runner, "AutoRefreshSuppressed"));
            Assert.IsFalse(validationTask.IsCompleted, "Fixture must still own the held worker.");
            Assert.IsTrue(source.Token.IsCancellationRequested, "Cancellation source was disposed before its worker released the token.");
            release.Set();
            Assert.IsTrue(SpinWait.SpinUntil(() => validationTask.IsCompleted, 5000));
            Assert.AreEqual(0, imports);
        }

        [Test]
        public void Reload_RejectsLateWorkerAndReleasesRefreshOwnership()
        {
            InstallBarrier();
            holdIgnoresCancellation = true;
            AssetGenJob job = StartDownload();
            Assert.IsTrue(
                SpinWait.SpinUntil(
                    () =>
                    {
                        AssetGenJobManager.TryAdvanceForTests(job.JobId);
                        return entered.IsSet || Terminal(job);
                    },
                    5000
                )
            );
            Assert.IsTrue(entered.IsSet, "Manager never dispatched saved validation.");
            validationTask = Get(runner, "ValidationTask") as Task;
            object previous = runner;
            AssetGenJobManager.ResetTransientForTests();
            Assert.AreEqual(false, Get(previous, "AutoRefreshSuppressed"));
            ClearBarrier();
            AssetGenJobManager.RestoreJobs();
            runner = FindRunner(job.JobId);
            Assert.AreNotSame(previous, runner);
            release.Set();
            Assert.IsTrue(SpinWait.SpinUntil(() => validationTask.IsCompleted, 5000));
            Assert.AreEqual(0, imports, "A stale worker must never publish into its replacement runner.");
            Assert.IsTrue(SpinWait.SpinUntil(() => AssetGenJobManager.TryAdvanceForTests(job.JobId), 5000));
            Assert.AreEqual(AssetGenJobState.Done, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual(1, imports);
        }

        [Test]
        public void CompletedValidation_ReloadDisposesUnconsumedHandlesAndCannotImport()
        {
            AssetGenJob job = Restore(SavedCheckpoint());
            StartRecoveredValidation(job);
            Assert.IsTrue(SpinWait.SpinUntil(() => validationTask.IsCompleted, 5000));
            Assert.AreEqual(TaskStatus.RanToCompletion, validationTask.Status);
            object result = validationTask.GetType().GetProperty("Result").GetValue(validationTask);
            Assert.IsNotNull(result.GetType().GetField("content", Instance).GetValue(result));
            AssetGenJobManager.ResetTransientForTests();
            Assert.IsNull(result.GetType().GetField("content", Instance).GetValue(result));
            Assert.AreEqual(0, imports);
            using var writable = new FileStream(AssetGenPaths.ToAbsolute(job.AssetPath), FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        }

        [Test]
        public void Recovery_RejectsChangedContentWithUnchangedLength()
        {
            AssetGenJobCheckpoint cp = SavedCheckpoint();
            string path = AssetGenPaths.ToAbsolute(cp.SavedPath);
            using (var file = new FileStream(path, FileMode.Open, FileAccess.Write))
            {
                file.Position = file.Length - 1;
                file.WriteByte(73);
            }
            AssetGenJob job = Restore(cp);
            StartRecoveredValidation(job);
            Assert.IsTrue(SpinWait.SpinUntil(() => AssetGenJobManager.TryAdvanceForTests(job.JobId), 5000));
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual("saved_asset_identity_changed", job.RecoveryCode);
            Assert.AreEqual(0, imports);
            Assert.AreEqual(cp.SavedLength, new FileInfo(path).Length);
            Assert.AreEqual(73, File.ReadAllBytes(path)[LargeBytes - 1], "Rejected content must be retained.");
        }

        [Test]
        public void NewMetadataAfterValidation_RefusesImporterAdmission()
        {
            AssetGenJob job = Restore(SavedCheckpoint());
            StartRecoveredValidation(job);
            Assert.IsTrue(SpinWait.SpinUntil(() => validationTask.IsCompleted, 5000));
            Assert.AreEqual(TaskStatus.RanToCompletion, validationTask.Status);
            File.WriteAllText(AssetGenPaths.ToAbsolute(job.AssetPath + ".meta"), "fileFormatVersion: 2\nguid: " + Guid.NewGuid().ToString("N") + "\n");
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual("saved_asset_identity_changed", job.RecoveryCode);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(0, imports);
        }

        [Test]
        public void RegisteredRecovery_PinsContentAndMetadataUntilImporterAdmission()
        {
            AssetGenJob job = Restore(SavedCheckpoint(registered: true));
            StartRecoveredValidation(job);
            Assert.IsTrue(SpinWait.SpinUntil(() => validationTask.IsCompleted, 5000));
            Assert.AreEqual(TaskStatus.RanToCompletion, validationTask.Status);
            string path = AssetGenPaths.ToAbsolute(job.AssetPath);
            Assert.Throws<IOException>(() =>
            {
                using var write = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.ReadWrite);
            });
            Assert.Throws<IOException>(() =>
            {
                using var write = new FileStream(path + ".meta", FileMode.Open, FileAccess.Write, FileShare.ReadWrite);
            });
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
            Assert.AreEqual(1, imports);
            using var writableMeta = new FileStream(path + ".meta", FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        }

        [UnityTest]
        public IEnumerator LargePublication_ImportsWithNativeAudioPipelineAfterValidation()
        {
            InstallBarrier();
            int fingerprintReads = 0;
            typeof(ImportFingerprintCache).GetField("FingerprintReadForTests", Static)?.SetValue(null, (Action)(() => fingerprintReads++));
            AssetGenJob job = StartDownload();
            Set(
                runner,
                "ImportFn",
                (Func<AssetGenJob, string, AssetGenJob>)(
                    (current, path) =>
                    {
                        Import(current, path);
                        return AudioImportPipeline.ImportInto(current, path);
                    }
                )
            );
            yield return Until(() => entered.IsSet || Terminal(job), "Manager did not reach validation.");
            Assert.IsTrue(entered.IsSet);
            validationTask = Get(runner, "ValidationTask") as Task;
            Assert.IsFalse(File.Exists(AssetGenPaths.ToAbsolute(job.AssetPath + ".meta")), "Auto-refresh must not race the newly published checkpoint.");
            release.Set();
            yield return Until(() => Terminal(job), "Validated publication did not import.");
            Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
            Assert.AreEqual(1, imports);
            Assert.IsNotEmpty(job.AssetGuid);
            Assert.AreEqual(false, Get(runner, "AutoRefreshSuppressed"));
            Assert.AreEqual(0, fingerprintReads, "The real pipeline must reuse the digest while the manager pins validated content.");
            Assert.IsTrue(ImportFingerprintCache.IsUnchanged(job.AssetPath, out _));
            Assert.AreEqual(1, fingerprintReads, "After importer admission ends, independent cache checks must hash content again.");
            using (var changed = new FileStream(AssetGenPaths.ToAbsolute(job.AssetPath), FileMode.Open, FileAccess.Write))
            {
                changed.Position = changed.Length - 1;
                changed.WriteByte(73);
            }
            Assert.IsFalse(ImportFingerprintCache.IsUnchanged(job.AssetPath, out _), "Independent retry must reject a changed source.");
            Assert.AreEqual(2, fingerprintReads);
        }

        [UnityTest]
        public IEnumerator ImporterHandoff_PathReplacementIsBlockedOrRejectedBeforeFingerprintReuse()
        {
            bool replacementBlocked = false;
            bool fingerprintAccepted = false;
            AssetGenJob job = StartDownload();
            Set(
                runner,
                "ImportFn",
                (Func<AssetGenJob, string, AssetGenJob>)(
                    (current, path) =>
                    {
                        string absolute = AssetGenPaths.ToAbsolute(path);
                        try
                        {
                            File.Move(absolute, absolute + ".retained");
                        }
                        catch (IOException)
                        {
                            replacementBlocked = true;
                        }
                        if (!replacementBlocked)
                        {
                            byte[] replacement = Wave(LargeBytes);
                            replacement[replacement.Length - 1] = 73;
                            File.WriteAllBytes(absolute, replacement);
                            File.SetLastWriteTimeUtc(absolute, DateTime.UtcNow.AddHours(-1));
                        }
                        ImportFingerprintCache.IsUnchanged(path, out _);
                        fingerprintAccepted = true;
                        return Import(current, path);
                    }
                )
            );
            yield return Until(() => Terminal(job), "Replacement control did not settle.");
            if (replacementBlocked)
            {
                Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
                Assert.IsTrue(fingerprintAccepted);
            }
            else
            {
                Assert.AreEqual(AssetGenJobState.Failed, job.State);
                Assert.AreEqual("saved_asset_identity_changed", job.RecoveryCode);
                Assert.IsFalse(fingerprintAccepted, "A path replaced after admission must not borrow the old digest.");
            }
        }

        [UnityTest]
        public IEnumerator SavedCheckpointFailure_ReleasesAutoRefreshBeforeFinalizing()
        {
            AssetGenJobManager.BeforeCheckpointWriteForTests = phase =>
            {
                if (phase == "saved")
                    throw new IOException("Owned fixture checkpoint fault.");
            };
            AssetGenJob job = StartDownload();
            yield return Until(() => Terminal(job), "Failed publication did not settle.");
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(false, Get(runner, "AutoRefreshSuppressed"));
            Assert.AreEqual(0, imports);
        }

        [Test]
        public void NestedImporterFailure_RestoresOuterFingerprintScopeAndClearsItOnExit()
        {
            int fingerprintReads = 0;
            typeof(ImportFingerprintCache).GetField("FingerprintReadForTests", Static)?.SetValue(null, (Action)(() => fingerprintReads++));
            AssetGenJob outer = StartDownload(64);
            AssetGenJob inner = null;
            Set(
                runner,
                "ImportFn",
                (Func<AssetGenJob, string, AssetGenJob>)(
                    (current, path) =>
                    {
                        ImportFingerprintCache.IsUnchanged(path, out _);
                        inner = StartDownload(64);
                        Set(runner, "ImportFn", (Func<AssetGenJob, string, AssetGenJob>)((_, __) => throw new IOException("Owned nested importer fault.")));
                        Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(inner.JobId));
                        Assert.AreEqual(AssetGenJobState.Failed, inner.State);
                        ImportFingerprintCache.IsUnchanged(path, out _);
                        Assert.AreEqual(0, fingerprintReads, "A nested import exception must restore the outer validated source.");
                        return current;
                    }
                )
            );
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(outer.JobId));
            Assert.AreEqual(AssetGenJobState.Done, outer.State, outer.Error);
            Assert.IsNotNull(inner);
            ImportFingerprintCache.IsUnchanged(outer.AssetPath, out _);
            Assert.AreEqual(1, fingerprintReads, "Exiting the outer importer must remove the borrowed digest.");
        }

        [Test]
        public void SmallPublication_RetainsSameUpdateImport()
        {
            AssetGenJob job = StartDownload(64);
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(job.JobId));
            Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
            Assert.AreEqual(1, imports);
            Assert.IsNull(Get(runner, "ValidationTask"));
            Assert.AreEqual(false, Get(runner, "AutoRefreshSuppressed") ?? false);
        }
    }
}
