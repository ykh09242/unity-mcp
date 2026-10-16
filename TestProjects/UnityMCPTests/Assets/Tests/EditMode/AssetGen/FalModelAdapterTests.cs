using System;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class FalModelAdapterTests
    {
        [TestCase("model_glb")]
        [TestCase("model_urls.glb")]
        public void RestoredOutputField_PollsOriginalReceiptWithoutCatalogOrSubmit(string field)
        {
            const string receipt = "https://queue.fal.run/fal-ai/owned-model/requests/owned-id";
            const string url = "https://fal.media/files/owned/model.glb";
            var payload =
                field == "model_glb"
                    ? new JObject { ["model_glb"] = new JObject { ["url"] = url } }
                    : new JObject { ["model_urls"] = new JObject { ["glb"] = new JObject { ["url"] = url } } };
            var http = new FakeHttpTransport
            {
                Handler = request => new HttpResult
                {
                    Status = 200,
                    IsSuccess = true,
                    Text = request.Url.EndsWith("/status", StringComparison.Ordinal) ? "{\"status\":\"COMPLETED\"}" : payload.ToString(),
                },
            };
            var adapter = new FalModelAdapter();
            adapter.RestoreOutputField(field);
            ProviderPollResult result = adapter.PollAsync(receipt, "owned-fake-key", http, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(ProviderPollState.Succeeded, result.State);
            Assert.AreEqual(url, result.DownloadUrl);
            Assert.AreEqual("glb", result.ResultExt);
            Assert.AreEqual(2, http.RecordedRequests.Count);
            Assert.AreEqual(receipt + "/status", http.RecordedRequests[0].Url);
            Assert.AreEqual(receipt, http.RecordedRequests[1].Url);
            foreach (var request in http.RecordedRequests)
                Assert.AreEqual("GET", request.Method);
        }

        [TestCase(null)]
        [TestCase("arbitrary.secret")]
        public void UnsupportedOutputField_IsRejected(string field) => Assert.Throws<ArgumentException>(() => new FalModelAdapter().RestoreOutputField(field));
    }
}
