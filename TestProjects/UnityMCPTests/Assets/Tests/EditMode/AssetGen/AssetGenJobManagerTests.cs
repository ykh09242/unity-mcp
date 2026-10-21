using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
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
    /// <summary>
    /// Drives the real TripoAdapter through FakeHttpTransport and pumps the job state machine
    /// deterministically (no editor loop, no network). Import is stubbed so no real model bytes
    /// are needed; the end-to-end test writes a tiny file under a throwaway Assets subfolder and
    /// cleans it up.
    /// </summary>
    public class AssetGenJobManagerTests
    {
        private const string EnvVar = "MCPFORUNITY_TRIPO_API_KEY";
        private const string Secret = "tsk_e2e_secret_value";
        private const string FalEnvVar = "MCPFORUNITY_FAL_API_KEY";
        private const string FalSecret = "fal_e2e_secret_value";
        private const string AudioResp = "https://queue.fal.run/fal-ai/stable-audio-25/text-to-audio/requests/r1";
        private const string TestFolder = "Assets/Generated/__assetgen_jobtest";

        private FakeHttpTransport _fake;

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            AssetGenModelCatalog.ResetForTests(true);
            AssetGenJobManager.SkipModelVerificationForTests = true;
            Environment.SetEnvironmentVariable(EnvVar, Secret);
            Environment.SetEnvironmentVariable(FalEnvVar, FalSecret);
            _fake = new FakeHttpTransport();
            AssetGenJobManager.TransportOverrideForTests = _fake;
            AssetGenJobManager.PollIntervalSeconds = 0;
            // Stub import: mark the asset path without touching AssetDatabase.
            AssetGenJobManager.ImportOverrideForTests = (job, path) =>
            {
                job.AssetPath = path;
                return job;
            };
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            Environment.SetEnvironmentVariable(EnvVar, null);
            Environment.SetEnvironmentVariable(FalEnvVar, null);
            try
            {
                // Output folders are registered through AssetFolderScope. Remove them through
                // Unity too, so the next test cannot observe a stale folder registration.
                AssetDatabase.DeleteAsset(TestFolder);
                string abs = Path.Combine(ProjectRoot(), TestFolder);
                if (Directory.Exists(abs))
                    Directory.Delete(abs, true);
                if (File.Exists(abs + ".meta"))
                    File.Delete(abs + ".meta");
            }
            catch { /* ignore */ }
        }

        private static string ProjectRoot()
        {
            string dp = Application.dataPath.Replace('\\', '/');
            return dp.Substring(0, dp.Length - "Assets".Length);
        }

        private static HttpResult Json(string body) =>
            new HttpResult
            {
                Status = 200,
                IsSuccess = true,
                Text = body,
            };

        private static ModelGenRequest Req() =>
            new ModelGenRequest
            {
                Provider = "tripo",
                Mode = "text",
                Prompt = "a low-poly oak tree",
                Format = "glb",
                TargetSize = 1f,
                Name = "jobtest",
                OutputFolder = TestFolder,
            };

        [TestCase(0f, false)]
        [TestCase(-2f, false)]
        [TestCase(2f, false)]
        [TestCase(0f, true)]
        [TestCase(-2f, true)]
        [TestCase(2f, true)]
        public void ModelTargetSize_PreservesNonpositiveAndPositiveRequests(float target, bool marketplace)
        {
            const string sketchfabEnv = "MCPFORUNITY_SKETCHFAB_API_KEY";
            // QA clears child key variables before this synthetic-key fixture; no actual value is read.
            Environment.SetEnvironmentVariable(sketchfabEnv, "owned-normalization-sketchfab-key");
            try
            {
                ModelGenRequest request = Req();
                request.TargetSize = target;
                AssetGenJob job = marketplace
                    ? AssetGenJobManager.StartMarketplaceImport("owned-model", target, "owned-target", TestFolder)
                    : AssetGenJobManager.StartModelGeneration(request);
                Assert.AreEqual(target, job.TargetSize);
                Assert.IsEmpty(_fake.RecordedRequests, "Creating the job must not call a real provider or dispatch before its controlled pump.");
            }
            finally
            {
                Environment.SetEnvironmentVariable(sketchfabEnv, null);
            }
        }

        [TestCase(true, 2)]
        [TestCase(false, 2)]
        [TestCase(true, 300)]
        public void ArchiveSelector_PropagatesToImportAndStatus(bool valid, int candidateCount)
        {
            const string key = "MCPFORUNITY_SKETCHFAB_API_KEY";
            Environment.SetEnvironmentVariable(key, "owned-archive-key");
            try
            {
                string observed = null;
                var evidence = new JObject
                {
                    ["archive_path"] = TestFolder + "/archive.zip",
                    ["requested_path"] = "main/model.obj",
                    ["selected_path"] = "main/model.obj",
                    ["candidates"] = new JArray("main/model.obj", "preview.obj"),
                    ["candidate_count"] = 2,
                    ["candidates_complete"] = true,
                    ["extraction"] = "complete",
                };
                var candidates = (JArray)evidence["candidates"];
                for (int i = candidates.Count; i < candidateCount; i++)
                    candidates.Add("model" + i + ".obj");
                evidence["candidate_count"] = candidateCount;
                AssetGenJobManager.ImportOverrideForTests = (job, path) =>
                {
                    observed = (string)JObject.FromObject(job)["ArchiveModelPath"];
                    typeof(AssetGenJob).GetField("ArchiveSelection")?.SetValue(job, evidence.DeepClone());
                    job.AssetPath = path;
                    if (!valid)
                    {
                        job.State = AssetGenJobState.Failed;
                        job.Error = "owned importer rejection";
                        job.ModelImportErrorCode = "unusable_model";
                    }
                    return job;
                };
                byte[] payload = ModelArchiveSelectionTests.ArchiveBytes(
                    ("main/model.obj", System.Text.Encoding.UTF8.GetBytes(ModelImportPipelineTests.TriangleObj))
                );
                _fake.Handler = spec =>
                    spec.DownloadProvider == null
                        ? Json("{\"gltf\":{\"url\":\"https://sketchfab-prod-media.s3.amazonaws.com/owned/archive.zip\"}}")
                        : new HttpResult
                        {
                            Status = 200,
                            IsSuccess = true,
                            Body = payload,
                        };
                var method = typeof(AssetGenJobManager).GetMethod(nameof(AssetGenJobManager.StartMarketplaceImport));
                object[] args =
                    method.GetParameters().Length == 5
                        ? new object[] { "owned-archive", 0f, "archive", TestFolder, "main/model.obj" }
                        : new object[] { "owned-archive", 0f, "archive", TestFolder };
                var result = (AssetGenJob)method.Invoke(null, args);
                Pump(result.JobId);
                Assert.AreEqual("main/model.obj", observed, result.State + ": " + result.Error);
                Assert.AreEqual(valid ? AssetGenJobState.Done : AssetGenJobState.Failed, result.State);
                JObject saved = JObject.Parse(SessionState.GetString("MCPForUnity.AssetGen.Job." + result.JobId, ""));
                Assert.IsTrue(JToken.DeepEquals(evidence, saved["ArchiveSelection"]));
                var request = new JObject { ["action"] = "status", ["job_id"] = result.JobId };
                JObject marketplace = JObject.FromObject(ImportModel.HandleCommand(request).GetAwaiter().GetResult());
                Assert.IsTrue(JToken.DeepEquals(evidence, marketplace["data"]["archive_selection"]), marketplace.ToString());
                JObject generation = JObject.FromObject(GenerateModel.HandleCommand(request));
                Assert.IsTrue(JToken.DeepEquals(evidence, generation["data"]["archive_selection"]));
                Assert.AreEqual(2, _fake.RecordedRequests.Count);
            }
            finally
            {
                Environment.SetEnvironmentVariable(key, null);
            }
        }

        [TestCase("../model.obj")]
        [TestCase("")]
        [TestCase("model.png")]
        public void ArchiveSelector_InvalidDoesNotSubmit(string selector)
        {
            const string key = "MCPFORUNITY_SKETCHFAB_API_KEY";
            Environment.SetEnvironmentVariable(key, "owned-archive-key");
            try
            {
                var method = typeof(AssetGenJobManager).GetMethod(nameof(AssetGenJobManager.StartMarketplaceImport));
                object[] args =
                    method.GetParameters().Length == 5
                        ? new object[] { "owned-archive", 0f, "archive", TestFolder, selector }
                        : new object[] { "owned-archive", 0f, "archive", TestFolder };
                Assert.Throws<System.Reflection.TargetInvocationException>(() => method.Invoke(null, args));
                Assert.IsEmpty(AssetGenJobManager.RecentJobs());
                Assert.IsEmpty(_fake.RecordedRequests);
            }
            finally
            {
                Environment.SetEnvironmentVariable(key, null);
            }
        }

        private static AudioGenRequest AudioReq() =>
            new AudioGenRequest
            {
                Provider = "fal",
                Prompt = "gentle rain",
                Name = "audiotest",
                OutputFolder = TestFolder,
            };

        private static void Pump(string jobId)
        {
            int guard = 0;
            while (!AssetGenJobManager.TryAdvanceForTests(jobId) && guard++ < 50) { }
            Assert.Less(guard, 50, "state machine did not reach a terminal state");
        }

        [TestCase("model", false)]
        [TestCase("image", false)]
        [TestCase("audio", false)]
        [TestCase("model", true)]
        [TestCase("image", true)]
        [TestCase("audio", true)]
        public void StartGeneration_FileConflictRejectsBeforeJobAllocation(string kind, bool configuredDefault)
        {
            string relative = "Assets/__McpJobOutput_" + Guid.NewGuid().ToString("N") + ".txt";
            string absolute = AssetGenPaths.ToAbsolute(relative);
            bool hadRoot = EditorPrefs.HasKey(EditorPrefKeys.AssetGenOutputRoot);
            string savedRoot = AssetGenPrefs.OutputRoot;
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                File.WriteAllText(absolute, "preserve");
                if (configuredDefault)
                    AssetGenPrefs.OutputRoot = relative;
                string output = configuredDefault ? null : relative + "/new/child";
                Assert.Throws<ArgumentException>(() =>
                {
                    if (kind == "model")
                    {
                        ModelGenRequest request = Req();
                        request.OutputFolder = output;
                        AssetGenJobManager.StartModelGeneration(request);
                    }
                    else if (kind == "image")
                        AssetGenJobManager.StartImageGeneration(
                            new ImageGenRequest
                            {
                                Provider = "fal",
                                Prompt = "fixture",
                                OutputFolder = output,
                            }
                        );
                    else
                    {
                        AudioGenRequest request = AudioReq();
                        request.OutputFolder = output;
                        AssetGenJobManager.StartAudioGeneration(request);
                    }
                });
                Assert.AreEqual(0, AssetGenJobManager.RecentJobs().Count);
                Assert.AreEqual(0, _fake.RecordedRequests.Count);
                Assert.AreEqual("preserve", File.ReadAllText(absolute));
            }
            finally
            {
                File.Delete(absolute);
                if (hadRoot)
                    AssetGenPrefs.OutputRoot = savedRoot;
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.AssetGenOutputRoot);
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [TestCase(false), TestCase(true)]
        public void ImageResult_JpegBytesUseJpgExtension_ForDownloadedAndInlineResults(bool inline)
        {
            byte[] jpeg = { 255, 216, 255, 224, 0, 16 };
            string env = "MCPFORUNITY_OPENROUTER_API_KEY";
            string old = Environment.GetEnvironmentVariable(env);
            Environment.SetEnvironmentVariable(env, "test-key");
            try
            {
                _fake.Handler = r =>
                    r.Method == "POST"
                        ? Json(
                            inline
                                ? "{\"choices\":[{\"message\":{\"images\":[{\"image_url\":{\"url\":\"data:image/png;base64,"
                                    + Convert.ToBase64String(jpeg)
                                    + "\"}}]}}]}"
                                : "{\"response_url\":\"https://queue.fal.run/test/image/requests/r1\"}"
                        )
                    : r.Url.EndsWith("/status") ? Json("{\"status\":\"COMPLETED\"}")
                    : r.Url.Contains("queue.fal.run") ? Json("{\"images\":[{\"url\":\"https://fal.media/image.png\",\"content_type\":\"image/png\"}]}")
                    : new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = jpeg,
                    };
                var job = AssetGenJobManager.StartImageGeneration(
                    new ImageGenRequest
                    {
                        Provider = inline ? "openrouter" : "fal",
                        Model = "test/image",
                        Prompt = "cat",
                        OutputFolder = TestFolder,
                    }
                );
                Pump(job.JobId);
                Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
                StringAssert.EndsWith(".jpg", job.AssetPath);
                Assert.AreEqual("jpg", job.Format);
            }
            finally
            {
                Environment.SetEnvironmentVariable(env, old);
            }
        }

        [Test]
        public void ProgressObservation_QueuedAndCancellationDoNotDispatchOrInventUnits()
        {
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            JObject Status() => JObject.FromObject(GenerateModel.HandleCommand(new JObject { ["action"] = "status", ["job_id"] = job.JobId }));
            JObject queued = Status();
            Assert.AreEqual("queued", (string)queued["data"]?["progress_detail"]?["phase"]);
            Assert.AreEqual(JTokenType.Null, queued["data"]["progress_detail"]["completed"].Type);
            Assert.IsEmpty(_fake.RecordedRequests);
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            JObject canceled = Status();
            Assert.IsTrue((bool)canceled["data"]["progress_detail"]["cancellation_requested"]);
            Assert.IsFalse((bool)canceled["data"]["progress_detail"]["active"]);
            Assert.IsEmpty(_fake.RecordedRequests);
        }

        [Test]
        public void ProgressObservation_ProviderWaitingNeverClaimsOverallPercentage()
        {
            _fake.Handler = spec =>
                Json(
                    spec.Method == "POST"
                        ? "{\"code\":0,\"data\":{\"task_id\":\"task_progress\"}}"
                        : "{\"code\":0,\"data\":{\"status\":\"running\",\"progress\":50}}"
                );
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            for (int i = 0; i < 5; i++)
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
            JObject status = JObject.FromObject(GenerateModel.HandleCommand(new JObject { ["action"] = "status", ["job_id"] = job.JobId }));
            Assert.AreEqual("waiting_provider", (string)status["data"]?["progress_detail"]?["phase"]);
            Assert.AreEqual(JTokenType.Null, status["data"]["progress_detail"]["total"].Type);
            Assert.IsFalse(((string)status["message"]).Contains("%"));
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
        }

        [Test]
        public void EndToEnd_ReachesDone_WithAssetPath()
        {
            AssetGenJobManager.ImportOverrideForTests = (importing, path) =>
            {
                Assert.AreEqual("importing", importing.ProgressEvidence.Read().phase);
                Assert.IsNull(importing.ProgressEvidence.Read().total);
                importing.AssetPath = path;
                return importing;
            };
            _fake.Handler = spec =>
            {
                if (spec.Method == "POST" && spec.Url.EndsWith("/v3/generation/text-to-model"))
                    return Json("{\"code\":0,\"data\":{\"task_id\":\"task_abc\"}}");
                if (spec.Url.Contains("/v3/tasks/"))
                    return Json(
                        "{\"code\":0,\"data\":{\"status\":\"success\",\"progress\":100,\"output\":{\"pbr_model\":\"https://tripo-data.rg1.data.tripo3d.com/model.glb\"}}}"
                    );
                return new HttpResult
                {
                    Status = 200,
                    IsSuccess = true,
                    Body = ModelImportPipelineTests.ContainerOnlyGlb(),
                }; // download
            };

            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            Pump(job.JobId);

            Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
            Assert.IsNotNull(job.AssetPath);
            StringAssert.EndsWith("jobtest.glb", job.AssetPath);
            Assert.AreEqual(1f, job.Progress);
            HttpRequestSpec download = _fake.RecordedRequests.Find(spec => spec.DownloadProvider != null);
            Assert.IsNotNull(download, "Artifacts must select the restricted download transport.");
            Assert.AreSame(job.ProgressEvidence, download.DownloadProgress);
            Assert.IsFalse(job.ProgressEvidence.Read().active);
            Assert.AreEqual("tripo", download.DownloadProvider);
            Assert.IsEmpty(download.Headers, "Provider API credentials must not be sent to the artifact host.");
        }

        [TestCase(true)]
        [TestCase(false)]
        public void Model_RealImport_PreservesUsableOrFailedEvidenceThroughStatus(bool valid)
        {
            const string meshyEnv = "MCPFORUNITY_MESHY_API_KEY";
            string previousKey = Environment.GetEnvironmentVariable(meshyEnv);
            bool previousLogs = LogAssert.ignoreFailingMessages;
            try
            {
                Environment.SetEnvironmentVariable(meshyEnv, "owned-meshy-test-key");
                AssetGenJobManager.ImportOverrideForTests = null;
                if (!valid)
                    LogAssert.ignoreFailingMessages = true; // Invalid owned OBJ produces a native diagnostic.
                byte[] bytes = System.Text.Encoding.UTF8.GetBytes(valid ? ModelImportPipelineTests.TriangleObj : "not a model");
                _fake.Handler = spec =>
                {
                    if (spec.Method == "POST")
                        return Json("{\"result\":\"owned-job\"}");
                    if (spec.DownloadProvider == null)
                        return Json("{\"status\":\"SUCCEEDED\",\"model_urls\":{\"obj\":\"https://assets.meshy.ai/model.obj\"}}");
                    return new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = bytes,
                    };
                };
                var request = Req();
                request.Provider = "meshy";
                request.Format = "obj";
                request.Texture = false;
                AssetGenJob job = AssetGenJobManager.StartModelGeneration(request);
                Pump(job.JobId);

                Assert.AreEqual(valid ? AssetGenJobState.Done : AssetGenJobState.Failed, job.State, job.Error);
                Assert.AreSame(job, AssetGenJobManager.GetJob(job.JobId));
                Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId), "Import result must remain terminal.");
                Assert.IsNotEmpty(job.AssetGuid);
                Assert.AreEqual(AssetDatabase.AssetPathToGUID(job.AssetPath), job.AssetGuid);
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(job.AssetPath));
                JObject persisted = JObject.Parse(SessionState.GetString("MCPForUnity.AssetGen.Job." + job.JobId, ""));
                Assert.AreEqual(valid ? "usable" : "registered", (string)persisted["ModelImportStage"]);
                if (!valid)
                    Assert.AreEqual("unusable_model", (string)persisted["ModelImportErrorCode"]);
                var parameters = new JObject { ["action"] = "status", ["job_id"] = job.JobId };
                JObject status = JObject.FromObject(GenerateModel.HandleCommand(parameters));
                Assert.AreEqual(valid, (bool)status["success"], status.ToString());
                Assert.AreEqual(valid ? "done" : "failed", (string)status["data"]["state"]);
                Assert.AreEqual(valid ? "usable" : "registered", (string)status["data"]["model_import_stage"]);
                Assert.AreEqual(job.AssetPath, (string)status["data"]["asset_path"]);
                Assert.AreEqual(job.AssetGuid, (string)status["data"]["asset_guid"]);
                if (!valid)
                    Assert.AreEqual("unusable_model", (string)status["data"]["model_import_error_code"]);

                // Marketplace polling must retain the same failed/successful import evidence.
                JObject marketplace = JObject.FromObject(ImportModel.HandleCommand(parameters).GetAwaiter().GetResult());
                Assert.AreEqual(valid, (bool)marketplace["success"], marketplace.ToString());
                Assert.AreEqual(job.AssetPath, (string)marketplace["data"]["asset_path"]);
                Assert.AreEqual(valid ? "usable" : "registered", (string)marketplace["data"]["model_import_stage"]);
                Assert.AreEqual(3, _fake.RecordedRequests.Count, "No recovery resubmission or external request is needed.");
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previousLogs;
                Environment.SetEnvironmentVariable(meshyEnv, previousKey);
            }
        }

        [Test]
        public void Audio_EndToEnd_ReachesDone_WithWavAssetPath_AndLeaksNoKey()
        {
            _fake.Handler = spec =>
            {
                if (spec.Method == "POST")
                    return Json("{\"response_url\":\"" + AudioResp + "\"}");
                if (spec.Url.EndsWith("/status"))
                    return Json("{\"status\":\"COMPLETED\"}");
                if (spec.Url.Contains("fal.media"))
                    return new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = new byte[] { 1, 2, 3, 4 },
                    };
                return Json("{\"audio_file\":{\"url\":\"https://fal.media/a.wav\"}}"); // result payload
            };

            AssetGenJob job = AssetGenJobManager.StartAudioGeneration(AudioReq());
            Pump(job.JobId);

            Assert.AreEqual(AssetGenJobState.Done, job.State);
            Assert.IsNotNull(job.AssetPath);
            StringAssert.EndsWith("audiotest.wav", job.AssetPath);
            Assert.AreEqual(1f, job.Progress);

            string serialized = JsonConvert.SerializeObject(job);
            StringAssert.DoesNotContain(FalSecret, serialized);
        }

        [Test]
        public void Audio_Mp3Result_DownloadsWithMp3Ext()
        {
            _fake.Handler = spec =>
            {
                if (spec.Method == "POST")
                    return Json("{\"response_url\":\"" + AudioResp + "\"}");
                if (spec.Url.EndsWith("/status"))
                    return Json("{\"status\":\"COMPLETED\"}");
                if (spec.Url.Contains("fal.media"))
                    return new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = new byte[] { 1, 2, 3, 4 },
                    };
                return Json("{\"audio_file\":{\"url\":\"https://fal.media/track.mp3\"}}");
            };

            AssetGenJob job = AssetGenJobManager.StartAudioGeneration(AudioReq());
            Pump(job.JobId);

            Assert.AreEqual(AssetGenJobState.Done, job.State, job.Error);
            StringAssert.EndsWith("audiotest.mp3", job.AssetPath);
        }

        [Test]
        public void Audio_NoKey_FailsImmediately()
        {
            Environment.SetEnvironmentVariable(FalEnvVar, null); // remove the env key
            string dir = Path.Combine(Path.GetTempPath(), "mcp_jobmgr_audio_nokey_" + Guid.NewGuid().ToString("N"));
            MCPForUnity.Editor.Security.SecureKeyStore.OverrideForTests(new MCPForUnity.Editor.Security.EncryptedFileKeyStore(dir));
            try
            {
                AssetGenJob job = AssetGenJobManager.StartAudioGeneration(AudioReq());
                Assert.AreEqual(AssetGenJobState.Failed, job.State);
                StringAssert.Contains("No API key", job.Error);
            }
            finally
            {
                MCPForUnity.Editor.Security.SecureKeyStore.ResetForTests();
                try
                {
                    if (Directory.Exists(dir))
                        Directory.Delete(dir, true);
                }
                catch { }
            }
        }

        [Test]
        public void FailedPoll_FailsJob_AndLeaksNoKey()
        {
            _fake.Handler = spec =>
            {
                if (spec.Method == "POST" && spec.Url.EndsWith("/v3/generation/text-to-model"))
                    return Json("{\"code\":0,\"data\":{\"task_id\":\"task_abc\"}}");
                return Json("{\"code\":0,\"data\":{\"status\":\"failed\",\"error\":\"out of credits\"}}");
            };

            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            Pump(job.JobId);

            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.IsNotEmpty(job.Error);

            string serialized = JsonConvert.SerializeObject(job);
            StringAssert.DoesNotContain(Secret, serialized);
        }

        [Test]
        public void IsAllowedResultExtension_AllowsSafeTypes_RejectsCodeTypes()
        {
            // H2/P8: a provider-controlled result extension must be gated per kind so a rogue provider
            // can't land a .cs/.asmdef/.meta/.asset under Assets/ and get it compiled/imported.
            Assert.IsTrue(AssetGenJobManager.IsAllowedResultExtension("audio", "wav"));
            Assert.IsTrue(AssetGenJobManager.IsAllowedResultExtension("audio", ".mp3"));
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension("audio", "cs"));
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension("audio", "asmdef"));
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension("audio", "meta"));

            Assert.IsTrue(AssetGenJobManager.IsAllowedResultExtension("image", "png"));
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension("image", "cs"));

            Assert.IsTrue(AssetGenJobManager.IsAllowedResultExtension("model", "glb"));
            Assert.IsTrue(AssetGenJobManager.IsAllowedResultExtension("model", "zip"));
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension("model", "cs"));
            // marketplace shares the model allowlist.
            Assert.IsTrue(AssetGenJobManager.IsAllowedResultExtension("marketplace", "zip"));
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension("marketplace", "dll"));

            // Fail closed: an unexpected/unknown kind allows nothing at the RCE boundary.
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension("bogus_kind", "glb"));
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension(null, "png"));
            Assert.IsFalse(AssetGenJobManager.IsAllowedResultExtension("", "wav"));
        }

        [Test]
        public void Audio_DisallowedResultExt_FailsJob_WritesNoAsset()
        {
            // H2/P8: a poll returning a .cs result extension must fail the job before any file is
            // written under Assets/ — never a compilable/importable payload.
            _fake.Handler = spec =>
            {
                if (spec.Method == "POST")
                    return Json("{\"response_url\":\"" + AudioResp + "\"}");
                if (spec.Url.EndsWith("/status"))
                    return Json("{\"status\":\"COMPLETED\"}");
                if (spec.Url.Contains("fal.media"))
                    return new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = new byte[] { 1, 2, 3, 4 },
                    };
                // Provider hands back a payload whose URL implies a .cs extension.
                return Json("{\"audio_file\":{\"url\":\"https://fal.media/payload.cs\"}}");
            };

            AssetGenJob job = AssetGenJobManager.StartAudioGeneration(AudioReq());
            Pump(job.JobId);

            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            StringAssert.Contains("disallowed", job.Error.ToLowerInvariant());
            Assert.IsTrue(string.IsNullOrEmpty(job.AssetPath), "no asset path should be recorded for a rejected type");

            // Nothing named payload.cs may have been written under the test output folder.
            string abs = Path.Combine(ProjectRoot(), TestFolder);
            if (Directory.Exists(abs))
                foreach (string f in Directory.GetFiles(abs))
                    StringAssert.DoesNotEndWith(".cs", f);
        }

        [TestCase("file:///etc/passwd")]
        [TestCase("http://127.0.0.1/private")]
        [TestCase("https://169.254.169.254/metadata")]
        [TestCase("https://[::1]/private")]
        [TestCase("https://attacker.example/model.glb")]
        [TestCase("https://tripo-data.rg1.data.tripo3d.com:8443/model.glb")]
        public void UnsafeDownloadUrl_FailsJobWithoutRequestOrAsset(string url)
        {
            _fake.Handler = spec =>
            {
                if (spec.Method == "POST" && spec.Url.EndsWith("/v3/generation/text-to-model"))
                    return Json("{\"code\":0,\"data\":{\"task_id\":\"task_abc\"}}");
                // Poll succeeds but hands back an unsafe URL as the model.
                return Json("{\"code\":0,\"data\":{\"status\":\"success\",\"progress\":100,\"output\":{\"pbr_model\":\"" + url + "\"}}}");
            };

            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            Pump(job.JobId);

            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            StringAssert.Contains("http", job.Error.ToLowerInvariant());
            Assert.AreEqual(2, _fake.RecordedRequests.Count, "Only submit and poll may reach the transport.");
            Assert.IsNull(job.AssetPath);
        }

        [Test]
        public void Cancel_BeforeRun_MarksCanceled()
        {
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(AssetGenJobState.Canceled, job.State);
        }

        // These requests use the real Tripo submit/poll parsing; only transport completion is controlled.
        private sealed class PendingTransport : IHttpTransport
        {
            public int PendingAt;
            public int Requests;
            public int CancellationCalls;
            public bool ThrowOnCancel;
            public bool IgnoreCancel;
            public byte[] SuccessDownloadBytes;
            public CancellationToken PendingToken;
            public TaskCompletionSource<HttpResult> Pending;

            public Task<HttpResult> SendAsync(HttpRequestSpec spec, CancellationToken ct)
            {
                Requests++;
                if (Requests == PendingAt)
                {
                    PendingToken = ct;
                    Pending = new TaskCompletionSource<HttpResult>();
                    ct.Register(() =>
                    {
                        CancellationCalls++;
                        if (ThrowOnCancel)
                            throw new InvalidOperationException("fixture cancellation callback");
                        if (!IgnoreCancel)
                            Pending.TrySetCanceled();
                    });
                    return Pending.Task;
                }
                // Retain completed-request callbacks to detect any unwanted cancellation on Done.
                ct.Register(() => CancellationCalls++);
                return Task.FromResult(
                    Requests == 3 && SuccessDownloadBytes != null
                        ? new HttpResult
                        {
                            Status = 200,
                            IsSuccess = true,
                            Body = SuccessDownloadBytes,
                        }
                        : ResultFor(Requests)
                );
            }

            public static HttpResult ResultFor(int request)
            {
                if (request == 1)
                    return Json("{\"code\":0,\"data\":{\"task_id\":\"task_pending\"}}");
                if (request == 2)
                    return Json("{\"code\":0,\"data\":{\"status\":\"success\",\"output\":{\"model\":\"https://tripo-data.rg1.data.tripo3d.com/model.glb\"}}}");
                return new HttpResult
                {
                    Status = 200,
                    IsSuccess = true,
                    Body = new byte[] { 1, 2, 3 },
                };
            }
        }

        private static void PumpUntilPending(AssetGenJob job, PendingTransport transport)
        {
            for (int i = 0; i < 10 && transport.Pending == null; i++)
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.IsNotNull(transport.Pending);
            Assert.IsFalse(transport.Pending.Task.IsCompleted);
        }

        [Test]
        public void SubstantialGenerationRemainsActiveBeyondTenMinutes()
        {
            var transport = new PendingTransport { PendingAt = 3 };
            AssetGenJobManager.TransportOverrideForTests = transport;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            PumpUntilPending(job, transport);
            AssetGenJobManager.ClockForTests = () => EditorApplication.timeSinceStartup + 601;
            Assert.IsFalse(AssetGenJobManager.TryAdvanceForTests(job.JobId));
            Assert.AreEqual(AssetGenJobState.Running, job.State);
            Assert.IsFalse(transport.PendingToken.IsCancellationRequested);
            Assert.IsNull(job.Error);
        }

        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        public void Timeout_CancelsPendingSubmitPollOrDownload(int pendingAt)
        {
            var transport = new PendingTransport { PendingAt = pendingAt };
            AssetGenJobManager.TransportOverrideForTests = transport;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            PumpUntilPending(job, transport);
            AssetGenJobManager.ClockForTests = () => EditorApplication.timeSinceStartup + AssetGenJobManager.TimeoutSeconds + 1; // Expire the original recorded deadline.
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(job.JobId));
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            StringAssert.Contains("Timed out", job.Error);
            Assert.IsTrue(transport.PendingToken.IsCancellationRequested);
            Assert.IsTrue(transport.Pending.Task.IsCanceled);
            Assert.AreEqual(pendingAt, transport.CancellationCalls);
        }

        [Test]
        public void Timeout_CallbackException_DoesNotEscapeOrLeaveRunner()
        {
            var transport = new PendingTransport { PendingAt = 1, ThrowOnCancel = true };
            AssetGenJobManager.TransportOverrideForTests = transport;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            PumpUntilPending(job, transport);
            AssetGenJobManager.ClockForTests = () => EditorApplication.timeSinceStartup + AssetGenJobManager.TimeoutSeconds + 1;
            Assert.DoesNotThrow(() => AssetGenJobManager.TryAdvanceForTests(job.JobId));
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual(1, transport.CancellationCalls);
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId), "Terminal runner must be removed even when a callback throws.");
        }

        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        public void Timeout_LateResponseCannotReviveJobOrImport(int pendingAt)
        {
            int imports = 0;
            var transport = new PendingTransport { PendingAt = pendingAt, IgnoreCancel = true };
            AssetGenJobManager.TransportOverrideForTests = transport;
            AssetGenJobManager.ImportOverrideForTests = (job, path) =>
            {
                imports++;
                return job;
            };
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            PumpUntilPending(job, transport);
            AssetGenJobManager.ClockForTests = () => EditorApplication.timeSinceStartup + AssetGenJobManager.TimeoutSeconds + 1;
            AssetGenJobManager.TryAdvanceForTests(job.JobId);
            transport.Pending.SetResult(PendingTransport.ResultFor(pendingAt));
            for (int i = 0; i < 10; i++)
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.IsTrue(transport.PendingToken.IsCancellationRequested);
            Assert.AreEqual(pendingAt, transport.Requests);
            Assert.AreEqual(0, imports);
            Assert.IsNull(job.AssetPath);
            Assert.IsFalse(Directory.Exists(Path.Combine(ProjectRoot(), TestFolder)), "Late data must not be written.");
        }

        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        public void Cancel_PendingRequest_PreservesCanceledState(int pendingAt)
        {
            var transport = new PendingTransport { PendingAt = pendingAt };
            AssetGenJobManager.TransportOverrideForTests = transport;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            PumpUntilPending(job, transport);
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(job.JobId));
            Assert.AreEqual(AssetGenJobState.Canceled, job.State);
            Assert.IsTrue(transport.Pending.Task.IsCanceled);
            Assert.AreEqual(pendingAt, transport.CancellationCalls);
        }

        [Test]
        public void CompletedJob_DoesNotRequestCancellation()
        {
            // Container validity only: import is the existing fake. Do not imply optional GLB importer success.
            byte[] bytes = ModelImportPipelineTests.ContainerOnlyGlb();
            Assert.AreEqual("glb", TripoAdapter.ValidateModelBytes(bytes, "glb"));
            var transport = new PendingTransport { SuccessDownloadBytes = bytes };
            AssetGenJobManager.TransportOverrideForTests = transport;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            Pump(job.JobId);
            Assert.AreEqual(AssetGenJobState.Done, job.State);
            Assert.AreEqual(1f, job.Progress);
            Assert.IsNotNull(job.AssetPath);
            Assert.AreEqual(0, transport.CancellationCalls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StagedDownloadIsReleasedAfterPublicationOrInvalidContainer(bool valid)
        {
            var transport = new PendingTransport { PendingAt = 3 };
            AssetGenJobManager.TransportOverrideForTests = transport;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            PumpUntilPending(job, transport);
            using var source = new MemoryStream(valid ? ModelImportPipelineTests.ContainerOnlyGlb() : new byte[] { 1, 2, 3 });
            var artifact = DownloadedArtifact
                .ReadAsync(source, source.Length, AssetDownloadTransport.MaxDownloadBytes, ProjectRoot(), CancellationToken.None)
                .GetAwaiter()
                .GetResult();
            string path = artifact.Path;
            using var result = new HttpResult
            {
                Status = 200,
                IsSuccess = true,
                DownloadedFile = artifact,
            };
            transport.Pending.SetResult(result);
            Pump(job.JobId);
            Assert.AreEqual(valid ? AssetGenJobState.Done : AssetGenJobState.Failed, job.State);
            Assert.IsFalse(File.Exists(path));
            if (valid)
                CollectionAssert.AreEqual(ModelImportPipelineTests.ContainerOnlyGlb(), File.ReadAllBytes(Path.Combine(ProjectRoot(), job.AssetPath)));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LateStagedDownloadIsReleasedAfterCancellationOrReload(bool reload)
        {
            var transport = new PendingTransport { PendingAt = 3, IgnoreCancel = true };
            AssetGenJobManager.TransportOverrideForTests = transport;
            AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
            PumpUntilPending(job, transport);
            using var source = new MemoryStream(ModelImportPipelineTests.ContainerOnlyGlb());
            var artifact = DownloadedArtifact
                .ReadAsync(source, source.Length, AssetDownloadTransport.MaxDownloadBytes, ProjectRoot(), CancellationToken.None)
                .GetAwaiter()
                .GetResult();
            string path = artifact.Path;
            using var result = new HttpResult
            {
                Status = 200,
                IsSuccess = true,
                DownloadedFile = artifact,
            };
            if (reload)
                AssetGenJobManager.ResetForTests();
            else
            {
                Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
                AssetGenJobManager.TryAdvanceForTests(job.JobId);
            }
            transport.Pending.SetResult(result);
            Assert.IsFalse(File.Exists(path));
            Assert.IsNull(job.AssetPath);
        }

        [Test]
        public void MissingKey_FailsImmediately()
        {
            Environment.SetEnvironmentVariable(EnvVar, null); // remove the env key
            // Ensure no stored key resolves for tripo by routing through an empty temp store.
            string dir = Path.Combine(Path.GetTempPath(), "mcp_jobmgr_nokey_" + Guid.NewGuid().ToString("N"));
            MCPForUnity.Editor.Security.SecureKeyStore.OverrideForTests(new MCPForUnity.Editor.Security.EncryptedFileKeyStore(dir));
            try
            {
                AssetGenJob job = AssetGenJobManager.StartModelGeneration(Req());
                Assert.AreEqual(AssetGenJobState.Failed, job.State);
                StringAssert.Contains("No API key", job.Error);
            }
            finally
            {
                MCPForUnity.Editor.Security.SecureKeyStore.ResetForTests();
                try
                {
                    if (Directory.Exists(dir))
                        Directory.Delete(dir, true);
                }
                catch { }
            }
        }
    }
}
