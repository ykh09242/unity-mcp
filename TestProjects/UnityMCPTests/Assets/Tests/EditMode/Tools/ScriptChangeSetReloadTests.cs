using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class ScriptChangeSetReloadTests
    {
        [UnityTest, Explicit("Controller-owned isolated warm-project reload verification only.")]
        public IEnumerator ActualDomainReload_RetainsAndRecoversInterruptedApplication()
        {
            try
            {
                ScriptChangeSetReloadScenario.Begin();
                yield return WorkloadProbe.RecordReloadIdle("before_reload");
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                ScriptChangeSetReloadScenario.VerifyAndRecover();
            }
            finally
            {
                ScriptChangeSetReloadScenario.Cleanup();
            }
            yield return WorkloadProbe.RecordReloadIdle("after_reload_cleanup");
        }
    }

    internal static class ScriptChangeSetReloadScenario
    {
        private const string Key = "MCPForUnity.Tests.DEV12.OwnedReloadReceipt";
        private const BindingFlags Flags = BindingFlags.Static | BindingFlags.NonPublic;

        private sealed class Scope : IDisposable
        {
            private readonly System.Collections.Generic.Dictionary<FieldInfo, object> saved = new();

            internal Scope(string storage, bool pause)
            {
                try
                {
                    Set(typeof(ManageScript), "_explicitConsentForTests", (Func<bool>)(() => true));
                    Set(typeof(ManageScript), "_parentEnabledForTests", (Func<bool>)(() => true));
                    Type helper = typeof(ManageScript).Assembly.GetType("MCPForUnity.Editor.Tools.ScriptChangeSet");
                    Assert.IsNotNull(helper);
                    Set(helper, "_storageRootForTests", (Func<string>)(() => storage));
                    Set(helper, "_domainForTests", null);
                    Set(helper, "_stageForTests", null);
                    Set(helper, "_continueForTests", pause ? (Func<int, bool>)(_ => false) : null);
                }
                catch
                {
                    Dispose();
                    throw;
                }
            }

            private void Set(Type type, string name, object value)
            {
                var field = type.GetField(name, Flags);
                Assert.IsNotNull(field, name);
                saved.Add(field, field.GetValue(null));
                field.SetValue(null, value);
            }

            public void Dispose()
            {
                foreach (var entry in saved)
                    entry.Key.SetValue(null, entry.Value);
                saved.Clear();
            }
        }

        private static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        internal static void Begin()
        {
            Assert.IsEmpty(SessionState.GetString(Key, ""), "An earlier owned reload receipt must be recovered or cleaned first.");
            string run = Guid.NewGuid().ToString("N");
            string folder = "Assets/Dev12Reload_" + run;
            string storage = SafePathUtility.ResolveWithinRoot(Path.GetTempPath(), "Dev12Reload_" + run);
            Directory.CreateDirectory(AssetPathUtility.GetFullAssetPath(folder));
            Directory.CreateDirectory(storage);
            var receipt = new JObject
            {
                ["root"] = Path.GetFullPath(Application.dataPath),
                ["folder"] = folder,
                ["storage_name"] = "Dev12Reload_" + run,
                ["files"] = new JArray(),
            };
            SessionState.SetString(Key, receipt.ToString());
            var files = new JArray();
            foreach (string suffix in new[] { "One", "Two" })
            {
                string name = "Dev12Reload" + suffix + run;
                string path = folder + "/" + name + ".cs";
                string full = AssetPathUtility.GetFullAssetPath(path);
                File.WriteAllText(full, "class " + name + " { }\r\n", new UTF8Encoding(false, true));
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                byte[] original = File.ReadAllBytes(full);
                ((JArray)receipt["files"]).Add(
                    new JObject
                    {
                        ["path"] = path,
                        ["original"] = Convert.ToBase64String(original),
                        ["meta_sha256"] = Digest(File.ReadAllBytes(full + ".meta")),
                    }
                );
                files.Add(
                    new JObject
                    {
                        ["path"] = path,
                        ["expected_bytes_sha256"] = Digest(original),
                        ["contents"] = "class " + name + " { int Value => 7; }\r\n",
                    }
                );
                SessionState.SetString(Key, receipt.ToString());
            }
            using var scope = new Scope(storage, true);
            var prepared = JObject.FromObject(ManageScript.HandleCommand(new JObject { ["action"] = "prepare_change_set", ["files"] = files }));
            Assert.IsTrue(prepared.Value<bool>("success"), prepared.ToString());
            receipt["id"] = prepared["data"]["change_set_id"];
            SessionState.SetString(Key, receipt.ToString());
            var applied = Command("apply_change_set", receipt);
            Assert.AreEqual("applying", (string)applied["data"]["phase"], applied.ToString());
            Assert.AreEqual("confirmed", (string)applied["data"]["files"][0]["state"]);
        }

        internal static void VerifyAndRecover()
        {
            JObject receipt = Receipt();
            string storage = SafePathUtility.ResolveWithinRoot(Path.GetTempPath(), receipt.Value<string>("storage_name"));
            using var scope = new Scope(storage, false);
            var status = Command("get_change_set", receipt);
            Assert.IsTrue((bool)status["data"]["interrupted"], "The actual replacement domain must differ from the persisted apply domain.");
            Assert.AreEqual("confirmed", (string)status["data"]["files"][0]["state"]);
            var recovered = Command("recover_change_set", receipt);
            Assert.IsTrue(recovered.Value<bool>("success"), recovered.ToString());
            Assert.AreEqual("compensated", (string)recovered["data"]["phase"]);
            foreach (JObject file in receipt["files"])
            {
                string full = AssetPathUtility.GetFullAssetPath(file.Value<string>("path"));
                CollectionAssert.AreEqual(Convert.FromBase64String(file.Value<string>("original")), File.ReadAllBytes(full));
                Assert.AreEqual(file.Value<string>("meta_sha256"), Digest(File.ReadAllBytes(full + ".meta")));
            }
        }

        private static JObject Command(string action, JObject receipt) =>
            JObject.FromObject(ManageScript.HandleCommand(new JObject { ["action"] = action, ["change_set_id"] = receipt["id"] }));

        private static JObject Receipt()
        {
            var receipt = JObject.Parse(SessionState.GetString(Key, ""));
            Assert.AreEqual(Path.GetFullPath(Application.dataPath), receipt.Value<string>("root"));
            StringAssert.StartsWith("Assets/Dev12Reload_", receipt.Value<string>("folder"));
            StringAssert.StartsWith("Dev12Reload_", receipt.Value<string>("storage_name"));
            return receipt;
        }

        internal static void Cleanup()
        {
            if (string.IsNullOrEmpty(SessionState.GetString(Key, "")))
                return;
            JObject receipt = Receipt();
            string folder = AssetPathUtility.GetFullAssetPath(receipt.Value<string>("folder"));
            string storage = SafePathUtility.ResolveWithinRoot(Path.GetTempPath(), receipt.Value<string>("storage_name"));
            if (Directory.Exists(folder))
                Directory.Delete(folder, true);
            if (File.Exists(folder + ".meta"))
                File.Delete(folder + ".meta");
            if (Directory.Exists(storage))
                Directory.Delete(storage, true);
            SessionState.EraseString(Key);
        }
    }
}
