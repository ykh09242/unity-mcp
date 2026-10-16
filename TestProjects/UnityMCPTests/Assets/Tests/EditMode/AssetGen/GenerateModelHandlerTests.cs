using System;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Tools.AssetGen;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class GenerateModelHandlerTests
    {
        private string _dir;
        private EncryptedFileKeyStore _store;

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            Environment.SetEnvironmentVariable("MCPFORUNITY_TRIPO_API_KEY", null);
            _dir = Path.Combine(Path.GetTempPath(), "mcp_handler_" + Guid.NewGuid().ToString("N"));
            _store = new EncryptedFileKeyStore(_dir);
            SecureKeyStore.OverrideForTests(_store);
            AssetGenJobManager.TransportOverrideForTests = new FakeHttpTransport();
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.ResetForTests();
            try
            {
                if (Directory.Exists(_dir))
                    Directory.Delete(_dir, true);
            }
            catch { }
            try
            {
                const string fixtureRoot = "Assets/Generated/__assetgen_model_handler";
                if (AssetDatabase.IsValidFolder(fixtureRoot))
                    AssetDatabase.DeleteAsset(fixtureRoot);
                string absoluteRoot = Path.Combine(ProjectRoot(), fixtureRoot);
                if (Directory.Exists(absoluteRoot))
                    Directory.Delete(absoluteRoot, true);
            }
            catch { }
        }

        private static JObject Call(JObject p) => JObject.Parse(JsonConvert.SerializeObject(GenerateModel.HandleCommand(p)));

        private static string ProjectRoot()
        {
            string dp = Application.dataPath.Replace('\\', '/');
            return dp.Substring(0, dp.Length - "Assets".Length);
        }

        private static string WriteProjectFile(string rel, byte[] bytes)
        {
            string abs = Path.Combine(ProjectRoot(), rel).Replace('\\', '/');
            Directory.CreateDirectory(Path.GetDirectoryName(abs));
            File.WriteAllBytes(abs, bytes);
            return rel;
        }

        [Test]
        public void Generate_WithKey_ReturnsPendingJobId_AndStatusReportsState()
        {
            _store.Set("tripo", "handlerkey123");

            JObject gen = Call(
                new JObject
                {
                    ["action"] = "generate",
                    ["provider"] = "tripo",
                    ["mode"] = "text",
                    ["prompt"] = "a tree",
                }
            );
            Assert.AreEqual("pending", (string)gen["_mcp_status"]);
            string jobId = (string)gen["data"]["job_id"];
            Assert.IsFalse(string.IsNullOrEmpty(jobId));

            JObject st = Call(new JObject { ["action"] = "status", ["job_id"] = jobId });
            Assert.AreEqual("pending", (string)st["_mcp_status"]);
            Assert.IsNotNull(st["data"]["state"]);
        }

        [Test]
        public void Generate_NoKey_ReturnsError()
        {
            JObject resp = Call(
                new JObject
                {
                    ["action"] = "generate",
                    ["provider"] = "tripo",
                    ["mode"] = "text",
                    ["prompt"] = "a tree",
                }
            );
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("No API key", (string)resp["error"]);
        }

        [Test]
        public void Generate_UnimplementedProvider_ReturnsError()
        {
            _store.Set("hunyuan", "k");
            JObject resp = Call(
                new JObject
                {
                    ["action"] = "generate",
                    ["provider"] = "hunyuan",
                    ["mode"] = "text",
                    ["prompt"] = "a tree",
                }
            );
            Assert.AreEqual(false, (bool)resp["success"]);
        }

        [Test]
        public void Generate_ImageMode_MissingFile_ReturnsError()
        {
            // Use a provider that supports local images (Meshy) so we exercise the file-existence
            // check; Tripo short-circuits on any local path (covered separately).
            _store.Set("meshy", "k");
            JObject resp = Call(
                new JObject
                {
                    ["action"] = "generate",
                    ["provider"] = "meshy",
                    ["mode"] = "image",
                    ["imagePath"] = "Assets/does_not_exist_zzz.png",
                }
            );
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("not found", ((string)resp["error"]).ToLowerInvariant());
        }

        [Test]
        public void Generate_ImageMode_PathOutsideAssets_ReturnsError()
        {
            _store.Set("meshy", "k");
            string tmp = Path.Combine(Path.GetTempPath(), "mcp_imgin_" + Guid.NewGuid().ToString("N") + ".png");
            File.WriteAllBytes(tmp, new byte[] { 137, 80, 78, 71 });
            try
            {
                JObject resp = Call(
                    new JObject
                    {
                        ["action"] = "generate",
                        ["provider"] = "meshy",
                        ["mode"] = "image",
                        ["imagePath"] = tmp,
                    }
                );
                Assert.AreEqual(false, (bool)resp["success"]);
                StringAssert.Contains("Assets", (string)resp["error"]);
            }
            finally
            {
                try
                {
                    File.Delete(tmp);
                }
                catch { }
            }
        }

        [Test]
        public void Generate_ImageMode_ProjectLocalPath_Accepted_ReturnsPending()
        {
            _store.Set("meshy", "k");
            string rel = WriteProjectFile("Assets/Generated/__assetgen_model_handler/ref.png", new byte[] { 137, 80, 78, 71 });

            JObject gen = Call(
                new JObject
                {
                    ["action"] = "generate",
                    ["provider"] = "meshy",
                    ["mode"] = "image",
                    ["imagePath"] = rel,
                }
            );

            Assert.AreEqual("pending", (string)gen["_mcp_status"]);
        }

        [Test]
        public void Generate_ImageMode_Tripo_LocalPath_ReturnsErrorSynchronously()
        {
            _store.Set("tripo", "k");
            string tmp = Path.Combine(Path.GetTempPath(), "mcp_tripoimg_" + Guid.NewGuid().ToString("N") + ".png");
            File.WriteAllBytes(tmp, new byte[] { 137, 80, 78, 71 });
            try
            {
                JObject resp = Call(
                    new JObject
                    {
                        ["action"] = "generate",
                        ["provider"] = "tripo",
                        ["mode"] = "image",
                        ["imagePath"] = tmp,
                    }
                );
                Assert.AreEqual(false, (bool)resp["success"]); // synchronous error, not a fake 'pending'
                StringAssert.Contains("Tripo", (string)resp["error"]);
            }
            finally
            {
                try
                {
                    File.Delete(tmp);
                }
                catch { }
            }
        }

        [Test]
        public void Generate_TextMode_RequiresPrompt()
        {
            _store.Set("tripo", "k");
            JObject resp = Call(
                new JObject
                {
                    ["action"] = "generate",
                    ["provider"] = "tripo",
                    ["mode"] = "text",
                }
            );
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("prompt", ((string)resp["error"]).ToLowerInvariant());
        }

        [Test]
        public void ListProviders_IncludesTripoModel()
        {
            JObject resp = Call(new JObject { ["action"] = "list_providers" });
            Assert.AreEqual(true, (bool)resp["success"]);
            string s = resp.ToString();
            StringAssert.Contains("tripo", s);
            StringAssert.Contains("model", s);
        }

        [Test]
        public void UnknownAction_ReturnsError()
        {
            JObject resp = Call(new JObject { ["action"] = "frobnicate" });
            Assert.AreEqual(false, (bool)resp["success"]);
        }

        [TestCase("tripo", "garbage")]
        [TestCase("meshy", "text_typo")]
        public void Generate_UnknownMode_DoesNotCreateJobOrSubmit(string provider, string mode)
        {
            _store.Set(provider, "fixture-only");
            var transport = new FakeHttpTransport();
            AssetGenJobManager.TransportOverrideForTests = transport;
            JObject response = Call(
                new JObject
                {
                    ["action"] = "generate",
                    ["provider"] = provider,
                    ["mode"] = mode,
                    ["prompt"] = "a tree",
                }
            );
            Assert.AreEqual(false, (bool)response["success"]);
            StringAssert.Contains("mode", (string)response["error"]);
            Assert.AreEqual(0, AssetGenJobManager.RecentJobs().Count);
            Assert.AreEqual(0, transport.RecordedRequests.Count);
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("TeXt")]
        [TestCase("ImAgE")]
        public void Generate_DefaultAndMixedCaseModes_StillSubmit(string mode)
        {
            _store.Set("tripo", "fixture-only");
            var transport = new FakeHttpTransport();
            AssetGenJobManager.TransportOverrideForTests = transport;
            JObject response = Call(
                new JObject
                {
                    ["action"] = "generate",
                    ["mode"] = mode,
                    ["prompt"] = "a tree",
                    ["imageUrl"] = "https://fixture.invalid/source.png",
                    ["texture"] = false,
                }
            );
            Assert.AreEqual(true, (bool)response["success"]);
            Assert.AreEqual(1, AssetGenJobManager.RecentJobs().Count);
            AssetGenJobManager.TryAdvanceForTests((string)response["data"]["job_id"]);
            Assert.AreEqual(1, transport.RecordedRequests.Count);
        }

        private static JObject ContractRequest() =>
            new JObject
            {
                ["action"] = "generate",
                ["provider"] = "tripo",
                ["model"] = TripoAdapter.ModelVersion,
                ["prompt"] = "fixture",
                ["name"] = "fixture",
                ["output_folder"] = "Assets/Generated/__assetgen_model_handler/output",
            };

        [TestCase("format", "fbx")]
        [TestCase("format", "obj")]
        [TestCase("tier", "premium")]
        [TestCase("model", "v1.4-20240625")]
        public void Contract_UnsupportedOptionsRejectedBeforeJob(string key, string value)
        {
            _store.Set("tripo", "fixture-key");
            JObject request = ContractRequest();
            request[key] = value;
            if (key == "model")
                request["texture"] = false;
            JObject response = Call(request);
            Assert.AreEqual(false, (bool)response["success"]);
            Assert.AreEqual(0, AssetGenJobManager.RecentJobs().Count);
            Assert.AreEqual(0, ((FakeHttpTransport)AssetGenJobManager.TransportOverrideForTests).RecordedRequests.Count);
            Assert.IsFalse(Directory.Exists(Path.Combine(ProjectRoot(), (string)request["output_folder"])));
        }

        [TestCase("tripo", null, null)]
        [TestCase("tripo", "glb", null)]
        [TestCase("tripo", " .GLB ", " ")]
        [TestCase("meshy", "fbx", "premium")]
        public void Contract_DefaultsAndOtherProviderOptionsStillStart(string provider, string format, string tier)
        {
            _store.Set(provider, "fixture-key");
            JObject request = ContractRequest();
            request["provider"] = provider;
            request["model"] = provider == "tripo" ? TripoAdapter.ModelVersion : "meshy-6";
            if (format != null)
                request["format"] = format;
            if (tier != null)
                request["tier"] = tier;
            JObject response = Call(request);
            Assert.AreEqual(true, (bool)response["success"], response.ToString());
            AssetGenJob job = AssetGenJobManager.GetJob((string)response["data"]["job_id"]);
            Assert.AreEqual(provider == "tripo" ? "glb" : "fbx", job.Format);
            Assert.AreEqual(0, ((FakeHttpTransport)AssetGenJobManager.TransportOverrideForTests).RecordedRequests.Count);
        }

        private static byte[] ContractGlb(string document = "{\"asset\":{\"version\":\"2.0\"}}")
        {
            byte[] json = Encoding.UTF8.GetBytes(document + new string(' ', (4 - Encoding.UTF8.GetByteCount(document) % 4) % 4));
            using var stream = new MemoryStream();
            using var writer = new BinaryWriter(stream);
            writer.Write(0x46546C67u);
            writer.Write(2u);
            writer.Write((uint)(20 + json.Length));
            writer.Write((uint)json.Length);
            writer.Write(0x4E4F534Au);
            writer.Write(json);
            return stream.ToArray();
        }

        [TestCase("single-quotes", false)]
        [TestCase("comments", false)]
        [TestCase("trailing-comma", false)]
        [TestCase("escapes", true)]
        [TestCase("unicode", true)]
        public void Contract_SaveRequiresStrictJson(string kind, bool valid) => Contract_SaveRequiresGlbBytesBeforeCreatingOutput(kind, "model.glb", valid);

        [TestCase("glb", "model.glb?signature=hidden.obj", true)]
        [TestCase("glb", "download?id=model.fbx", true)]
        [TestCase("html", "model.glb", false)]
        [TestCase("obj", "model.glb", false)]
        [TestCase("length", "model.glb", false)]
        [TestCase("json", "download", false)]
        [TestCase("glb", "model.fbx", false)]
        public void Contract_SaveRequiresGlbBytesBeforeCreatingOutput(string bytesKind, string path, bool valid)
        {
            _store.Set("tripo", "fixture-key");
            byte[] bytes = ContractGlb();
            if (bytesKind == "single-quotes")
                bytes = ContractGlb("{'asset':{'version':'2.0'}}");
            if (bytesKind == "comments")
                bytes = ContractGlb("{\"asset\":{/* comment */\"version\":\"2.0\"}}");
            if (bytesKind == "trailing-comma")
                bytes = ContractGlb("{\"asset\":{\"version\":\"2.0\",}}");
            if (bytesKind == "escapes")
                bytes = ContractGlb(
                    "{\"asset\":{\"version\":\"2.0\"},\"extras\":{\"label\":\"quote: \\\" slash: \\\\ newline: \\n rocket: \\uD83D\\uDE80\",\"values\":[null,true,false,0,-2,1.5,1e-3,[],{}]}}"
                );
            if (bytesKind == "unicode")
                bytes = ContractGlb("{\"asset\":{\"version\":\"2.0\"},\"extras\":{\"label\":\"Ω🚀\"}}");
            if (bytesKind == "html")
                bytes = Encoding.UTF8.GetBytes("<html>provider error</html>");
            if (bytesKind == "obj")
                bytes = Encoding.UTF8.GetBytes("v 0 0 0\nv 1 0 0\nf 1 2 3\n");
            if (bytesKind == "length")
                bytes[8]++;
            if (bytesKind == "json")
                bytes[20] = (byte)'!';
            var http = new FakeHttpTransport
            {
                Handler = spec =>
                {
                    if (spec.Method == "POST")
                        return new HttpResult
                        {
                            Status = 200,
                            IsSuccess = true,
                            Text = "{\"code\":0,\"data\":{\"task_id\":\"fixture\"}}",
                        };
                    if (spec.Url.Contains("/v2/openapi/task/"))
                        return new HttpResult
                        {
                            Status = 200,
                            IsSuccess = true,
                            Text = new JObject
                            {
                                ["code"] = 0,
                                ["data"] = new JObject
                                {
                                    ["status"] = "success",
                                    ["output"] = new JObject { ["model"] = "https://tripo-data.rg1.data.tripo3d.com/" + path },
                                },
                            }.ToString(),
                        };
                    return new HttpResult
                    {
                        Status = 200,
                        IsSuccess = true,
                        Body = bytes,
                    };
                },
            };
            AssetGenJobManager.TransportOverrideForTests = http;
            AssetGenJobManager.PollIntervalSeconds = 0;
            AssetGenJobManager.ImportOverrideForTests = (job, assetPath) =>
            {
                job.AssetPath = assetPath;
                return job;
            };
            AssetGenJobManager.SkipModelVerificationForTests = true;
            string source = WriteProjectFile("Assets/Generated/__assetgen_model_handler/source.bin", new byte[] { 1, 2, 3 });
            JObject request = ContractRequest();
            JObject response = Call(request);
            Assert.AreEqual(true, (bool)response["success"]);
            string id = (string)response["data"]["job_id"];
            for (int i = 0; i < 30 && !AssetGenJobManager.TryAdvanceForTests(id); i++) { }
            AssetGenJob result = AssetGenJobManager.GetJob(id);
            Assert.AreEqual(valid ? AssetGenJobState.Done : AssetGenJobState.Failed, result.State, result.Error);
            CollectionAssert.AreEqual(new byte[] { 1, 2, 3 }, File.ReadAllBytes(Path.Combine(ProjectRoot(), source)));
            string output = Path.Combine(ProjectRoot(), (string)request["output_folder"]);
            if (valid)
            {
                Assert.AreEqual("glb", result.Format);
                StringAssert.EndsWith(".glb", result.AssetPath);
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Path.Combine(ProjectRoot(), result.AssetPath)));
            }
            else
                Assert.IsFalse(Directory.Exists(output), "Invalid artifacts must fail before output folder creation.");
        }
    }
}
