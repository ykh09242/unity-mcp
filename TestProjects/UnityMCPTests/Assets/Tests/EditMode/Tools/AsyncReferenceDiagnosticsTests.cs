using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Stopwatch = System.Diagnostics.Stopwatch;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class AsyncReferenceDiagnosticsTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("Asynchronous read leases require verified Windows sharing semantics.");
            root = "Assets/__McpAsyncReferences_" + Guid.NewGuid().ToString("N");
            Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", root.Substring(7)));
            AssetDatabase.DisallowAutoRefresh();
            CommandRegistry.Initialize();
        }

        [TearDown]
        public void TearDown()
        {
            if (root == null)
                return;
            try
            {
                StringAssert.StartsWith("Assets/__McpAsyncReferences_", root);
                Assert.IsTrue(AssetDatabase.DeleteAsset(root));
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        private string Asset(int mib)
        {
            string path = root + "/source" + mib + ".bytes";
            var buffer = new byte[64 * 1024];
            new System.Random(311).NextBytes(buffer);
            using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                for (int i = 0; i < mib * 16; i++)
                    file.Write(buffer, 0, buffer.Length);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            return path;
        }

        private JObject Request(string path) =>
            new JObject
            {
                ["action"] = "get_dependencies",
                ["path"] = path,
                ["scanRoot"] = root,
                ["maxAssets"] = 250000,
                ["maxWork"] = 4000000,
            };

        private static JObject Success(object value)
        {
            var response = JObject.FromObject(value);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        [Test]
        public async Task LargeReadYieldsAndPreservesExactStrongRevision()
        {
            string path = Asset(17);
            JObject request = Request(path);
            JObject expected = Success(ManageAsset.HandleCommand(request));
            Task<object> pending = CommandRegistry.InvokeCommandAsync("asset_reference_diagnostics", request);
            Assert.IsFalse(pending.IsCompleted, "Complete file hashing must yield the Editor thread.");
            Assert.IsTrue(JToken.DeepEquals(expected, Success(await pending)));
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        [Test]
        public async Task CancellationSettlesTheActualFileWorkerAndReleasesLeases()
        {
            string path = Asset(17);
            using (var cancel = new CancellationTokenSource())
            {
                Task<object> pending = CommandRegistry.InvokeCommandAsync("asset_reference_diagnostics", Request(path), cancel.Token);
                Assert.IsFalse(pending.IsCompleted);
                cancel.Cancel();
                try
                {
                    await pending;
                    Assert.Fail("Canceled hashing cannot publish a reference revision.");
                }
                catch (OperationCanceledException) { }
            }
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            using (File.Open(path + ".meta", FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
        }

        [Test]
        public async Task SameLengthWritesCannotSlipBetweenHashAndEditorContinuation()
        {
            string path = Asset(17);
            var request = Request(path);
            Task<object> pending = CommandRegistry.InvokeCommandAsync("asset_reference_diagnostics", request);
            Assert.IsFalse(pending.IsCompleted);
            Assert.Throws<IOException>(() =>
            {
                using (File.Open(path, FileMode.Open, FileAccess.Write, FileShare.ReadWrite)) { }
            });
            JObject first = Success(await pending);
            var time = File.GetLastWriteTimeUtc(path);
            using (var file = File.Open(path, FileMode.Open, FileAccess.Write, FileShare.None))
                file.WriteByte(0xFE);
            File.SetLastWriteTimeUtc(path, time);
            request["referenceRevision"] = first["referenceRevision"];
            JObject changed = JObject.FromObject(await CommandRegistry.InvokeCommandAsync("asset_reference_diagnostics", request));
            Assert.IsFalse(changed.Value<bool>("success"), changed.ToString());
            Assert.AreEqual("reference_revision_conflict", changed.Value<string>("error"));
        }

        [Test]
        public async Task ReloadCleanupDrainsReadersAndDoesNotPoisonLaterRequests()
        {
            string path = Asset(17);
            Task<object> pending = CommandRegistry.InvokeCommandAsync("asset_reference_diagnostics", Request(path));
            Assert.IsFalse(pending.IsCompleted);
            Type preparation = typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceFilePreparation", true);
            preparation.GetMethod("BeforeReload", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            try
            {
                await pending;
                Assert.Fail("Reload cleanup must cancel the pending observation.");
            }
            catch (OperationCanceledException) { }
            using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            Success(await CommandRegistry.InvokeCommandAsync("asset_reference_diagnostics", Request(path)));
        }

        [Test, Explicit("Owned complete-source timing and update-gap comparison.")]
        public async Task Complete17_64_128MiBSourcesRetainStrongRevisionWithResponsiveUpdates()
        {
            foreach (int mib in new[] { 17, 64, 128 })
            {
                string path = Asset(mib);
                JObject request = Request(path);
                JObject expected = Success(ManageAsset.HandleCommand(request));
                for (int round = 0; round < 4; round++)
                for (int position = 0; position < 2; position++)
                {
                    bool asynchronous = (round + position) % 2 == 0;
                    int updates = 0;
                    double previous = 0,
                        gap = 0;
                    var timer = Stopwatch.StartNew();
                    void Observe()
                    {
                        double now = timer.Elapsed.TotalMilliseconds;
                        gap = Math.Max(gap, now - previous);
                        previous = now;
                        updates++;
                    }
                    EditorApplication.update += Observe;
                    try
                    {
                        Task<object> pending = asynchronous
                            ? CommandRegistry.InvokeCommandAsync("asset_reference_diagnostics", request)
                            : Task.FromResult(ManageAsset.HandleCommand(request));
                        double dispatch = timer.Elapsed.TotalMilliseconds;
                        JObject result = Success(await pending);
                        Observe();
                        Assert.IsTrue(JToken.DeepEquals(expected, result));
                        TestContext.WriteLine(
                            $"B13F03 mode={(asynchronous ? "async" : "sync")} mib={mib} round={round} dispatch_ms={dispatch:F3} total_ms={timer.Elapsed.TotalMilliseconds:F3} updates={updates} max_gap_ms={gap:F3}"
                        );
                    }
                    finally
                    {
                        EditorApplication.update -= Observe;
                    }
                }
            }
        }
    }
}
