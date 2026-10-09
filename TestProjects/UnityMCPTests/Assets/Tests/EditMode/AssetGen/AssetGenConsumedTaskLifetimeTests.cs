using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetGenConsumedTaskLifetimeTests
    {
        private string _folder;
        private bool _ownsFolder;
        private const int PayloadBytes = 4 * 1024 * 1024;
        private static readonly Type RunnerType = typeof(AssetGenJobManager).GetNestedType("Runner", BindingFlags.NonPublic);
        private static readonly BindingFlags Fields = BindingFlags.Instance | BindingFlags.Public;
        private object _runner;
        private WeakReference _payload;
        private byte[] _expectedHash;
        private bool _aliveDuringImport;
        private bool _tasksDuringImport;
        private int _imports;

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            _ownsFolder = false;
            _folder = "Assets/__McpConsumedTaskLifetimeTests_" + Guid.NewGuid().ToString("N");
            string absolute = AssetGenPaths.ToAbsolute(_folder);
            Assert.IsFalse(Directory.Exists(absolute) || File.Exists(absolute), "Test output path already exists.");
            Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder)), "Could not create owned test folder.");
            _ownsFolder = true;
            _runner = null;
            _payload = null;
            _imports = 0;
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            if (_ownsFolder)
                AssetDatabase.DeleteAsset(_folder);
        }

        private static void Set(object runner, string name, object value) => RunnerType.GetField(name, Fields).SetValue(runner, value);

        private static object Get(object runner, string name) => RunnerType.GetField(name, Fields).GetValue(runner);

        private static void Phase(object runner, string phase)
        {
            var field = RunnerType.GetField("Phase", Fields);
            field.SetValue(runner, Enum.Parse(field.FieldType, phase));
        }

        private AssetGenJob Register(string phase)
        {
            var job = new AssetGenJob
            {
                JobId = Guid.NewGuid().ToString("N"),
                Kind = "audio",
                Provider = "synthetic",
                State = AssetGenJobState.Running,
            };
            _runner = Activator.CreateInstance(RunnerType, true);
            Set(_runner, "Job", job);
            Set(_runner, "Ext", "wav");
            Set(_runner, "Name", job.JobId);
            Set(_runner, "OutputFolder", _folder);
            Set(_runner, "StartedAt", EditorApplication.timeSinceStartup);
            Set(_runner, "ImportFn", (Func<AssetGenJob, string, AssetGenJob>)ObserveImport);
            Phase(_runner, phase);
            typeof(AssetGenJobManager).GetMethod("Register", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new[] { (object)job, _runner });
            return job;
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private Task<ProviderPollResult> InlineTask()
        {
            byte[] bytes = new byte[PayloadBytes];
            Array.Fill(bytes, (byte)37);
            _payload = new WeakReference(bytes);
            using (var hash = SHA256.Create())
                _expectedHash = hash.ComputeHash(bytes);
            return Task.FromResult(
                new ProviderPollResult
                {
                    State = ProviderPollState.Succeeded,
                    Progress = 1,
                    ResultExt = "wav",
                    InlineData = bytes,
                }
            );
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private Task<HttpResult> DownloadTask()
        {
            byte[] bytes = new byte[PayloadBytes];
            Array.Fill(bytes, (byte)37);
            _payload = new WeakReference(bytes);
            using (var hash = SHA256.Create())
                _expectedHash = hash.ComputeHash(bytes);
            return Task.FromResult(
                new HttpResult
                {
                    Status = 200,
                    IsSuccess = true,
                    Body = bytes,
                }
            );
        }

        private AssetGenJob ObserveImport(AssetGenJob job, string path)
        {
            _imports++;
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
            _aliveDuringImport = _payload.IsAlive;
            _tasksDuringImport = Get(_runner, "SubmitTask") != null || Get(_runner, "PollTask") != null || Get(_runner, "DownloadTask") != null;
            string absolute = AssetGenPaths.ToAbsolute(path);
            using (var stream = File.OpenRead(absolute))
            {
                Assert.AreEqual(PayloadBytes, stream.Length);
                using (var hash = SHA256.Create())
                    CollectionAssert.AreEqual(_expectedHash, hash.ComputeHash(stream));
            }
            job.AssetPath = path;
            return job;
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private AssetGenJob PrepareForImport(bool inline)
        {
            var job = Register("AwaitSubmit");
            Set(_runner, "SubmitTask", Task.FromResult("synthetic-provider-id"));
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            // Drive only the completed provider/download boundary; provider/key APIs are never used.
            if (inline)
            {
                Set(_runner, "PollTask", InlineTask());
                Phase(_runner, "AwaitPoll");
            }
            else
            {
                Set(
                    _runner,
                    "PollTask",
                    Task.FromResult(
                        new ProviderPollResult
                        {
                            State = ProviderPollState.Succeeded,
                            DownloadUrl = "synthetic-no-network",
                            ResultExt = "wav",
                        }
                    )
                );
                Phase(_runner, "AwaitPoll");
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
                Set(_runner, "DownloadTask", DownloadTask());
                Phase(_runner, "AwaitDownload");
            }
            Assert.IsFalse(AssetGenJobManager.TryAdvanceForTests(job.JobId));
            return job;
        }

        [TestCase(true)]
        [TestCase(false)]
        public void ConsumedArtifactTasks_ReleaseBodyBeforeImportAndPreserveFile(bool inline)
        {
            var job = PrepareForImport(inline);
            Assert.AreEqual(AssetGenJobState.Importing, job.State);
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(job.JobId));
            Console.WriteLine(
                $"PAYLOAD inline={inline} bytes={PayloadBytes} alive_at_import={_aliveDuringImport} completed_tasks_at_import={_tasksDuringImport}"
            );
            Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
            Assert.AreEqual(1, _imports);
            Assert.IsFalse(_aliveDuringImport, "consumed raw bytes remained rooted during import");
            Assert.IsFalse(_tasksDuringImport, "completed request Tasks remained owned during import");
        }

        [Test]
        public void NonterminalPoll_ReplacesClearedTaskOnNextPoll()
        {
            var job = Register("AwaitPoll");
            Set(_runner, "PollTask", Task.FromResult(new ProviderPollResult { State = ProviderPollState.Running, Progress = .5f }));
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.IsNull(Get(_runner, "PollTask"));
            int polls = 0;
            Set(_runner, "NextPollAt", 0d);
            Set(
                _runner,
                "PollFn",
                (Func<string, CancellationToken, Task<ProviderPollResult>>)(
                    (id, token) =>
                    {
                        polls++;
                        return Task.FromResult(new ProviderPollResult { State = ProviderPollState.Running });
                    }
                )
            );
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(1, polls);
            Assert.IsNotNull(Get(_runner, "PollTask"));
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.IsNull(Get(_runner, "PollTask"));
            Assert.AreEqual(AssetGenJobState.Running, job.State);
        }

        [TestCase("AwaitSubmit")]
        [TestCase("AwaitPoll")]
        [TestCase("AwaitDownload")]
        public void IncompleteTask_RemainsOwnedUntilCompletion(string phase)
        {
            var job = Register(phase);
            string field;
            if (phase == "AwaitSubmit")
            {
                field = "SubmitTask";
                Set(_runner, field, new TaskCompletionSource<string>().Task);
            }
            else if (phase == "AwaitPoll")
            {
                field = "PollTask";
                Set(_runner, field, new TaskCompletionSource<ProviderPollResult>().Task);
            }
            else
            {
                field = "DownloadTask";
                Set(_runner, field, new TaskCompletionSource<HttpResult>().Task);
            }
            var task = Get(_runner, field);
            Assert.IsFalse(AssetGenJobManager.TryAdvanceForTests(job.JobId));
            Assert.AreSame(task, Get(_runner, field));
            Assert.AreEqual(AssetGenJobState.Running, job.State);
        }

        [TestCase("AwaitSubmit", false)]
        [TestCase("AwaitSubmit", true)]
        [TestCase("AwaitPoll", false)]
        [TestCase("AwaitPoll", true)]
        [TestCase("AwaitDownload", false)]
        [TestCase("AwaitDownload", true)]
        public void FaultedOrCanceledTask_FinalizesWithoutImport(string phase, bool canceled)
        {
            var job = Register(phase);
            using var cts = new CancellationTokenSource();
            cts.Cancel();
            Exception error = new InvalidOperationException("synthetic request failure");
            if (phase == "AwaitSubmit")
                Set(_runner, "SubmitTask", canceled ? Task.FromCanceled<string>(cts.Token) : Task.FromException<string>(error));
            else if (phase == "AwaitPoll")
                Set(_runner, "PollTask", canceled ? Task.FromCanceled<ProviderPollResult>(cts.Token) : Task.FromException<ProviderPollResult>(error));
            else
                Set(_runner, "DownloadTask", canceled ? Task.FromCanceled<HttpResult>(cts.Token) : Task.FromException<HttpResult>(error));
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(job.JobId));
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(0, _imports);
            var runners = (IDictionary)typeof(AssetGenJobManager).GetField("Runners", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            Assert.IsFalse(runners.Contains(job.JobId));
        }
    }
}
