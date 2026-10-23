using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetGenRequestBudgetTests
    {
        private double now;
        private RecordingHttp http;

        private sealed class MemoryKeys : ISecureKeyStore
        {
            public bool TryGet(string provider, out string key)
            {
                key = "owned-budget-fixture";
                return true;
            }

            public bool Has(string provider) => true;

            public void Set(string provider, string key) => throw new InvalidOperationException();

            public void Delete(string provider) => throw new InvalidOperationException();
        }

        private sealed class RecordingHttp : IHttpTransport
        {
            internal readonly List<HttpRequestSpec> Requests = new();
            internal Func<HttpRequestSpec, Task<HttpResult>> Handler;
            internal CancellationToken Token;

            public Task<HttpResult> SendAsync(HttpRequestSpec spec, CancellationToken ct)
            {
                Requests.Add(spec);
                Token = ct;
                return Handler?.Invoke(spec) ?? Task.FromResult(Json("{}"));
            }
        }

        private static HttpResult Json(string value) =>
            new()
            {
                IsSuccess = true,
                Status = 200,
                Text = value,
            };

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.OverrideForTests(new MemoryKeys());
            now = 100;
            http = new RecordingHttp();
            Configure();
        }

        private void Configure()
        {
            AssetGenJobManager.ClockForTests = () => now;
            AssetGenJobManager.TransportOverrideForTests = http;
            AssetGenJobManager.SkipModelVerificationForTests = true;
            AssetGenJobManager.PollIntervalSeconds = 0;
            AssetGenJobManager.ImportOverrideForTests = (_, _) => throw new AssertionException("No file import is allowed in this fixture.");
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.ResetForTests();
        }

        private static FieldInfo TimeoutField()
        {
            var field = typeof(HttpRequestSpec).GetField("TimeoutSeconds");
            Assert.IsNotNull(field, "Provider requests need an explicit finite wait policy.");
            return field;
        }

        private static double? Budget(HttpRequestSpec spec) => (double?)TimeoutField().GetValue(spec);

        private static void SetBudget(HttpRequestSpec spec, double value) => TimeoutField().SetValue(spec, (double?)value);

        private static ImageGenRequest Image() =>
            new()
            {
                Provider = "openrouter",
                Prompt = "owned",
                OutputFolder = "Assets",
            };

        private static ModelGenRequest Model() =>
            new()
            {
                Provider = "tripo",
                Prompt = "owned",
                OutputFolder = "Assets",
                Format = "glb",
            };

        private static IHttpTransport ForJob(string id)
        {
            var method = typeof(AssetGenJobManager).GetMethod("CreateJobTransport", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method, "Every job request must use its retained deadline and current owner.");
            return (IHttpTransport)method.Invoke(null, new object[] { id });
        }

        [Test]
        public void DirectSynchronousImageRequest_AllowsTheGenerationBudget()
        {
            http.Handler = _ => Task.FromResult(Json("{\"choices\":[{\"message\":{\"images\":[{\"image_url\":{\"url\":\"data:image/png;base64,AQID\"}}]}}]}"));
            var task = new OpenRouterAdapter().SubmitAsync(Image(), "owned", http, CancellationToken.None);
            Assert.AreEqual("ready", task.GetAwaiter().GetResult());
            Assert.AreEqual(7200, Budget(http.Requests[0]));
        }

        [TestCase("image")]
        [TestCase("model")]
        [TestCase("audio")]
        public void SubmitUsesRemainingBudgetAfterQueueDelay(string kind)
        {
            http.Handler = _ => new TaskCompletionSource<HttpResult>().Task;
            AssetGenJob job =
                kind == "image" ? AssetGenJobManager.StartImageGeneration(Image())
                : kind == "model" ? AssetGenJobManager.StartModelGeneration(Model())
                : AssetGenJobManager.StartAudioGeneration(
                    new AudioGenRequest
                    {
                        Provider = "fal",
                        Prompt = "owned",
                        OutputFolder = "Assets",
                    }
                );
            now += 500;
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(1, http.Requests.Count, job.Error);
            Assert.AreEqual(6700, Budget(http.Requests[0]));
        }

        [TestCase(null, 120)]
        [TestCase("GET", 120)]
        [TestCase("HEAD", 120)]
        [TestCase("POST", 7000)]
        [TestCase("PUT", 7000)]
        public void RequestPolicy_UsesRemainingJobTimeAndKeepsShortReads(string method, double expected)
        {
            var job = AssetGenJobManager.StartModelGeneration(Model());
            var original = new HttpRequestSpec
            {
                Method = method,
                Url = "https://example.invalid",
                Body = new byte[] { 1, 2 },
                ContentType = "fixture",
            };
            original.Headers["Fixture"] = "owned";
            now += 200;
            ForJob(job.JobId).SendAsync(original, CancellationToken.None).GetAwaiter().GetResult();
            var sent = http.Requests[0];
            Assert.AreEqual(expected, Budget(sent));
            Assert.IsNull(Budget(original), "Applying a job budget must not mutate caller-owned requests.");
            Assert.AreNotSame(original, sent);
            Assert.AreSame(original.Body, sent.Body, "Do not copy large upload buffers.");
            Assert.AreSame(original.Headers, sent.Headers);
            Assert.AreEqual(original.ContentType, sent.ContentType);
            Assert.AreEqual(original.Url, sent.Url);
            Assert.AreEqual(original.Method, sent.Method);
        }

        [TestCase("GET", 1000, 50)]
        [TestCase("POST", 15, 15)]
        [TestCase("POST", 86400, 50)]
        public void ExplicitTimeout_IsClampedToActualRemainingTime(string method, double requested, double expected)
        {
            var job = AssetGenJobManager.StartModelGeneration(Model());
            var request = new HttpRequestSpec { Method = method };
            SetBudget(request, requested);
            now += 7150;
            ForJob(job.JobId).SendAsync(request, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(expected, Budget(http.Requests[0]));
            Assert.AreEqual(requested, Budget(request));
        }

        [Test]
        public void ArtifactDownload_PreservesSeparateStreamingPolicy()
        {
            var job = AssetGenJobManager.StartModelGeneration(Model());
            var request = new HttpRequestSpec { DownloadProvider = "tripo", Url = "https://example.invalid" };
            ForJob(job.JobId).SendAsync(request, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreSame(request, http.Requests[0]);
        }

        [TestCase("expired")]
        [TestCase("canceled")]
        [TestCase("reloaded")]
        public void StaleOwnerOrDeadline_CannotSendAnotherRequest(string state)
        {
            var job = AssetGenJobManager.StartModelGeneration(Model());
            IHttpTransport retained = ForJob(job.JobId);
            if (state == "expired")
                now += 7200;
            else if (state == "canceled")
                AssetGenJobManager.Cancel(job.JobId);
            else
            {
                AssetGenJobManager.ResetTransientForTests();
                Configure();
                AssetGenJobManager.RestoreJobs();
            }
            Assert.Catch(() => retained.SendAsync(new HttpRequestSpec { Method = "POST" }, CancellationToken.None).GetAwaiter().GetResult());
            Assert.IsEmpty(http.Requests);
        }

        [Test]
        public void CallerCancellation_DoesNotReachInnerTransport()
        {
            var job = AssetGenJobManager.StartModelGeneration(Model());
            using var cts = new CancellationTokenSource();
            cts.Cancel();
            Assert.Throws<OperationCanceledException>(() => ForJob(job.JobId).SendAsync(new HttpRequestSpec(), cts.Token).GetAwaiter().GetResult());
            Assert.IsEmpty(http.Requests);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void DelayedGenerationOrReceipt_Beyond120SecondsDoesNotResubmit(bool synchronousImage)
        {
            var pending = new TaskCompletionSource<HttpResult>();
            http.Handler = _ => pending.Task;
            var job = synchronousImage ? AssetGenJobManager.StartImageGeneration(Image()) : AssetGenJobManager.StartModelGeneration(Model());
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(7200, Budget(http.Requests[0]));
            now += 301;
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(AssetGenJobState.Running, job.State);
            pending.SetResult(
                Json(
                    synchronousImage
                        ? "{\"choices\":[{\"message\":{\"images\":[{\"image_url\":{\"url\":\"data:image/png;base64,AQID\"}}]}}]}"
                        : "{\"code\":0,\"data\":{\"task_id\":\"owned-budget-receipt\"}}"
                )
            );
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(AssetGenJobState.Running, job.State, job.Error);
            Assert.AreEqual(1, http.Requests.Count);
            var saved = JObject.Parse(SessionState.GetString("MCPForUnity.AssetGen.Job." + job.JobId, ""));
            Assert.IsNotNull(saved["_resume_v1"], saved.ToString());
            Assert.AreEqual(synchronousImage ? "nonresumable" : "polling", (string)saved["_resume_v1"]["Phase"]);
        }

        [Test]
        public void LostSubmissionResponse_IsExplicitlyUnknownAndNeverReplayed()
        {
            var pending = new TaskCompletionSource<HttpResult>();
            http.Handler = _ => pending.Task;
            var job = AssetGenJobManager.StartImageGeneration(Image());
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(7200, Budget(http.Requests[0]));
            pending.SetException(new TimeoutException("owned lost response"));
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual("outcome_unknown", job.RecoveryState);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            AssetGenJobManager.ResetTransientForTests();
            Configure();
            AssetGenJobManager.RestoreJobs();
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual("outcome_unknown", AssetGenJobManager.GetJob(job.JobId).RecoveryState);
            Assert.AreEqual(1, http.Requests.Count);
        }

        [Test]
        public void RecoveredPolling_UsesOriginalRemainingDeadline()
        {
            http.Handler = spec =>
                Task.FromResult(
                    Json(
                        spec.Method == "POST"
                            ? "{\"code\":0,\"data\":{\"task_id\":\"owned-budget-receipt\"}}"
                            : "{\"code\":0,\"data\":{\"status\":\"running\",\"progress\":10}}"
                    )
                );
            var job = AssetGenJobManager.StartModelGeneration(Model());
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            now += 7150;
            AssetGenJobManager.ResetTransientForTests();
            Configure();
            AssetGenJobManager.RestoreJobs();
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(2, http.Requests.Count);
            Assert.AreEqual("GET", http.Requests[1].Method);
            Assert.AreEqual(50, Budget(http.Requests[1]));
        }

        [Test]
        public void OuterDeadline_CancelsLocalWaitAndCannotReplayLateReceipt()
        {
            var pending = new TaskCompletionSource<HttpResult>();
            http.Handler = _ => pending.Task;
            var job = AssetGenJobManager.StartModelGeneration(Model());
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            now += 7201;
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.IsTrue(http.Token.IsCancellationRequested);
            Assert.AreEqual("outcome_unknown", job.RecoveryState);
            pending.SetResult(Json("{\"code\":0,\"data\":{\"task_id\":\"owned-late-receipt\"}}"));
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual("job_timeout", job.RecoveryCode);
            Assert.AreEqual(1, http.Requests.Count);
        }

        [TestCase(double.NaN)]
        [TestCase(86401)]
        public void InvalidExplicitPolicy_IsNotHiddenByJobClamping(double timeout)
        {
            var job = AssetGenJobManager.StartModelGeneration(Model());
            var spec = new HttpRequestSpec { Method = "POST" };
            SetBudget(spec, timeout);
            Assert.Throws<ArgumentOutOfRangeException>(() => ForJob(job.JobId).SendAsync(spec, CancellationToken.None));
            Assert.IsEmpty(http.Requests);
        }

        [TestCase(0)]
        [TestCase(-1)]
        [TestCase(double.NaN)]
        [TestCase(double.PositiveInfinity)]
        [TestCase(86401)]
        public void InvalidRequestTimeout_IsRejectedBeforeNativeAllocation(double value)
        {
            var spec = new HttpRequestSpec();
            SetBudget(spec, value);
            Assert.Throws<ArgumentOutOfRangeException>(() => new UnityWebRequestTransport().SendAsync(spec, CancellationToken.None));
        }

        [TestCase(0.01, 1)]
        [TestCase(301.1, 302)]
        [TestCase(7200, 7200)]
        [TestCase(86400, 86400)]
        public void NativeTimeout_RoundsUpWithoutRestoring120SecondCap(double value, int expected)
        {
            var method = typeof(UnityWebRequestTransport).GetMethod("ResolveTimeoutSeconds", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            Assert.AreEqual(expected, method.Invoke(null, new object[] { (double?)value }));
            Assert.AreEqual(120, method.Invoke(null, new object[] { null }));
        }
    }
}
