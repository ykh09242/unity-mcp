using System;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    /// <summary>
    /// Exercises the Tripo adapter against a <see cref="FakeHttpTransport"/> — no network.
    /// Asserts request shaping (endpoint, method, auth scheme, body) and poll-state mapping.
    /// The bearer secret is never asserted beyond the "Bearer " scheme prefix.
    /// </summary>
    public class TripoAdapterTests
    {
        private static HttpResult Json(string json, int status = 200) =>
            new HttpResult
            {
                Status = status,
                IsSuccess = status >= 200 && status < 300,
                Text = json,
                Body = Encoding.UTF8.GetBytes(json),
            };

        [Test]
        public void Submit_PostsTaskEndpoint_WithBearerHeader_AndReturnsTaskId()
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"task_id\":\"abc123\"}}") };
            var adapter = new TripoAdapter();
            var req = new ModelGenRequest
            {
                Provider = "tripo",
                Mode = "text",
                Prompt = "a red dragon",
            };

            string taskId = adapter.SubmitAsync(req, "tsk_secret_value", http, CancellationToken.None).GetAwaiter().GetResult();

            Assert.AreEqual("abc123", taskId);
            Assert.AreEqual(1, http.RecordedRequests.Count);

            HttpRequestSpec rec = http.RecordedRequests[0];
            Assert.AreEqual("https://openapi.tripo3d.ai/v3/generation/text-to-model", rec.Url);
            Assert.AreEqual("POST", rec.Method);
            Assert.IsTrue(rec.Headers.ContainsKey("Authorization"));
            StringAssert.StartsWith("Bearer ", rec.Headers["Authorization"]);

            string body = Encoding.UTF8.GetString(rec.Body);
            StringAssert.Contains("a red dragon", body);
            Assert.IsNull(JObject.Parse(body)["type"]);
        }

        [Test]
        public void Poll_MapsSuccess_WithDownloadUrl()
        {
            var http = new FakeHttpTransport
            {
                Handler = _ =>
                    Json(
                        "{\"code\":0,\"data\":{\"status\":\"success\",\"progress\":100," + "\"output\":{\"pbr_model\":\"https://cdn.tripo3d.ai/model.glb\"}}}"
                    ),
            };
            var adapter = new TripoAdapter();

            ProviderPollResult res = adapter.PollAsync("abc123", "tsk_secret_value", http, CancellationToken.None).GetAwaiter().GetResult();

            Assert.AreEqual(ProviderPollState.Succeeded, res.State);
            Assert.AreEqual("https://cdn.tripo3d.ai/model.glb", res.DownloadUrl);
        }

        [Test]
        public void Poll_MapsRunning_WithProgress()
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"status\":\"running\",\"progress\":42}}") };
            var adapter = new TripoAdapter();

            ProviderPollResult res = adapter.PollAsync("abc123", "k", http, CancellationToken.None).GetAwaiter().GetResult();

            Assert.AreEqual(ProviderPollState.Running, res.State);
            Assert.AreEqual(0.42f, res.Progress, 0.001f);
        }

        [Test]
        public void Poll_MapsFailed_WithError()
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"status\":\"failed\",\"progress\":0}}") };
            var adapter = new TripoAdapter();

            ProviderPollResult res = adapter.PollAsync("abc123", "k", http, CancellationToken.None).GetAwaiter().GetResult();

            Assert.AreEqual(ProviderPollState.Failed, res.State);
            Assert.IsNotEmpty(res.Error);
        }

        [Test]
        public void Submit_UsesRequestModel_WhenProvided()
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"task_id\":\"t\"}}") };
            var adapter = new TripoAdapter();
            var req = new ModelGenRequest
            {
                Provider = "tripo",
                Mode = "text",
                Prompt = "x",
                Model = "P1-20260311",
            };

            adapter.SubmitAsync(req, "k", http, CancellationToken.None).GetAwaiter().GetResult();

            string body = Encoding.UTF8.GetString(http.RecordedRequests[0].Body);
            StringAssert.Contains("\"model\":\"P1-20260311\"", body);
        }

        [Test]
        public void Submit_FallsBackToDefault_WhenModelEmpty()
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"task_id\":\"t\"}}") };
            var adapter = new TripoAdapter();
            var req = new ModelGenRequest
            {
                Provider = "tripo",
                Mode = "text",
                Prompt = "x",
            }; // Model null

            adapter.SubmitAsync(req, "k", http, CancellationToken.None).GetAwaiter().GetResult();

            string body = Encoding.UTF8.GetString(http.RecordedRequests[0].Body);
            StringAssert.Contains("\"model\":\"" + TripoAdapter.ModelVersion + "\"", body);
        }

        [Test]
        public void Submit_ImageMode_UsesImageToModel()
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"task_id\":\"img1\"}}") };
            var adapter = new TripoAdapter();
            var req = new ModelGenRequest
            {
                Provider = "tripo",
                Mode = "image",
                ImageUrl = "https://example.com/in.png",
            };

            adapter.SubmitAsync(req, "k", http, CancellationToken.None).GetAwaiter().GetResult();

            string body = Encoding.UTF8.GetString(http.RecordedRequests[0].Body);
            Assert.AreEqual("https://openapi.tripo3d.ai/v3/generation/image-to-model", http.RecordedRequests[0].Url);
            StringAssert.Contains("https://example.com/in.png", body);
        }

        [Test]
        public void Submit_ImageMode_HonorsModelVersion_WhenProvided()
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"task_id\":\"img1\"}}") };
            var adapter = new TripoAdapter();
            var req = new ModelGenRequest
            {
                Provider = "tripo",
                Mode = "image",
                ImageUrl = "https://example.com/in.png",
                Model = "P1-20260311",
            };

            adapter.SubmitAsync(req, "k", http, CancellationToken.None).GetAwaiter().GetResult();

            string body = Encoding.UTF8.GetString(http.RecordedRequests[0].Body);
            Assert.AreEqual("https://openapi.tripo3d.ai/v3/generation/image-to-model", http.RecordedRequests[0].Url);
            StringAssert.Contains("\"model\":\"P1-20260311\"", body);
        }

        [Test]
        public void Submit_ImageMode_FallsBackToDefaultModelVersion_WhenModelEmpty()
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"task_id\":\"img1\"}}") };
            var adapter = new TripoAdapter();
            var req = new ModelGenRequest
            {
                Provider = "tripo",
                Mode = "image",
                ImageUrl = "https://example.com/in.png", // Model null
            };

            adapter.SubmitAsync(req, "k", http, CancellationToken.None).GetAwaiter().GetResult();

            string body = Encoding.UTF8.GetString(http.RecordedRequests[0].Body);
            StringAssert.Contains("\"model\":\"" + TripoAdapter.ModelVersion + "\"", body);
        }

        [Test]
        public void Submit_ImageMode_LocalPathOnly_Throws()
        {
            // Direct adapter callers also require explicit local-upload consent.
            var adapter = new TripoAdapter();
            var req = new ModelGenRequest
            {
                Provider = "tripo",
                Mode = "image",
                ImagePath = "/tmp/whatever.png",
            };

            var http = new FakeHttpTransport();
            Assert.Throws<InvalidOperationException>(() => adapter.SubmitAsync(req, "k", http, CancellationToken.None).GetAwaiter().GetResult());
            Assert.IsEmpty(http.RecordedRequests);
        }

        [TestCase("fbx", null, true, null)]
        [TestCase("obj", null, true, null)]
        [TestCase("glb", "premium", true, null)]
        [TestCase("glb", null, false, "v1.4-20240625")]
        [TestCase("glb", null, false, "unverified-model")]
        public void Contract_UnsupportedOptionsNeverSubmit(string format, string tier, bool texture, string model)
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"task_id\":\"fixture\"}}") };
            var req = new ModelGenRequest
            {
                Mode = "text",
                Prompt = "fixture",
                Format = format,
                Tier = tier,
                Texture = texture,
                Model = model,
            };
            Assert.Throws<NotSupportedException>(() =>
                new TripoAdapter().SubmitAsync(req, "fixture-key", http, CancellationToken.None).GetAwaiter().GetResult()
            );
            Assert.AreEqual(0, http.RecordedRequests.Count);
        }

        [Test]
        public void Poll_PrefersV3ModelUrl_AndUsesTaskQueryEndpoint()
        {
            const string current = "https://cdn.tripo3d.ai/current.glb";
            var http = new FakeHttpTransport
            {
                Handler = _ =>
                    Json(
                        "{\"code\":0,\"data\":{\"status\":\"success\",\"output\":{\"model_url\":\""
                            + current
                            + "\",\"pbr_model\":\"https://fixture.invalid/legacy.glb\"}}}"
                    ),
            };
            ProviderPollResult result = new TripoAdapter().PollAsync("legacy-task-id", "k", http, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual("https://openapi.tripo3d.ai/v3/tasks/legacy-task-id", http.RecordedRequests[0].Url);
            Assert.AreEqual("GET", http.RecordedRequests[0].Method);
            Assert.AreEqual(current, result.DownloadUrl);
            Assert.AreEqual(ProviderPollState.Succeeded, result.State);
        }

        [TestCase("output", "pbr_model", false)]
        [TestCase("result", "pbr_model", true)]
        [TestCase("output", "model", false)]
        [TestCase("result", "model", true)]
        [TestCase("output", "base_model", false)]
        [TestCase("result", "base_model", true)]
        public void Poll_PreservesLegacyOutputAliases(string container, string field, bool nested)
        {
            const string url = "https://tripo-data.rg1.data.tripo3d.com/legacy.glb";
            var data = new JObject
            {
                ["status"] = "success",
                [container] = new JObject { [field] = nested ? (JToken)new JObject { ["url"] = url } : new JValue(url) },
            };
            var http = new FakeHttpTransport { Handler = _ => Json(new JObject { ["code"] = 0, ["data"] = data }.ToString()) };
            Assert.AreEqual(url, new TripoAdapter().PollAsync("legacy-task-id", "k", http, CancellationToken.None).GetAwaiter().GetResult().DownloadUrl);
        }

        [TestCase("text", true, null)]
        [TestCase("text", false, null)]
        [TestCase("image", false, "v3.0-20250812")]
        [TestCase("image", false, "P1-20260311")]
        public void Contract_TextureAndPbrAreMappedTogether(string mode, bool texture, string model)
        {
            var http = new FakeHttpTransport { Handler = _ => Json("{\"code\":0,\"data\":{\"task_id\":\"fixture\"}}") };
            new TripoAdapter()
                .SubmitAsync(
                    new ModelGenRequest
                    {
                        Mode = mode,
                        Prompt = "fixture",
                        ImageUrl = "https://fixture.invalid/in.png",
                        Texture = texture,
                        Model = model,
                    },
                    "fixture-key",
                    http,
                    CancellationToken.None
                )
                .GetAwaiter()
                .GetResult();
            JObject body = JObject.Parse(Encoding.UTF8.GetString(http.RecordedRequests[0].Body));
            Assert.AreEqual(texture, (bool?)body["texture"]);
            Assert.AreEqual(texture, (bool?)body["pbr"]);
            Assert.IsNull(body["tier"]);
        }

        [TestCase("https://fixture.invalid/model.GLB?signature=secret.fbx", ProviderPollState.Succeeded, "glb")]
        [TestCase("https://fixture.invalid/download?id=model.glb", ProviderPollState.Succeeded, null)]
        [TestCase("https://fixture.invalid/model.fbx?signature=secret.glb", ProviderPollState.Failed, null)]
        [TestCase("https://fixture.invalid/model.obj", ProviderPollState.Failed, null)]
        public void Contract_PollUsesActualUrlPathFormat(string url, ProviderPollState state, string extension)
        {
            var payload = new JObject
            {
                ["code"] = 0,
                ["data"] = new JObject
                {
                    ["status"] = "success",
                    ["output"] = new JObject { ["model"] = url },
                },
            };
            var http = new FakeHttpTransport { Handler = _ => Json(payload.ToString()) };
            ProviderPollResult result = new TripoAdapter().PollAsync("fixture", "fixture-key", http, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(state, result.State);
            Assert.AreEqual(extension, result.ResultExt);
            if (state == ProviderPollState.Failed)
                StringAssert.DoesNotContain(url, result.Error);
        }

        [Test]
        public void Contract_UndocumentedOutputIsNotAFormatFallback()
        {
            var http = new FakeHttpTransport
            {
                Handler = _ => Json("{\"code\":0,\"data\":{\"status\":\"success\",\"output\":{\"download_url\":\"https://fixture.invalid/model.glb\"}}}"),
            };
            var result = new TripoAdapter().PollAsync("fixture", "k", http, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(ProviderPollState.Failed, result.State);
        }
    }
}
