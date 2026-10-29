using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Build;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class BuildOwnerActualReloadTests
    {
        private const string Prefix = "MCPForUnity.Tests.BuildOwnerActualReload.";
        private const string NativeOwnerKey = "MCPForUnity.Build.NativeOwnerV1";
        private static readonly string DomainNonce = Guid.NewGuid().ToString("N");
        private const BindingFlags PrivateStatic = BindingFlags.NonPublic | BindingFlags.Static;

        [UnityTest, Explicit("Owned disposable batch Editor only; requires exact UNITY_MCP_BUILD_RELOAD_PROJECT and performs a real domain reload.")]
        public IEnumerator QueuedBuildAtActualReloadLosesCallbackWithoutFalseRecovery()
        {
            Prepare();
            try
            {
                AssemblyReloadEvents.beforeAssemblyReload += QueueBuildAtReload;
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                Assert.IsTrue(SessionState.GetBool(Prefix + "queued", false), "The build must be queued inside the old domain's final reload callback.");
                Assert.AreEqual(0, SessionState.GetInt(Prefix + "invocations", 0), "No native build delegate may run across the reload boundary.");
                Assert.IsFalse(BuildRunner.HasNativeOwner, "The old update callback and its in-memory reservation must disappear with the old domain.");
                Assert.IsFalse(SessionState.GetBool(NativeOwnerKey, false), "An uninvoked queued callback must not leave a lost-native-invocation marker.");
                Assert.IsFalse(BuildRunner.NativeRecoveryRequired, "The new domain must admit work without demanding an unnecessary Editor restart.");
            }
            finally
            {
                Cleanup();
            }
        }

        [UnityTest, Explicit("Owned disposable batch Editor only; simulates an invocation marker before a real domain reload without building a Player.")]
        public IEnumerator RecordedInvocationAtActualReloadRetainsConservativeRecoveryFence()
        {
            Prepare();
            try
            {
                AssemblyReloadEvents.beforeAssemblyReload += RecordInvocationAtReload;
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                Assert.IsTrue(SessionState.GetBool(Prefix + "recorded", false), "The control must record its simulated invocation before the real reload.");
                Assert.AreEqual(0, SessionState.GetInt(Prefix + "invocations", 0), "The conservative-marker control must not invoke BuildPipeline.");
                Assert.IsFalse(BuildRunner.HasNativeOwner, "Old-domain invocation ownership must not survive as an in-memory owner.");
                Assert.IsTrue(SessionState.GetBool(NativeOwnerKey, false));
                Assert.IsTrue(BuildRunner.NativeRecoveryRequired, "A recorded lost invocation must remain conservative after the real domain boundary.");
            }
            finally
            {
                Cleanup();
            }
        }

        private static void Prepare()
        {
            Assert.IsTrue(Application.isBatchMode, "This disruptive probe requires an owned batch Editor.");
            Assert.AreEqual(
                Path.GetFullPath(Path.GetDirectoryName(Application.dataPath)),
                Environment.GetEnvironmentVariable("UNITY_MCP_BUILD_RELOAD_PROJECT"),
                "The caller must explicitly identify the exact disposable project."
            );
            Assert.IsFalse(SessionState.GetBool(Prefix + "active", false), "Preserve any unfinished owned probe.");
            Assert.IsFalse(
                BuildPipeline.isBuildingPlayer || BuildRunner.HasNativeOwner || BuildRunner.NativeRecoveryRequired,
                "Preserve existing build ownership."
            );
            Assert.IsFalse(CurrentCallbacks().Any(IsBuildRunnerCallback), "Preserve existing queued build callbacks.");
            SessionState.SetBool(Prefix + "active", true);
            SessionState.SetString(Prefix + "domain", DomainNonce);
            SessionState.SetString(Prefix + "job", "owned-reload-" + Guid.NewGuid().ToString("N"));
            SessionState.SetInt(Prefix + "invocations", 0);
        }

        private static void QueueBuildAtReload()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= QueueBuildAtReload;
            try
            {
                string id = SessionState.GetString(Prefix + "job", "");
                var job = new BuildJob(id, BuildTarget.StandaloneWindows64, "unused-owned-reload-output");
                typeof(BuildRunner).GetMethod("ReserveNative", PrivateStatic).Invoke(null, new object[] { id });
                Func<BuildReport> fakeBuild = () =>
                {
                    SessionState.SetInt(Prefix + "invocations", SessionState.GetInt(Prefix + "invocations", 0) + 1);
                    return null;
                };
                typeof(BuildRunner).GetMethod("ScheduleOwnedBuild", PrivateStatic).Invoke(null, new object[] { job, fakeBuild, null });
                SessionState.SetBool(Prefix + "queued", BuildRunner.HasNativeOwner);
            }
            catch (Exception error)
            {
                SessionState.SetString(Prefix + "error", error.ToString());
            }
        }

        private static void RecordInvocationAtReload()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= RecordInvocationAtReload;
            try
            {
                string id = SessionState.GetString(Prefix + "job", "");
                Owners("NativeOwners").Add(id);
                // This control injects a historical invocation receipt; it never calls BuildPipeline.
                Owners("InvokedOwners")?.Add(id);
                SessionState.SetBool(NativeOwnerKey, true);
                SessionState.SetBool(Prefix + "recorded", true);
            }
            catch (Exception error)
            {
                SessionState.SetString(Prefix + "error", error.ToString());
            }
        }

        private static HashSet<string> Owners(string name) => (HashSet<string>)typeof(BuildRunner).GetField(name, PrivateStatic)?.GetValue(null);

        private static void AssertReloaded()
        {
            Assert.AreNotEqual(SessionState.GetString(Prefix + "domain", ""), DomainNonce, "The native domain must actually reload.");
            Assert.IsEmpty(SessionState.GetString(Prefix + "error", ""), "The old-domain boundary setup must complete before unloading.");
            LogAssert.NoUnexpectedReceived();
        }

        private static EditorApplication.CallbackFunction[] CurrentCallbacks() =>
            EditorApplication.update?.GetInvocationList().Cast<EditorApplication.CallbackFunction>().ToArray()
            ?? Array.Empty<EditorApplication.CallbackFunction>();

        private static bool IsBuildRunnerCallback(EditorApplication.CallbackFunction callback) =>
            callback.Method.DeclaringType == typeof(BuildRunner) || callback.Method.DeclaringType?.DeclaringType == typeof(BuildRunner);

        private static void Cleanup()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= QueueBuildAtReload;
            AssemblyReloadEvents.beforeAssemblyReload -= RecordInvocationAtReload;
            if (!SessionState.GetBool(Prefix + "active", false))
                return;
            foreach (var callback in CurrentCallbacks().Where(IsBuildRunnerCallback))
                EditorApplication.update -= callback;
            string id = SessionState.GetString(Prefix + "job", "");
            Owners("NativeOwners")?.Remove(id);
            Owners("InvokedOwners")?.Remove(id);
            SessionState.SetBool(NativeOwnerKey, false);
            foreach (string name in new[] { "domain", "job", "error" })
                SessionState.EraseString(Prefix + name);
            foreach (string name in new[] { "active", "queued", "recorded" })
                SessionState.EraseBool(Prefix + name);
            SessionState.EraseInt(Prefix + "invocations");
        }
    }
}
