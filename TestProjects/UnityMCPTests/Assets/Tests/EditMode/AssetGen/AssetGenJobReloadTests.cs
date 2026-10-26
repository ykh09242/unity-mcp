using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Tools.AssetGen;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.AssetGen
{
    // Ordinary tests use only an in-memory key store and fake HTTP. No EditorPrefs or provider calls.
    public class AssetGenJobReloadTests
    {
        private const string Prefix = "MCPForUnity.AssetGen.Job.";
        private const string Index = "MCPForUnity.AssetGen.JobIndex";
        private const string TaskId = "gen06-owned-task";
        private const string Secret = "gen06-synthetic-key";
        private FakeHttpTransport http;
        private MemoryKeys keys;
        private string output;
        private string outputGuid;
        private double now;

        private sealed class MemoryKeys : ISecureKeyStore
        {
            internal int Reads;
            internal bool Available = true;

            public bool TryGet(string provider, out string key)
            {
                Reads++;
                key = Available ? Secret : null;
                return Available;
            }

            public bool Has(string provider) => Available;

            public void Set(string provider, string key) => throw new InvalidOperationException("No key writes permitted.");

            public void Delete(string provider) => throw new InvalidOperationException("No key writes permitted.");
        }

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            keys = new MemoryKeys();
            SecureKeyStore.OverrideForTests(keys);
            output = "Assets/gen06_" + Guid.NewGuid().ToString("N");
            outputGuid = null;
            now = 100;
            AssetGenJobManager.ClockForTests = () => now;
            http = new FakeHttpTransport
            {
                Handler = request =>
                    request.Method == "POST"
                        ? Json(
                            new JObject
                            {
                                ["code"] = 0,
                                ["data"] = new JObject { ["task_id"] = TaskId },
                            }
                        )
                        : Json(
                            new JObject
                            {
                                ["code"] = 0,
                                ["data"] = new JObject { ["status"] = "running", ["progress"] = 25 },
                            }
                        ),
            };
            Configure();
        }

        private void Configure()
        {
            AssetGenJobManager.TransportOverrideForTests = http;
            AssetGenJobManager.PollIntervalSeconds = 0;
            AssetGenJobManager.SkipModelVerificationForTests = true;
            AssetGenJobManager.ImportOverrideForTests = (job, path) => throw new AssertionException("This ordinary polling fixture must never import.");
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.ResetForTests();
            if (outputGuid != null)
            {
                Assert.AreEqual(outputGuid, AssetDatabase.AssetPathToGUID(output), "Cleanup may delete only this fixture's registered folder.");
                Assert.IsTrue(AssetDatabase.DeleteAsset(output));
            }
            Assert.IsFalse(AssetDatabase.IsValidFolder(output));
        }

        private static HttpResult Json(JObject value) =>
            new HttpResult
            {
                Status = 200,
                IsSuccess = true,
                Text = value.ToString(Formatting.None),
            };

        private AssetGenJob Start() =>
            AssetGenJobManager.StartModelGeneration(
                new ModelGenRequest
                {
                    Provider = "tripo",
                    Mode = "text",
                    Prompt = "gen06-private-prompt",
                    Format = "glb",
                    Name = "owned-output",
                    OutputFolder = output,
                }
            );

        private static void Pump(string id, int count)
        {
            for (int i = 0; i < count; i++)
                AssetGenJobManager.TryAdvanceForTests(id);
        }

        // Run the production reload fence, then discard only transient state. Session bytes remain untouched.
        // The immutable baseline used existing ResetForTests plus an exact saved-byte restore.
        private void SimulateReload()
        {
            AssetGenJobManager.ResetTransientForTests();
            SecureKeyStore.OverrideForTests(keys);
            Configure();
            AssetGenJobManager.RestoreJobs();
        }

        [Test]
        public void RecordedProviderTask_ReloadContinuesSamePollWithoutAnotherPost()
        {
            AssetGenJob job = Start();
            Pump(job.JobId, 2);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
            SimulateReload();
            Pump(job.JobId, 4);
            AssetGenJob restored = AssetGenJobManager.GetJob(job.JobId);
            Assert.AreEqual(AssetGenJobState.Running, restored.State);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
            Assert.IsTrue(http.RecordedRequests.Any(r => r.Method == "GET" && r.Url.EndsWith("/" + TaskId, StringComparison.Ordinal)));
            Assert.AreEqual(job.JobId, restored.JobId);
        }

        [Test]
        public void CancelThenReloadBeforeNextTick_RetainsCanceledWithoutRequests()
        {
            AssetGenJob job = Start();
            Pump(job.JobId, 2);
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            int calls = http.RecordedRequests.Count;
            SimulateReload();
            Pump(job.JobId, 4);
            Assert.AreEqual(AssetGenJobState.Canceled, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual(calls, http.RecordedRequests.Count);
        }

        [Test]
        public void PersistedProviderFailure_ExcludesOpaquePayloadFromEntireRecord()
        {
            const string marker = "GEN06_RAW_PROMPT_MARKER";
            const string signed = "https://example.invalid/output?signature=GEN06_SIGNED_SECRET";
            http.Handler = _ => new HttpResult
            {
                Status = 400,
                IsSuccess = false,
                Text = new JObject { ["code"] = 1, ["message"] = marker + " " + signed + " data:image/png;base64,GEN06_BYTES" }.ToString(),
            };
            AssetGenJob job = Start();
            Pump(job.JobId, 3);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            string record = SessionState.GetString(Prefix + job.JobId, "");
            Assert.IsNotEmpty(record);
            StringAssert.DoesNotContain(marker, record);
            StringAssert.DoesNotContain("GEN06_SIGNED_SECRET", record);
            StringAssert.DoesNotContain("GEN06_BYTES", record);
            StringAssert.DoesNotContain(Secret, record);
        }

        [Test]
        public void MalformedFirstRecord_DoesNotHideLaterTerminalHistory()
        {
            string broken = Guid.NewGuid().ToString("N");
            string valid = Guid.NewGuid().ToString("N");
            SessionState.SetString(Prefix + broken, "{invalid");
            SessionState.SetString(Prefix + valid, JsonConvert.SerializeObject(new AssetGenJob { JobId = valid, State = AssetGenJobState.Done }));
            SessionState.SetString(Index, broken + "," + valid);
            AssetGenJobManager.RestoreJobs();
            Assert.IsNotNull(AssetGenJobManager.GetJob(valid));
            Assert.AreEqual(AssetGenJobState.Done, AssetGenJobManager.GetJob(valid).State);
            Assert.IsEmpty(http.RecordedRequests);
        }

        private AssetGenJobCheckpoint Checkpoint(string phase = "polling", string provider = "tripo", string kind = "model") =>
            new AssetGenJobCheckpoint
            {
                JobId = Guid.NewGuid().ToString("N"),
                Kind = kind,
                Provider = provider,
                Phase = phase,
                StartedAt = 100,
                Deadline = 700,
                Receipt = provider == "fal" ? "https://queue.fal.run/fal-ai/owned-model/requests/owned-task" : TaskId,
                OutputFolder = output,
                Name = "owned",
                Extension = kind == "image" ? "png" : "obj",
                Subfolder = kind == "image" ? "Images" : "Models",
            };

        private AssetGenJob Inject(AssetGenJobCheckpoint cp, AssetGenJobState state = AssetGenJobState.Running)
        {
            var job = new AssetGenJob
            {
                JobId = cp.JobId,
                Kind = cp.Kind,
                Provider = cp.Provider,
                Action = "generate",
                State = state,
                Format = cp.Extension,
            };
            SessionState.SetString(Prefix + cp.JobId, AssetGenJobCheckpoint.Project(job, cp).ToString(Formatting.None));
            SessionState.SetString(Index, cp.JobId);
            AssetGenJobManager.RestoreJobs();
            return AssetGenJobManager.GetJob(cp.JobId);
        }

        [TestCase("preparing", "reload_before_submission")]
        [TestCase("submitting", "submission_outcome_unknown")]
        [TestCase("writing", "local_write_outcome_unknown")]
        [TestCase("importing", "import_outcome_unknown")]
        public void UnreplayablePhase_RestoreRetainsOutcomeWithoutSideEffects(string phase, string code)
        {
            AssetGenJobCheckpoint cp = phase == "writing" || phase == "importing" ? SavedCheckpoint(register: phase == "importing") : Checkpoint(phase);
            cp.Phase = phase;
            byte[] retained = cp.SavedPath == null ? null : File.ReadAllBytes(AssetGenPaths.ToAbsolute(cp.SavedPath));
            AssetGenJob job = Inject(cp);
            Pump(job.JobId, 4);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(code, job.RecoveryCode);
            if (retained != null)
            {
                Assert.AreEqual(cp.SavedPath, job.AssetPath, "The recorded intended/retained path must remain inspectable.");
                CollectionAssert.AreEqual(retained, File.ReadAllBytes(AssetGenPaths.ToAbsolute(cp.SavedPath)));
                Assert.AreEqual(cp.RegisteredGuid, job.AssetGuid);
            }
            Assert.AreEqual(0, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [TestCase("https://queue.fal.run/fal-ai/model/requests/id", true)]
        [TestCase("https://queue.fal.run:443/fal-ai/model/requests/id", true)]
        [TestCase("https://queue.fal.run/fal-ai/model/requests/id/status", false)]
        [TestCase("https://queue.fal.run/fal-ai/model/edit/requests/id", false)]
        [TestCase("https://queue.fal.run/fal-ai//requests/id", false)]
        [TestCase("https://queue.fal.run/fal-ai/model/requests/id?signature=SECRET", false)]
        [TestCase("https://queue.fal.run/fal-ai/model/requests/%2fid", false)]
        [TestCase("https://queue.fal.run/fal-ai/../model/requests/id", false)]
        [TestCase("https://queue.fal.run@evil.invalid/fal-ai/model/requests/id", false)]
        [TestCase("http://queue.fal.run/fal-ai/model/requests/id", false)]
        public void FalReceipt_RequiresExactSupportedResponseRoute(string receipt, bool accepted) =>
            Assert.AreEqual(accepted, AssetGenJobCheckpoint.FalReceipt(receipt));

        [Test]
        public void OriginalDeadline_ExpiresBeforeReacquiringKeyOrPolling()
        {
            AssetGenJobCheckpoint cp = Checkpoint();
            now = 701;
            AssetGenJob job = Inject(cp);
            Pump(job.JobId, 3);
            Assert.AreEqual("job_timeout", job.RecoveryCode);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(0, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void MissingKey_FailsNetworkContinuationWithoutPost()
        {
            keys.Available = false;
            AssetGenJob job = Inject(Checkpoint());
            Pump(job.JobId, 3);
            Assert.AreEqual("reconnect_key_unavailable", job.RecoveryCode);
            Assert.AreEqual(1, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [TestCase("preparing")]
        [TestCase("submitting")]
        public void RequiredCheckpointFailure_BlocksInitialPost(string phase)
        {
            AssetGenJobManager.BeforeCheckpointWriteForTests = at =>
            {
                if (at == phase)
                    throw new IOException("owned fixture storage failure");
            };
            AssetGenJob job = Start();
            Pump(job.JobId, 3);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual("checkpoint_write_failed", job.RecoveryCode);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void LostReceiptCheckpoint_DoesNotResubmitAfterReload()
        {
            AssetGenJobManager.BeforeCheckpointWriteForTests = at =>
            {
                if (at == "polling")
                    throw new IOException("owned fixture storage failure");
            };
            AssetGenJob job = Start();
            Pump(job.JobId, 2);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            SimulateReload();
            Pump(job.JobId, 3);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(job.JobId).State);
        }

        [Test]
        public void ActiveCapacity_RejectsBeforeKeyReadAndNeverEvictsActiveMetadata()
        {
            var ids = new List<string>();
            for (int i = 0; i < 64; i++)
                ids.Add(Start().JobId);
            int reads = keys.Reads;
            Assert.Throws<InvalidOperationException>(() => Start());
            Assert.AreEqual(reads, keys.Reads);
            Assert.AreEqual(64, AssetGenJobManager.RecentJobs(1000).Count);
            foreach (string id in ids)
                Assert.IsNotNull(AssetGenJobManager.GetJob(id));
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void MeshyPreviewCompletingAfterDeadline_DoesNotSubmitRefine()
        {
            var cp = Checkpoint(provider: "meshy");
            cp.Meshy = new MeshyRecoveryState
            {
                Format = "obj",
                Model = "meshy-6",
                WantTexture = true,
            };
            AssetGenJob job = Inject(cp);
            http.Handler = request =>
            {
                Assert.AreEqual("GET", request.Method);
                now = 701;
                return Json(
                    new JObject
                    {
                        ["status"] = "SUCCEEDED",
                        ["model_urls"] = new JObject { ["obj"] = "https://example.invalid/owned.obj" },
                    }
                );
            };
            now = 699;
            Pump(job.JobId, 4);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            Assert.AreEqual("job_timeout", job.RecoveryCode);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
        }

        [Test]
        public void MeshyDeadlineCrossedDuringIntentWrite_StillBlocksRefinePost()
        {
            var cp = Checkpoint(provider: "meshy");
            cp.Meshy = new MeshyRecoveryState
            {
                Format = "obj",
                Model = "meshy-6",
                WantTexture = true,
            };
            AssetGenJob job = Inject(cp);
            http.Handler = _ => Json(new JObject { ["status"] = "SUCCEEDED" });
            now = 699;
            AssetGenJobManager.BeforeCheckpointWriteForTests = phase =>
            {
                if (phase == "refine_submitting")
                    now = 701;
            };
            Pump(job.JobId, 4);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            Assert.AreEqual("GET", http.RecordedRequests[0].Method);
            Assert.AreEqual("job_timeout", job.RecoveryCode);
        }

        [TestCase(float.NaN, false)]
        [TestCase(float.PositiveInfinity, false)]
        [TestCase(float.NegativeInfinity, false)]
        [TestCase(float.NaN, true)]
        [TestCase(float.PositiveInfinity, true)]
        [TestCase(float.NegativeInfinity, true)]
        public void NonfiniteTarget_CannotBecomeAResumableFiniteDefault(float target, bool marketplace)
        {
            AssetGenJob job = marketplace
                ? AssetGenJobManager.StartMarketplaceImport("49d97ca2fbf34f85b6c88ae8ebc7514f", target, "owned", output)
                : AssetGenJobManager.StartModelGeneration(
                    new ModelGenRequest
                    {
                        Provider = "meshy",
                        TargetSize = target,
                        Name = "owned",
                        OutputFolder = output,
                    }
                );
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual("invalid_target_size", job.ModelImportErrorCode);
            Assert.AreEqual(0, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
            SimulateReload();
            Pump(job.JobId, 3);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual(0, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
        }

        private AssetGenJob StartMeshy() =>
            AssetGenJobManager.StartModelGeneration(
                new ModelGenRequest
                {
                    Provider = "meshy",
                    Mode = "text",
                    Format = "obj",
                    Texture = true,
                    Prompt = "owned",
                    Name = "owned",
                    OutputFolder = output,
                }
            );

        [Test]
        public void MeshyReloadAtBothReceipts_SubmitsPreviewAndRefineOnceEach()
        {
            const string preview = "owned-preview";
            const string refine = "owned-refine";
            http.Handler = request =>
            {
                if (request.Method == "POST")
                {
                    JObject body = JObject.Parse(Encoding.UTF8.GetString(request.Body));
                    if ((string)body["mode"] == "preview")
                        return Json(new JObject { ["result"] = preview });
                    Assert.AreEqual("refine", (string)body["mode"]);
                    Assert.AreEqual(preview, (string)body["preview_task_id"]);
                    return Json(new JObject { ["result"] = refine });
                }
                Assert.AreEqual("GET", request.Method);
                if (request.Url.EndsWith("/" + preview, StringComparison.Ordinal))
                    return Json(
                        new JObject
                        {
                            ["status"] = "SUCCEEDED",
                            ["model_urls"] = new JObject { ["obj"] = "https://assets.meshy.ai/untextured-preview.obj" },
                        }
                    );
                Assert.IsTrue(request.Url.EndsWith("/" + refine, StringComparison.Ordinal));
                return Json(new JObject { ["status"] = "IN_PROGRESS", ["progress"] = 20 });
            };
            AssetGenJob job = StartMeshy();
            Pump(job.JobId, 2);
            SimulateReload();
            Pump(job.JobId, 2); // reconnect, preview GET and refine POST/checkpoint in its continuation
            JObject record = JObject.Parse(SessionState.GetString(Prefix + job.JobId, ""));
            Assert.AreEqual("refining", (string)record["_resume_v1"]["Phase"]);
            Assert.AreEqual(refine, (string)record["_resume_v1"]["Meshy"]["RefineId"]);
            SimulateReload();
            Pump(job.JobId, 4);
            Assert.AreEqual(2, http.RecordedRequests.Count(r => r.Method == "POST"), "One preview plus one refine; neither phase is replayed.");
            Assert.AreEqual(AssetGenJobState.Running, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual(refine, AssetGenJobManager.GetJob(job.JobId).ProviderTaskId);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Url.EndsWith("/" + preview, StringComparison.Ordinal)));
            Assert.IsTrue(http.RecordedRequests.Any(r => r.Url.EndsWith("/" + refine, StringComparison.Ordinal)));
            Assert.IsFalse(Directory.Exists(AssetGenPaths.ToAbsolute(output)), "The untextured preview must not be downloaded/imported.");
        }

        [TestCase("refine_submitting", 0)]
        [TestCase("refining", 1)]
        public void MeshyCheckpointFailure_DoesNotRepeatRefine(string failingPhase, int refinePosts)
        {
            var cp = Checkpoint(provider: "meshy");
            cp.Meshy = new MeshyRecoveryState
            {
                Format = "obj",
                Model = "meshy-6",
                WantTexture = true,
            };
            AssetGenJob job = Inject(cp);
            http.Handler = request =>
                request.Method == "POST" ? Json(new JObject { ["result"] = "owned-refine" }) : Json(new JObject { ["status"] = "SUCCEEDED" });
            AssetGenJobManager.BeforeCheckpointWriteForTests = phase =>
            {
                if (phase == failingPhase)
                    throw new IOException("owned checkpoint failure");
            };
            Pump(job.JobId, 4);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual("checkpoint_write_failed", job.RecoveryCode);
            Assert.AreEqual(refinePosts, http.RecordedRequests.Count(r => r.Method == "POST"));
            int requests = http.RecordedRequests.Count;
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            SimulateReload();
            Pump(job.JobId, 4);
            Assert.AreEqual(requests, http.RecordedRequests.Count);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(job.JobId).State);
        }

        [TestCase("model", "model_glb")]
        [TestCase("model", "model_urls.glb")]
        [TestCase("image", null)]
        [TestCase("audio", null)]
        public void FalReload_UsesRecordedQueueRouteWithoutCatalogOrPost(string kind, string outputField)
        {
            var cp = Checkpoint(provider: "fal", kind: kind);
            cp.Extension =
                kind == "model" ? "glb"
                : kind == "audio" ? "wav"
                : "png";
            cp.Subfolder =
                kind == "model" ? "Models"
                : kind == "audio" ? "Audio"
                : "Images";
            cp.FalOutputField = outputField;
            http.Handler = request =>
            {
                Assert.AreEqual("GET", request.Method);
                Assert.AreEqual(cp.Receipt + "/status", request.Url);
                return Json(new JObject { ["status"] = "IN_PROGRESS" });
            };
            AssetGenJob job = Inject(cp);
            Pump(job.JobId, 3);
            Assert.AreEqual(AssetGenJobState.Running, job.State, job.Error);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            Assert.AreEqual("owned-task", job.ProviderTaskId);
        }

        [Test]
        public void OpenRouterUnsavedResult_ReloadNeverResubmitsOrReadsKey()
        {
            var cp = Checkpoint("nonresumable", "openrouter", "image");
            cp.Receipt = null;
            AssetGenJob job = Inject(cp);
            Pump(job.JobId, 3);
            Assert.AreEqual("synchronous_result_unavailable_after_reload", job.RecoveryCode);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(0, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void MarketplaceReload_ResolvesSameUidByGetAndKeepsArchiveSelectorWithoutSignedUrl()
        {
            var cp = Checkpoint(provider: "sketchfab", kind: "marketplace");
            cp.Extension = "zip";
            cp.Subfolder = "Sketchfab";
            cp.Receipt = null;
            cp.MarketplaceUid = "owned-marketplace";
            AssetGenJob job = Inject(cp);
            job.ArchiveModelPath = "nested/main.obj";
            SessionState.SetString(Prefix + job.JobId, AssetGenJobCheckpoint.Project(job, cp).ToString(Formatting.None));
            SimulateReload();
            const string signed = "https://media.sketchfab.com/owned.zip?signature=GEN06_SIGNED_MARKER";
            http.Handler = request =>
            {
                Assert.AreEqual("GET", request.Method);
                Assert.AreEqual("https://api.sketchfab.com/v3/models/owned-marketplace/download", request.Url);
                return Json(new JObject { ["gltf"] = new JObject { ["url"] = signed } });
            };
            Pump(job.JobId, 3);
            AssetGenJob restored = AssetGenJobManager.GetJob(job.JobId);
            Assert.AreEqual(AssetGenJobState.Running, restored.State, restored.Error);
            Assert.AreEqual("nested/main.obj", restored.ArchiveModelPath);
            Assert.AreEqual("owned-marketplace", restored.ProviderTaskId);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            StringAssert.DoesNotContain("GEN06_SIGNED_MARKER", SessionState.GetString(Prefix + job.JobId, ""));
        }

        private void CreateOwnedFolder()
        {
            string abs = AssetGenPaths.ToAbsolute(output);
            Assert.IsFalse(Directory.Exists(abs) || File.Exists(abs) || File.Exists(abs + ".meta"));
            outputGuid = AssetDatabase.CreateFolder("Assets", output.Substring("Assets/".Length));
            Assert.IsNotEmpty(outputGuid);
        }

        private static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private AssetGenJobCheckpoint SavedCheckpoint(string provider = "tripo", bool register = false)
        {
            CreateOwnedFolder();
            bool image = provider == "openrouter";
            var cp = Checkpoint("saved", provider, image ? "image" : "model");
            if (image)
                cp.Receipt = null;
            byte[] bytes = image
                ? Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==")
                : Encoding.UTF8.GetBytes("o owned\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n");
            cp.SavedPath = output + "/owned." + cp.Extension;
            cp.SavedLength = bytes.LongLength;
            cp.SavedHash = Digest(bytes);
            File.WriteAllBytes(AssetGenPaths.ToAbsolute(cp.SavedPath), bytes);
            if (register)
                AssetDatabase.ImportAsset(cp.SavedPath, ImportAssetOptions.ForceSynchronousImport);
            string meta = AssetGenPaths.ToAbsolute(cp.SavedPath + ".meta");
            cp.MetadataPresent = File.Exists(meta);
            cp.MetadataLength = cp.MetadataPresent ? new FileInfo(meta).Length : 0;
            cp.MetadataHash = cp.MetadataPresent ? Digest(File.ReadAllBytes(meta)) : null;
            cp.RegisteredGuid = AssetDatabase.AssetPathToGUID(cp.SavedPath);
            if (string.IsNullOrEmpty(cp.RegisteredGuid))
                cp.RegisteredGuid = null;
            return cp;
        }

        [TestCase("tripo", false)]
        [TestCase("tripo", true)]
        [TestCase("openrouter", false)]
        public void SavedLocalOutput_ImportsOnceWithoutCredentialOrProvider(string provider, bool registered)
        {
            AssetGenJobCheckpoint cp = SavedCheckpoint(provider, registered);
            byte[] original = File.ReadAllBytes(AssetGenPaths.ToAbsolute(cp.SavedPath));
            keys.Available = false;
            int imports = 0;
            AssetGenJobManager.ImportOverrideForTests = (job, path) =>
            {
                Assert.AreEqual(cp.SavedPath, path);
                imports++;
                job.AssetPath = path;
                return job;
            };
            AssetGenJob job = Inject(cp);
            Pump(job.JobId, 4);
            Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
            Assert.AreEqual(1, imports);
            Assert.AreEqual(0, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(AssetGenPaths.ToAbsolute(cp.SavedPath)));
        }

        [TestCase("bytes")]
        [TestCase("missing")]
        [TestCase("new_meta")]
        [TestCase("replaced_meta")]
        [TestCase("missing_meta")]
        [TestCase("changed_guid")]
        public void ChangedSavedOwnership_RefusesImportAndPreservesObservedBytes(string change)
        {
            var cp = SavedCheckpoint(register: change == "replaced_meta" || change == "missing_meta" || change == "changed_guid");
            string path = AssetGenPaths.ToAbsolute(cp.SavedPath);
            string meta = path + ".meta";
            if (change == "bytes")
                File.AppendAllText(path, "# changed\n");
            if (change == "missing")
                File.Delete(path);
            if (change == "new_meta")
                File.WriteAllText(meta, "fileFormatVersion: 2\nguid: " + Guid.NewGuid().ToString("N") + "\n");
            if (change == "replaced_meta")
                File.AppendAllText(meta, "\n# identity changed\n");
            if (change == "missing_meta")
                File.Delete(meta);
            if (change == "changed_guid")
            {
                string changed = File.ReadAllText(meta).Replace(cp.RegisteredGuid, Guid.NewGuid().ToString("N"));
                File.WriteAllText(meta, changed);
                AssetDatabase.ImportAsset(cp.SavedPath, ImportAssetOptions.ForceSynchronousImport);
                Assert.AreNotEqual(cp.RegisteredGuid, AssetDatabase.AssetPathToGUID(cp.SavedPath));
            }
            byte[] before = File.Exists(path) ? File.ReadAllBytes(path) : null;
            AssetGenJob job = Inject(cp);
            Pump(job.JobId, 4);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(change == "missing" ? "saved_asset_missing" : "saved_asset_identity_changed", job.RecoveryCode);
            Assert.IsEmpty(http.RecordedRequests);
            Assert.AreEqual(0, keys.Reads);
            if (before != null)
                CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
        }

        [TestCase("version")]
        [TestCase("identity")]
        [TestCase("oversized")]
        [TestCase("unsafe_receipt")]
        [TestCase("saved_escape")]
        [TestCase("disallowed_extension")]
        [TestCase("unused_saved_path")]
        [TestCase("unused_saved_metadata")]
        public void InvalidCheckpoint_FailsClosedWithoutCredentials(string fault)
        {
            var cp = Checkpoint();
            var record = AssetGenJobCheckpoint.Project(
                new AssetGenJob
                {
                    JobId = cp.JobId,
                    Kind = cp.Kind,
                    Provider = cp.Provider,
                    State = AssetGenJobState.Running,
                },
                cp
            );
            if (fault == "version")
                record["_resume_v1"]["schema_version"] = 999;
            if (fault == "identity")
                record["_resume_v1"]["JobId"] = Guid.NewGuid().ToString("N");
            if (fault == "oversized")
                record["_resume_v1"]["Name"] = new string('x', 17000);
            if (fault == "unsafe_receipt")
                record["_resume_v1"]["Receipt"] = "https://example.invalid/task?signature=SECRET";
            if (fault == "saved_escape")
            {
                record["_resume_v1"]["Phase"] = "saved";
                record["_resume_v1"]["SavedPath"] = "Assets/../foreign.obj";
                record["_resume_v1"]["SavedLength"] = 1;
                record["_resume_v1"]["SavedHash"] = new string('a', 64);
            }
            if (fault == "disallowed_extension")
                record["_resume_v1"]["Extension"] = "cs";
            if (fault == "unused_saved_path")
                record["_resume_v1"]["SavedPath"] = "https://example.invalid/file?signature=GEN06_UNUSED_SECRET";
            if (fault == "unused_saved_metadata")
                record["_resume_v1"]["MetadataHash"] = "GEN06_UNUSED_SECRET";
            SessionState.SetString(Prefix + cp.JobId, record.ToString(Formatting.None));
            SessionState.SetString(Index, cp.JobId);
            AssetGenJobManager.RestoreJobs();
            Pump(cp.JobId, 4);
            AssetGenJob job = AssetGenJobManager.GetJob(cp.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual("checkpoint_invalid", job.RecoveryCode);
            Assert.AreEqual(0, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
            StringAssert.DoesNotContain("GEN06_UNUSED_SECRET", SessionState.GetString(Prefix + cp.JobId, ""));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PollingCheckpoint_UnsafeInactiveIdentityCannotBePublished(bool metadata)
        {
            var cp = Checkpoint();
            if (metadata)
                cp.MetadataHash = "GEN06_UNUSED_SECRET";
            else
                cp.SavedPath = "https://example.invalid/file?signature=GEN06_UNUSED_SECRET";
            var job = new AssetGenJob
            {
                JobId = cp.JobId,
                Kind = cp.Kind,
                Provider = cp.Provider,
                State = AssetGenJobState.Running,
            };
            Assert.Throws<InvalidDataException>(() => AssetGenJobCheckpoint.Project(job, cp));
            Assert.IsEmpty(SessionState.GetString(Prefix + cp.JobId, ""));
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void UnsafeLiveReceipt_RemainsAbsentFromEntirePersistedRecord()
        {
            const string unsafeId = "GEN06_PRIVATE_RECEIPT?signature=GEN06_SECRET";
            http.Handler = _ =>
                Json(
                    new JObject
                    {
                        ["code"] = 0,
                        ["data"] = new JObject { ["task_id"] = unsafeId },
                    }
                );
            AssetGenJob job = Start();
            Pump(job.JobId, 2);
            string record = SessionState.GetString(Prefix + job.JobId, "");
            StringAssert.DoesNotContain(unsafeId, record);
            StringAssert.DoesNotContain("GEN06_SECRET", record);
            SimulateReload();
            Pump(job.JobId, 4);
            Assert.AreEqual("generation_failed", AssetGenJobManager.GetJob(job.JobId).RecoveryCode);
            Assert.AreEqual(1, http.RecordedRequests.Count);
        }

        private AssetGenJob DownloadOwnedObj()
        {
            var cp = Checkpoint(provider: "meshy");
            cp.Meshy = new MeshyRecoveryState
            {
                Format = "obj",
                Model = "meshy-6",
                WantTexture = false,
            };
            http.Handler = request =>
                request.Url.EndsWith("/" + TaskId, StringComparison.Ordinal)
                    ? Json(
                        new JObject
                        {
                            ["status"] = "SUCCEEDED",
                            ["model_urls"] = new JObject { ["obj"] = "https://assets.meshy.ai/owned.obj" },
                        }
                    )
                : request.Url == "https://assets.meshy.ai/owned.obj"
                    ? new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = Encoding.UTF8.GetBytes("o owned\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"),
                    }
                : throw new AssertionException("Unexpected fake request " + request.Method);
            return Inject(cp);
        }

        [TestCase("orphan_meta")]
        [TestCase("registered_only")]
        [TestCase("directory")]
        [TestCase("all_slots")]
        public void OutputAdmission_PreservesOccupiedSlotsAndNeverAdoptsTheirIdentity(string occupancy)
        {
            CreateOwnedFolder();
            string path = output + "/owned.obj";
            string abs = AssetGenPaths.ToAbsolute(path);
            byte[] original = Encoding.UTF8.GetBytes("fileFormatVersion: 2\nguid: " + Guid.NewGuid().ToString("N") + "\n");
            if (occupancy == "directory")
                Directory.CreateDirectory(abs);
            else if (occupancy == "registered_only")
            {
                File.WriteAllText(abs, "o owned\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n");
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(path));
                File.Delete(abs);
                File.Delete(abs + ".meta");
                Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(path), "The owned fixture must actually exercise registered-only occupancy before refresh.");
            }
            else
            {
                int slots = occupancy == "all_slots" ? 64 : 1;
                for (int i = 0; i < slots; i++)
                    File.WriteAllBytes(AssetGenPaths.ToAbsolute(output + "/owned" + (i == 0 ? "" : "_" + i) + ".obj.meta"), original);
            }
            int imports = 0;
            AssetGenJobManager.ImportOverrideForTests = (job, imported) =>
            {
                imports++;
                job.AssetPath = imported;
                return job;
            };
            AssetGenJob generated = DownloadOwnedObj();
            Pump(generated.JobId, 8);
            Assert.AreEqual(occupancy == "all_slots" ? AssetGenJobState.Failed : AssetGenJobState.Done, generated.State, generated.Error);
            Assert.AreEqual(occupancy == "all_slots" ? 0 : 1, imports);
            if (occupancy != "all_slots")
                Assert.AreEqual(output + "/owned_1.obj", generated.AssetPath);
            Assert.IsFalse(File.Exists(abs));
            if (occupancy == "directory")
                Assert.IsTrue(Directory.Exists(abs));
            if (occupancy == "orphan_meta" || occupancy == "all_slots")
                CollectionAssert.AreEqual(original, File.ReadAllBytes(abs + ".meta"));
            if (occupancy == "all_slots")
                Assert.AreEqual(64, Directory.GetFiles(AssetGenPaths.ToAbsolute(output)).Length);
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [Test]
        public void OutputAdmission_DanglingMetadataLinkRefusesWithoutCreatingTargetOrImport()
        {
            CreateOwnedFolder();
            string abs = AssetGenPaths.ToAbsolute(output + "/owned.obj");
            string link = abs + ".meta";
            string target = AssetGenPaths.ToAbsolute(output + "/owned-missing-target");
            bool created = false;
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                created = Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(link, target, 2) : symlink(target, link) == 0;
                if (!created)
                    Assert.Ignore("Owned symlink fixture unavailable: " + Marshal.GetLastWin32Error());
                int imports = 0;
                AssetGenJobManager.ImportOverrideForTests = (job, path) =>
                {
                    imports++;
                    return job;
                };
                AssetGenJob job = DownloadOwnedObj();
                Pump(job.JobId, 8);
                Assert.AreEqual(AssetGenJobState.Failed, job.State);
                Assert.AreEqual(0, imports);
                Assert.IsFalse(File.Exists(abs));
                Assert.IsFalse(File.Exists(target));
                Assert.AreNotEqual((FileAttributes)0, File.GetAttributes(link) & FileAttributes.ReparsePoint);
            }
            finally
            {
                if (created)
                    File.Delete(link); // Unlink the exact owned path before ordinary folder teardown.
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SavedOutput_LinkSubstitutionRefusesImportWithoutChangingOwnedTarget(bool metadata)
        {
            var cp = SavedCheckpoint(register: metadata);
            string source = AssetGenPaths.ToAbsolute(cp.SavedPath + (metadata ? ".meta" : ""));
            string target = AssetGenPaths.ToAbsolute(output + "/owned-link-target.bin");
            byte[] original = File.ReadAllBytes(source);
            bool created = false;
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                File.WriteAllBytes(target, original);
                File.Delete(source);
                created = Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(source, target, 2) : symlink(target, source) == 0;
                if (!created)
                    Assert.Ignore("Owned symlink fixture unavailable: " + Marshal.GetLastWin32Error());
                int imports = 0;
                AssetGenJobManager.ImportOverrideForTests = (job, path) =>
                {
                    imports++;
                    return job;
                };
                AssetGenJob job = Inject(cp);
                Pump(job.JobId, 4);
                Assert.AreEqual(AssetGenJobState.Failed, job.State);
                Assert.AreEqual(0, imports);
                Assert.AreEqual(0, keys.Reads);
                Assert.IsEmpty(http.RecordedRequests);
                CollectionAssert.AreEqual(original, File.ReadAllBytes(target));
                Assert.AreNotEqual((FileAttributes)0, File.GetAttributes(source) & FileAttributes.ReparsePoint);
            }
            finally
            {
                if (created)
                    File.Delete(source);
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [TestCase("duplicate")]
        [TestCase("deep")]
        [TestCase("large_record")]
        [TestCase("duplicate_property")]
        public void InvalidFirstRecord_NeverResumesAndKeepsLaterTerminalRecord(string defect)
        {
            var cp = Checkpoint();
            var record = AssetGenJobCheckpoint.Project(
                new AssetGenJob
                {
                    JobId = cp.JobId,
                    Kind = cp.Kind,
                    Provider = cp.Provider,
                    State = AssetGenJobState.Running,
                },
                cp
            );
            string json = record.ToString(Formatting.None);
            if (defect == "deep")
                json = json.Insert(json.Length - 1, ",\"extra\":" + new string('[', 20) + "0" + new string(']', 20));
            if (defect == "large_record")
                json = json.Insert(json.Length - 1, ",\"extra\":\"" + new string('x', AssetGenJobCheckpoint.MaxRecordBytes) + "\"");
            if (defect == "duplicate_property")
                json = json.Insert(json.Length - 1, ",\"JobId\":\"" + cp.JobId + "\"");
            string terminal = Guid.NewGuid().ToString("N");
            SessionState.SetString(Prefix + cp.JobId, json);
            SessionState.SetString(Prefix + terminal, JsonConvert.SerializeObject(new AssetGenJob { JobId = terminal, State = AssetGenJobState.Done }));
            SessionState.SetString(Index, cp.JobId + (defect == "duplicate" ? "," + cp.JobId : "") + "," + terminal);
            AssetGenJobManager.RestoreJobs();
            Pump(cp.JobId, 4);
            Assert.AreEqual(AssetGenJobState.Done, AssetGenJobManager.GetJob(terminal).State);
            AssetGenJob invalid = AssetGenJobManager.GetJob(cp.JobId);
            Assert.IsTrue(invalid == null || invalid.State == AssetGenJobState.Failed);
            Assert.AreEqual(0, keys.Reads);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [TestCase(AssetGenJobState.Running)]
        [TestCase(AssetGenJobState.Done)]
        [TestCase(AssetGenJobState.Failed)]
        [TestCase(AssetGenJobState.Canceled)]
        public void ProductionStatusHelper_ExposesRecoveryAndRetainedOutput(AssetGenJobState state)
        {
            AssetGenJob job = Inject(Checkpoint());
            job.State = state;
            job.AssetPath = output + "/owned.obj";
            job.AssetGuid = Guid.NewGuid().ToString("N");
            job.ModelImportStage = "registered";
            job.ModelImportErrorCode = "unusable_model";
            job.ArchiveSelection = new JObject { ["selected_path"] = "owned.obj" };
            JObject response = JObject.FromObject(AssetGenToolHelpers.Status(new ToolParams(new JObject { ["job_id"] = job.JobId }), "Model", 3));
            JObject data = (JObject)response["data"];
            Assert.AreEqual("resumed", (string)data["recovery_code"]);
            Assert.AreEqual(TaskId, (string)data["provider_task_id"]);
            Assert.AreEqual("poll", (string)data["provider_phase"]);
            if (state != AssetGenJobState.Running)
            {
                Assert.AreEqual(job.AssetPath, (string)data["asset_path"]);
                Assert.AreEqual(job.AssetGuid, (string)data["asset_guid"]);
                Assert.AreEqual("registered", (string)data["model_import_stage"]);
                Assert.IsTrue(JToken.DeepEquals(job.ArchiveSelection, data["archive_selection"]));
            }
        }

        [TestCase(404)]
        [TestCase(410)]
        public void ProviderMissingOrExpiredTask_RetainsReceiptWithoutResubmitting(int status)
        {
            http.Handler = _ => new HttpResult
            {
                Status = status,
                IsSuccess = false,
                Text = "owned expired task",
            };
            AssetGenJob job = Inject(Checkpoint());
            Pump(job.JobId, 4);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(TaskId, job.ProviderTaskId);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            Assert.AreEqual("GET", http.RecordedRequests[0].Method);
            SimulateReload();
            Pump(job.JobId, 3);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            Assert.AreEqual(TaskId, AssetGenJobManager.GetJob(job.JobId).ProviderTaskId);
        }

        private sealed class DeferredTripoUpload : IHttpTransport
        {
            internal readonly TaskCompletionSource<HttpResult> Upload = new();
            internal readonly List<HttpRequestSpec> Requests = new();

            public Task<HttpResult> SendAsync(HttpRequestSpec request, CancellationToken ct)
            {
                Requests.Add(request);
                return request.Url.EndsWith("/files", StringComparison.Ordinal)
                    ? Upload.Task
                    : Task.FromResult(
                        Json(
                            new JObject
                            {
                                ["code"] = 0,
                                ["data"] = new JObject { ["task_id"] = TaskId },
                            }
                        )
                    );
            }
        }

        [TestCase("cancel")]
        [TestCase("reload")]
        [TestCase("deadline")]
        [TestCase("checkpoint")]
        public void TripoUpload_ReturnedTokenCannotCrossGenerationAdmissionFence(string interruption)
        {
            CreateOwnedFolder();
            string image = output + "/private-reference.png";
            byte[] bytes = { 137, 80, 78, 71, 13, 10, 26, 10, 1 };
            File.WriteAllBytes(AssetGenPaths.ToAbsolute(image), bytes);
            var pending = new DeferredTripoUpload();
            AssetGenJobManager.TransportOverrideForTests = pending;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(
                new ModelGenRequest
                {
                    Provider = "tripo",
                    Mode = "image",
                    ImagePath = image,
                    AllowImageUpload = true,
                    Name = "owned-output",
                    OutputFolder = output,
                }
            );
            Pump(job.JobId, 1);
            Assert.AreEqual(1, pending.Requests.Count);
            Assert.AreEqual("https://openapi.tripo3d.ai/v3/files", pending.Requests[0].Url);
            string beforeReload = null;
            AssetGenJob restored = null;
            if (interruption == "cancel")
                Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            if (interruption == "reload")
            {
                SimulateReload();
                restored = AssetGenJobManager.GetJob(job.JobId);
                beforeReload = SessionState.GetString(Prefix + job.JobId, "");
            }
            if (interruption == "deadline")
                now = 100 + AssetGenJobManager.TimeoutSeconds + 1;
            if (interruption == "checkpoint")
                AssetGenJobManager.BeforeCheckpointWriteForTests = phase =>
                {
                    if (phase == "submitting")
                        throw new IOException("Owned post-upload intent storage failure.");
                };
            pending.Upload.SetResult(
                Json(
                    new JObject
                    {
                        ["code"] = 0,
                        ["data"] = new JObject { ["file_token"] = "gen09_private_upload_token" },
                    }
                )
            );
            Pump(job.JobId, 3);
            Assert.AreEqual(1, pending.Requests.Count, "An upload may survive locally canceled work; no generation POST may follow this fence.");
            AssetGenJob current = AssetGenJobManager.GetJob(job.JobId);
            Assert.AreEqual(interruption == "cancel" ? AssetGenJobState.Canceled : AssetGenJobState.Failed, current.State);
            if (interruption == "reload")
            {
                Assert.AreSame(restored, current);
                Assert.AreEqual(beforeReload, SessionState.GetString(Prefix + job.JobId, ""));
                Assert.AreEqual("submission_outcome_unknown", current.RecoveryCode);
            }
            if (interruption == "deadline")
                Assert.AreEqual("job_timeout", current.RecoveryCode);
            string record = SessionState.GetString(Prefix + job.JobId, "");
            StringAssert.DoesNotContain("gen09_private_upload_token", record);
            StringAssert.DoesNotContain(image, record);
            StringAssert.DoesNotContain(Secret, record);
            Assert.IsTrue(string.IsNullOrEmpty(current.ProviderTaskId));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(AssetGenPaths.ToAbsolute(image)));
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void TripoLegacyReceipt_ReloadPollsV3WithoutAnyUploadOrGeneration()
        {
            AssetGenJob job = Inject(Checkpoint());
            Pump(job.JobId, 4);
            Assert.IsNotEmpty(http.RecordedRequests);
            Assert.IsTrue(http.RecordedRequests.All(request => request.Method == "GET" && request.Url == "https://openapi.tripo3d.ai/v3/tasks/" + TaskId));
            Assert.AreEqual(TaskId, job.ProviderTaskId);
            Assert.AreEqual(AssetGenJobState.Running, job.State);
        }

        [Test]
        public void TripoV3Result_UsesProviderScopedDownloadAndPreservesGlbBytes()
        {
            CreateOwnedFolder();
            byte[] document = Encoding.UTF8.GetBytes("{\"asset\":{\"version\":\"2.0\"}} ");
            Assert.AreEqual(0, document.Length % 4);
            byte[] glb;
            using (var buffer = new MemoryStream())
            {
                using (var writer = new BinaryWriter(buffer, Encoding.UTF8, true))
                {
                    writer.Write(0x46546C67u);
                    writer.Write(2u);
                    writer.Write((uint)(20 + document.Length));
                    writer.Write((uint)document.Length);
                    writer.Write(0x4E4F534Au);
                    writer.Write(document);
                }
                glb = buffer.ToArray();
            }
            const string url = "https://cdn.tripo3d.ai/owned.glb?Signature=synthetic";
            int downloads = 0;
            int imports = 0;
            http.Handler = request =>
            {
                if (request.Url == url)
                {
                    downloads++;
                    Assert.AreEqual("tripo", request.DownloadProvider);
                    Assert.AreEqual("GET", request.Method);
                    Assert.IsFalse(request.Headers.ContainsKey("Authorization"));
                    return new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = glb,
                    };
                }
                return Json(
                    new JObject
                    {
                        ["code"] = 0,
                        ["data"] = new JObject
                        {
                            ["status"] = "success",
                            ["output"] = new JObject { ["model_url"] = url },
                        },
                    }
                );
            };
            AssetGenJobManager.ImportOverrideForTests = (job, path) =>
            {
                imports++;
                job.AssetPath = path;
                return job;
            };
            AssetGenJobCheckpoint checkpoint = Checkpoint();
            checkpoint.Extension = "glb";
            AssetGenJob restored = Inject(checkpoint);
            Pump(restored.JobId, 10);
            Assert.AreEqual(AssetGenJobState.Done, restored.State, restored.Error);
            Assert.AreEqual(1, downloads);
            Assert.AreEqual(1, imports);
            CollectionAssert.AreEqual(glb, File.ReadAllBytes(AssetGenPaths.ToAbsolute(restored.AssetPath)));
            Assert.IsTrue(http.RecordedRequests.All(request => request.Method == "GET"));
        }

        private sealed class DeferredRefineTransport : IHttpTransport
        {
            internal readonly TaskCompletionSource<HttpResult> Refine = new();
            internal readonly List<HttpRequestSpec> Requests = new();

            public Task<HttpResult> SendAsync(HttpRequestSpec request, CancellationToken ct)
            {
                Requests.Add(request);
                return request.Method == "POST" ? Refine.Task : Task.FromResult(Json(new JObject { ["status"] = "SUCCEEDED" }));
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LateRefineReceipt_AfterCancelOrReloadCannotReviveOrRepublish(bool cancel)
        {
            var pending = new DeferredRefineTransport();
            AssetGenJobManager.TransportOverrideForTests = pending;
            var cp = Checkpoint(provider: "meshy");
            cp.Meshy = new MeshyRecoveryState
            {
                Format = "obj",
                Model = "meshy-6",
                WantTexture = true,
            };
            AssetGenJob original = Inject(cp);
            Pump(original.JobId, 2);
            Assert.AreEqual(1, pending.Requests.Count(r => r.Method == "POST"));
            if (cancel)
                Assert.IsTrue(AssetGenJobManager.Cancel(original.JobId));
            SimulateReload();
            AssetGenJob restored = AssetGenJobManager.GetJob(original.JobId);
            Assert.AreEqual(cancel ? AssetGenJobState.Canceled : AssetGenJobState.Failed, restored.State);
            if (!cancel)
                Assert.AreEqual("refine_outcome_unknown", restored.RecoveryCode);
            string before = SessionState.GetString(Prefix + original.JobId, "");
            pending.Refine.SetResult(Json(new JObject { ["result"] = "owned-late-refine" }));
            Pump(original.JobId, 4);
            Assert.AreSame(restored, AssetGenJobManager.GetJob(original.JobId));
            Assert.AreEqual(before, SessionState.GetString(Prefix + original.JobId, ""));
            Assert.IsEmpty(http.RecordedRequests);
            Assert.IsFalse(Directory.Exists(AssetGenPaths.ToAbsolute(output)));
        }

        private sealed class DeferredTransport : IHttpTransport
        {
            internal readonly TaskCompletionSource<HttpResult> Pending = new();
            internal int Calls;

            public Task<HttpResult> SendAsync(HttpRequestSpec request, CancellationToken ct)
            {
                Calls++;
                return Pending.Task;
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ReloadInsideReceiptAcceptance_UsesLastAcceptedIntentAndNeverRepeatsPost(bool refine)
        {
            AssetGenJob job;
            TaskCompletionSource<HttpResult> response;
            Func<int> postCount;
            if (refine)
            {
                var transport = new DeferredRefineTransport();
                AssetGenJobManager.TransportOverrideForTests = transport;
                var cp = Checkpoint(provider: "meshy");
                cp.Meshy = new MeshyRecoveryState
                {
                    Format = "obj",
                    Model = "meshy-6",
                    WantTexture = true,
                };
                job = Inject(cp);
                Pump(job.JobId, 2);
                response = transport.Refine;
                postCount = () => transport.Requests.Count(r => r.Method == "POST");
            }
            else
            {
                var transport = new DeferredTransport();
                AssetGenJobManager.TransportOverrideForTests = transport;
                job = Start();
                Pump(job.JobId, 1);
                response = transport.Pending;
                postCount = () => transport.Calls;
            }
            Assert.AreEqual(1, postCount());
            string expectedIntent = refine ? "refine_submitting" : "submitting";
            Assert.AreEqual(expectedIntent, (string)JObject.Parse(SessionState.GetString(Prefix + job.JobId, ""))["_resume_v1"]["Phase"]);
            bool interrupted = false;
            AssetGenJobManager.BeforeCheckpointWriteForTests = phase =>
            {
                if (phase != (refine ? "refining" : "polling"))
                    return;
                interrupted = true;
                AssetGenJobManager.ResetTransientForTests(); // Discard the returned receipt before storage accepts it.
                throw new IOException("Owned reload at unrecorded receipt boundary.");
            };
            response.SetResult(
                refine
                    ? Json(new JObject { ["result"] = "owned-unrecorded-refine" })
                    : Json(
                        new JObject
                        {
                            ["code"] = 0,
                            ["data"] = new JObject { ["task_id"] = "owned-unrecorded-submit" },
                        }
                    )
            );
            Assert.IsTrue(interrupted, "The real adapter await continuation must reach the acceptance boundary.");
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            Configure();
            AssetGenJobManager.RestoreJobs();
            Pump(job.JobId, 4);
            AssetGenJob restored = AssetGenJobManager.GetJob(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, restored.State);
            Assert.AreEqual(refine ? "refine_outcome_unknown" : "submission_outcome_unknown", restored.RecoveryCode);
            Assert.AreEqual("outcome_unknown", restored.RecoveryState);
            Assert.AreEqual(1, postCount());
            Assert.IsEmpty(http.RecordedRequests);
            StringAssert.DoesNotContain("owned-unrecorded", SessionState.GetString(Prefix + job.JobId, ""));
        }

        [Test]
        public void OldIgnoredCancellationCompletion_CannotOverwriteRestoredJob()
        {
            var deferred = new DeferredTransport();
            AssetGenJobManager.TransportOverrideForTests = deferred;
            AssetGenJob original = Start();
            Pump(original.JobId, 1);
            SimulateReload();
            AssetGenJob restored = AssetGenJobManager.GetJob(original.JobId);
            Assert.AreEqual("submission_outcome_unknown", restored.RecoveryCode);
            string before = SessionState.GetString(Prefix + original.JobId, "");
            deferred.Pending.SetResult(
                Json(
                    new JObject
                    {
                        ["code"] = 0,
                        ["data"] = new JObject { ["task_id"] = TaskId },
                    }
                )
            );
            Pump(original.JobId, 4);
            Assert.AreSame(restored, AssetGenJobManager.GetJob(original.JobId));
            Assert.AreEqual(before, SessionState.GetString(Prefix + original.JobId, ""));
            Assert.AreEqual(1, deferred.Calls);
            Assert.IsEmpty(http.RecordedRequests);
        }
    }

    public class AssetGenActualReloadTests
    {
        [UnityTest, Explicit("Sole-QA disposable project only; requires exact UNITY_MCP_GEN06_RELOAD_PROJECT and reviewed reload bootstrap.")]
        public IEnumerator ActualDomainReload_ResumesRecordedTaskWithoutResubmit()
        {
            try
            {
                Gen06ReloadProbe.Begin();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                // Iterator locals do not survive reload; all retained probe state is under the owned SessionState prefix.
                for (int step = 0; step < 200 && !Gen06ReloadProbe.Terminal(); step++)
                    yield return null;
                Gen06ReloadProbe.Verify();
            }
            finally
            {
                Gen06ReloadProbe.Cleanup();
            }
        }
    }

    [InitializeOnLoad]
    internal static class Gen06ReloadProbe
    {
        private const string Prefix = "MCPForUnity.Gen06OwnedReloadProbe.";
        private const string JobPrefix = "MCPForUnity.AssetGen.Job.";
        private const string Index = "MCPForUnity.AssetGen.JobIndex";
        private const string Receipt = "gen06-owned-preview";
        private const string ModelUrl = "https://assets.meshy.ai/gen06-owned.obj";
        private const string Obj = "o owned\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n";
        private static readonly string DomainNonce = Guid.NewGuid().ToString("N");
        private static readonly string[] Strings =
        {
            "nonce",
            "root",
            "guid",
            "before",
            "witness",
            "bootstrap",
            "job",
            "deadline",
            "posts",
            "polls",
            "downloads",
            "resumed",
            "error",
        };

        static Gen06ReloadProbe()
        {
            AssemblyReloadEvents.beforeAssemblyReload += ObserveReload;
            if (Enabled())
                Configure();
        }

        private static string Read(string key) => SessionState.GetString(Prefix + key, "");

        private static void Store(string key, string value) => SessionState.SetString(Prefix + key, value);

        private static int Count(string key) => int.TryParse(Read(key), out int value) ? value : 0;

        private static void Increment(string key) => Store(key, (Count(key) + 1).ToString(System.Globalization.CultureInfo.InvariantCulture));

        private static string Project() => Directory.GetParent(Application.dataPath).FullName;

        private static bool Enabled() =>
            SessionState.GetBool(Prefix + "active", false)
            && Environment.GetEnvironmentVariable("UNITY_MCP_GEN06_RELOAD_PROJECT") == Project()
            && AssetGenJobCheckpoint.Hex(Read("nonce"), 32)
            && Read("root") == "Assets/gen06_reload_" + Read("nonce");

        private sealed class Keys : ISecureKeyStore
        {
            public bool TryGet(string provider, out string key)
            {
                if (!Enabled() || provider != "meshy")
                    throw new InvalidOperationException("Owned reload key boundary rejected.");
                key = "gen06-owned-bootstrap-key";
                return true;
            }

            public bool Has(string provider) => provider == "meshy";

            public void Set(string provider, string key) => throw new InvalidOperationException("No key-store writes.");

            public void Delete(string provider) => throw new InvalidOperationException("No key-store writes.");
        }

        private static void Configure()
        {
            // Every InitializeOnLoad constructor completes before update callbacks. No default store/transport is consulted here.
            SecureKeyStore.OverrideForTests(new Keys());
            AssetGenJobManager.TransportOverrideForTests = new FakeHttpTransport { Handler = Respond };
            AssetGenJobManager.ImportOverrideForTests = (job, path) =>
            {
                if (!Enabled() || !path.StartsWith(Read("root") + "/", StringComparison.Ordinal))
                    throw new InvalidOperationException("Owned import boundary rejected.");
                return ModelImportPipeline.ImportInto(job, path, false);
            };
            AssetGenJobManager.PollIntervalSeconds = 0;
            AssetGenJobManager.ClockForTests = null;
            AssetGenJobManager.SkipModelVerificationForTests = true;
            Store("bootstrap", DomainNonce);
        }

        private static HttpResult Respond(HttpRequestSpec request)
        {
            if (!Enabled() || Read("bootstrap") != DomainNonce)
                throw new InvalidOperationException("Fake bootstrap did not precede HTTP.");
            if (request.Method == "POST" && request.Url == "https://api.meshy.ai/openapi/v2/text-to-3d")
            {
                if (DomainNonce != Read("before") || Count("posts") != 0)
                    throw new InvalidOperationException("Recovery attempted another generation POST.");
                Increment("posts");
                return Json(new JObject { ["result"] = Receipt });
            }
            if (request.Method == "GET" && request.Url == "https://api.meshy.ai/openapi/v2/text-to-3d/" + Receipt)
            {
                Increment("polls");
                if (DomainNonce == Read("before"))
                    return Json(new JObject { ["status"] = "IN_PROGRESS", ["progress"] = 25 });
                AssetGenJob job = AssetGenJobManager.GetJob(Read("job"));
                if (job == null || job.RecoveryCode != "resumed")
                    throw new InvalidOperationException("The recorded job did not actually resume.");
                JObject record = JObject.Parse(SessionState.GetString(JobPrefix + job.JobId, ""));
                if (record["_resume_v1"]?["Deadline"]?.ToString(Formatting.None) != Read("deadline"))
                    throw new InvalidOperationException("Reload reset the original deadline.");
                Store("resumed", DomainNonce);
                return Json(
                    new JObject
                    {
                        ["status"] = "SUCCEEDED",
                        ["model_urls"] = new JObject { ["obj"] = ModelUrl },
                    }
                );
            }
            if (request.Method == "GET" && request.Url == ModelUrl && Read("resumed") == DomainNonce)
            {
                Increment("downloads");
                return new HttpResult
                {
                    Status = 200,
                    IsSuccess = true,
                    Body = Encoding.UTF8.GetBytes(Obj),
                };
            }
            throw new InvalidOperationException("Unexpected request rejected by the owned fake transport.");
        }

        private static HttpResult Json(JObject value) =>
            new HttpResult
            {
                Status = 200,
                IsSuccess = true,
                Text = value.ToString(Formatting.None),
            };

        private static void ObserveReload()
        {
            if (Enabled())
                Store("witness", DomainNonce);
        }

        internal static void Begin()
        {
            Assert.AreEqual(Project(), Environment.GetEnvironmentVariable("UNITY_MCP_GEN06_RELOAD_PROJECT"), "Only sole QA may opt in its disposable project.");
            Assert.IsFalse(SessionState.GetBool(Prefix + "active", false), "An earlier probe must be inspected, not overwritten.");
            Assert.IsEmpty(SessionState.GetString(Index, ""), "Actual reload requires an empty owned generation history.");
            string nonce = Guid.NewGuid().ToString("N");
            string root = "Assets/gen06_reload_" + nonce;
            string absolute = AssetGenPaths.ToAbsolute(root);
            Assert.IsFalse(Directory.Exists(absolute) || File.Exists(absolute) || File.Exists(absolute + ".meta"));
            Store("nonce", nonce);
            Store("root", root);
            Store("before", DomainNonce);
            Store("witness", "");
            Store("posts", "0");
            Store("polls", "0");
            Store("downloads", "0");
            Store("resumed", "");
            Store("guid", AssetDatabase.CreateFolder("Assets", root.Substring("Assets/".Length)));
            Assert.IsNotEmpty(Read("guid"));
            SessionState.SetBool(Prefix + "active", true);
            Configure();
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(
                new ModelGenRequest
                {
                    Provider = "meshy",
                    Mode = "text",
                    Prompt = "owned native fixture",
                    Format = "obj",
                    Texture = false,
                    Name = "owned-model",
                    OutputFolder = root,
                    TargetSize = 0,
                }
            );
            Store("job", job.JobId);
            for (int step = 0; step < 2; step++)
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(1, Count("posts"));
            Assert.AreEqual(AssetGenJobState.Running, job.State, job.Error);
            JObject record = JObject.Parse(SessionState.GetString(JobPrefix + job.JobId, ""));
            Assert.AreEqual(Receipt, (string)record["_resume_v1"]?["Receipt"]);
            Store("deadline", record["_resume_v1"]["Deadline"].ToString(Formatting.None));
        }

        internal static bool Terminal()
        {
            AssetGenJob job = AssetGenJobManager.GetJob(Read("job"));
            return job != null && (job.State == AssetGenJobState.Done || job.State == AssetGenJobState.Failed || job.State == AssetGenJobState.Canceled);
        }

        internal static void Verify()
        {
            Assert.IsTrue(Enabled());
            Assert.AreNotEqual(Read("before"), DomainNonce, "A static reset alone is not actual domain reload evidence.");
            Assert.AreEqual(Read("before"), Read("witness"));
            Assert.AreEqual(DomainNonce, Read("bootstrap"));
            Assert.AreEqual(DomainNonce, Read("resumed"));
            Assert.AreEqual(1, Count("posts"));
            Assert.AreEqual(1, Count("downloads"));
            AssetGenJob job = AssetGenJobManager.GetJob(Read("job"));
            Assert.IsNotNull(job);
            Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
            Assert.AreEqual(Receipt, job.ProviderTaskId);
            Assert.AreEqual("usable", job.ModelImportStage);
            Assert.IsNotEmpty(job.AssetGuid);
            Assert.AreEqual(job.AssetGuid, AssetDatabase.AssetPathToGUID(job.AssetPath));
            StringAssert.StartsWith(Read("root") + "/", job.AssetPath);
            CollectionAssert.AreEqual(Encoding.UTF8.GetBytes(Obj), File.ReadAllBytes(AssetGenPaths.ToAbsolute(job.AssetPath)));
        }

        internal static void Cleanup()
        {
            if (!Enabled())
                return;
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.ResetForTests();
            string root = Read("root");
            Assert.AreEqual(Read("guid"), AssetDatabase.AssetPathToGUID(root), "Only the probe's exact owned folder may be deleted.");
            Assert.IsTrue(AssetDatabase.DeleteAsset(root));
            foreach (string key in Strings)
                SessionState.EraseString(Prefix + key);
            SessionState.EraseBool(Prefix + "active");
        }
    }
}
