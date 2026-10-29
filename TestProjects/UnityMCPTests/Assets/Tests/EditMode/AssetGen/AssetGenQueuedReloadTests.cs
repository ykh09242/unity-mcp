using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetGenQueuedReloadTests
    {
        private const string JobPrefix = "MCPForUnity.AssetGen.Job.";
        private const string Receipt = "queued-owned-receipt";
        private const string Prompt = "QUEUED_PRIVATE_PROMPT";
        private QueuedReloadKeys keys;
        private FakeHttpTransport http;
        private double now;
        private string root;

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            AssetGenModelCatalog.ResetForTests(true);
            keys = new QueuedReloadKeys();
            SecureKeyStore.OverrideForTests(keys);
            now = 100;
            root = "Assets/queued_reload_" + Guid.NewGuid().ToString("N");
            http = new FakeHttpTransport { Handler = Respond };
            Configure();
        }

        private void Configure()
        {
            AssetGenJobManager.ClockForTests = () => now;
            AssetGenJobManager.TransportOverrideForTests = http;
            AssetGenJobManager.SubmissionAuthorizationForTests = _ => true;
            AssetGenJobManager.SkipModelVerificationForTests = true;
            AssetGenJobManager.ImportOverrideForTests = (_, __) => throw new AssertionException("This fixture must not import.");
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.ResetForTests();
            string absolute = AssetGenPaths.ToAbsolute(root);
            if (Directory.Exists(absolute))
                Directory.Delete(absolute, true);
            if (File.Exists(absolute + ".meta"))
                File.Delete(absolute + ".meta");
        }

        private static HttpResult Respond(HttpRequestSpec request)
        {
            JObject response =
                request.Url.Contains("tripo3d")
                    ? new JObject
                    {
                        ["code"] = 0,
                        ["data"] = new JObject { ["task_id"] = Receipt, ["status"] = "running" },
                    }
                : request.Url.Contains("meshy") ? new JObject { ["result"] = Receipt, ["status"] = "IN_PROGRESS" }
                : request.Url.Contains("sketchfab") ? new JObject { ["gltf"] = new JObject { ["url"] = "https://example.invalid/owned.zip" } }
                : new JObject
                {
                    ["request_id"] = Receipt,
                    ["response_url"] = "https://queue.fal.run/fal-ai/model/requests/" + Receipt,
                    ["status"] = "IN_QUEUE",
                };
            return new HttpResult
            {
                Status = 200,
                IsSuccess = true,
                Text = response.ToString(Formatting.None),
            };
        }

        private ModelGenRequest Request(string provider = "tripo") =>
            new ModelGenRequest
            {
                Provider = provider,
                Mode = "text",
                Prompt = Prompt,
                Name = "owned",
                OutputFolder = root,
                Texture = false,
            };

        private AssetGenJob Start() => AssetGenJobManager.StartModelGeneration(Request());

        private void Reload()
        {
            AssetGenJobManager.ResetTransientForTests();
            Configure();
            AssetGenJobManager.RestoreJobs();
        }

        private static void Pump(string id, int count = 3)
        {
            for (int i = 0; i < count; i++)
                AssetGenJobManager.TryAdvanceForTests(id);
        }

        private static JObject Record(string id) => JObject.Parse(SessionState.GetString(JobPrefix + id, ""));

        private void Refused(string id, string code)
        {
            Reload();
            Pump(id);
            AssetGenJob job = AssetGenJobManager.GetJob(id);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(code, job.RecoveryCode);
            Assert.AreEqual("not_submitted", job.RecoveryState);
            Assert.IsEmpty(http.RecordedRequests);
            Assert.IsEmpty(SessionState.GetString(AssetGenSubmissionIntent.KeyPrefix + id, ""));
        }

        [Test]
        public void RepeatedReload_SubmitsOriginalIdentityAndFrozenRequestExactlyOnce()
        {
            ModelGenRequest request = Request();
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(request);
            JToken deadline = Record(job.JobId)["_resume_v1"]["Deadline"].DeepClone();
            request.Prompt = "caller changed mutable request";
            request.Model = "changed";
            Reload();
            Pump(job.JobId, 1); // Reconstruct the delegate, but no submission has started.
            Reload();
            Reload();
            Pump(job.JobId);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            JObject body = JObject.Parse(Encoding.UTF8.GetString(http.RecordedRequests[0].Body));
            Assert.AreEqual(Prompt, (string)body["prompt"]);
            Assert.AreEqual(TripoAdapter.ModelVersion, (string)body["model"]);
            Assert.AreEqual(job.JobId, AssetGenJobManager.GetJob(job.JobId).JobId);
            Assert.AreEqual(Receipt, AssetGenJobManager.GetJob(job.JobId).ProviderTaskId);
            Assert.IsTrue(JToken.DeepEquals(deadline, Record(job.JobId)["_resume_v1"]["Deadline"]));
            Reload();
            Pump(job.JobId, 3);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
        }

        [TestCase("model", "meshy")]
        [TestCase("model", "fal")]
        [TestCase("image", "fal")]
        [TestCase("image", "openrouter")]
        [TestCase("audio", "fal")]
        [TestCase("marketplace", "sketchfab")]
        public void EverySubmissionFamily_ReconstructsOriginalProviderRequest(string kind, string provider)
        {
            ModelEntry falModel = AssetGenModelCatalog.Find(FalModelAdapter.DefaultModel);
            string priorVerified = falModel.VerifiedAt;
            string priorOutput = falModel.ModelOutputField;
            try
            {
                falModel.VerifiedAt = "fixture";
                falModel.ModelOutputField = "model_glb";
                ModelGenRequest model = Request(provider);
                model.Texture = true;
                AssetGenJob job =
                    kind == "model" ? AssetGenJobManager.StartModelGeneration(model)
                    : kind == "image"
                        ? AssetGenJobManager.StartImageGeneration(
                            new ImageGenRequest
                            {
                                Provider = provider,
                                Prompt = Prompt,
                                Mode = "text",
                                OutputFolder = root,
                                Name = "owned",
                                Width = 512,
                                Height = 512,
                            }
                        )
                    : kind == "audio"
                        ? AssetGenJobManager.StartAudioGeneration(
                            new AudioGenRequest
                            {
                                Provider = provider,
                                Prompt = Prompt,
                                Duration = 12,
                                OutputFolder = root,
                                Name = "owned",
                            }
                        )
                    : AssetGenJobManager.StartMarketplaceImport(new string('a', 32), 0, "owned", root);
                Reload();
                Pump(job.JobId);
                Assert.AreEqual(1, http.RecordedRequests.Count, AssetGenJobManager.GetJob(job.JobId).Error);
                Assert.AreNotEqual("preparing", (string)Record(job.JobId)["_resume_v1"]["Phase"]);
                Assert.IsEmpty(SessionState.GetString(AssetGenSubmissionIntent.KeyPrefix + job.JobId, ""));
                if (kind != "marketplace")
                {
                    Reload();
                    Pump(job.JobId, 1);
                    Assert.AreEqual(1, http.RecordedRequests.Count);
                }
            }
            finally
            {
                falModel.VerifiedAt = priorVerified;
                falModel.ModelOutputField = priorOutput;
            }
        }

        [Test]
        public void CancellationBeforeReload_RemovesIntentAndSendsNothing()
        {
            var job = Start();
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            Reload();
            Pump(job.JobId);
            Assert.AreEqual(AssetGenJobState.Canceled, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.IsEmpty(http.RecordedRequests);
            Assert.IsEmpty(SessionState.GetString(AssetGenSubmissionIntent.KeyPrefix + job.JobId, ""));
        }

        [Test]
        public void MissingCredentials_RefusesWithoutConsultingRealStore()
        {
            var job = Start();
            keys.Available = false;
            Refused(job.JobId, "reconnect_key_unavailable");
        }

        [Test]
        public void CredentialsRemovedAfterReconstruction_StillCannotSubmit()
        {
            var job = Start();
            Reload();
            Pump(job.JobId, 1);
            keys.Available = false;
            Pump(job.JobId);
            Assert.AreEqual("reconnect_key_unavailable", AssetGenJobManager.GetJob(job.JobId).RecoveryCode);
            Assert.AreEqual("not_submitted", AssetGenJobManager.GetJob(job.JobId).RecoveryState);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void RevokedAuthorization_RefusesBeforeCredentialResolution()
        {
            var job = Start();
            int reads = keys.Reads;
            Reload();
            AssetGenJobManager.SubmissionAuthorizationForTests = _ => false;
            Pump(job.JobId);
            Assert.AreEqual("submission_authorization_revoked", AssetGenJobManager.GetJob(job.JobId).RecoveryCode);
            Assert.AreEqual(reads, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void ChangedCatalogConfiguration_RefusesWithoutChangingSelectedModel()
        {
            var job = Start();
            ModelEntry entry = AssetGenModelCatalog.Find(TripoAdapter.ModelVersion);
            string previous = entry.TextureField;
            try
            {
                entry.TextureField = "changed_input_schema";
                Refused(job.JobId, "submission_configuration_changed");
            }
            finally
            {
                entry.TextureField = previous;
            }
        }

        [Test]
        public void ChangedAudioDurationPolicy_RefusesBeforeProviderRequest()
        {
            var job = AssetGenJobManager.StartAudioGeneration(
                new AudioGenRequest
                {
                    Provider = "fal",
                    Prompt = Prompt,
                    OutputFolder = root,
                    Name = "owned",
                }
            );
            ModelEntry entry = AssetGenModelCatalog.Find(FalAudioAdapter.DefaultModel);
            bool prior = entry.FromRefresh;
            try
            {
                entry.FromRefresh = !prior;
                Refused(job.JobId, "submission_configuration_changed");
            }
            finally
            {
                entry.FromRefresh = prior;
            }
        }

        [Test]
        public void OutputFolderReplacedByFile_RefusesOriginalDestination()
        {
            var job = Start();
            string absolute = AssetGenPaths.ToAbsolute(root);
            File.WriteAllText(absolute, "owned obstruction");
            try
            {
                Refused(job.JobId, "submission_output_changed");
            }
            finally
            {
                File.Delete(absolute);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LocalImage_RevalidatesContentBeforeSubmission(bool changed)
        {
            Directory.CreateDirectory(AssetGenPaths.ToAbsolute(root));
            string path = root + "/owned.png";
            byte[] bytes = { 137, 80, 78, 71, 13, 10, 26, 10, 1 };
            File.WriteAllBytes(AssetGenPaths.ToAbsolute(path), bytes);
            ModelGenRequest request = Request("meshy");
            request.Mode = "image";
            request.ImagePath = path;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(request);
            if (changed)
            {
                File.WriteAllBytes(AssetGenPaths.ToAbsolute(path), new byte[] { 1, 2, 3 });
                Refused(job.JobId, "submission_input_changed");
            }
            else
            {
                Reload();
                Pump(job.JobId);
                Assert.AreEqual(1, http.RecordedRequests.Count);
                StringAssert.Contains(Convert.ToBase64String(bytes), Encoding.UTF8.GetString(http.RecordedRequests[0].Body));
            }
        }

        [Test]
        public void RemoteUrl_IsNeverStoredAndGetsPreciseReloadRefusal()
        {
            var request = Request();
            request.Mode = "image";
            request.ImageUrl = "https://example.invalid/input.png?signature=DO_NOT_RETAIN";
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(request);
            StringAssert.DoesNotContain("DO_NOT_RETAIN", Record(job.JobId).ToString());
            Assert.IsEmpty(SessionState.GetString(AssetGenSubmissionIntent.KeyPrefix + job.JobId, ""));
            Refused(job.JobId, "submission_url_input_not_retained");
        }

        [TestCase("missing", "submission_intent_unavailable")]
        [TestCase("corrupt", "submission_intent_invalid")]
        [TestCase("oversized", "submission_intent_too_large")]
        public void UntrustedSessionIntent_FailsClosed(string fault, string code)
        {
            var job = Start();
            SessionState.SetString(
                AssetGenSubmissionIntent.KeyPrefix + job.JobId,
                fault == "missing" ? ""
                    : fault == "corrupt" ? "{}"
                    : new string('x', AssetGenSubmissionIntent.MaxBytes + 1)
            );
            Refused(job.JobId, code);
        }

        [Test]
        public void OriginalDeadline_IsNotExtendedAcrossReload()
        {
            AssetGenJobManager.TimeoutSeconds = 20;
            var job = Start();
            now = 121;
            Refused(job.JobId, "job_timeout");
        }

        [Test]
        public void SubmissionFence_WithLostResponseNeverReplays()
        {
            var job = Start();
            http.Handler = _ => throw new IOException("synthetic lost response");
            Pump(job.JobId, 1);
            Assert.AreEqual("submitting", (string)Record(job.JobId)["_resume_v1"]["Phase"]);
            Reload();
            Pump(job.JobId);
            Assert.AreEqual("submission_outcome_unknown", AssetGenJobManager.GetJob(job.JobId).RecoveryCode);
            Assert.AreEqual(1, http.RecordedRequests.Count);
        }

        [Test]
        public void SessionIntentDoesNotEnterJournalProjectionOrRetainKeys()
        {
            var job = Start();
            string record = Record(job.JobId).ToString();
            string intent = SessionState.GetString(AssetGenSubmissionIntent.KeyPrefix + job.JobId, "");
            StringAssert.DoesNotContain(Prompt, record);
            StringAssert.Contains(Prompt, intent);
            StringAssert.DoesNotContain(QueuedReloadKeys.Secret, record);
            StringAssert.DoesNotContain(QueuedReloadKeys.Secret, intent);
            Pump(job.JobId, 1);
            Assert.IsEmpty(SessionState.GetString(AssetGenSubmissionIntent.KeyPrefix + job.JobId, ""));
        }
    }

    internal sealed class QueuedReloadKeys : ISecureKeyStore
    {
        internal const string Secret = "queued-owned-synthetic-key";
        internal bool Available = true;
        internal int Reads;

        public bool TryGet(string provider, out string key)
        {
            Reads++;
            key = Available ? Secret : null;
            return Available;
        }

        public bool Has(string provider) => Available;

        public void Set(string provider, string key) => throw new InvalidOperationException("No key writes.");

        public void Delete(string provider) => throw new InvalidOperationException("No key writes.");
    }

    public class AssetGenQueuedActualReloadTests
    {
        [UnityTest, Explicit("Sole-QA disposable project only; requires exact UNITY_MCP_ASSETGEN_QUEUE_RELOAD_PROJECT.")]
        public IEnumerator ActualDomainReload_QueuedIntentSubmitsOnceWithSameId()
        {
            try
            {
                AssetGenQueuedReloadProbe.Begin();
                if (AssetGenQueuedReloadProbe.RequestReloadOnce())
                    yield return new WaitForDomainReload();
                AssetGenQueuedReloadProbe.ObserveIteratorResume();
                for (int step = 0; step < 100 && !AssetGenQueuedReloadProbe.Submitted(); step++)
                    yield return null;
                AssetGenQueuedReloadProbe.Verify();
            }
            finally
            {
                AssetGenQueuedReloadProbe.Cleanup();
            }
        }
    }

    [InitializeOnLoad]
    internal static class AssetGenQueuedReloadProbe
    {
        private const string Prefix = "MCPForUnity.AssetGenQueuedReloadProbe.";
        private static readonly string Domain = Guid.NewGuid().ToString("N");

        private static string Read(string key) => SessionState.GetString(Prefix + key, "");

        private static void Store(string key, string value) => SessionState.SetString(Prefix + key, value);

        private static string Project => Directory.GetParent(Application.dataPath).FullName;
        private static bool Owned =>
            Environment.GetEnvironmentVariable("UNITY_MCP_ASSETGEN_QUEUE_RELOAD_PROJECT") == Project
            && AssetGenJobCheckpoint.Hex(Read("nonce"), 32)
            && Read("active") == "true";

        static AssetGenQueuedReloadProbe()
        {
            // Even a removed environment opt-in must leave restored work behind fake boundaries.
            // The current authorization callback then refuses it before any provider invocation.
            if (Read("active") == "true")
            {
                Configure();
                Debug.Log("AssetGen queued reload probe: restored fake boundaries in a new domain.");
                EditorApplication.update += ObserveRestoredUpdate;
            }
        }

        private static void Configure()
        {
            SecureKeyStore.OverrideForTests(new QueuedReloadKeys());
            AssetGenJobManager.TransportOverrideForTests = new FakeHttpTransport
            {
                Handler = request =>
                {
                    if (!Owned || Read("bootstrap") != Domain)
                        throw new InvalidOperationException("Owned fake boundary is not ready.");
                    if (request.Method == "POST")
                    {
                        if (Domain == Read("before"))
                            Store("early_posts", "1");
                        if (Domain == Read("before") || Read("posts") != "0")
                            throw new InvalidOperationException("A queued generation was submitted early or duplicated.");
                        Store("posts", "1");
                    }
                    return new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Text = new JObject
                        {
                            ["code"] = 0,
                            ["data"] = new JObject { ["task_id"] = "owned-queued-task", ["status"] = "running" },
                        }.ToString(),
                    };
                },
            };
            AssetGenJobManager.SubmissionAuthorizationForTests = _ => Owned;
            AssetGenJobManager.SkipModelVerificationForTests = true;
            AssetGenJobManager.ImportOverrideForTests = (_, __) => throw new InvalidOperationException("No import in queued probe.");
            AssetGenModelCatalog.ResetForTests(true);
            Store("bootstrap", Domain);
        }

        internal static void ObserveIteratorResume()
        {
            if (Owned)
                Debug.Log("AssetGen queued reload probe: top-level iterator resumed after reload wait.");
        }

        private static void ObserveRestoredUpdate()
        {
            EditorApplication.update -= ObserveRestoredUpdate;
            if (!Owned)
                return;
            AssetGenJob job = AssetGenJobManager.GetJob(Read("job"));
            Debug.Log(
                "AssetGen queued reload probe: first restored update; state="
                    + (job?.State.ToString() ?? "missing")
                    + "; provider_phase="
                    + (job?.ProviderPhase ?? "none")
                    + "; submissions="
                    + Read("posts")
                    + "."
            );
        }

        internal static void Begin()
        {
            Assert.AreEqual(Project, Environment.GetEnvironmentVariable("UNITY_MCP_ASSETGEN_QUEUE_RELOAD_PROJECT"));
            // Unity Test Framework normally restores the iterator PC. Retain an explicit phase
            // too: restarted iterators must not allocate another job or request another reload.
            if (Read("active") == "true")
            {
                Assert.IsTrue(Owned, "An unfinished probe must retain its original owned session.");
                Assert.AreEqual("reload_requested", Read("phase"));
                return;
            }
            Assert.IsEmpty(SessionState.GetString("MCPForUnity.AssetGen.JobIndex", ""));
            Store("nonce", Guid.NewGuid().ToString("N"));
            Store("active", "true");
            Store("before", Domain);
            Store("posts", "0");
            Store("early_posts", "0");
            Store("queued_at_reload", "false");
            Store("queue_error", "");
            Store("phase", "queued");
            Store("reload_requests", "0");
            Configure();
            Debug.Log("AssetGen queued reload probe: armed for admission at the actual reload boundary.");
        }

        internal static bool RequestReloadOnce()
        {
            Assert.IsTrue(Owned);
            if (Read("phase") == "reload_requested")
            {
                Assert.AreEqual("1", Read("reload_requests"));
                return false;
            }
            Assert.AreEqual("queued", Read("phase"));
            Assert.AreEqual(Read("before"), Domain);
            Assert.AreEqual("0", Read("reload_requests"));
            // Persist the claim before invoking Unity; iterator disposal cannot erase this claim.
            Store("phase", "reload_requested");
            Store("reload_requests", "1");
            // UTF can run Editor updates between yielding WaitForDomainReload and unlocking
            // assemblies. Admit at the native boundary so no old-domain Tick can submit first.
            AssemblyReloadEvents.beforeAssemblyReload += QueueAtReload;
            Debug.Log("AssetGen queued reload probe: requesting its single domain reload.");
            EditorUtility.RequestScriptReload();
            return true;
        }

        private static void QueueAtReload()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= QueueAtReload;
            try
            {
                if (!Owned || Domain != Read("before") || Read("phase") != "reload_requested" || Read("job") != "")
                    throw new InvalidOperationException("Owned queue boundary rejected.");
                var job = AssetGenJobManager.StartModelGeneration(
                    new ModelGenRequest
                    {
                        Provider = "tripo",
                        Mode = "text",
                        Prompt = "owned queued reload",
                        Name = "owned",
                        OutputFolder = "Assets/queued_reload_" + Read("nonce"),
                    }
                );
                Store("job", job.JobId);
                Store(
                    "deadline",
                    JObject.Parse(SessionState.GetString("MCPForUnity.AssetGen.Job." + job.JobId, ""))["_resume_v1"]["Deadline"].ToString(Formatting.None)
                );
                if (job.State != AssetGenJobState.Queued || Read("posts") != "0" || Read("early_posts") != "0")
                    throw new InvalidOperationException("Submission crossed the owned queue boundary.");
                Store("queued_at_reload", "true");
                Debug.Log("AssetGen queued reload probe: original job queued at the actual reload boundary.");
            }
            catch (Exception error)
            {
                Store("queue_error", error.GetType().Name);
            }
        }

        internal static bool Submitted() => AssetGenJobManager.GetJob(Read("job"))?.ProviderTaskId == "owned-queued-task";

        internal static void Verify()
        {
            Assert.IsTrue(Owned);
            Assert.IsEmpty(Read("queue_error"), "The original-domain admission callback failed.");
            Assert.AreEqual("true", Read("queued_at_reload"), "The job must be queued at the actual reload boundary.");
            Assert.AreEqual("0", Read("early_posts"), "The old domain must not reach the provider boundary.");
            Assert.AreNotEqual(Read("before"), Domain, "An actual new managed domain is required.");
            Assert.AreEqual(Domain, Read("bootstrap"));
            Assert.AreEqual("1", Read("posts"));
            Assert.AreEqual("1", Read("reload_requests"));
            Assert.IsTrue(Submitted());
            AssetGenJob job = AssetGenJobManager.GetJob(Read("job"));
            Assert.AreEqual("resumed", job.RecoveryCode);
            Assert.AreEqual(
                Read("deadline"),
                JObject.Parse(SessionState.GetString("MCPForUnity.AssetGen.Job." + job.JobId, ""))["_resume_v1"]["Deadline"].ToString(Formatting.None)
            );
            Assert.IsEmpty(SessionState.GetString(AssetGenSubmissionIntent.KeyPrefix + job.JobId, ""));
            Store("phase", "verified");
            Debug.Log("AssetGen queued reload probe: same job submitted once and verified.");
        }

        internal static void Cleanup()
        {
            if (!Owned)
                return;
            // Disposing an iterator while its old domain unloads is not test completion.
            if (Read("phase") == "reload_requested" && Read("before") == Domain)
                return;
            AssemblyReloadEvents.beforeAssemblyReload -= QueueAtReload;
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.ResetForTests();
            foreach (
                string key in new[]
                {
                    "nonce",
                    "active",
                    "before",
                    "posts",
                    "bootstrap",
                    "job",
                    "deadline",
                    "phase",
                    "reload_requests",
                    "early_posts",
                    "queued_at_reload",
                    "queue_error",
                }
            )
                SessionState.EraseString(Prefix + key);
        }
    }
}
