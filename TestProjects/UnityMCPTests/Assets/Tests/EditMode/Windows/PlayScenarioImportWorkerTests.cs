using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Windows.PlayScenarios;
using MCPForUnityTests.Editor;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    internal static class PlayScenarioImportTestPump
    {
        internal static Task Track(OwnedTestTasks owned, PlayScenarioImportWorker worker)
        {
            var active = typeof(PlayScenarioImportWorker).GetField("active", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(worker);
            Assert.IsNotNull(active, "An accepted read must own a task before its first pump.");
            var task = (Task)active.GetType().GetField("Task", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(active);
            owned.Track(task);
            owned.OnCleanup(worker.Dispose);
            return task;
        }

        internal static IEnumerator Phase(Func<bool> ready, Task producer, string phase) => TestUtilities.WaitUntil(ready, phase, () => producer.IsCompleted);

        // Existing synchronous UI assertions explicitly pump a pure background reader's completion.
        internal static void Drain(VisualElement parent, string sectionName)
        {
            var worker = parent.Q(sectionName)?.userData as PlayScenarioImportWorker;
            var elapsed = Stopwatch.StartNew();
            while (worker != null && worker.IsBusy && elapsed.ElapsedMilliseconds < 10000)
            {
                worker.Pump();
                Thread.Sleep(1);
            }
            Assert.IsTrue(worker == null || !worker.IsBusy, "The bounded import did not complete.");
        }

        internal static IEnumerator Wait(PlayScenarioImportWorker worker)
        {
            yield return TestUtilities.WaitUntil(() => !worker.IsBusy, "the bounded import to complete");
        }
    }

    public class PlayScenarioImportWorkerTests
    {
        private string root;
        private ImportTestWindow window;
        private OwnedTestTasks owned;

        private sealed class ImportTestWindow : UnityEditor.EditorWindow { }

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            owned = new OwnedTestTasks();
            root = Path.Combine(Path.GetTempPath(), "unity-mcp-import-worker-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            window = ScriptableObject.CreateInstance<ImportTestWindow>();
            window.Show();
            yield return null;
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            try
            {
                yield return owned.Cleanup();
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                if (Directory.Exists(root))
                    Directory.Delete(root, true);
            }
        }

        [UnityTest]
        public IEnumerator DuplicateRequestsReadOnceAndDeliverOnMainThreadWithNoRecurringWork()
        {
            int mainThread = Thread.CurrentThread.ManagedThreadId;
            int readerThread = mainThread;
            int callbackThread = -1;
            int reads = 0;
            int callbacks = 0;
            using (var gate = new ManualResetEventSlim())
            using (var worker = new PlayScenarioImportWorker())
            {
                Func<CancellationToken, object> read = token =>
                {
                    readerThread = Thread.CurrentThread.ManagedThreadId;
                    Interlocked.Increment(ref reads);
                    gate.Wait(token);
                    return "saved";
                };
                Action<object, Exception> apply = (value, error) =>
                {
                    Assert.IsNull(error);
                    Assert.AreEqual("saved", value);
                    callbackThread = Thread.CurrentThread.ManagedThreadId;
                    callbacks++;
                };
                Assert.IsTrue(worker.RequestRead("source", read, apply));
                Assert.IsFalse(worker.RequestRead("source", read, apply));
                Assert.AreEqual(0, callbacks);
                gate.Set();
                yield return PlayScenarioImportTestPump.Wait(worker);
                Assert.AreEqual(1, reads);
                Assert.AreEqual(1, callbacks);
                Assert.AreNotEqual(mainThread, readerThread);
                Assert.AreEqual(mainThread, callbackThread);
                Assert.IsFalse(worker.IsPumping);
                for (int tick = 0; tick < 5; tick++)
                    yield return null;
                Assert.AreEqual(1, reads);
            }
        }

        [UnityTest]
        public IEnumerator SupersedingRequestsKeepOneReaderAndOnlyTheNewestPendingPath()
        {
            int running = 0;
            int maximum = 0;
            int oldCallbacks = 0;
            int skippedReads = 0;
            string delivered = null;
            var entered = owned.Own(new ManualResetEventSlim());
            var cancelled = owned.Own(new ManualResetEventSlim());
            var release = owned.Own(new ManualResetEventSlim());
            owned.OnCleanup(release.Set);
            using (var worker = new PlayScenarioImportWorker())
            {
                worker.RequestRead(
                    "old",
                    token =>
                    {
                        Interlocked.Increment(ref running);
                        maximum = Math.Max(maximum, running);
                        entered.Set();
                        token.WaitHandle.WaitOne();
                        cancelled.Set();
                        release.Wait();
                        Interlocked.Decrement(ref running);
                        token.ThrowIfCancellationRequested();
                        return "old";
                    },
                    (_, __) => oldCallbacks++
                );
                var producer = PlayScenarioImportTestPump.Track(owned, worker);
                try
                {
                    yield return PlayScenarioImportTestPump.Phase(() => entered.IsSet, producer, "the superseded reader to enter");
                    worker.RequestRead(
                        "middle",
                        _ =>
                        {
                            Interlocked.Increment(ref skippedReads);
                            return "middle";
                        },
                        (_, __) => oldCallbacks++
                    );
                    worker.RequestRead(
                        "newest",
                        _ =>
                        {
                            Interlocked.Increment(ref running);
                            maximum = Math.Max(maximum, running);
                            Interlocked.Decrement(ref running);
                            return "newest";
                        },
                        (value, error) =>
                        {
                            Assert.IsNull(error);
                            delivered = (string)value;
                        }
                    );
                    yield return PlayScenarioImportTestPump.Phase(() => cancelled.IsSet, producer, "the superseded reader to observe cancellation");
                    Assert.AreEqual(1, running);
                    release.Set();
                    yield return PlayScenarioImportTestPump.Wait(worker);
                    Assert.AreEqual("newest", delivered);
                    Assert.AreEqual(1, maximum);
                    Assert.AreEqual(0, oldCallbacks);
                    Assert.AreEqual(0, skippedReads);
                }
                finally
                {
                    release.Set();
                }
            }
        }

        [UnityTest]
        public IEnumerator DisposeCancelsWorkAndReleasesCallbacksWithoutLateDelivery()
        {
            int callbacks = 0;
            var entered = owned.Own(new ManualResetEventSlim());
            var cancelled = owned.Own(new ManualResetEventSlim());
            {
                var worker = new PlayScenarioImportWorker();
                worker.RequestRead(
                    "source",
                    token =>
                    {
                        entered.Set();
                        token.WaitHandle.WaitOne();
                        cancelled.Set();
                        token.ThrowIfCancellationRequested();
                        return null;
                    },
                    (_, __) => callbacks++
                );
                var producer = PlayScenarioImportTestPump.Track(owned, worker);
                yield return PlayScenarioImportTestPump.Phase(() => entered.IsSet, producer, "the disposed reader to enter");
                worker.Dispose();
                worker.Dispose();
                yield return PlayScenarioImportTestPump.Phase(() => cancelled.IsSet, producer, "the disposed reader to observe cancellation");
                yield return null;
                worker.Pump();
                Assert.AreEqual(0, callbacks);
                Assert.IsFalse(worker.IsPumping);
                Assert.IsFalse(worker.IsBusy);
                Assert.IsTrue(worker.IsDisposed);
                Assert.IsFalse(worker.RequestRead("another", _ => null, (_, __) => callbacks++));
            }
        }

        [UnityTest]
        public IEnumerator ObservedFileRaceRetriesButMissingMalformedAndContradictoryEvidenceDoNot()
        {
            string file = Path.Combine(root, "summary.json");
            File.WriteAllText(file, "{\"value\":1}", new UTF8Encoding(false));
            int attempts = 0;
            object imported = null;
            using (var worker = new PlayScenarioImportWorker())
            {
                worker.RequestRead(
                    file,
                    token =>
                    {
                        Interlocked.Increment(ref attempts);
                        if (attempts == 1)
                        {
                            PlayScenarioImportFileStamp stamp = PlayScenarioImportFileStamp.Capture(file);
                            File.WriteAllText(file, "{\"value\":22}", new UTF8Encoding(false));
                            stamp.Verify(file);
                        }
                        return PlayScenarioPlayerSessionEditor.ReadJson(file, 65536, token);
                    },
                    (value, error) =>
                    {
                        Assert.IsNull(error);
                        imported = value;
                    }
                );
                yield return PlayScenarioImportTestPump.Wait(worker);
                Assert.AreEqual(2, attempts);
                Assert.AreEqual(22, (int)((JObject)imported)["value"]);

                foreach (
                    Exception failure in new Exception[]
                    {
                        new FileNotFoundException("Initially missing file"),
                        new JsonReaderException("Malformed document"),
                        new DecoderFallbackException("Invalid UTF-8"),
                        new InvalidDataException("Completed checkpoint SHA256 mismatch"),
                        new IOException("Unclassified filesystem failure"),
                    }
                )
                {
                    attempts = 0;
                    Exception delivered = null;
                    worker.RequestRead(
                        Guid.NewGuid().ToString("N"),
                        _ =>
                        {
                            Interlocked.Increment(ref attempts);
                            throw failure;
                        },
                        (_, error) => delivered = error
                    );
                    yield return PlayScenarioImportTestPump.Wait(worker);
                    Assert.AreEqual(1, attempts, failure.GetType().Name);
                    Assert.AreSame(failure, delivered);
                }
            }
        }

        [UnityTest]
        public IEnumerator TransientRetriesAndCooperativeDeadlineHaveHardBounds()
        {
            int attempts = 0;
            Exception delivered = null;
            using (var worker = new PlayScenarioImportWorker())
            {
                worker.RequestRead(
                    "racing",
                    _ =>
                    {
                        Interlocked.Increment(ref attempts);
                        throw new PlayScenarioImportRaceException("Observed length changed");
                    },
                    (_, error) => delivered = error
                );
                yield return PlayScenarioImportTestPump.Wait(worker);
                Assert.AreEqual(PlayScenarioImportWorker.MaximumAttempts, attempts);
                Assert.IsInstanceOf<PlayScenarioImportRaceException>(delivered);
                var elapsed = Stopwatch.StartNew();
                worker.RequestRead(
                    "deadline",
                    token =>
                    {
                        token.WaitHandle.WaitOne();
                        token.ThrowIfCancellationRequested();
                        return null;
                    },
                    (_, error) => delivered = error
                );
                yield return PlayScenarioImportTestPump.Wait(worker);
                Assert.IsInstanceOf<TimeoutException>(delivered);
                Assert.GreaterOrEqual(elapsed.ElapsedMilliseconds, PlayScenarioImportWorker.DeadlineMilliseconds - 100);
                Assert.Less(elapsed.ElapsedMilliseconds, 10000);
            }
        }

        [UnityTest]
        public IEnumerator ActualSharingViolationIsRetriedAfterTheExclusiveOwnerReleasesItsFile()
        {
            string file = Path.Combine(root, "locked.json");
            File.WriteAllText(file, "{\"value\":7}", new UTF8Encoding(false));
            int attempts = 0;
            object imported = null;
            Exception observed = null;
            using (var sharing = new ManualResetEventSlim())
            using (var release = new ManualResetEventSlim())
            using (var worker = new PlayScenarioImportWorker())
            {
                var owner = new FileStream(file, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
                try
                {
                    worker.RequestRead(
                        file,
                        token =>
                        {
                            Interlocked.Increment(ref attempts);
                            try
                            {
                                return PlayScenarioPlayerSessionEditor.ReadJson(file, 65536, token);
                            }
                            catch (IOException exception)
                            {
                                observed = exception;
                                sharing.Set();
                                release.Wait(token);
                                throw;
                            }
                        },
                        (value, error) =>
                        {
                            Assert.IsNull(error);
                            imported = value;
                        }
                    );
                    var elapsed = Stopwatch.StartNew();
                    while (!sharing.IsSet && worker.IsBusy && elapsed.ElapsedMilliseconds < 4000)
                        yield return null;
                    Assert.IsTrue(sharing.IsSet, "The native reader did not observe the exclusive owner.");
                    Assert.IsTrue(PlayScenarioImportWorker.IsTransient(observed));
                    owner.Dispose();
                    release.Set();
                    yield return PlayScenarioImportTestPump.Wait(worker);
                    Assert.AreEqual(2, attempts);
                    Assert.AreEqual(7, (int)((JObject)imported)["value"]);
                }
                finally
                {
                    owner.Dispose();
                    release.Set();
                }
            }
        }

        [Test]
        public void OnlyObservedRacesAndSpecificSharingErrorsAreTransient()
        {
            Assert.IsTrue(PlayScenarioImportWorker.IsTransient(new IOException("sharing", unchecked((int)0x80070020))));
            Assert.IsTrue(PlayScenarioImportWorker.IsTransient(new IOException("locking", unchecked((int)0x80070021))));
            Assert.IsFalse(PlayScenarioImportWorker.IsTransient(new UnauthorizedAccessException()));
            Assert.IsFalse(PlayScenarioImportWorker.IsTransient(new FileNotFoundException()));
            Assert.IsFalse(PlayScenarioImportWorker.IsTransient(new InvalidDataException("Unknown schema")));
            string missing = Path.Combine(root, "missing.json");
            Assert.Throws<FileNotFoundException>(() => PlayScenarioImportFileStamp.Capture(missing));
            string file = Path.Combine(root, "known.json");
            File.WriteAllText(file, "{}");
            PlayScenarioImportFileStamp stamp = PlayScenarioImportFileStamp.Capture(file);
            File.Delete(file);
            Assert.Throws<PlayScenarioImportRaceException>(() => stamp.Verify(file));
        }

        private static void Click(VisualElement parent, string name)
        {
            typeof(Clickable)
                .GetMethod("Invoke", BindingFlags.Instance | BindingFlags.NonPublic)
                .Invoke(parent.Q<Button>(name).clickable, new object[] { null });
        }

        private string SessionFile()
        {
            string file = Path.Combine(root, "session.json");
            File.WriteAllText(
                file,
                new JObject
                {
                    ["schema_version"] = 1,
                    ["session_id"] = new string('1', 32),
                    ["mode"] = "shared-batches",
                    ["status"] = "failed",
                    ["requested_iterations"] = 2,
                    ["outcomes_recorded"] = 0,
                    ["last_outcomes"] = new JArray(),
                }.ToString(Formatting.None),
                new UTF8Encoding(false)
            );
            return file;
        }

        [UnityTest]
        public IEnumerator SessionRefreshPreservesSameSourceAsStaleButNeverShowsItForAnotherPath()
        {
            string selected = SessionFile();
            string message = "";
            var parent = new VisualElement();
            window.rootVisualElement.Add(parent);
            using (var view = new PlayScenarioPlayerSessionEditor(parent, (text, _) => message = text, () => selected))
            {
                Click(parent, "openPlayerSession");
                var worker = (PlayScenarioImportWorker)parent.Q("playerSession").userData;
                yield return PlayScenarioImportTestPump.Wait(worker);
                string original = string.Join("\n", parent.Q("playerSessionSummary").Query<Label>().ToList().Select(label => label.text));
                Assert.IsNotEmpty(original);
                File.WriteAllText(selected, "{ malformed");
                Click(parent, "importPlayerSession");
                StringAssert.Contains("stale", parent.Q<Label>("playerSessionImportState").text);
                yield return PlayScenarioImportTestPump.Wait(worker);
                StringAssert.Contains("import failed", message);
                StringAssert.Contains("stale", parent.Q<Label>("playerSessionImportState").text);
                Assert.AreEqual(original, string.Join("\n", parent.Q("playerSessionSummary").Query<Label>().ToList().Select(label => label.text)));
                parent.Q<TextField>("playerSessionPath").value = Path.Combine(root, "another", "session.json");
                Assert.IsEmpty(parent.Q("playerSessionSummary").Query<Label>().ToList());
                Assert.IsEmpty(parent.Q("playerSessionOutcomes").Query<Button>().ToList());
                StringAssert.Contains("Selected source changed", parent.Q<Label>("playerSessionImportState").text);
                Assert.IsFalse(worker.IsBusy);
            }
        }

        [UnityTest]
        public IEnumerator SessionPathChangeCancelsPendingReadAndWindowCloseDisposesItsImporter()
        {
            int callbacks = 0;
            var entered = owned.Own(new ManualResetEventSlim());
            var cancelled = owned.Own(new ManualResetEventSlim());
            {
                var parent = new VisualElement();
                window.rootVisualElement.Add(parent);
                using (
                    var view = new PlayScenarioPlayerSessionEditor(
                        parent,
                        (_, __) => callbacks++,
                        () => Path.Combine(root, "session.json"),
                        (_, token) =>
                        {
                            entered.Set();
                            token.WaitHandle.WaitOne();
                            cancelled.Set();
                            token.ThrowIfCancellationRequested();
                            return null;
                        }
                    )
                )
                {
                    Click(parent, "openPlayerSession");
                    var worker = (PlayScenarioImportWorker)parent.Q("playerSession").userData;
                    var producer = PlayScenarioImportTestPump.Track(owned, worker);
                    yield return PlayScenarioImportTestPump.Phase(() => entered.IsSet, producer, "the session reader to enter");
                    parent.Q<TextField>("playerSessionPath").value = Path.Combine(root, "new", "session.json");
                    yield return PlayScenarioImportTestPump.Phase(() => cancelled.IsSet, producer, "the session reader to observe cancellation");
                    yield return PlayScenarioImportTestPump.Wait(worker);
                    Assert.AreEqual(0, callbacks);
                    Assert.IsEmpty(parent.Q("playerSessionSummary").Query<Label>().ToList());
                    StringAssert.Contains("Selected source changed", parent.Q<Label>("playerSessionImportState").text);
                    window.Close();
                    yield return null;
                    Assert.IsTrue(worker.IsDisposed);
                    Assert.IsFalse(worker.IsPumping);
                }
            }
        }

        [UnityTest]
        public IEnumerator SoakDuplicateClicksCoalesceAndDetachCancelsWithoutUpdatingTheView()
        {
            int reads = 0;
            int callbacks = 0;
            var entered = owned.Own(new ManualResetEventSlim());
            var cancelled = owned.Own(new ManualResetEventSlim());
            {
                var parent = new VisualElement();
                window.rootVisualElement.Add(parent);
                using (
                    var view = new PlayScenarioSoakEditor(
                        parent,
                        (_, __) => callbacks++,
                        () => Path.Combine(root, "soak.json"),
                        (_, token) =>
                        {
                            Interlocked.Increment(ref reads);
                            entered.Set();
                            token.WaitHandle.WaitOne();
                            cancelled.Set();
                            token.ThrowIfCancellationRequested();
                            return null;
                        }
                    )
                )
                {
                    Click(parent, "openPlayerSoak");
                    Click(parent, "importPlayerSoak");
                    var worker = (PlayScenarioImportWorker)parent.Q("playerSoak").userData;
                    var producer = PlayScenarioImportTestPump.Track(owned, worker);
                    yield return PlayScenarioImportTestPump.Phase(() => entered.IsSet, producer, "the soak reader to enter");
                    Assert.AreEqual(1, reads);
                    parent.RemoveFromHierarchy();
                    yield return PlayScenarioImportTestPump.Phase(() => cancelled.IsSet, producer, "the soak reader to observe cancellation");
                    yield return null;
                    Assert.IsTrue(worker.IsDisposed);
                    Assert.IsFalse(worker.IsPumping);
                    Assert.AreEqual(0, callbacks);
                    Assert.IsEmpty(parent.Q("playerSoakSummary").Query<Label>().ToList());
                }
            }
        }
    }
}
