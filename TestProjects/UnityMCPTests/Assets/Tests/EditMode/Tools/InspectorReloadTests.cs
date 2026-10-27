using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class InspectorReloadTests
    {
        private const string Key = "MCPForUnity.Tests.InspectorReload.";
        private static readonly string Domain = Guid.NewGuid().ToString("N");
        private const BindingFlags Flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        [TestCase(false, false), TestCase(false, true), TestCase(true, false), TestCase(true, true)]
        [Explicit("Owned disposable graphical Editor only; creates real Inspector windows.")]
        public void StageTransition_ReleasesStaleInspectorsAndPreservesUnrelatedLocks(bool close, bool lockCurrent)
        {
            try
            {
                Prepare();
                var selected = Selection.activeObject;
                Type inspectorType = typeof(UnityEditor.Editor).Assembly.GetType("UnityEditor.InspectorWindow", true);
                Selection.activeObject = AssetDatabase.LoadAssetAtPath<GameObject>(SessionState.GetString(Key + "folder", "") + "/Second.prefab");
                var other = (EditorWindow)ScriptableObject.CreateInstance(inspectorType);
                SessionState.SetInt(Key + "otherWindow", other.GetInstanceIDCompat());
                other.Show();
                var otherTracker = (ActiveEditorTracker)inspectorType.GetProperty("tracker", Flags).GetValue(other);
                otherTracker.ForceRebuild();
                inspectorType.GetProperty("isLocked", Flags).SetValue(other, true);
                int[] otherTargets = otherTracker.activeEditors.SelectMany(e => e.targets).Select(o => o.GetInstanceIDCompat()).ToArray();
                Selection.activeObject = selected;
                var current = UnityObjectIdCompat.InstanceIDToObjectCompat(SessionState.GetInt(Key + "window", 0));
                ((ActiveEditorTracker)inspectorType.GetProperty("tracker", Flags).GetValue(current)).ForceRebuild();
                inspectorType.GetProperty("isLocked", Flags).SetValue(current, lockCurrent);
                Command(
                    close
                        ? new JObject { ["action"] = "close_prefab_stage" }
                        : new JObject { ["action"] = "open_prefab_stage", ["prefabPath"] = SessionState.GetString(Key + "folder", "") + "/Second.prefab" }
                );
                AssertValidEditors("before returning to the Editor update loop");
                Assert.IsTrue((bool)inspectorType.GetProperty("isLocked", Flags).GetValue(other), "A valid unrelated Inspector must remain locked.");
                CollectionAssert.AreEqual(otherTargets, otherTracker.activeEditors.SelectMany(e => e.targets).Select(o => o.GetInstanceIDCompat()).ToArray());
            }
            finally
            {
                Cleanup();
            }
        }

        [UnityTest, Explicit("Owned disposable graphical Editor only; imports a temporary compilation trigger.")]
        public IEnumerator CloseWithDirtySourceAndForceRefresh_ActualReload()
        {
            try
            {
                Prepare();
                yield return null;
                var scheduler = typeof(RefreshUnity).Assembly.GetType("MCPForUnity.Editor.Services.EditorMainThreadWakeup", true);
                scheduler
                    .GetMethod("Schedule", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(
                        null,
                        new object[]
                        {
                            (Action)(
                                () =>
                                {
                                    string folder = SessionState.GetString(Key + "folder", "");
                                    File.WriteAllText(
                                        Path.Combine(Path.GetDirectoryName(Application.dataPath), folder, "ReloadTrigger.cs"),
                                        "internal class InspectorReloadTrigger {}\n"
                                    );
                                    Command(new JObject { ["action"] = "close_prefab_stage" });
                                    SessionState.SetString(Key + "domain", Domain);
                                    SessionState.SetString(Key + "scenario", "close-dirty-source");
                                    SessionState.SetBool(Key + "staleBeforeReload", HasMissingTargets());
                                    SessionState.SetBool(
                                        Key + "nativeTick",
                                        new System.Diagnostics.StackTrace()
                                            .GetFrames()
                                            .Any(frame => frame.GetMethod()?.DeclaringType == scheduler && frame.GetMethod().Name == "NativeTick")
                                    );
                                    Debug.Log("InspectorReload before dirty-source refresh: " + DescribeEditors());
                                    AssetDatabase.Refresh(ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
                                }
                            ),
                        }
                    );
                yield return new WaitForDomainReload();
                VerifyReload();
            }
            finally
            {
                Cleanup();
            }
            EditorUtility.RequestScriptReload();
            yield return new WaitForDomainReload();
        }

        [UnityTest, Explicit("Owned disposable graphical Editor only; performs an actual domain reload.")]
        public IEnumerator SelectedPrefab_ActualReload()
        {
            try
            {
                Prepare();
                yield return null;
                RequestReload("baseline");
                yield return new WaitForDomainReload();
                VerifyReload();
            }
            finally
            {
                Cleanup();
            }
        }

        private static void Prepare()
        {
            Assert.IsTrue(Application.isBatchMode, "This disruptive probe only runs in an owned batch Editor.");
            Assert.AreEqual(
                Path.GetFullPath(Path.GetDirectoryName(Application.dataPath)),
                Environment.GetEnvironmentVariable("UNITY_MCP_INSPECTOR_RELOAD_PROJECT")
            );
            Assert.IsEmpty(SessionState.GetString(Key + "folder", ""));
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage(), "Preserve any existing prefab stage.");
            string folder = "Assets/InspectorReload_" + Guid.NewGuid().ToString("N");
            SessionState.SetString(Key + "folder", folder);
            SessionState.SetString(Key + "selection", string.Join(",", Selection.instanceIDs));
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            for (int i = 0; i < 2; i++)
            {
                var root = new GameObject(i == 0 ? "First" : "Second", typeof(RectTransform));
                try
                {
                    var imageType = Type.GetType("UnityEngine.UI.Image, UnityEngine.UI", true);
                    var child = new GameObject("SelectedImage", typeof(RectTransform), imageType);
                    child.transform.SetParent(root.transform, false);
                    PrefabUtility.SaveAsPrefabAsset(root, folder + "/" + root.name + ".prefab");
                }
                finally
                {
                    Object.DestroyImmediate(root);
                }
            }
            Command(new JObject { ["action"] = "open_prefab_stage", ["prefabPath"] = folder + "/First.prefab" });
            Selection.activeGameObject = PrefabStageUtility.GetCurrentPrefabStage().prefabContentsRoot.transform.GetChild(0).gameObject;
            Type inspectorType = typeof(UnityEditor.Editor).Assembly.GetType("UnityEditor.InspectorWindow", true);
            var window = (EditorWindow)ScriptableObject.CreateInstance(inspectorType);
            SessionState.SetInt(Key + "window", window.GetInstanceIDCompat());
            window.Show();
            var tracker = (ActiveEditorTracker)inspectorType.GetProperty("tracker", Flags).GetValue(window);
            tracker.ForceRebuild();
            Assert.IsTrue(tracker.activeEditors.Any(e => e.GetType().Name == "ImageEditor"), "The real Image Inspector must exist before the operation.");
            AssertValidEditors("prepared");
        }

        private static void Command(JObject parameters)
        {
            JObject result = JObject.FromObject(ManagePrefabs.HandleCommand(parameters));
            Assert.IsTrue((bool)result["success"], result.ToString());
        }

        private static void RequestReload(string scenario)
        {
            SessionState.SetString(Key + "domain", Domain);
            SessionState.SetString(Key + "scenario", scenario);
            Debug.Log("InspectorReload before " + scenario + ": " + DescribeEditors());
            EditorUtility.RequestScriptReload();
        }

        private static void VerifyReload()
        {
            Assert.AreNotEqual(SessionState.GetString(Key + "domain", ""), Domain, "The native domain must actually reload.");
            var window = UnityObjectIdCompat.InstanceIDToObjectCompat(SessionState.GetInt(Key + "window", 0)) as EditorWindow;
            Assert.IsNotNull(window, "The real Inspector must survive the reload.");
            if (SessionState.GetString(Key + "scenario", "") == "baseline")
                Assert.IsTrue(
                    ((ActiveEditorTracker)window.GetType().GetProperty("tracker", Flags).GetValue(window)).activeEditors.Any(e =>
                        e.GetType().Name == "ImageEditor"
                    ),
                    "The selected prefab's Image Inspector must be restored after reload."
                );
            else
            {
                Assert.IsTrue(SessionState.GetBool(Key + "nativeTick", false), "The operation must run in MCP's background-capable native tick.");
                Assert.IsFalse(
                    SessionState.GetBool(Key + "staleBeforeReload", true),
                    "The command must remove stale Inspector targets before synchronous import."
                );
            }
            AssertValidEditors("after " + SessionState.GetString(Key + "scenario", ""));
            LogAssert.NoUnexpectedReceived();
        }

        private static string DescribeEditors() =>
            string.Join(
                "; ",
                UnityEngine
                    .Resources.FindObjectsOfTypeAll<UnityEditor.Editor>()
                    .Select(e => e.GetType().Name + "[" + string.Join(",", e.targets.Select(t => t == null ? "null" : t.name)) + "]")
            );

        private static void AssertValidEditors(string phase)
        {
            foreach (var editor in UnityEngine.Resources.FindObjectsOfTypeAll<UnityEditor.Editor>())
            foreach (var target in editor.targets)
                Assert.IsTrue(target != null, phase + ": " + DescribeEditors());
        }

        private static bool HasMissingTargets() =>
            UnityEngine.Resources.FindObjectsOfTypeAll<UnityEditor.Editor>().Any(editor => editor.targets.Any(target => target == null));

        private static void Cleanup()
        {
            if (string.IsNullOrEmpty(SessionState.GetString(Key + "folder", "")))
                return;
            var other = UnityObjectIdCompat.InstanceIDToObjectCompat(SessionState.GetInt(Key + "otherWindow", 0)) as EditorWindow;
            if (other != null)
                other.Close();
            var window = UnityObjectIdCompat.InstanceIDToObjectCompat(SessionState.GetInt(Key + "window", 0)) as EditorWindow;
            if (window != null)
                window.Close();
            StageUtility.GoToMainStage();
            string folder = SessionState.GetString(Key + "folder", "");
            if (folder.StartsWith("Assets/InspectorReload_", StringComparison.Ordinal) && AssetDatabase.IsValidFolder(folder))
                AssetDatabase.DeleteAsset(folder);
            string selection = SessionState.GetString(Key + "selection", "");
            Selection.instanceIDs = string.IsNullOrEmpty(selection) ? Array.Empty<int>() : selection.Split(',').Select(int.Parse).ToArray();
            foreach (string name in new[] { "folder", "selection", "domain", "scenario" })
                SessionState.EraseString(Key + name);
            SessionState.EraseInt(Key + "window");
            SessionState.EraseInt(Key + "otherWindow");
            SessionState.EraseBool(Key + "nativeTick");
            SessionState.EraseBool(Key + "staleBeforeReload");
        }
    }
}
