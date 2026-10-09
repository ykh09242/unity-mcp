using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetGenProviderPayloadLifetimeTests
    {
        private const int PayloadBytes = 4 * 1024 * 1024;
        private string _folder;
        private bool _ownsFolder;
        private ISecureKeyStore _previousStore;
        private InlineTransport _transport;
        private WeakReference _payload;
        private WeakReference _adapter;
        private AssetGenJob _job;
        private int _imports;
        private bool _aliveDuringImport;
        private bool _adapterDuringImport;

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            _ownsFolder = false;
            _previousStore = (ISecureKeyStore)typeof(SecureKeyStore).GetField("_current", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            SecureKeyStore.OverrideForTests(new SyntheticStore());
            _folder = "Assets/__McpProviderPayload_" + Guid.NewGuid().ToString("N");
            string absolute = AssetGenPaths.ToAbsolute(_folder);
            Assert.IsFalse(Directory.Exists(absolute) || File.Exists(absolute));
            Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder)));
            _ownsFolder = true;
            _transport = new InlineTransport();
            AssetGenJobManager.TransportOverrideForTests = _transport;
            AssetGenJobManager.SkipModelVerificationForTests = true;
            AssetGenJobManager.ImportOverrideForTests = ObserveImport;
            _imports = 0;
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.OverrideForTests(_previousStore);
            if (_ownsFolder)
                AssetDatabase.DeleteAsset(_folder);
        }

        private static bool Alive(WeakReference weak)
        {
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
            return weak.IsAlive;
        }

        private ImageGenRequest Request(bool imageApi) =>
            new ImageGenRequest
            {
                Provider = "openrouter",
                Prompt = "synthetic",
                OutputFolder = _folder,
                Name = "payload",
                AsSprite = false,
                Transparent = true,
                CatalogEntry = imageApi ? new ModelEntry { RouterProviderTag = "synthetic", OutputFormat = "png" } : null,
            };

        [MethodImpl(MethodImplOptions.NoInlining)]
        private void Submit(bool imageApi)
        {
            _job = AssetGenJobManager.StartImageGeneration(Request(imageApi));
            AssetGenJobManager.TryAdvanceForTests(_job.JobId);
            // Observe the actual adapter-owned allocation; do not inject Runner or task state.
            var runners = (IDictionary)typeof(AssetGenJobManager).GetField("Runners", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            object runner = runners[_job.JobId];
            var submit = (Delegate)runner.GetType().GetField("SubmitFn").GetValue(runner);
            object adapter = submit.Target.GetType().GetField("adapter", BindingFlags.Instance | BindingFlags.Public).GetValue(submit.Target);
            byte[] bytes = (byte[])typeof(OpenRouterAdapter).GetField("_inlineData", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(adapter);
            Assert.AreEqual(PayloadBytes, bytes.Length);
            _payload = new WeakReference(bytes);
            _adapter = new WeakReference(adapter);
            AssetGenJobManager.TryAdvanceForTests(_job.JobId);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private void PrepareForImport(bool imageApi)
        {
            Submit(imageApi);
            Assert.IsTrue(Alive(_payload), "Payload must remain available until the poll result is consumed.");
            AssetGenJobManager.TryAdvanceForTests(_job.JobId);
            AssetGenJobManager.TryAdvanceForTests(_job.JobId);
            Assert.AreEqual(AssetGenJobState.Importing, _job.State);
        }

        private AssetGenJob ObserveImport(AssetGenJob job, string path)
        {
            _imports++;
            _aliveDuringImport = Alive(_payload);
            _adapterDuringImport = Alive(_adapter);
            using (var hash = SHA256.Create())
            using (var file = File.OpenRead(AssetGenPaths.ToAbsolute(path)))
            {
                Assert.AreEqual(PayloadBytes, file.Length);
                CollectionAssert.AreEqual(_transport.Hash, hash.ComputeHash(file));
            }
            Console.WriteLine($"PROVIDER_PAYLOAD bytes={PayloadBytes} alive_at_import={_aliveDuringImport} adapter_alive_at_import={_adapterDuringImport}");
            job.AssetPath = path;
            return job;
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ConsumedInlineResult_ReleasesProviderPayloadBeforeImport(bool imageApi)
        {
            PrepareForImport(imageApi);
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(_job.JobId));
            Assert.AreEqual(AssetGenJobState.Done, _job.State);
            Assert.AreEqual(1, _imports);
            Assert.IsFalse(_aliveDuringImport, "Adapter-owned inline bytes remain alive during import after delivery.");
            Assert.IsFalse(_adapterDuringImport, "Completed provider adapter remains rooted during import.");
            Assert.AreEqual(1, _transport.Calls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CancelAfterSubmission_ReleasesProviderPayloadWithoutImport(bool imageApi)
        {
            Submit(imageApi);
            Assert.IsTrue(Alive(_payload));
            Assert.IsTrue(AssetGenJobManager.Cancel(_job.JobId));
            Assert.IsTrue(AssetGenJobManager.TryAdvanceForTests(_job.JobId));
            Assert.AreEqual(AssetGenJobState.Canceled, _job.State);
            Assert.AreEqual(0, _imports);
            Assert.IsFalse(Alive(_payload));
            Assert.IsFalse(Alive(_adapter));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void Adapter_RepeatedPollsPreserveBorrowedResult(bool imageApi)
        {
            var adapter = new OpenRouterAdapter();
            string id = adapter.SubmitAsync(Request(imageApi), "synthetic-only", _transport, CancellationToken.None).GetAwaiter().GetResult();
            ProviderPollResult first = adapter.PollAsync(id, "synthetic-only", _transport, CancellationToken.None).GetAwaiter().GetResult();
            ProviderPollResult second = adapter.PollAsync(id, "synthetic-only", _transport, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(ProviderPollState.Succeeded, first.State);
            Assert.AreEqual(first.State, second.State);
            Assert.AreSame(first.InlineData, second.InlineData);
            Assert.AreEqual(PayloadBytes, second.InlineData.Length);
            Assert.AreEqual(first.ResultExt, second.ResultExt);
        }

        private sealed class InlineTransport : IHttpTransport
        {
            public byte[] Hash;
            public int Calls;

            [MethodImpl(MethodImplOptions.NoInlining)]
            public Task<HttpResult> SendAsync(HttpRequestSpec spec, CancellationToken ct)
            {
                Calls++;
                byte[] bytes = new byte[PayloadBytes];
                Array.Fill(bytes, (byte)37);
                using (var hash = SHA256.Create())
                    Hash = hash.ComputeHash(bytes);
                string b64 = Convert.ToBase64String(bytes);
                string text = spec.Url.EndsWith("/images")
                    ? "{\"data\":[{\"media_type\":\"image/png\",\"b64_json\":\"" + b64 + "\"}]}"
                    : "{\"choices\":[{\"message\":{\"images\":[{\"image_url\":{\"url\":\"data:image/png;base64," + b64 + "\"}}]}}]}";
                return Task.FromResult(new HttpResult { Status = 200, Text = text });
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
