using System;
using System.Collections;
using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class ReferenceGraphReloadTests
    {
        private const string Key = "MCP.Tests.ReferenceGraphReload";
        private static readonly string Domain = Guid.NewGuid().ToString("N");

        [UnityTest, Explicit("Controller-owned isolated native domain reload only.")]
        public IEnumerator ActualReloadRetainsJournalAndEditorIdentity()
        {
            try
            {
                Assert.IsEmpty(SessionState.GetString(Key + ".root", ""));
                string root = "Assets/__McpGraphReload_" + Guid.NewGuid().ToString("N");
                AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
                SessionState.SetString(Key + ".root", root);
                SessionState.SetString(Key + ".domain", Domain);
                SessionState.SetString(Key + ".before", ReferenceGraphCollectorTests.Call("status").ToString());
                string capture = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
                SessionState.SetString(Key + ".capture", capture);
                ReferenceGraphRuntimeTests.Call("runtime_start", capture);
                MCPForUnity.Runtime.ReferenceGraphRuntime.Load("resources", "reload-evidence");
                File.WriteAllText(root + "/source.txt", "owned reload evidence");
                AssetDatabase.ImportAsset(root + "/source.txt", ImportAssetOptions.ForceSynchronousImport);
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                Assert.AreNotEqual(SessionState.GetString(Key + ".domain", ""), Domain);
                JObject runtime = ReferenceGraphRuntimeTests.Call("runtime_read", SessionState.GetString(Key + ".capture", ""));
                Assert.AreEqual("domain_reload", runtime.Value<string>("state"));
                Assert.AreEqual("reload-evidence", runtime["events"][0].Value<string>("key"));
                Assert.IsFalse(MCPForUnity.Runtime.ReferenceGraphRuntime.Enabled);
                JObject before = JObject.Parse(SessionState.GetString(Key + ".before", ""));
                JObject after = ReferenceGraphCollectorTests.Call("status");
                Assert.AreEqual(before["instance_id"], after["instance_id"]);
                Assert.AreEqual(before["epoch"], after["epoch"]);
                Assert.IsTrue(
                    JToken.DeepEquals(before["compilation_context"], after["compilation_context"]),
                    "Reload rebuilds the same current compiler context."
                );
                JObject changes = ReferenceGraphCollectorTests.Call("changes", "epoch", before.Value<string>("epoch"), "after", before.Value<long>("revision"));
                Assert.IsFalse(changes.Value<bool>("requires_census"));
                Assert.IsNotEmpty(changes["changes"]);
                Assert.IsTrue(
                    JToken.DeepEquals(
                        changes,
                        ReferenceGraphCollectorTests.Call("changes", "epoch", before.Value<string>("epoch"), "after", before.Value<long>("revision"))
                    )
                );
            }
            finally
            {
                string capture = SessionState.GetString(Key + ".capture", "");
                SessionState.EraseString(Key + ".capture");
                if (capture.Length > 0)
                    ReferenceGraphRuntimeTests.Call("runtime_release", capture);
                string root = SessionState.GetString(Key + ".root", "");
                SessionState.EraseString(Key + ".root");
                SessionState.EraseString(Key + ".domain");
                SessionState.EraseString(Key + ".before");
                if (root.Length > 0)
                {
                    StringAssert.StartsWith("Assets/__McpGraphReload_", root);
                    Assert.IsTrue(AssetDatabase.DeleteAsset(root));
                }
            }
        }
    }
}
