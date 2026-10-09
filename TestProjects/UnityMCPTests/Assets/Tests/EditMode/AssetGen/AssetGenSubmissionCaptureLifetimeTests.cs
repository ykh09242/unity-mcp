using System;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetGenSubmissionCaptureLifetimeTests
    {
        private const int PayloadChars = 2 * 1024 * 1024;
        private ISecureKeyStore _previousStore;
        private GatedTransport _transport;
        private WeakReference _request;
        private WeakReference _payload;
        private AssetGenJob _job;
        private string _folder;

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            // Read the cached instance without invoking Current's platform/credential fallback.
            _previousStore = (ISecureKeyStore)typeof(SecureKeyStore).GetField("_current", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            SecureKeyStore.OverrideForTests(new SyntheticStore());
            _transport = new GatedTransport();
            AssetGenJobManager.TransportOverrideForTests = _transport;
            AssetGenJobManager.PollIntervalSeconds = 0;
            _folder = "Assets/__McpSubmissionCapture_" + Guid.NewGuid().ToString("N");
            // These tests stop before download/import; no asset folder is created or removed.
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.OverrideForTests(_previousStore);
        }

        private static bool Alive(WeakReference weak)
        {
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
            return weak.IsAlive;
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private ModelGenRequest Request()
        {
            var req = new ModelGenRequest
            {
                Provider = "tripo",
                Prompt = new string('x', PayloadChars),
                Name = "capture",
                OutputFolder = _folder,
            };
            _request = new WeakReference(req);
            _payload = new WeakReference(req.Prompt);
            return req;
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private void Enqueue()
        {
            _job = AssetGenJobManager.StartModelGeneration(Request());
            AssetGenJobManager.TryAdvanceForTests(_job.JobId);
        }

        private void CompleteSubmission()
        {
            _transport.Submit.SetResult(new HttpResult { Status = 200, Text = "{\"code\":0,\"data\":{\"task_id\":\"provider-id\"}}" });
            AssetGenJobManager.TryAdvanceForTests(_job.JobId);
        }

        [Test]
        public void AcceptedSubmission_ReleasesInputWhileRepolling()
        {
            Enqueue();
            Assert.IsTrue(Alive(_request) && Alive(_payload), "Pending submission must still own its required input.");
            Assert.AreEqual(1, _transport.Submissions);
            Assert.AreEqual(PayloadChars, _transport.PromptChars);
            CompleteSubmission();
            Assert.IsFalse(Alive(_request), "Accepted request is retained by submission/poll closures.");
            Assert.IsFalse(Alive(_payload), "Large submission input is retained during polling.");
            for (int i = 0; i < 2; i++)
            {
                AssetGenJobManager.TryAdvanceForTests(_job.JobId);
                AssetGenJobManager.TryAdvanceForTests(_job.JobId);
            }
            Assert.AreEqual(2, _transport.Polls);
            Assert.AreEqual(AssetGenJobState.Running, _job.State);
            Assert.AreEqual(0.5f, _job.Progress);
            Assert.IsTrue(AssetGenJobManager.Cancel(_job.JobId));
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(_job.JobId));
            Assert.AreEqual(AssetGenJobState.Canceled, _job.State);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private void SubmitWithCallerOwnership()
        {
            ModelGenRequest req = Request();
            _job = AssetGenJobManager.StartModelGeneration(req);
            AssetGenJobManager.TryAdvanceForTests(_job.JobId);
            CompleteSubmission();
            Assert.IsTrue(Alive(_payload), "A caller retaining its request must still retain the original input.");
            Assert.AreEqual(PayloadChars, req.Prompt.Length);
            Assert.AreEqual('x', req.Prompt[0]);
            Assert.AreEqual("tripo", req.Provider);
            Assert.AreEqual(_folder, req.OutputFolder);
            GC.KeepAlive(req);
        }

        [Test]
        public void Submission_DoesNotMutateCallerOwnedRequest()
        {
            SubmitWithCallerOwnership();
            Assert.IsFalse(Alive(_request));
            Assert.IsFalse(Alive(_payload));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FailedOrCanceledSubmission_FinalizesAndReleasesRequest(bool canceled)
        {
            Enqueue();
            Assert.IsTrue(Alive(_payload));
            if (canceled)
                _transport.Submit.SetCanceled();
            else
                _transport.Submit.SetException(new IOException("synthetic submission failure"));
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(_job.JobId));
            Assert.AreEqual(AssetGenJobState.Failed, _job.State);
            Assert.AreEqual(0, _transport.Polls);
            Assert.IsFalse(Alive(_request));
            Assert.IsFalse(Alive(_payload));
        }

        private sealed class GatedTransport : IHttpTransport
        {
            public readonly TaskCompletionSource<HttpResult> Submit = new();
            public int Submissions;
            public int Polls;
            public int PromptChars;

            public Task<HttpResult> SendAsync(HttpRequestSpec spec, CancellationToken ct)
            {
                if (spec.Method == "POST")
                {
                    Submissions++;
                    // Inspect actual serialized Tripo input; retain no request/body/string copy.
                    PromptChars = ((string)Newtonsoft.Json.Linq.JObject.Parse(System.Text.Encoding.UTF8.GetString(spec.Body))["prompt"]).Length;
                    return Submit.Task;
                }
                if (spec.Url != "https://api.tripo3d.ai/v2/openapi/task/provider-id")
                    throw new InvalidOperationException("Unexpected synthetic poll URL.");
                Polls++;
                return Task.FromResult(new HttpResult { Status = 200, Text = "{\"code\":0,\"data\":{\"status\":\"running\",\"progress\":50}}" });
            }
        }

        private sealed class SyntheticStore : ISecureKeyStore
        {
            public bool TryGet(string providerId, out string apiKey)
            {
                apiKey = "synthetic-only";
                return true;
            }

            public bool Has(string providerId) => true;

            public void Set(string providerId, string apiKey) => throw new NotSupportedException();

            public void Delete(string providerId) => throw new NotSupportedException();
        }
    }
}
