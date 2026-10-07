using System;
using System.Collections.Generic;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Cameras;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class CameraCreationIntegrityTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene originalScene;
        private Scene ownedScene;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveSelection;
        private bool captured;
        private object originalHas;
        private object originalCameraType;
        private object originalBrainType;
        private readonly List<GameObject> ownedObjects = new();
        private const BindingFlags CacheFlags = BindingFlags.NonPublic | BindingFlags.Static;

        [OneTimeSetUp]
        public void PrepareRunnerBootstrap() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunnerBootstrap() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            captured = false;
            ownedObjects.Clear();
            ownedScene = default;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned Prefab Stage is open.");
            originalScene = SceneManager.GetActiveScene();
            originalSelection = Selection.objects;
            originalActiveSelection = Selection.activeObject;
            originalHas = Cache("_hasCinemachine").GetValue(null);
            originalCameraType = Cache("_cmCameraType").GetValue(null);
            originalBrainType = Cache("_cmBrainType").GetValue(null);
            captured = true;
            ownedScene = sceneFixture.Create("McpCameraCreation_", Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void TearDown()
        {
            if (!captured)
                return;
            try
            {
                foreach (GameObject go in ownedObjects)
                {
                    if (go == null)
                        continue;
                    foreach (Component component in go.GetComponents<Component>())
                        if (component != null)
                            Undo.ClearUndo(component);
                    Undo.ClearUndo(go);
                    UnityEngine.Object.DestroyImmediate(go);
                }
                ownedObjects.Clear();
                if (ownedScene.IsValid() && ownedScene.isLoaded)
                {
                    Assert.AreEqual(0, ownedScene.rootCount, "Unexpected objects retained for diagnosis.");
                    sceneFixture.Close();
                }
            }
            finally
            {
                Cache("_hasCinemachine").SetValue(null, originalHas);
                Cache("_cmCameraType").SetValue(null, originalCameraType);
                Cache("_cmBrainType").SetValue(null, originalBrainType);
                if (originalScene.IsValid() && originalScene.isLoaded)
                    SceneManager.SetActiveScene(originalScene);
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveSelection;
                captured = false;
            }
        }

        [TestCase(42f)]
        [TestCase(60f)]
        [TestCase(75f)]
        public void BasicCreationAppliesLensAndReturnsOwnedSceneInstance(float fov)
        {
            BasicMode();
            var response = Send("create_camera", new JObject { ["name"] = UniqueName(), ["fieldOfView"] = fov });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            GameObject go = ReturnedObject(response);
            Assert.AreEqual(ownedScene, go.scene);
            Assert.AreEqual(fov, go.GetComponent<Camera>().fieldOfView);
            Assert.IsFalse(response["data"].Value<bool>("cinemachine"));
        }

        [Test]
        public void BasicDefaultsAndMissingOptionalTargetsRemainAccepted()
        {
            BasicMode();
            var response = Send(
                "create_camera",
                new JObject
                {
                    ["name"] = UniqueName(),
                    ["follow"] = UniqueName(),
                    ["look_at"] = UniqueName(),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Camera camera = ReturnedObject(response).GetComponent<Camera>();
            Assert.AreEqual(60f, camera.fieldOfView);
            Assert.AreEqual(.3f, camera.nearClipPlane);
            Assert.AreEqual(1000f, camera.farClipPlane);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MalformedPropertiesRejectBeforeCreatingObject(bool encoded)
        {
            BasicMode();
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageCamera\\] Action 'create_camera' failed:"));
            var response = Send("create_camera", encoded ? new JValue("not-json") : new JArray());
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0, ownedScene.rootCount);
        }

        [TestCase("name")]
        [TestCase("path")]
        [TestCase("id")]
        public void ExplicitBrainTargetDoesNotAcknowledgeOtherBrain(string selector)
        {
            SyntheticBrainMode();
            GameObject other = NewObject();
            var otherBrain = other.AddComponent<CameraCreationTestBrain>();
            GameObject target = NewObject();
            Camera camera = target.AddComponent<Camera>();
            string reference =
                selector == "id" ? target.GetInstanceIDCompat().ToString()
                : selector == "path" ? "/" + target.name
                : target.name;
            var response = Send(
                "ensure_brain",
                new JObject
                {
                    ["camera"] = reference,
                    ["defaultBlendStyle"] = "Cut",
                    ["defaultBlendDuration"] = 0,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(target.GetInstanceIDCompat(), response["data"].Value<int>("instanceID"));
            Assert.IsFalse(response["data"].Value<bool>("alreadyExisted"));
            Assert.AreSame(camera, target.GetComponent<Camera>());
            var brain = target.GetComponent<CameraCreationTestBrain>();
            Assert.IsNotNull(brain);
            Assert.AreEqual(0f, brain.DefaultBlend.Time);
            Assert.AreEqual(CameraCreationTestBlendStyle.Cut, brain.DefaultBlend.Style);
            Assert.AreSame(otherBrain, other.GetComponent<CameraCreationTestBrain>());
            Assert.AreEqual(2f, otherBrain.DefaultBlend.Time);
        }

        [Test]
        public void ExplicitMissingCameraRejectsDespiteExistingBrain()
        {
            SyntheticBrainMode();
            var brain = NewObject().AddComponent<CameraCreationTestBrain>();
            var response = Send("ensure_brain", new JObject { ["camera"] = UniqueName() });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(brain, brain.gameObject.GetComponent<CameraCreationTestBrain>());
            Assert.AreEqual(1, ownedScene.rootCount);
        }

        [Test]
        public void ExistingSelectedBrainRetainsBlendAndComponentIdentity()
        {
            SyntheticBrainMode();
            GameObject target = NewObject();
            target.AddComponent<Camera>();
            var brain = target.AddComponent<CameraCreationTestBrain>();
            var response = Send("ensure_brain", new JObject { ["camera"] = target.GetInstanceIDCompat().ToString(), ["defaultBlendDuration"] = 0 });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(response["data"].Value<bool>("alreadyExisted"));
            Assert.AreSame(brain, target.GetComponent<CameraCreationTestBrain>());
            Assert.AreEqual(2f, brain.DefaultBlend.Time);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OmittedOrNullCameraRetainsGlobalExistingBrainBehavior(bool explicitNull)
        {
            SyntheticBrainMode();
            Assert.IsNull(UnityFindObjectsCompat.FindAny(typeof(CameraCreationTestBrain)), "Unowned test Brain exists.");
            var brain = NewObject().AddComponent<CameraCreationTestBrain>();
            JObject props = explicitNull ? new JObject { ["camera"] = JValue.CreateNull() } : new JObject();
            var response = Send("ensure_brain", props);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(brain.gameObject.GetInstanceIDCompat(), response["data"].Value<int>("instanceID"));
            Assert.IsTrue(response["data"].Value<bool>("alreadyExisted"));
        }

        [Test]
        public void InstalledCinemachineCreationAppliesExplicitFieldOfView()
        {
            UseInstalledCinemachine();
            var response = Send(
                "create_camera",
                new JObject
                {
                    ["name"] = UniqueName(),
                    ["preset"] = "static",
                    ["priority"] = 0,
                    ["fieldOfView"] = 42,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            GameObject go = ReturnedObject(response);
            using var serialized = new SerializedObject(go.GetComponent(CameraHelpers.CinemachineCameraType));
            var lens = serialized.FindProperty("Lens") ?? serialized.FindProperty("m_Lens");
            Assert.IsNotNull(lens, "Installed Cinemachine Lens layout precondition.");
            Assert.AreEqual(42f, lens.FindPropertyRelative("FieldOfView").floatValue);
            var priority = serialized.FindProperty("Priority");
            Assert.IsNotNull(priority, "Installed Cinemachine Priority layout precondition.");
            Assert.IsTrue(priority.FindPropertyRelative("Enabled").boolValue);
            Assert.AreEqual(0, priority.FindPropertyRelative("m_Value").intValue);
        }

        [Test]
        public void UnsupportedOwnedCameraLayoutFailureRemovesOnlyNewObject()
        {
            UseInstalledCinemachine();
            Cache("_cmCameraType").SetValue(null, typeof(CameraCreationTestCamera));
            GameObject existing = NewObject();
            var response = Send(
                "create_camera",
                new JObject
                {
                    ["name"] = UniqueName(),
                    ["preset"] = "static",
                    ["fieldOfView"] = 42,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("Priority", response.Value<string>("error"));
            Assert.AreEqual(1, ownedScene.rootCount, "Failed operation retained its newly allocated object.");
            Assert.AreSame(existing, ownedScene.GetRootGameObjects()[0]);
        }

        private JObject Send(string action, JToken properties)
        {
            try
            {
                return JObject.FromObject(ManageCamera.HandleCommand(new JObject { ["action"] = action, ["properties"] = properties }));
            }
            finally
            {
                // Only roots created in this fixture's empty owned scene belong to its cleanup.
                foreach (GameObject root in ownedScene.GetRootGameObjects())
                    if (!ownedObjects.Contains(root))
                        ownedObjects.Add(root);
            }
        }

        private GameObject ReturnedObject(JObject response)
        {
            int id = response["data"].Value<int>("instanceID");
            foreach (GameObject go in ownedObjects)
                if (go.GetInstanceIDCompat() == id)
                    return go;
            Assert.Fail("Returned ID must identify the fixture-owned scene instance.");
            return null;
        }

        private GameObject NewObject()
        {
            var go = new GameObject(UniqueName());
            Assert.AreEqual(ownedScene, go.scene);
            ownedObjects.Add(go);
            return go;
        }

        private static string UniqueName() => "McpCameraCreation_" + Guid.NewGuid().ToString("N");

        private static FieldInfo Cache(string name) => typeof(CameraHelpers).GetField(name, CacheFlags);

        private static void BasicMode() => Cache("_hasCinemachine").SetValue(null, false);

        private static void SyntheticBrainMode()
        {
            Cache("_hasCinemachine").SetValue(null, true);
            Cache("_cmCameraType").SetValue(null, typeof(CameraCreationTestCamera));
            Cache("_cmBrainType").SetValue(null, typeof(CameraCreationTestBrain));
        }

        private static void UseInstalledCinemachine()
        {
            Type camera = UnityTypeResolver.ResolveComponent("CinemachineCamera");
            Type brain = UnityTypeResolver.ResolveComponent("CinemachineBrain");
            Type aim = UnityTypeResolver.ResolveComponent("CinemachineHardLookAt");
            if (camera == null || brain == null || aim == null)
                Assert.Ignore("Real Cinemachine camera/brain/static preset is unavailable.");
            Cache("_hasCinemachine").SetValue(null, true);
            Cache("_cmCameraType").SetValue(null, camera);
            Cache("_cmBrainType").SetValue(null, brain);
        }
    }

    public enum CameraCreationTestBlendStyle
    {
        Cut,
        EaseInOut,
    }

    [Serializable]
    public struct CameraCreationTestBlend
    {
        public CameraCreationTestBlendStyle Style;
        public float Time;
    }

    public class CameraCreationTestBrain : MonoBehaviour
    {
        public CameraCreationTestBlend DefaultBlend = new() { Style = CameraCreationTestBlendStyle.EaseInOut, Time = 2 };
    }

    public class CameraCreationTestCamera : MonoBehaviour
    {
        public int Priority;
    }
}
