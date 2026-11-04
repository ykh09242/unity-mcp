using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Physics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Tools
{
    public class NativeAuthoringPersistenceTests
    {
        private string folder;
        private Scene scene;

        [SetUp]
        public void SetUp()
        {
            Assert.IsTrue(
                Application.isBatchMode && Environment.GetEnvironmentVariable("UNITY_MCP_OWNED_TRANSPORT_TESTS") == "1",
                "Requires an owned batch test Editor."
            );
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage(), "Run in an owned Editor without an existing Prefab Stage.");
            scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            SceneManager.SetActiveScene(scene);
            folder = "Assets/NativeAuthoring_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
        }

        [TearDown]
        public void TearDown()
        {
            StageUtility.GoToMainStage();
            Undo.ClearAll();
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            if (folder != null)
                AssetDatabase.DeleteAsset(folder);
        }

        [TestCase("body3d")]
        [TestCase("body2d")]
        [TestCase("joint3d")]
        [TestCase("joint2d")]
        [TestCase("collider3d")]
        [TestCase("collider2d")]
        [TestCase("ui")]
        public void InheritedComponent_RecordsOverrideAndSurvivesUndoRedoAndReopen(string kind)
        {
            var source = new GameObject("AuthoringTarget");
            switch (kind)
            {
                case "body3d":
                    source.AddComponent<Rigidbody>();
                    break;
                case "body2d":
                    source.AddComponent<Rigidbody2D>();
                    break;
                case "joint3d":
                    source.AddComponent<FixedJoint>();
                    break;
                case "joint2d":
                    source.AddComponent<FixedJoint2D>();
                    break;
                case "collider3d":
                    source.AddComponent<BoxCollider>();
                    break;
                case "collider2d":
                    source.AddComponent<BoxCollider2D>();
                    break;
                case "ui":
                    source.AddComponent<UIDocument>();
                    break;
            }
            string prefabPath = folder + "/Target.prefab";
            var prefab = PrefabUtility.SaveAsPrefabAsset(source, prefabPath);
            UnityEngine.Object.DestroyImmediate(source);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
            string scenePath = folder + "/Target.unity";
            Assert.IsTrue(EditorSceneManager.SaveScene(scene, scenePath));
            Component component = TargetComponent(instance, kind);
            Undo.ClearAll();
            Undo.IncrementCurrentGroup();
            JObject request = new JObject { ["target"] = instance.GetInstanceIDCompat().ToString(), ["search_method"] = "by_id" };
            if (kind.StartsWith("body"))
            {
                request["action"] = "configure_rigidbody";
                request["dimension"] = kind == "body2d" ? "2d" : "3d";
                request["properties"] = new JObject { ["mass"] = 7 };
            }
            else if (kind.StartsWith("joint"))
            {
                request["action"] = "configure_joint";
                request["joint_type"] = "fixed";
                request["properties"] = new JObject { ["breakForce"] = 7 };
            }
            else if (kind.StartsWith("collider"))
            {
                request["action"] = "assign_physics_material";
                string materialPath = folder + (kind == "collider2d" ? "/Material.physicsMaterial2D" : "/Material.physicMaterial");
                UnityEngine.Object material;
                if (kind == "collider2d")
                    material = new PhysicsMaterial2D();
                else
#if UNITY_6000_0_OR_NEWER
                    material = new PhysicsMaterial();
#else
                    material = new PhysicMaterial();
#endif
                AssetDatabase.CreateAsset(material, materialPath);
                request["material_path"] = materialPath;
            }
            else
            {
                string uxml = folder + "/Document.uxml";
                File.WriteAllText(uxml, "<ui:UXML xmlns:ui=\"UnityEngine.UIElements\"><ui:Label text=\"probe\" /></ui:UXML>");
                AssetDatabase.ImportAsset(uxml, ImportAssetOptions.ForceSynchronousImport);
                var settings = ScriptableObject.CreateInstance<PanelSettings>();
                string settingsPath = folder + "/Panel.asset";
                AssetDatabase.CreateAsset(settings, settingsPath);
                request["action"] = "attach_ui_document";
                request["source_asset"] = uxml;
                request["panel_settings"] = settingsPath;
                request["component_type"] = "ui_document";
                request["sort_order"] = 7;
            }
            JObject response = JObject.FromObject(kind == "ui" ? ManageUI.HandleCommand(request) : ManagePhysics.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(PrefabUtility.HasPrefabInstanceAnyOverrides(instance, false), "Direct writes must publish prefab overrides before returning.");
            AssertChanged(component, kind);
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            AssertOriginal(component, kind);
            Undo.PerformRedo();
            AssertChanged(component, kind);
            Assert.IsTrue(EditorSceneManager.SaveScene(scene, scenePath));
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
            AssertChanged(TargetComponent(scene.GetRootGameObjects()[0], kind), kind);
            AssertOriginal(TargetComponent(prefab, kind), kind);
        }

        private static Component TargetComponent(GameObject go, string kind)
        {
            switch (kind)
            {
                case "body3d":
                    return go.GetComponent<Rigidbody>();
                case "body2d":
                    return go.GetComponent<Rigidbody2D>();
                case "joint3d":
                    return go.GetComponent<FixedJoint>();
                case "joint2d":
                    return go.GetComponent<FixedJoint2D>();
                case "collider3d":
                    return go.GetComponent<BoxCollider>();
                case "collider2d":
                    return go.GetComponent<BoxCollider2D>();
                default:
                    return go.GetComponent<UIDocument>();
            }
        }

        private static void AssertChanged(Component component, string kind)
        {
            if (component is Rigidbody body)
                Assert.AreEqual(7, body.mass);
            else if (component is Rigidbody2D body2d)
                Assert.AreEqual(7, body2d.mass);
            else if (component is Joint joint)
                Assert.AreEqual(7, joint.breakForce);
            else if (component is Joint2D joint2d)
                Assert.AreEqual(7, joint2d.breakForce);
            else if (component is Collider collider)
                Assert.IsNotNull(collider.sharedMaterial);
            else if (component is Collider2D collider2d)
                Assert.IsNotNull(collider2d.sharedMaterial);
            else if (component is UIDocument document)
            {
                Assert.AreEqual(7, document.sortingOrder);
                Assert.IsNotNull(document.visualTreeAsset);
                Assert.IsNotNull(document.panelSettings);
            }
            else
                Assert.Fail("Missing component for " + kind);
        }

        private static void AssertOriginal(Component component, string kind)
        {
            if (component is Rigidbody body)
                Assert.AreEqual(1, body.mass);
            else if (component is Rigidbody2D body2d)
                Assert.AreEqual(1, body2d.mass);
            else if (component is Joint joint)
                Assert.AreEqual(float.PositiveInfinity, joint.breakForce);
            else if (component is Joint2D joint2d)
                Assert.AreEqual(float.PositiveInfinity, joint2d.breakForce);
            else if (component is Collider collider)
                Assert.IsNull(collider.sharedMaterial);
            else if (component is Collider2D collider2d)
                Assert.IsNull(collider2d.sharedMaterial);
            else if (component is UIDocument document)
            {
                Assert.AreEqual(0, document.sortingOrder);
                Assert.IsNull(document.visualTreeAsset);
                Assert.IsNull(document.panelSettings);
            }
            else
                Assert.Fail("Missing component for " + kind);
        }
    }
}
