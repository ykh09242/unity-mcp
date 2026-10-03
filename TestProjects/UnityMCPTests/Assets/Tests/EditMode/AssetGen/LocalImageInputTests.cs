using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Tools.AssetGen;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class LocalImageInputTests
    {
        private const int ImageLimit = 32 * 1024 * 1024;
        private const int RequestLimit = 48 * 1024 * 1024;
        private string _folder;
        private string _absoluteFolder;

        [SetUp]
        public void SetUp()
        {
            _folder = "Assets/__local_image_" + Guid.NewGuid().ToString("N");
            _absoluteFolder = AssetGenPaths.ToAbsolute(_folder);
            Directory.CreateDirectory(_absoluteFolder);
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.OverrideForTests(new FixtureKeys());
            AssetGenJobManager.TransportOverrideForTests = Transport();
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.ResetForTests();
            if (Directory.Exists(_absoluteFolder)) Directory.Delete(_absoluteFolder, true);
            if (File.Exists(_absoluteFolder + ".meta")) File.Delete(_absoluteFolder + ".meta");
        }

        private string WriteSized(string name, int length)
        {
            string path = _folder + "/" + name;
            using (var stream = new FileStream(AssetGenPaths.ToAbsolute(path), FileMode.CreateNew))
                stream.SetLength(length);
            return path;
        }

        private static FakeHttpTransport Transport() => new FakeHttpTransport
        {
            Handler = spec => new HttpResult
            {
                IsSuccess = true, Status = 200,
                Text = spec.Url.Contains("meshy") ? "{\"result\":\"fixture-task\"}"
                    : spec.Url.Contains("openrouter") ? "{}"
                    : "{\"response_url\":\"https://queue.fal.run/fixture/requests/fixture-task\"}"
            }
        };

        private static Task Submit(string provider, string path, FakeHttpTransport http, string extra = "edit")
        {
            if (provider == "meshy")
                return new MeshyAdapter().SubmitAsync(new ModelGenRequest
                { Mode = "image", ImagePath = path, Model = extra }, "fixture-only", http, CancellationToken.None);
            var request = new ImageGenRequest { Mode = "image", ImagePath = path, Prompt = extra };
            return provider == "fal"
                ? new FalAdapter().SubmitAsync(request, "fixture-only", http, CancellationToken.None)
                : new OpenRouterAdapter().SubmitAsync(request, "fixture-only", http, CancellationToken.None);
        }

        private static JObject Handle(string provider, string path)
        {
            var p = new JObject { ["action"] = "generate", ["provider"] = provider,
                ["mode"] = "ImAgE", ["image_path"] = path, ["prompt"] = "edit" };
            return JObject.FromObject(provider == "meshy"
                ? GenerateModel.HandleCommand(p) : GenerateImage.HandleCommand(p));
        }

        [TestCase("fal")]
        [TestCase("openrouter")]
        [TestCase("meshy")]
        public void OversizedFile_HandlerRejectsBeforeJobOrRequest(string provider)
        {
            var http = Transport();
            AssetGenJobManager.TransportOverrideForTests = http;
            string path = WriteSized("oversize.PNG", ImageLimit + 1);
            JObject result = Handle(provider, path);
            Assert.AreEqual(false, (bool)result["success"]);
            Assert.AreEqual(0, AssetGenJobManager.RecentJobs().Count);
            Assert.AreEqual(0, http.RecordedRequests.Count);
        }

        [TestCase("fal")]
        [TestCase("openrouter")]
        [TestCase("meshy")]
        public void OversizedFile_DirectAdapterRejectsBeforeRequest(string provider)
        {
            var http = Transport();
            string path = WriteSized("oversize.png", ImageLimit + 1);
            Assert.ThrowsAsync<IOException>(async () => await Submit(provider, path, http));
            Assert.AreEqual(0, http.RecordedRequests.Count);
        }

        [TestCase("fal", 4)]
        [TestCase("openrouter", 4)]
        [TestCase("meshy", 4)]
        [TestCase("fal", 64 * 1024)]
        [TestCase("openrouter", 64 * 1024)]
        [TestCase("meshy", 64 * 1024)]
        public async Task NormalFiles_HandlerAndAdapterAccept(string provider, int length)
        {
            string path = WriteSized("accepted.PnG", length);
            JObject result = Handle(provider, path);
            Assert.AreEqual(true, (bool)result["success"]);
            Assert.AreEqual(1, AssetGenJobManager.RecentJobs().Count);
            var http = Transport();
            await Submit(provider, path, http);
            Assert.AreEqual(1, http.RecordedRequests.Count);
            Assert.LessOrEqual(http.RecordedRequests[0].Body.Length, RequestLimit);
            var body = JObject.Parse(Encoding.UTF8.GetString(http.RecordedRequests[0].Body));
            string uri = provider == "fal" ? (string)body["image_urls"][0]
                : provider == "meshy" ? (string)body["image_url"]
                : (string)body["messages"][0]["content"][1]["image_url"]["url"];
            StringAssert.StartsWith("data:image/png;base64,", uri);
            Assert.AreEqual(length, Convert.FromBase64String(uri.Substring(uri.IndexOf(',') + 1)).Length);
        }

        [TestCase(".PNG", "image/png")]
        [TestCase(".JpG", "image/jpeg")]
        [TestCase(".jpeg", "image/jpeg")]
        [TestCase(".WEBP", "image/webp")]
        [TestCase(".gif", "image/gif")]
        public async Task SupportedFormats_RelativeAndAbsolutePathsPreserved(string extension, string mime)
        {
            string path = WriteSized("format" + extension, 3);
            foreach (string input in new[] { path, AssetGenPaths.ToAbsolute(path) })
            {
                var http = Transport();
                await Submit("fal", input, http);
                string uri = (string)JObject.Parse(Encoding.UTF8.GetString(http.RecordedRequests[0].Body))["image_urls"][0];
                Assert.AreEqual("data:" + mime + ";base64,AAAA", uri);
            }
        }

        [Test]
        public void EscapedJson_SerializerRejectsBeforeWholeBodyAllocation()
        {
            string chunk = new string('\0', 4096);
            var tokens = new JArray();
            // Values share one small immutable string; escaped JSON exceeds the request budget.
            for (int i = 0; i < 2048; i++) tokens.Add(chunk);
            Assert.Throws<IOException>(() => Serialize(new JObject { ["p"] = tokens }));
        }

        [TestCase("fal")]
        [TestCase("openrouter")]
        [TestCase("meshy")]
        public void ExactRawBoundary_HandlerAcceptsWithoutFullEncoding(string provider)
        {
            string path = WriteSized("boundary.png", ImageLimit);
            Assert.AreEqual(true, (bool)Handle(provider, path)["success"]);
            Assert.AreEqual(1, AssetGenJobManager.RecentJobs().Count);
        }

        [Test]
        public void ImageLengthMath_AcceptsBoundaryAndRejectsOversizeBeforeAllocation()
        {
            Type type = typeof(FalAdapter).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Providers.LocalImage");
            MethodInfo method = type.GetMethod("RequireSize", BindingFlags.Static | BindingFlags.NonPublic);
            Invoke(method, (long)ImageLimit, "image/png");
            Assert.Throws<IOException>(() => Invoke(method, (long)ImageLimit + 1, "image/png"));
            Assert.Throws<IOException>(() => Invoke(method, long.MaxValue, "image/png"));
        }

        [TestCase("fal")]
        [TestCase("openrouter")]
        [TestCase("meshy")]
        public void FileGrowthAfterHandlerValidation_AdapterRechecksBeforeRequest(string provider)
        {
            string path = WriteSized("changed.png", 4);
            Assert.AreEqual(true, (bool)Handle(provider, path)["success"]);
            using (var stream = new FileStream(AssetGenPaths.ToAbsolute(path), FileMode.Open, FileAccess.Write))
                stream.SetLength(ImageLimit + 1);
            var http = Transport();
            Assert.ThrowsAsync<IOException>(async () => await Submit(provider, path, http));
            Assert.AreEqual(0, http.RecordedRequests.Count);
        }

        [TestCase("fal", "text")]
        [TestCase("openrouter", "text")]
        [TestCase("meshy", "text")]
        [TestCase("fal", "image")]
        [TestCase("openrouter", "image")]
        [TestCase("meshy", "image")]
        public async Task TextAndHostedImage_NormalRequestsPreserved(string provider, string mode)
        {
            var http = Transport();
            const string hosted = "https://fixture.invalid/ref.png";
            if (provider == "meshy")
                await new MeshyAdapter().SubmitAsync(new ModelGenRequest
                { Mode = mode, ImageUrl = hosted, Prompt = "한글😀" }, "fixture-only", http, CancellationToken.None);
            else
            {
                var request = new ImageGenRequest { Mode = mode, ImageUrl = hosted, Prompt = "한글😀" };
                if (provider == "fal") await new FalAdapter().SubmitAsync(request, "fixture-only", http, CancellationToken.None);
                else await new OpenRouterAdapter().SubmitAsync(request, "fixture-only", http, CancellationToken.None);
            }
            Assert.AreEqual(1, http.RecordedRequests.Count);
            string json = Encoding.UTF8.GetString(http.RecordedRequests[0].Body);
            StringAssert.Contains(mode == "image" ? hosted : "한글😀", json);
            Assert.AreEqual(json, JObject.Parse(json).ToString(Formatting.None));
        }

        [TestCase("Assets/../outside.png")]
        [TestCase("assets/file.png")]
        [TestCase("file.png")]
        public void UnsafePaths_RemainRejected(string path)
        {
            foreach (string provider in new[] { "fal", "openrouter", "meshy" })
                Assert.AreEqual(false, (bool)Handle(provider, path)["success"]);
            Assert.AreEqual(0, AssetGenJobManager.RecentJobs().Count);
        }

        [Test]
        public void UnsupportedExtension_RemainsRejected()
        {
            string path = WriteSized("unsupported.tga", 3);
            foreach (string provider in new[] { "fal", "openrouter", "meshy" })
                Assert.AreEqual(false, (bool)Handle(provider, path)["success"]);
            Assert.AreEqual(0, AssetGenJobManager.RecentJobs().Count);
        }

        [Test]
        public void SerializedRequest_ExactByteBoundaryAndUnicodeAccounting()
        {
            Type type = typeof(FalAdapter).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Providers.ProviderHttp+RequestByteCounter");
            using var counter = (Stream)Activator.CreateInstance(type, true);
            byte[] chunk = new byte[4096];
            for (int i = 0; i < RequestLimit / chunk.Length; i++) counter.Write(chunk, 0, chunk.Length);
            Assert.AreEqual(RequestLimit, counter.Length);
            Assert.Throws<IOException>(() => counter.WriteByte(0));
            var body = new JObject
            {
                ["p"] = "한글😀\ud800\u0000\"\\\u0085\u2028\u2029",
                ["integer"] = 123, ["float"] = 1.25, ["bool"] = true, ["null"] = null
            };
            CultureInfo previous = CultureInfo.CurrentCulture;
            try
            {
                foreach (string culture in new[] { "en-US", "fr-FR" })
                {
                    CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo(culture);
                    CollectionAssert.AreEqual(Encoding.UTF8.GetBytes(body.ToString(Formatting.None)), Serialize(body));
                }
            }
            finally { CultureInfo.CurrentCulture = previous; }
        }

        [TestCase(true)]
        [TestCase(false)]
        public void SerializerBodyChanges_RejectsGrowthAndTruncation(bool grows)
        {
            Type type = typeof(FalAdapter).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Providers.ProviderHttp");
            Assert.IsNotNull(type.GetMethod("SerializeRequest", BindingFlags.Static | BindingFlags.Public));
            Assert.Catch<Exception>(() => Serialize(new ChangingBody(grows)));
        }

        private sealed class ChangingBody : JObject
        {
            private readonly bool _grows;
            private int _writes;
            public ChangingBody(bool grows) { _grows = grows; this["p"] = "xx"; }
            public override void WriteTo(JsonWriter writer, params JsonConverter[] converters)
            {
                if (++_writes == 2) this["p"] = _grows ? "xxxx" : "x";
                base.WriteTo(writer, converters);
            }
        }

        private static byte[] Serialize(JObject body)
        {
            Type type = typeof(FalAdapter).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Providers.ProviderHttp");
            return (byte[])Invoke(type.GetMethod("SerializeRequest", BindingFlags.Static | BindingFlags.Public), body);
        }

        private static object Invoke(MethodInfo method, params object[] args)
        {
            try { return method.Invoke(null, args); }
            catch (TargetInvocationException e) { throw e.InnerException; }
        }

        [TestCase(ImageLimit + 1, 0, 0)]
        [TestCase(4, 5, 5)]
        [TestCase(4, 3, 3)]
        public void ActualReader_OversizedGrowthAndTruncationStayBounded(int advertised, int available, int expectedRead)
        {
            Type type = typeof(FalAdapter).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Providers.LocalImage");
            MethodInfo method = type.GetMethod("ReadDataUri", BindingFlags.Static | BindingFlags.NonPublic);
            using var stream = new ChangingStream(advertised, available);
            Assert.Throws<IOException>(() => Invoke(method, stream, "image/png"));
            Assert.AreEqual(expectedRead, stream.BytesRead);
            Assert.LessOrEqual(stream.LargestRequest, Math.Min(advertised, ImageLimit));
        }

        private sealed class ChangingStream : MemoryStream
        {
            private readonly long _advertised;
            public int BytesRead;
            public int LargestRequest;
            public ChangingStream(long advertised, int available) : base(new byte[available]) { _advertised = advertised; }
            public override long Length => _advertised;
            public override int Read(byte[] buffer, int offset, int count)
            {
                LargestRequest = Math.Max(LargestRequest, count);
                int read = base.Read(buffer, offset, count);
                BytesRead += read;
                return read;
            }
            public override int ReadByte()
            {
                int value = base.ReadByte();
                if (value >= 0) BytesRead++;
                return value;
            }
        }

        private sealed class FixtureKeys : ISecureKeyStore
        {
            public bool Has(string providerId) => true;
            public bool TryGet(string providerId, out string apiKey) { apiKey = "fixture-only"; return true; }
            public void Set(string providerId, string apiKey) { }
            public void Delete(string providerId) { }
        }
    }
}
