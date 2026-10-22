using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class ReferenceGraphRuntimeTests
    {
        private static readonly string Domain = Guid.NewGuid().ToString("N");
        private readonly List<string> captures = new List<string>();

        internal static JObject Call(string action, string token, long offset = 0, int pageSize = 128)
        {
            var type = typeof(ManageReferenceGraph).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphRuntimeCapture");
            return (JObject)
                type.GetMethod("Handle", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(
                        null,
                        new object[]
                        {
                            new JObject
                            {
                                ["action"] = action,
                                ["token"] = token,
                                ["offset"] = offset,
                                ["page_size"] = pageSize,
                            },
                        }
                    );
        }

        private string Start()
        {
            string token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            Call("runtime_start", token);
            captures.Add(token);
            return token;
        }

        [TearDown]
        public void TearDown()
        {
            foreach (string token in captures)
                Call("runtime_release", token);
            captures.Clear();
        }

        [Test]
        public void DisabledHooksDoNotRetainResourcesOrCreateTokens()
        {
            Assert.IsFalse(ReferenceGraphRuntime.Enabled);
            ReferenceGraphRuntime.Load("resources", "disabled");
            Assert.IsNull(ReferenceGraphRuntime.Acquire("addressables", "disabled"));
            ReferenceGraphRuntime.Release("unknown");
            Assert.IsFalse(ReferenceGraphRuntime.Enabled);
        }

        [Test]
        public void IndependentCapturesKeepPagedHistoryAndStopSeparately()
        {
            string first = Start(),
                second = Start();
            for (int i = 0; i < 1201; i++)
                ReferenceGraphRuntime.Load("resources", "dynamic/" + i);
            var keys = new List<string>();
            long offset = 0;
            while (true)
            {
                JObject page = Call("runtime_read", first, offset, 137);
                keys.AddRange(page["events"].Select(row => row.Value<string>("key")));
                if (!page.Value<bool>("has_more"))
                    break;
                offset = page.Value<long>("next_offset");
            }
            Assert.AreEqual(1201, keys.Count);
            Assert.AreEqual("dynamic/1200", keys.Last());
            Call("runtime_stop", first);
            ReferenceGraphRuntime.Load("addressables", "later");
            Assert.AreEqual(1201, Call("runtime_read", first).Value<int>("count"));
            Assert.AreEqual(1202, Call("runtime_read", second).Value<int>("count"));
        }

        [Test]
        public void FailingObserverCannotBreakAnotherConsumerOrGameplay()
        {
            string token = Start();
            using (ReferenceGraphRuntime.Observe(_ => throw new IOException("owned observer failure")))
            {
                Assert.DoesNotThrow(() => ReferenceGraphRuntime.Load("resources", "kept"));
                ReferenceGraphRuntime.Load("resources", "still-kept");
            }
            Assert.AreEqual(2, Call("runtime_read", token).Value<int>("count"));
        }

        [Test]
        public void HandlesAndWorkerThreadKeysRemainScalarObservations()
        {
            string token = Start();
            string handle = ReferenceGraphRuntime.Acquire("addressables", "constructed/" + 17);
            ReferenceGraphRuntime.Release(handle);
            ReferenceGraphRuntime.Release("unpaired");
            Task.Run(() => ReferenceGraphRuntime.Load("custom", "worker-key")).GetAwaiter().GetResult();
            JObject result = Call("runtime_read", token);
            Assert.AreEqual(4, result["events"].Count());
            Assert.AreEqual(handle, result["events"][0].Value<string>("handle"));
            Assert.AreEqual(handle, result["events"][1].Value<string>("handle"));
            Assert.AreEqual("", result["events"][3].Value<string>("target_guid"));
        }

        [Test]
        public void FailedSinkStopsIndependentlyAndReleaseIsRetryable()
        {
            string failed = Start(),
                healthy = Start();
            Type type = typeof(ManageReferenceGraph).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphRuntimeCapture");
            var active = (IDictionary)type.GetField("Active", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            object sink = active[failed];
            ((IDisposable)sink.GetType().GetField("writer", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(sink)).Dispose();
            Assert.DoesNotThrow(() => ReferenceGraphRuntime.Load("custom", "after-write-failure"));
            JObject stopped = Call("runtime_stop", failed);
            Assert.AreEqual("observer_failed", stopped.Value<string>("state"));
            Assert.IsNotEmpty(stopped.Value<string>("error"));
            Assert.AreEqual(1, Call("runtime_read", healthy).Value<int>("count"));
            Call("runtime_release", failed);
            Assert.IsTrue(Call("runtime_release", failed).Value<bool>("released"));
        }

        [Test]
        public void MissingCaptureErrorDoesNotExposeNativeCredentialOrUserPath()
        {
            string token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            JObject status = ReferenceGraphCollectorTests.Call("status");
            JObject result = JObject.FromObject(
                ManageReferenceGraph.HandleCommand(
                    new JObject
                    {
                        ["action"] = "runtime_read",
                        ["token"] = token,
                        ["expected_instance_id"] = status["instance_id"],
                        ["expected_project_id"] = status["project_id"],
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.DoesNotContain(token, result.ToString());
            StringAssert.DoesNotContain(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), result.ToString());
            StringAssert.Contains("Runtime capture unavailable", result.Value<string>("message"));
        }

        [UnityTest, Explicit("Controller-owned two Play sessions with domain reload disabled.")]
        public IEnumerator CaptureGapSeparatesTwoPlaySessionsWithoutDomainReload()
        {
            bool originalEnabled = EditorSettings.enterPlayModeOptionsEnabled;
            EnterPlayModeOptions originalOptions = EditorSettings.enterPlayModeOptions;
            string domain = Domain,
                previous = null;
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
            try
            {
                for (int i = 0; i < 2; i++)
                {
                    yield return new EnterPlayMode(false);
                    Assert.AreEqual(domain, Domain);
                    string first = Start(),
                        concurrent = Start();
                    ReferenceGraphRuntime.Load("custom", "session/" + i);
                    JObject firstPage = Call("runtime_read", first),
                        concurrentPage = Call("runtime_read", concurrent);
                    string session = firstPage.Value<string>("runtime_session");
                    Assert.IsTrue(firstPage.Value<bool>("playing"));
                    Assert.AreEqual(session, concurrentPage.Value<string>("runtime_session"));
                    if (previous != null)
                        Assert.AreNotEqual(previous, session);
                    previous = session;
                    Call("runtime_stop", first);
                    Call("runtime_stop", concurrent);
                    Assert.IsFalse(ReferenceGraphRuntime.Enabled);
                    yield return new ExitPlayMode();
                }
            }
            finally
            {
                EditorSettings.enterPlayModeOptions = originalOptions;
                EditorSettings.enterPlayModeOptionsEnabled = originalEnabled;
                if (EditorApplication.isPlaying)
                    EditorApplication.isPlaying = false;
            }
        }

        [UnityTest, Explicit("Controller-owned isolated Play Mode observation only.")]
        public IEnumerator ActualPlayLoadKeepsGuidAndReleasedHandle()
        {
            yield return new EnterPlayMode();
            string root = "Assets/__McpRuntimeGraph_" + Guid.NewGuid().ToString("N");
            string key = "owned_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
            AssetDatabase.CreateFolder(root, "Resources");
            string path = root + "/Resources/" + key + ".txt";
            TextAsset asset = null;
            try
            {
                File.WriteAllText(path, "owned dynamic runtime evidence");
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                string token = Start();
                asset = Resources.Load<TextAsset>(key);
                Assert.IsNotNull(asset);
                ReferenceGraphRuntime.Load("resources", key, asset);
                string handle = ReferenceGraphRuntime.Acquire("resources", key, asset);
                Resources.UnloadAsset(asset);
                asset = null;
                ReferenceGraphRuntime.Release(handle);
                JObject page = Call("runtime_read", token);
                Assert.IsTrue(page.Value<bool>("playing"));
                Assert.AreEqual(AssetDatabase.AssetPathToGUID(path), page["events"][0].Value<string>("target_guid"));
                Assert.AreEqual(handle, page["events"][2].Value<string>("handle"));
                Call("runtime_stop", token);
                Assert.IsFalse(ReferenceGraphRuntime.Enabled);
            }
            finally
            {
                if (asset != null)
                    Resources.UnloadAsset(asset);
                foreach (string token in captures)
                    Call("runtime_release", token);
                captures.Clear();
                Assert.IsTrue(AssetDatabase.DeleteAsset(root));
            }
            yield return new ExitPlayMode();
        }
    }
}
