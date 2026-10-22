using System;
using System.Collections;
using System.IO;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class AsyncReferenceReloadTests
    {
        private const string Key = "MCPForUnity.Tests.AsyncReferenceReload";
        private static readonly string Domain = Guid.NewGuid().ToString("N");
        private static Task<object> pending;

        private static void ObserveClosedReaders()
        {
            SessionState.SetBool(Key + ".pending", pending != null && !pending.IsCompleted);
            string path = SessionState.GetString(Key + ".root", "") + "/source.bytes";
            try
            {
                using (File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                using (File.Open(path + ".meta", FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                SessionState.SetBool(Key + ".closed", true);
            }
            catch (IOException)
            {
                SessionState.SetBool(Key + ".closed", false);
            }
        }

        [UnityTest, Explicit("Controller-owned isolated native domain reload only.")]
        public IEnumerator ActualReloadDrainsPendingReferenceReaders()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                Assert.Ignore("The asynchronous read-lease path is Windows-specific.");
            try
            {
                Assert.IsEmpty(SessionState.GetString(Key + ".root", ""));
                string root = "Assets/__McpReferenceReload_" + Guid.NewGuid().ToString("N");
                Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", root.Substring(7)));
                SessionState.SetString(Key + ".root", root);
                SessionState.SetString(Key + ".domain", Domain);
                string path = root + "/source.bytes";
                using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    file.SetLength(128L * 1024 * 1024);
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                CommandRegistry.Initialize();
                pending = CommandRegistry.InvokeCommandAsync(
                    "asset_reference_diagnostics",
                    new JObject
                    {
                        ["action"] = "get_dependencies",
                        ["path"] = path,
                        ["scanRoot"] = root,
                    }
                );
                Assert.IsFalse(pending.IsCompleted);
                // The production helper is initialized before this observer. Verify its
                // actual before-reload callback closes both readers before domain unload.
                AssemblyReloadEvents.beforeAssemblyReload += ObserveClosedReaders;
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                Assert.AreNotEqual(SessionState.GetString(Key + ".domain", ""), Domain);
                Assert.IsTrue(SessionState.GetBool(Key + ".pending", false), "Exercise an actually pending request at reload.");
                Assert.IsTrue(SessionState.GetBool(Key + ".closed", false), "Readers must close before the old domain is unloaded.");
                string retainedPath = SessionState.GetString(Key + ".root", "") + "/source.bytes";
                using (File.Open(retainedPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                CommandRegistry.Initialize();
                Task<object> next = CommandRegistry.InvokeCommandAsync(
                    "asset_reference_diagnostics",
                    new JObject
                    {
                        ["action"] = "get_dependencies",
                        ["path"] = retainedPath,
                        ["scanRoot"] = SessionState.GetString(Key + ".root", ""),
                    }
                );
                while (!next.IsCompleted)
                    yield return null;
                Assert.IsTrue(JObject.FromObject(next.GetAwaiter().GetResult()).Value<bool>("success"));
            }
            finally
            {
                AssemblyReloadEvents.beforeAssemblyReload -= ObserveClosedReaders;
                string root = SessionState.GetString(Key + ".root", "");
                SessionState.EraseString(Key + ".root");
                SessionState.EraseString(Key + ".domain");
                SessionState.EraseBool(Key + ".pending");
                SessionState.EraseBool(Key + ".closed");
                if (!string.IsNullOrEmpty(root))
                {
                    StringAssert.StartsWith("Assets/__McpReferenceReload_", root);
                    Assert.IsTrue(AssetDatabase.DeleteAsset(root));
                }
            }
        }
    }
}
