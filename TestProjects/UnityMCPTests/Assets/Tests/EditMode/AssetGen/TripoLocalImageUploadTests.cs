using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Tools.AssetGen;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class TripoLocalImageUploadTests
    {
        private const string Key = "gen09_synthetic_key_not_a_real_credential";
        private const string UploadToken = "file_gen09_synthetic_upload";
        private const string TaskId = "task_gen09_synthetic_receipt";
        private const string ApiRoot = "https://openapi.tripo3d.ai/v3";
        private const string IndexKey = "MCPForUnity.AssetGen.JobIndex";
        private const string Prompt = "GEN09 synthetic model prompt";
        private static readonly byte[] Png = Convert.FromBase64String(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGZkAAAAASUVORK5CYII="
        );

        private string _root;
        private string _absoluteRoot;
        private string _source;
        private string _absoluteSource;
        private FakeHttpTransport _http;
        private bool _ownsManager;
        private bool _ownsStore;
        private bool _ownsDirectory;

        [SetUp]
        public void SetUp()
        {
            _ownsManager = false;
            _ownsStore = false;
            _ownsDirectory = false;
            // The native launcher supplies a fresh owned project/session. Refuse existing jobs
            // before resetting process-local state; never run this fixture against a user's jobs.
            Assert.IsTrue(string.IsNullOrEmpty(SessionState.GetString(IndexKey, string.Empty)), "GEN09 requires an empty owned job session.");
            SecureKeyStore.OverrideForTests(new SyntheticKeyStore());
            _ownsStore = true;
            AssetGenJobManager.ResetForTests();
            _ownsManager = true;
            AssetGenJobManager.ClockForTests = () => 100.0;
            _http = new FakeHttpTransport { Handler = Respond };
            AssetGenJobManager.TransportOverrideForTests = _http;

            _root = "Assets/__gen09_upload_" + Guid.NewGuid().ToString("N");
            _absoluteRoot = SafePathUtility.ResolveWithinRoot(Application.dataPath, _root.Substring("Assets/".Length));
            Assert.IsFalse(Directory.Exists(_absoluteRoot));
            Assert.IsFalse(File.Exists(_absoluteRoot));
            string metadata = SafePathUtility.ResolveWithinRoot(Application.dataPath, _absoluteRoot + ".meta");
            Assert.IsFalse(File.Exists(metadata));
            Assert.IsFalse(Directory.Exists(metadata));
            Directory.CreateDirectory(_absoluteRoot);
            _ownsDirectory = true;
            _source = _root + "/reference.png";
            _absoluteSource = SafePathUtility.ResolveWithinRoot(Application.dataPath, _source.Substring("Assets/".Length));
            File.WriteAllBytes(_absoluteSource, Png);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (_ownsManager)
                    AssetGenJobManager.ResetForTests();
            }
            finally
            {
                if (_ownsStore)
                    SecureKeyStore.ResetForTests();
                if (_ownsDirectory)
                {
                    string path = SafePathUtility.ResolveWithinRoot(Application.dataPath, _absoluteRoot);
                    string meta = SafePathUtility.ResolveWithinRoot(Application.dataPath, path + ".meta");
                    Assert.AreEqual(_absoluteRoot, path);
                    Assert.IsTrue(_root.StartsWith("Assets/__gen09_upload_", StringComparison.Ordinal));
                    if (AssetDatabase.IsValidFolder(_root))
                        Assert.IsTrue(AssetDatabase.DeleteAsset(_root), "Owned GEN09 folder deletion failed.");
                    if (Directory.Exists(path))
                        Directory.Delete(path, true);
                    if (File.Exists(meta))
                        File.Delete(meta);
                    Assert.IsFalse(Directory.Exists(path));
                    Assert.IsFalse(File.Exists(meta));
                }
            }
        }

        [TestCase(false, true)]
        [TestCase(true, false)]
        public void LocalImage_PublicHandler_UploadsThenSubmits(bool absolutePath, bool texture)
        {
            JObject request = Request("image");
            request["imagePath"] = absolutePath ? _absoluteSource : _source;
            request["allowImageUpload"] = true;
            request["texture"] = texture;

            string id = StartAndAdvance(request);

            Assert.AreEqual(2, _http.RecordedRequests.Count, "One file upload must precede one generation submission.");
            HttpRequestSpec upload = _http.RecordedRequests[0];
            Assert.AreEqual("POST", upload.Method);
            Assert.AreEqual(ApiRoot + "/files", upload.Url);
            Assert.AreEqual("Bearer " + Key, upload.Headers["Authorization"]);
            AssertMultipartContainsOnlyOriginalPng(upload);
            AssertGeneration(_http.RecordedRequests[1], "image-to-model", UploadToken, texture);
            AssertReceiptIsPrivate(id);
            AssertSourceUnchangedAndNoOutput();
        }

        [Test]
        public void HostedImage_PublicHandler_UsesV3WithoutReadingLocalFile()
        {
            const string hosted = "https://fixture.invalid/gen09-reference.png";
            JObject request = Request("image");
            request["imageUrl"] = hosted;
            request["imagePath"] = _root + "/does-not-exist.png";
            request["allowImageUpload"] = false;

            string id = StartAndAdvance(request);

            Assert.AreEqual(1, _http.RecordedRequests.Count, "Hosted URL precedence must not upload or read the missing local path.");
            AssertGeneration(_http.RecordedRequests[0], "image-to-model", hosted, true);
            AssertReceiptIsPrivate(id);
            AssertSourceUnchangedAndNoOutput();
        }

        [Test]
        public void Text_PublicHandler_UsesV3WithExistingModelAndOptions()
        {
            JObject request = Request("text");
            request["prompt"] = Prompt;
            request["model"] = "P1-20260311";
            request["texture"] = false;
            request["imagePath"] = _root + "/does-not-exist.png";

            string id = StartAndAdvance(request);

            Assert.AreEqual(1, _http.RecordedRequests.Count);
            HttpRequestSpec sent = _http.RecordedRequests[0];
            Assert.AreEqual("POST", sent.Method);
            Assert.AreEqual(ApiRoot + "/generation/text-to-model", sent.Url);
            JObject body = JObject.Parse(Encoding.UTF8.GetString(sent.Body));
            Assert.AreEqual(Prompt, (string)body["prompt"]);
            Assert.AreEqual("P1-20260311", (string)body["model"]);
            Assert.AreEqual(false, (bool)body["texture"]);
            Assert.AreEqual(false, (bool)body["pbr"]);
            Assert.IsNull(body["type"]);
            Assert.IsNull(body["model_version"]);
            Assert.IsNull(body["input"]);
            AssertReceiptIsPrivate(id);
            AssertSourceUnchangedAndNoOutput();
        }

        [TestCase("missing")]
        [TestCase("false")]
        [TestCase("string_true")]
        public void LocalImage_WithoutLiteralConsent_RefusesBeforeJobOrRequest(string consent)
        {
            JObject request = Request("image");
            request["imagePath"] = _source;
            if (consent == "false")
                request["allowImageUpload"] = false;
            else if (consent == "string_true")
                request["allowImageUpload"] = "true";

            JObject result = Call(request);

            Assert.AreEqual(false, (bool)result["success"]);
            Assert.AreEqual(0, _http.RecordedRequests.Count);
            Assert.AreEqual(0, AssetGenJobManager.RecentJobs().Count);
            Assert.IsTrue(string.IsNullOrEmpty(SessionState.GetString(IndexKey, string.Empty)));
            StringAssert.DoesNotContain(Key, result.ToString(Formatting.None));
            AssertSourceUnchangedAndNoOutput();
        }

        [TestCase(".jpg")]
        [TestCase(".jpeg")]
        [TestCase(".JPG")]
        public void LocalJpeg_UsesConstantFilenameAndPreservesBytes(string extension)
        {
            byte[] jpeg = { 0xff, 0xd8, 0xff, 0x00, 0x7f };
            string path = _absoluteRoot + "/private-original-name" + extension;
            File.WriteAllBytes(path, jpeg);
            var request = new ModelGenRequest
            {
                Mode = "image",
                ImagePath = path,
                AllowImageUpload = true,
            };
            new TripoAdapter().SubmitAsync(request, Key, _http, CancellationToken.None).GetAwaiter().GetResult();
            HttpRequestSpec upload = _http.RecordedRequests[0];
            string boundary = upload.ContentType.Substring("multipart/form-data; boundary=".Length);
            byte[] prefix = Encoding.ASCII.GetBytes(
                "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"image.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"
            );
            byte[] suffix = Encoding.ASCII.GetBytes("\r\n--" + boundary + "--\r\n");
            byte[] expected = new byte[prefix.Length + jpeg.Length + suffix.Length];
            Buffer.BlockCopy(prefix, 0, expected, 0, prefix.Length);
            Buffer.BlockCopy(jpeg, 0, expected, prefix.Length, jpeg.Length);
            Buffer.BlockCopy(suffix, 0, expected, prefix.Length + jpeg.Length, suffix.Length);
            CollectionAssert.AreEqual(expected, upload.Body);
            CollectionAssert.AreEqual(jpeg, File.ReadAllBytes(path));
            Assert.AreEqual(2, _http.RecordedRequests.Count);
        }

        [TestCase("missing")]
        [TestCase("extension")]
        [TestCase("empty")]
        [TestCase("signature")]
        [TestCase("oversize")]
        [TestCase("traversal")]
        [TestCase("uri")]
        [TestCase("control")]
        [TestCase("long")]
        public void LocalInputRefusal_HappensBeforeJobOrHttp(string defect)
        {
            string path = _source;
            if (defect == "missing")
                path += ".missing.png";
            if (defect == "extension")
            {
                path = _root + "/input.gif";
                File.WriteAllBytes(_absoluteRoot + "/input.gif", Png);
            }
            if (defect == "empty")
                File.WriteAllBytes(_absoluteSource, Array.Empty<byte>());
            if (defect == "signature")
                File.WriteAllBytes(_absoluteSource, new byte[] { 1, 2, 3 });
            if (defect == "oversize")
                using (var file = new FileStream(_absoluteSource, FileMode.Open, FileAccess.Write))
                    file.SetLength(20_000_001);
            if (defect == "traversal")
                path = _root + "/../" + Path.GetFileName(_absoluteRoot) + "/reference.png";
            if (defect == "uri")
                path = new Uri(_absoluteSource).AbsoluteUri;
            if (defect == "control")
                path += "\n";
            if (defect == "long")
                path = "Assets/" + new string('x', 2049) + ".png";
            JObject request = Request("image");
            request["imagePath"] = path;
            request["allowImageUpload"] = true;
            JObject result = Call(request);
            if (defect == "signature")
            {
                // Header checks run during bounded reading, after handler admission but before HTTP.
                string id = (string)result["data"]?["job_id"];
                Assert.IsNotNull(id);
                AssetGenJobManager.TryAdvanceForTests(id);
                AssetGenJobManager.TryAdvanceForTests(id);
                Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(id).State);
            }
            else
            {
                Assert.AreEqual(false, (bool)result["success"]);
                Assert.AreEqual(0, AssetGenJobManager.RecentJobs().Count);
            }
            Assert.IsEmpty(_http.RecordedRequests);
            StringAssert.DoesNotContain(_absoluteRoot, result.ToString(Formatting.None));
        }

        [TestCase(0, 0)]
        [TestCase(20_000_001, 0)]
        [TestCase(12, 8)]
        [TestCase(8, 9)]
        public void MultipartStream_RejectsBoundsTruncationAndGrowth(long declared, int available)
        {
            using var stream = new SizedStream(declared, available);
            Assert.Throws<InvalidDataException>(() => TripoLocalImageUpload.ReadMultipart(stream, ".png", CancellationToken.None));
            if (declared <= 0 || declared > 20_000_000)
                Assert.AreEqual(0, stream.Reads, "Length must be admitted before reading or allocating the image buffer.");
        }

        [Test]
        public void MultipartStream_ExactLimitIsBoundedAndChecksCancellationBetweenChunks()
        {
            using var exact = new SizedStream(20_000_000, 20_000_000);
            using var upload = TripoLocalImageUpload.ReadMultipart(exact, ".png", CancellationToken.None);
            Assert.Greater(upload.Body.Length, 20_000_000);
            Assert.LessOrEqual(upload.Body.Length, 20_000_000 + 4096);
            using var cancellation = new CancellationTokenSource();
            using var interrupted = new SizedStream(100_000, 100_000) { AfterRead = cancellation.Cancel };
            Assert.Throws<OperationCanceledException>(() => TripoLocalImageUpload.ReadMultipart(interrupted, ".png", cancellation.Token));
            Assert.AreEqual(1, interrupted.Reads);
            using var neverRead = new SizedStream(8, 8);
            Assert.Throws<OperationCanceledException>(() => TripoLocalImageUpload.ReadMultipart(neverRead, ".png", cancellation.Token));
            Assert.AreEqual(0, neverRead.Reads);
        }

        [TestCase("null")]
        [TestCase("[]")]
        [TestCase("42")]
        [TestCase("\"https://fixture.invalid/token\"")]
        [TestCase("\"bad\\nvalue\"")]
        [TestCase("\"\"")]
        [TestCase("{}")]
        public void Upload_InvalidTokenNeverSubmitsGenerationOrLeaksResponse(string tokenJson)
        {
            _http.Handler = _ => JsonResponse("{\"code\":0,\"private\":\"" + Key + "\",\"data\":{\"file_token\":" + tokenJson + "}}");
            Exception error = Assert.Throws<InvalidDataException>(() => SubmitLocal(_http));
            Assert.AreEqual(1, _http.RecordedRequests.Count);
            StringAssert.DoesNotContain(Key, error.ToString());
            StringAssert.DoesNotContain("fixture.invalid", error.ToString());
            Assert.IsTrue(string.IsNullOrEmpty(SessionState.GetString(IndexKey, string.Empty)));
        }

        [TestCase("oversize")]
        [TestCase("depth")]
        [TestCase("duplicate")]
        [TestCase("string_code")]
        [TestCase("http_error")]
        [TestCase("body_error")]
        [TestCase("transport")]
        public void Upload_FailureDoesNotExposeSensitivePayloadOrStartGeneration(string failure)
        {
            string marker = Key + _absoluteSource + Prompt + UploadToken;
            string response = "{\"code\":0,\"data\":{\"file_token\":\"ok\"},\"private\":" + JsonConvert.SerializeObject(marker) + "}";
            if (failure == "oversize")
                response = new string('x', 65_537);
            if (failure == "depth")
                response = "{\"code\":0,\"deep\":" + new string('[', 20) + "0" + new string(']', 20) + "}";
            if (failure == "duplicate")
                response = "{\"code\":0,\"code\":0,\"data\":{\"file_token\":\"ok\"}}";
            if (failure == "string_code")
                response = response.Replace("\"code\":0", "\"code\":\"0\"");
            if (failure == "body_error")
                response = response.Replace("\"code\":0", "\"code\":42");
            _http.Handler = _ => failure == "transport" ? throw new IOException(marker) : JsonResponse(response, failure == "http_error" ? 500 : 200);
            Exception error = Assert.Catch(() => SubmitLocal(_http));
            Assert.AreEqual(1, _http.RecordedRequests.Count);
            foreach (string secret in new[] { Key, _absoluteSource, Prompt, UploadToken })
                StringAssert.DoesNotContain(secret, error.ToString());
        }

        [TestCase(1, true)]
        [TestCase(256, true)]
        [TestCase(257, false)]
        public void Upload_TokenHasBoundedAsciiShapeWithoutRequiredPrefix(int length, bool accepted)
        {
            string token = new string('a', length);
            _http.Handler = spec =>
                spec.Url.EndsWith("/files", StringComparison.Ordinal)
                    ? JsonResponse("{\"code\":0,\"data\":{\"file_token\":\"" + token + "\"}}")
                    : JsonResponse("{\"code\":0,\"data\":{\"task_id\":\"" + TaskId + "\"}}");
            if (accepted)
            {
                SubmitLocal(_http);
                Assert.AreEqual(token, (string)JObject.Parse(Encoding.UTF8.GetString(_http.RecordedRequests[1].Body))["input"]);
                Assert.AreEqual(2, _http.RecordedRequests.Count);
            }
            else
            {
                Assert.Throws<InvalidDataException>(() => SubmitLocal(_http));
                Assert.AreEqual(1, _http.RecordedRequests.Count);
            }
        }

        [Test]
        public void Upload_TokenCannotBecomeTheSavedGenerationReceipt()
        {
            _http.Handler = spec =>
                JsonResponse(
                    spec.Url.EndsWith("/files", StringComparison.Ordinal)
                        ? "{\"code\":0,\"data\":{\"file_token\":\"" + UploadToken + "\"}}"
                        : "{\"code\":0,\"data\":{\"task_id\":\"" + UploadToken + "\"}}"
                );
            JObject request = Request("image");
            request["imagePath"] = _source;
            request["allowImageUpload"] = true;
            string id = StartAndAdvance(request);
            AssetGenJobManager.TryAdvanceForTests(id);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(id).State);
            Assert.IsTrue(string.IsNullOrEmpty(AssetGenJobManager.GetJob(id).ProviderTaskId));
            StringAssert.DoesNotContain(UploadToken, SessionState.GetString("MCPForUnity.AssetGen.Job." + id, string.Empty));
            Assert.AreEqual(2, _http.RecordedRequests.Count);
        }

        [Test]
        public void Upload_LateResponseAfterCancellationCannotStartGeneration()
        {
            using var cancellation = new CancellationTokenSource();
            var http = new DelayedUpload();
            Task<string> pending = new TripoAdapter().SubmitAsync(LocalRequest(), Key, http, cancellation.Token);
            Assert.AreEqual(1, http.Calls);
            cancellation.Cancel();
            http.Pending.SetResult(JsonResponse("{\"code\":0,\"data\":{\"file_token\":\"" + UploadToken + "\"}}"));
            Assert.Throws<OperationCanceledException>(() => pending.GetAwaiter().GetResult());
            Assert.AreEqual(1, http.Calls);
        }

        private ModelGenRequest LocalRequest() =>
            new ModelGenRequest
            {
                Mode = "image",
                ImagePath = _source,
                AllowImageUpload = true,
            };

        private void SubmitLocal(IHttpTransport http) =>
            new TripoAdapter().SubmitAsync(LocalRequest(), Key, http, CancellationToken.None).GetAwaiter().GetResult();

        private static HttpResult JsonResponse(string value, int status = 200) =>
            new HttpResult
            {
                Status = status,
                IsSuccess = status == 200,
                Text = value,
            };

        private sealed class DelayedUpload : IHttpTransport
        {
            internal int Calls;
            internal readonly TaskCompletionSource<HttpResult> Pending = new();

            public Task<HttpResult> SendAsync(HttpRequestSpec spec, CancellationToken cancellation)
            {
                Calls++;
                return Pending.Task;
            }
        }

        private sealed class SizedStream : Stream
        {
            private readonly long _declared;
            private readonly long _available;
            private long _position;
            internal int Reads;
            internal Action AfterRead;

            internal SizedStream(long declared, long available)
            {
                _declared = declared;
                _available = available;
            }

            public override long Length => _declared;
            public override bool CanRead => true;
            public override bool CanSeek => false;
            public override bool CanWrite => false;
            public override long Position
            {
                get => _position;
                set => throw new NotSupportedException();
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                Reads++;
                int length = (int)Math.Min(count, _available - _position);
                for (int i = 0; i < length; i++)
                    buffer[offset + i] = _position + i < 8 ? Png[(int)(_position + i)] : (byte)0;
                _position += length;
                AfterRead?.Invoke();
                return length;
            }

            public override void Flush() => throw new NotSupportedException();

            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        }

        private JObject Request(string mode) =>
            new JObject
            {
                ["action"] = "generate",
                ["provider"] = "tripo",
                ["mode"] = mode,
                ["model"] = "v3.1-20260211",
                ["format"] = "glb",
                ["targetSize"] = 0,
                ["name"] = "gen09_fixture_model",
                ["outputFolder"] = _root + "/Output",
            };

        private static JObject Call(JObject request) => JObject.Parse(JsonConvert.SerializeObject(GenerateModel.HandleCommand(request)));

        private static string StartAndAdvance(JObject request)
        {
            JObject result = Call(request);
            Assert.AreEqual("pending", (string)result["_mcp_status"], "Expected an admitted generation job; actual: " + result.ToString(Formatting.None));
            string id = (string)result["data"]?["job_id"];
            Assert.IsFalse(string.IsNullOrEmpty(id));
            AssetGenJobManager.TryAdvanceForTests(id);
            return id;
        }

        private static HttpResult Respond(HttpRequestSpec spec)
        {
            Assert.AreEqual("POST", spec.Method, "Baseline scope never polls, downloads, or imports.");
            return new HttpResult
            {
                Status = 200,
                IsSuccess = true,
                Text =
                    spec.Url == ApiRoot + "/files"
                        ? "{\"code\":0,\"data\":{\"file_token\":\"" + UploadToken + "\"}}"
                        : "{\"code\":0,\"data\":{\"task_id\":\"" + TaskId + "\"}}",
            };
        }

        private static void AssertGeneration(HttpRequestSpec sent, string endpoint, string input, bool texture)
        {
            Assert.AreEqual("POST", sent.Method);
            Assert.AreEqual(ApiRoot + "/generation/" + endpoint, sent.Url);
            Assert.AreEqual("application/json", sent.ContentType);
            Assert.AreEqual("Bearer " + Key, sent.Headers["Authorization"]);
            JObject body = JObject.Parse(Encoding.UTF8.GetString(sent.Body));
            Assert.AreEqual(input, (string)body["input"]);
            Assert.AreEqual("v3.1-20260211", (string)body["model"]);
            Assert.AreEqual(texture, (bool)body["texture"]);
            Assert.AreEqual(texture, (bool)body["pbr"]);
            Assert.IsNull(body["type"]);
            Assert.IsNull(body["model_version"]);
            Assert.IsNull(body["file"]);
        }

        private void AssertMultipartContainsOnlyOriginalPng(HttpRequestSpec upload)
        {
            const string contentTypePrefix = "multipart/form-data; boundary=";
            StringAssert.StartsWith(contentTypePrefix, upload.ContentType);
            string boundary = upload.ContentType.Substring(contentTypePrefix.Length);
            Assert.IsTrue(boundary.Length > 0 && boundary.Length <= 128);
            string prefix = "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"image.png\"\r\nContent-Type: image/png\r\n\r\n";
            byte[] prefixBytes = Encoding.ASCII.GetBytes(prefix);
            byte[] suffixBytes = Encoding.ASCII.GetBytes("\r\n--" + boundary + "--\r\n");
            Assert.AreEqual(prefixBytes.Length + Png.Length + suffixBytes.Length, upload.Body.Length);
            byte[] expected = new byte[upload.Body.Length];
            Buffer.BlockCopy(prefixBytes, 0, expected, 0, prefixBytes.Length);
            Buffer.BlockCopy(Png, 0, expected, prefixBytes.Length, Png.Length);
            Buffer.BlockCopy(suffixBytes, 0, expected, prefixBytes.Length + Png.Length, suffixBytes.Length);
            CollectionAssert.AreEqual(expected, upload.Body, "Multipart must carry exactly the original fixture bytes in one part.");
            StringAssert.DoesNotContain(_source, prefix);
            StringAssert.DoesNotContain(_absoluteSource, prefix);
            StringAssert.DoesNotContain(Key, prefix);
        }

        private void AssertReceiptIsPrivate(string id)
        {
            AssetGenJob job = AssetGenJobManager.GetJob(id);
            Assert.AreEqual(TaskId, job.ProviderTaskId);
            string record = SessionState.GetString("MCPForUnity.AssetGen.Job." + id, string.Empty);
            Assert.IsFalse(string.IsNullOrEmpty(record));
            StringAssert.DoesNotContain(UploadToken, record);
            StringAssert.DoesNotContain(Key, record);
            StringAssert.DoesNotContain(Prompt, record);
            StringAssert.DoesNotContain(_source, record);
            StringAssert.DoesNotContain(_absoluteSource, record);
            StringAssert.DoesNotContain(Convert.ToBase64String(Png), record);
            Assert.AreEqual(TaskId, (string)JObject.Parse(record)["_resume_v1"]?["Receipt"]);
        }

        private void AssertSourceUnchangedAndNoOutput()
        {
            CollectionAssert.AreEqual(Png, File.ReadAllBytes(_absoluteSource));
            Assert.IsFalse(Directory.Exists(SafePathUtility.ResolveWithinRoot(Application.dataPath, _absoluteRoot + "/Output")));
        }

        private sealed class SyntheticKeyStore : ISecureKeyStore
        {
            public bool TryGet(string providerId, out string apiKey)
            {
                apiKey = providerId == "tripo" ? Key : null;
                return apiKey != null;
            }

            public bool Has(string providerId) => providerId == "tripo";

            public void Set(string providerId, string apiKey) => throw new NotSupportedException("Fixture key store is read-only.");

            public void Delete(string providerId) => throw new NotSupportedException("Fixture key store is read-only.");
        }
    }
}
