using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools.Cameras;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class CameraConfigurationIntegrityTests
    {
        private readonly List<GameObject> objects = new List<GameObject>();
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene originalScene;
        private Scene ownedScene;
        private Object originalSelection;
        private Object[] originalSelections;
        private GameObject target;
        private GameObject oldReference;
        private GameObject newReference;
        private Component camera;
        private int originalOverrideId;
        private Component originalOverrideBrain;
        private bool capturedState;

        private static FieldInfo OverrideId => typeof(CameraControl).GetField("_overrideId", BindingFlags.NonPublic | BindingFlags.Static);
        private static FieldInfo OverrideBrain => typeof(CameraControl).GetField("_overrideBrain", BindingFlags.NonPublic | BindingFlags.Static);

        [OneTimeSetUp]
        public void PrepareRunnerBootstrap() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunnerBootstrap() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            capturedState = false;
            objects.Clear();
            ownedScene = default;
            if (!CameraHelpers.HasCinemachine)
                Assert.Ignore("Cinemachine is not installed.");
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned prefab stage is open.");
            if (CameraHelpers.FindBrain() != null)
                Assert.Ignore("An unowned CinemachineBrain is present.");
            Assert.IsNotNull(OverrideId);
            Assert.IsNotNull(OverrideBrain);
            originalOverrideId = (int)OverrideId.GetValue(null);
            originalOverrideBrain = (Component)OverrideBrain.GetValue(null);
            if (originalOverrideId >= 0 || !ReferenceEquals(originalOverrideBrain, null))
                Assert.Ignore("Existing override state must not be changed by this fixture.");
            originalScene = SceneManager.GetActiveScene();
            originalSelection = Selection.activeObject;
            originalSelections = Selection.objects;
            capturedState = true;
            ownedScene = sceneFixture.Create("McpCameraConfiguration_", Guid.NewGuid().ToString("N"));
            target = Owned("Camera");
            camera = target.AddComponent(CameraHelpers.CinemachineCameraType);
            if (camera is Behaviour behaviour)
                behaviour.enabled = false;
            oldReference = Owned("Old");
            newReference = Owned("Reference");
            RequireTargetProperty("Follow").SetValue(camera, oldReference.transform);
            RequireTargetProperty("LookAt").SetValue(camera, oldReference.transform);
            Assert.AreSame(target, CameraHelpers.FindTargetGameObject(Request("set_target", new JObject())));
        }

        private GameObject Owned(string label)
        {
            var go = new GameObject("CameraConfiguration_" + label + "_" + Guid.NewGuid().ToString("N"));
            objects.Add(go);
            SceneManager.MoveGameObjectToScene(go, ownedScene);
            return go;
        }

        private PropertyInfo RequireTargetProperty(string name)
        {
            var property = camera.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            Assert.IsNotNull(property);
            Assert.IsTrue(property.CanWrite);
            Assert.AreEqual(typeof(Transform), property.PropertyType);
            return property;
        }

        private Transform TargetValue(string name) => (Transform)RequireTargetProperty(name).GetValue(camera);

        private JObject Request(string action, JObject properties) =>
            new JObject
            {
                ["action"] = action,
                ["target"] = target.GetInstanceIDCompat().ToString(),
                ["searchMethod"] = "by_id",
                ["properties"] = properties,
            };

        private JObject Send(string action, JObject properties) => JObject.FromObject(ManageCamera.HandleCommand(Request(action, properties)));

        private Component OwnedBrain()
        {
            var go = Owned("Brain");
            go.AddComponent<Camera>().enabled = false;
            var brain = go.AddComponent(CameraHelpers.CinemachineBrainType);
            if (brain is Behaviour behaviour)
                behaviour.enabled = false;
            Assert.AreSame(brain, CameraHelpers.FindBrain(), "The public control path must resolve only the owned Brain.");
            var method = brain.GetType().GetMethod("SetCameraOverride", BindingFlags.Public | BindingFlags.Instance);
            if (method == null || method.GetParameters().Length != 6)
                Assert.Ignore("This ownership fixture requires the documented six-argument override API.");
            return brain;
        }

        private JObject Force(Component brain)
        {
            Assert.AreSame(brain, CameraHelpers.FindBrain());
            var response = Send("force_camera", new JObject());
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("override", response["data"]?.Value<string>("method"), response.ToString());
            Assert.AreSame(brain, OverrideBrain.GetValue(null));
            Assert.GreaterOrEqual((int)OverrideId.GetValue(null), 0);
            return response;
        }

        [TearDown]
        public void TearDown()
        {
            if (!capturedState)
                return;
            try
            {
                var owner = (Component)OverrideBrain.GetValue(null);
                int id = (int)OverrideId.GetValue(null);
                if (owner != null && id >= 0 && objects.Contains(owner.gameObject))
                {
                    var release = owner.GetType().GetMethod("ReleaseCameraOverride", BindingFlags.Public | BindingFlags.Instance);
                    if (release != null)
                        release.Invoke(owner, new object[] { id });
                }
            }
            finally
            {
                OverrideId.SetValue(null, originalOverrideId);
                OverrideBrain.SetValue(null, originalOverrideBrain);
                foreach (var go in objects)
                {
                    if (go == null)
                        continue;
                    foreach (var component in go.GetComponents<Component>())
                        if (component != null)
                            Undo.ClearUndo(component);
                    Undo.ClearUndo(go);
                    Object.DestroyImmediate(go);
                }
                objects.Clear();
                Selection.objects = originalSelections;
                Selection.activeObject = originalSelection;
                if (originalScene.IsValid() && originalScene.isLoaded)
                    SceneManager.SetActiveScene(originalScene);
                if (ownedScene.IsValid() && ownedScene.isLoaded)
                    sceneFixture.Close();
                capturedState = false;
            }
        }

        [TestCase("follow")]
        [TestCase("lookAt")]
        public void MissingRequestedTargetDoesNotChangeEitherReference(string missingField)
        {
            int objectDirty = EditorUtility.GetDirtyCount(target);
            int componentDirty = EditorUtility.GetDirtyCount(camera);
            var properties = new JObject { ["follow"] = newReference.name, ["lookAt"] = newReference.GetInstanceIDCompat().ToString() };
            properties[missingField] = "MissingCameraReference_" + Guid.NewGuid().ToString("N");
            var response = Send("set_target", properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(oldReference.transform, TargetValue("Follow"));
            Assert.AreSame(oldReference.transform, TargetValue("LookAt"));
            Assert.AreEqual(objectDirty, EditorUtility.GetDirtyCount(target));
            Assert.AreEqual(componentDirty, EditorUtility.GetDirtyCount(camera));
        }

        [Test]
        public void ExplicitNullClearsBothReferences()
        {
            var response = Send("set_target", new JObject { ["follow"] = JValue.CreateNull(), ["look_at"] = JValue.CreateNull() });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(TargetValue("Follow"));
            Assert.IsNull(TargetValue("LookAt"));
        }

        [Test]
        public void OmittedTargetsKeepBothReferences()
        {
            var response = Send("set_target", new JObject());
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(oldReference.transform, TargetValue("Follow"));
            Assert.AreSame(oldReference.transform, TargetValue("LookAt"));
        }

        [Test]
        public void NameAndIdReferencesAreApplied()
        {
            var response = Send("set_target", new JObject { ["follow"] = newReference.name, ["lookAt"] = newReference.GetInstanceIDCompat().ToString() });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(newReference.transform, TargetValue("Follow"));
            Assert.AreSame(newReference.transform, TargetValue("LookAt"));
        }

        [TestCase(0)]
        [TestCase(-2)]
        public void PriorityPreservesZeroAndNegativeValues(int priority)
        {
            var response = Send("set_priority", new JObject { ["priority"] = priority });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            using var serialized = new SerializedObject(camera);
            var field = serialized.FindProperty("Priority");
            Assert.IsNotNull(field);
            Assert.IsTrue(field.FindPropertyRelative("Enabled").boolValue);
            Assert.AreEqual(priority, field.FindPropertyRelative("m_Value").intValue);
        }

        [Test]
        public void MissingPriorityLayoutReportsFailureWithoutDirtyingComponent()
        {
            var basic = Owned("Basic").AddComponent<Camera>();
            basic.enabled = false;
            int before = EditorUtility.GetDirtyCount(basic);
            Assert.IsNotNull(CameraConfigure.SetPriority(basic, 0));
            Assert.AreEqual(before, EditorUtility.GetDirtyCount(basic));
        }

        [TestCase("add_extension", "Camera")]
        [TestCase("add_extension", "Transform")]
        [TestCase("add_extension", "CinemachineCamera")]
        [TestCase("add_extension", "CinemachineExtension")]
        [TestCase("remove_extension", "Camera")]
        [TestCase("remove_extension", "Transform")]
        [TestCase("remove_extension", "CinemachineCamera")]
        [TestCase("remove_extension", "CinemachineExtension")]
        public void ExtensionOperationsRejectNonExtensionAndAbstractTypesWithoutMutation(string action, string typeName)
        {
            if (typeName == "Camera")
                target.AddComponent<Camera>().enabled = false;
            if (action == "remove_extension" && typeName == "CinemachineExtension")
            {
                var extensionType = CameraHelpers.ResolveComponentType("CinemachineRecomposer");
                Assert.IsNotNull(extensionType);
                target.AddComponent(extensionType);
            }
            var components = target.GetComponents<Component>();
            int objectDirty = EditorUtility.GetDirtyCount(target);
            int componentDirty = EditorUtility.GetDirtyCount(camera);

            var response = Send(action, new JObject { ["extensionType"] = typeName });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(typeName, response.ToString());
            CollectionAssert.AreEqual(components, target.GetComponents<Component>());
            Assert.AreEqual(objectDirty, EditorUtility.GetDirtyCount(target));
            Assert.AreEqual(componentDirty, EditorUtility.GetDirtyCount(camera));
        }

        [TestCase("set_body", "Body", "bodyType", "Camera")]
        [TestCase("set_body", "Body", "bodyType", "CinemachineCamera")]
        [TestCase("set_body", "Body", "bodyType", "CinemachineRecomposer")]
        [TestCase("set_body", "Body", "bodyType", "CinemachineComponentBase")]
        [TestCase("set_aim", "Aim", "aimType", "Camera")]
        [TestCase("set_aim", "Aim", "aimType", "CinemachineCamera")]
        [TestCase("set_aim", "Aim", "aimType", "CinemachineRecomposer")]
        [TestCase("set_aim", "Aim", "aimType", "CinemachineComponentBase")]
        public void PipelineReplacementRejectsNonPipelineAndAbstractTypesWithoutMutation(string action, string stage, string typeKey, string typeName)
        {
            var existingType = CameraHelpers.ResolveComponentType(stage == "Body" ? "CinemachineFollow" : "CinemachineRotationComposer");
            Assert.IsNotNull(existingType);
            var existing = target.AddComponent(existingType);
            Assert.AreSame(existing, CameraHelpers.GetPipelineComponent(camera, stage));
            var components = target.GetComponents<Component>();
            int objectDirty = EditorUtility.GetDirtyCount(target);
            int componentDirty = EditorUtility.GetDirtyCount(existing);

            var response = Send(action, new JObject { [typeKey] = typeName });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(typeName, response.ToString());
            Assert.AreSame(existing, CameraHelpers.GetPipelineComponent(camera, stage));
            CollectionAssert.AreEqual(components, target.GetComponents<Component>());
            Assert.AreEqual(objectDirty, EditorUtility.GetDirtyCount(target));
            Assert.AreEqual(componentDirty, EditorUtility.GetDirtyCount(existing));
        }

        [TestCase("set_body", "Body", "bodyType", "CinemachineHardLookAt", false)]
        [TestCase("set_body", "Body", "bodyType", "CinemachineBasicMultiChannelPerlin", false)]
        [TestCase("set_aim", "Aim", "aimType", "CinemachineFollow", false)]
        [TestCase("set_aim", "Aim", "aimType", "CinemachineBasicMultiChannelPerlin", false)]
        [TestCase("set_body", "Body", "body_type", "CinemachineHardLookAt", false)]
        [TestCase("set_aim", "Aim", "aim_type", "CinemachineFollow", false)]
        [TestCase("set_body", "Body", "bodyType", "CinemachineHardLookAt", true)]
        [TestCase("set_aim", "Aim", "aimType", "CinemachineFollow", true)]
        public void PipelineReplacementRejectsAnotherDeclaredStageWithoutMutation(
            string action,
            string stage,
            string typeKey,
            string typeName,
            bool alreadyPresent
        )
        {
            var existingType = CameraHelpers.ResolveComponentType(stage == "Body" ? "CinemachineFollow" : "CinemachineRotationComposer");
            var wrongType = CameraHelpers.ResolveComponentType(typeName);
            Assert.IsNotNull(existingType);
            Assert.IsNotNull(wrongType);
            var existing = target.AddComponent(existingType);
            if (alreadyPresent)
                target.AddComponent(wrongType);
            Assert.AreSame(existing, CameraHelpers.GetPipelineComponent(camera, stage));
            var components = target.GetComponents<Component>();
            int objectDirty = EditorUtility.GetDirtyCount(target);
            int componentDirty = EditorUtility.GetDirtyCount(existing);

            var response = Send(action, new JObject { [typeKey] = wrongType.FullName });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(typeName, response.ToString());
            Assert.AreSame(existing, CameraHelpers.GetPipelineComponent(camera, stage));
            CollectionAssert.AreEqual(components, target.GetComponents<Component>());
            Assert.AreEqual(objectDirty, EditorUtility.GetDirtyCount(target));
            Assert.AreEqual(componentDirty, EditorUtility.GetDirtyCount(existing));
        }

        [TestCase("set_body", "Body", "bodyType", "body_type", "CinemachineFollow", "CinemachineHardLookAt", false)]
        [TestCase("set_body", "Body", "bodyType", "body_type", "CinemachineFollow", "CinemachineHardLookAt", true)]
        [TestCase("set_aim", "Aim", "aimType", "aim_type", "CinemachineRotationComposer", "CinemachineFollow", false)]
        [TestCase("set_aim", "Aim", "aimType", "aim_type", "CinemachineRotationComposer", "CinemachineFollow", true)]
        public void PrimaryPipelineTypeKeepsPrecedenceOverAlias(
            string action,
            string stage,
            string typeKey,
            string aliasKey,
            string typeName,
            string wrongTypeName,
            bool explicitNull
        )
        {
            var existing = (Behaviour)target.AddComponent(CameraHelpers.ResolveComponentType(typeName));
            existing.enabled = true;
            var components = target.GetComponents<Component>();
            var properties = new JObject
            {
                [typeKey] = explicitNull ? JValue.CreateNull() : new JValue(typeName),
                [aliasKey] = wrongTypeName,
                ["enabled"] = false,
            };

            var response = Send(action, properties);

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(existing, CameraHelpers.GetPipelineComponent(camera, stage));
            CollectionAssert.AreEqual(components, target.GetComponents<Component>());
            Assert.IsFalse(existing.enabled);
        }

        [TestCase("set_body", "Body", "bodyType", "CinemachineFollow", "CinemachineThirdPersonFollow")]
        [TestCase("set_aim", "Aim", "aimType", "CinemachineRotationComposer", "CinemachineHardLookAt")]
        public void InvalidLaterPipelinePropertyPreservesExistingStage(string action, string stage, string typeKey, string oldTypeName, string newTypeName)
        {
            var existing = target.AddComponent(CameraHelpers.ResolveComponentType(oldTypeName));
            var components = target.GetComponents<Component>();
            int objectDirty = EditorUtility.GetDirtyCount(target);
            int componentDirty = EditorUtility.GetDirtyCount(existing);

            var response = Send(
                action,
                new JObject
                {
                    [typeKey] = newTypeName,
                    ["enabled"] = false,
                    ["NoSuchPipelineProperty"] = 3,
                }
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("NoSuchPipelineProperty", response.ToString());
            Assert.AreSame(existing, CameraHelpers.GetPipelineComponent(camera, stage));
            CollectionAssert.AreEqual(components, target.GetComponents<Component>());
            Assert.AreEqual(objectDirty, EditorUtility.GetDirtyCount(target));
            Assert.AreEqual(componentDirty, EditorUtility.GetDirtyCount(existing));
        }

        [TestCase("set_body", "Body", "CinemachineFollow")]
        [TestCase("set_aim", "Aim", "CinemachineRotationComposer")]
        public void InvalidLaterExistingPipelinePropertyPreservesEarlierValue(string action, string stage, string typeName)
        {
            var existing = (Behaviour)target.AddComponent(CameraHelpers.ResolveComponentType(typeName));
            existing.enabled = true;
            int componentDirty = EditorUtility.GetDirtyCount(existing);

            var response = Send(action, new JObject { ["enabled"] = false, ["NoSuchPipelineProperty"] = 3 });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(existing, CameraHelpers.GetPipelineComponent(camera, stage));
            Assert.IsTrue(existing.enabled);
            Assert.AreEqual(componentDirty, EditorUtility.GetDirtyCount(existing));
        }

        [TestCase("set_noise", "CinemachineBasicMultiChannelPerlin")]
        [TestCase("add_extension", "CinemachineRecomposer")]
        public void InvalidLaterAddedComponentPropertyDoesNotAddComponent(string action, string typeName)
        {
            var components = target.GetComponents<Component>();
            int objectDirty = EditorUtility.GetDirtyCount(target);
            var properties = new JObject { ["enabled"] = false, ["NoSuchPipelineProperty"] = 3 };
            if (action == "add_extension")
                properties.AddFirst(new JProperty("extensionType", typeName));

            var response = Send(action, properties);

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("NoSuchPipelineProperty", response.ToString());
            CollectionAssert.AreEqual(components, target.GetComponents<Component>());
            Assert.AreEqual(objectDirty, EditorUtility.GetDirtyCount(target));
        }

        [Test]
        public void InvalidLaterNoisePropertyPreservesExistingComponentAndValue()
        {
            var existing = (Behaviour)target.AddComponent(CameraHelpers.ResolveComponentType("CinemachineBasicMultiChannelPerlin"));
            existing.enabled = true;
            int componentDirty = EditorUtility.GetDirtyCount(existing);
            var response = Send("set_noise", new JObject { ["enabled"] = false, ["NoSuchPipelineProperty"] = 3 });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(existing.enabled);
            Assert.AreEqual(componentDirty, EditorUtility.GetDirtyCount(existing));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void NullNoiseProfilePathDoesNotAddOrChangeNoiseComponent(bool existingNoise)
        {
            var noiseType = CameraHelpers.ResolveComponentType("CinemachineBasicMultiChannelPerlin");
            Assert.IsNotNull(noiseType);
            var existing = existingNoise ? (Behaviour)target.AddComponent(noiseType) : null;
            if (existing != null)
                existing.enabled = true;
            var components = target.GetComponents<Component>();
            int objectDirty = EditorUtility.GetDirtyCount(target);
            int componentDirty = existing != null ? EditorUtility.GetDirtyCount(existing) : 0;

            var response = Send("set_noise", new JObject { ["enabled"] = false, ["NoiseProfile.name"] = "Changed" });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("NoiseProfile.name", response.ToString());
            CollectionAssert.AreEqual(components, target.GetComponents<Component>());
            Assert.AreEqual(objectDirty, EditorUtility.GetDirtyCount(target));
            if (existing != null)
            {
                Assert.IsTrue(existing.enabled);
                Assert.AreEqual(componentDirty, EditorUtility.GetDirtyCount(existing));
            }
        }

        [Test]
        public void ConcreteExtensionCanBeAdded()
        {
            var extensionType = CameraHelpers.ResolveComponentType("CinemachineRecomposer");
            Assert.IsNotNull(extensionType);

            var response = Send("add_extension", new JObject { ["extensionType"] = extensionType.FullName });

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(target.GetComponent(extensionType));
        }

        [Test]
        public void ConcreteExtensionCanBeRemoved()
        {
            var extensionType = CameraHelpers.ResolveComponentType("CinemachineRecomposer");
            Assert.IsNotNull(extensionType);
            target.AddComponent(extensionType);

            var response = Send("remove_extension", new JObject { ["extensionType"] = extensionType.FullName });

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(target.GetComponent(extensionType));
            Assert.IsNotNull(camera);
        }

        [TestCase("set_body", "Body", "bodyType", "CinemachineFollow", "CinemachineThirdPersonFollow")]
        [TestCase("set_aim", "Aim", "aimType", "CinemachineRotationComposer", "CinemachineHardLookAt")]
        public void ConcretePipelineComponentCanReplaceItsStage(string action, string stage, string typeKey, string oldTypeName, string newTypeName)
        {
            var oldType = CameraHelpers.ResolveComponentType(oldTypeName);
            var newType = CameraHelpers.ResolveComponentType(newTypeName);
            Assert.IsNotNull(oldType);
            Assert.IsNotNull(newType);
            target.AddComponent(oldType);

            var response = Send(action, new JObject { [typeKey] = newType.FullName });

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(target.GetComponent(oldType));
            Assert.AreEqual(newType, CameraHelpers.GetPipelineComponent(camera, stage)?.GetType());
        }

        [Test]
        public void OverrideReleaseClearsTheRecordedOwnerAndId()
        {
            var brain = OwnedBrain();
            int id = Force(brain)["data"].Value<int>("overrideId");
            var response = Send("release_override", new JObject());
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(id, response["data"].Value<int>("releasedOverrideId"));
            Assert.AreEqual(-1, OverrideId.GetValue(null));
            Assert.IsNull(OverrideBrain.GetValue(null));
        }

        [Test]
        public void SameBrainReusesItsOverrideId()
        {
            var brain = OwnedBrain();
            int id = Force(brain)["data"].Value<int>("overrideId");
            Assert.AreEqual(id, Force(brain)["data"].Value<int>("overrideId"));
        }

        [Test]
        public void DestroyedOwnerCanBeReleasedWithoutAnotherBrain()
        {
            var brain = OwnedBrain();
            Force(brain);
            Object.DestroyImmediate(brain.gameObject);
            Assert.IsNull(CameraHelpers.FindBrain());
            var response = Send("release_override", new JObject());
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(-1, OverrideId.GetValue(null));
            Assert.IsNull(OverrideBrain.GetValue(null));
        }

        [Test]
        public void DestroyedOwnerDoesNotBlockANewOwnedBrain()
        {
            var first = OwnedBrain();
            Force(first);
            Object.DestroyImmediate(first.gameObject);
            var second = OwnedBrain();
            Force(second);
            Assert.AreSame(second, OverrideBrain.GetValue(null));
        }
    }
}
