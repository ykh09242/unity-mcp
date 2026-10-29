#if MCP_FOR_UNITY_AI_MODULE
using System;
using System.Reflection;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.AI;

namespace MCPForUnityTests.EditMode.Tools
{
    public class NavigationBakeTargetsTests
    {
        [Test]
        public void SavedSourceFingerprintUsesStableIdentityAndChangesWithSourceConfiguration()
        {
            var fixture = new PrefabTestSceneFixture();
            fixture.PrepareRunnerBootstrap();
            string folder = "Assets/__McpNavigationCheckpoint_" + Guid.NewGuid().ToString("N");
            string guid = null;
            try
            {
                var scene = fixture.Create("__McpNavigationCheckpoint_", Guid.NewGuid().ToString("N"));
                var root = new GameObject("OwnedCheckpointSource");
                var agent = root.AddComponent<NavMeshAgent>();
                agent.enabled = false;
                if (NavMesh.GetSettingsCount() == 0)
                    Assert.Ignore("No configured native agent type.");
                agent.agentTypeID = NavMesh.GetSettingsByIndex(0).agentTypeID;
                Type targets = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.Navigation.NavigationBakeTargets");
                var capture = targets.GetMethod("Capture", BindingFlags.Static | BindingFlags.NonPublic);
                var refusal = Assert.Throws<TargetInvocationException>(() => capture.Invoke(null, new object[] { agent }));
                StringAssert.Contains("saved, clean scene", refusal.InnerException.Message);
                guid = AssetDatabase.CreateFolder("Assets", folder.Substring("Assets/".Length));
                Assert.IsTrue(EditorSceneManager.SaveScene(scene, folder + "/__McpNavigationCheckpoint_Owned.unity"));
                JObject first = (JObject)capture.Invoke(null, new object[] { agent });
                JObject second = (JObject)capture.Invoke(null, new object[] { agent });
                Assert.IsTrue(JToken.DeepEquals(first, second));
                StringAssert.StartsWith("GlobalObjectId_", (string)first["target"]);
                Assert.IsNull(first["component_id"]);
                Assert.IsNull(first["scene_handle"]);
                root.transform.position = Vector3.one;
                EditorSceneManager.MarkSceneDirty(scene);
                Assert.IsTrue(EditorSceneManager.SaveScene(scene));
                JObject changed = (JObject)capture.Invoke(null, new object[] { agent });
                Assert.AreEqual((string)first["target"], (string)changed["target"]);
                Assert.AreNotEqual((string)first["configuration_hash"], (string)changed["configuration_hash"]);
                Assert.AreNotEqual((string)first["scene_hash"], (string)changed["scene_hash"]);
            }
            finally
            {
                fixture.Close();
                if (!string.IsNullOrEmpty(guid))
                {
                    Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(folder));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(folder));
                }
                fixture.RestoreRunnerBootstrap();
            }
        }
    }
}
#endif
