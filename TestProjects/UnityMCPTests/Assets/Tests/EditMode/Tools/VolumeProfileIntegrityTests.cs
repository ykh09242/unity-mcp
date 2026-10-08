using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    // Actual installed Core types are required; these tests do not exercise rendering.
    [Parallelizable(ParallelScope.None)]
    public class VolumeProfileIntegrityTests
    {
        private Scene originalScene;
        private Scene ownedScene;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveSelection;
        private string assetRoot;
        private string folderGuid;
        private bool captured;
        private bool ownsFolder;
        private readonly List<GameObject> ownedObjects = new();
        private readonly List<UnityEngine.Object> ownedTransients = new();
        private readonly PrefabTestSceneFixture sceneFixture = new();

        [OneTimeSetUp]
        public void PrepareRunnerScene() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunnerScene() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            captured = ownsFolder = false;
            ownedScene = default;
            folderGuid = null;
            ownedObjects.Clear();
            ownedTransients.Clear();
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned Prefab Stage is open.");
            if (!GraphicsHelpers.HasVolumeSystem)
                Assert.Ignore("Core Volume/VolumeProfile package types are not installed.");
            originalScene = SceneManager.GetActiveScene();
            originalSelection = Selection.objects;
            originalActiveSelection = Selection.activeObject;
            captured = true;
            ownedScene = sceneFixture.Create("McpVolumeProfileIntegrity_", Guid.NewGuid().ToString("N"));
            assetRoot = "Assets/__McpVolumeProfileIntegrity_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsFalse(Directory.Exists(FullPath(assetRoot)));
            Assert.IsTrue(string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(assetRoot)));
            folderGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(assetRoot));
            Assert.IsFalse(string.IsNullOrEmpty(folderGuid));
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(folderGuid));
            ownsFolder = true;
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
                    Assert.AreEqual(ownedScene, go.scene);
                    foreach (Component component in go.GetComponents<Component>())
                        if (component != null)
                            Undo.ClearUndo(component);
                    Undo.ClearUndo(go);
                    UnityEngine.Object.DestroyImmediate(go);
                }
                foreach (UnityEngine.Object obj in ownedTransients)
                {
                    if (obj == null || AssetDatabase.Contains(obj))
                        continue;
                    Undo.ClearUndo(obj);
                    UnityEngine.Object.DestroyImmediate(obj);
                }
                if (ownedScene.IsValid() && ownedScene.isLoaded)
                {
                    Assert.AreEqual(0, ownedScene.rootCount, "Unexpected objects retained for diagnosis.");
                    sceneFixture.Close();
                }
                if (ownsFolder)
                {
                    Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(folderGuid));
                    Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
                    ownsFolder = false;
                }
            }
            finally
            {
                if (originalScene.IsValid() && originalScene.isLoaded)
                    SceneManager.SetActiveScene(originalScene);
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveSelection;
                captured = false;
            }
        }

        [TestCase("volume_create")]
        [TestCase("volume_create_profile")]
        public void OccupiedOtherAssetPreservesIdentityGuidAndBytes(string action)
        {
            string path = assetRoot + "/Occupied.asset";
            var old = new Mesh { name = "Owned collision mesh" };
            ownedTransients.Add(old);
            old.vertices = new[] { new Vector3(7, 0, 0) };
            AssetDatabase.CreateAsset(old, path);
            AssetDatabase.SaveAssets();
            Assert.IsTrue(AssetDatabase.Contains(old));
            Assert.AreSame(old, AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            string guid = AssetDatabase.AssetPathToGUID(path);
            byte[] bytes = File.ReadAllBytes(FullPath(path));
            var request = new JObject { [action == "volume_create" ? "profile_path" : "path"] = path };
            if (action == "volume_create")
                request["name"] = UniqueName();
            var response = Send(action, request);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(old, AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(FullPath(path)));
            Assert.AreEqual(new Vector3(7, 0, 0), old.vertices[0]);
            Assert.AreEqual(0, ownedScene.rootCount);
        }

        [Test]
        public void StandaloneCreatePreservesExistingProfile()
        {
            string path = assetRoot + "/Existing.asset";
            UnityEngine.Object profile = NewProfile(path);
            string guid = AssetDatabase.AssetPathToGUID(path);
            var response = Send("volume_create_profile", new JObject { ["path"] = path });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(profile, AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
        }

        [Test]
        public void ColdFileIsPreservedWithoutImportOrMetadata()
        {
            string path = assetRoot + "/Cold.asset";
            Assert.IsFalse(File.Exists(FullPath(path)));
            Assert.IsFalse(File.Exists(FullPath(path) + ".meta"));
            byte[] bytes = { 65, 0, 66, 255 };
            File.WriteAllBytes(FullPath(path), bytes);
            var response = Send("volume_create_profile", new JObject { ["path"] = path });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(FullPath(path)));
            Assert.IsFalse(File.Exists(FullPath(path) + ".meta"));
            Assert.AreEqual(0, ownedScene.rootCount);
        }

        [Test]
        public void ExistingProfileIsBorrowedWithFalseAndZeroProperties()
        {
            string path = assetRoot + "/Borrowed.asset";
            UnityEngine.Object profile = NewProfile(path);
            var response = Send(
                "volume_create",
                new JObject
                {
                    ["name"] = UniqueName(),
                    ["profile_path"] = path,
                    ["is_global"] = false,
                    ["weight"] = 0,
                    ["priority"] = 0,
                }
            );
            Component volume = ReturnedVolume(response);
            Assert.AreSame(profile, Member(volume, "sharedProfile"));
            Assert.AreEqual(false, Member(volume, "isGlobal"));
            Assert.AreEqual(0f, Member(volume, "weight"));
            Assert.AreEqual(0f, Member(volume, "priority"));
            Assert.IsTrue(AssetDatabase.Contains(profile));
        }

        [Test]
        public void EmbeddedProfileDefaultsAndUnsupportedEffectsRemainAccepted()
        {
            var response = Send(
                "volume_create",
                new JObject
                {
                    ["name"] = UniqueName(),
                    ["effects"] = new JArray
                    {
                        new JObject { ["type"] = "__MissingVolumeEffect_" + Guid.NewGuid().ToString("N") },
                        new JObject(),
                    },
                }
            );
            Component volume = ReturnedVolume(response);
            var profile = (UnityEngine.Object)Member(volume, "sharedProfile");
            Assert.IsNotNull(profile);
            Assert.IsFalse(AssetDatabase.Contains(profile));
            Assert.AreEqual(true, Member(volume, "isGlobal"));
            Assert.AreEqual(1f, Member(volume, "weight"));
            Assert.AreEqual(0, Components(profile).Count);
        }

        [TestCase("volume_create")]
        [TestCase("volume_add_effect")]
        public void PersistentAddedEffectBelongsToTheProfileAsset(string action)
        {
            Type effect = RequireEffect();
            string path = assetRoot + "/Effects.asset";
            UnityEngine.Object profile;
            if (action == "volume_create")
            {
                var response = Send(
                    action,
                    new JObject
                    {
                        ["name"] = UniqueName(),
                        ["profile_path"] = path,
                        ["effects"] = new JArray { new JObject { ["type"] = effect.Name } },
                    }
                );
                profile = (UnityEngine.Object)Member(ReturnedVolume(response), "sharedProfile");
            }
            else
            {
                profile = NewProfile(path);
                Component volume = OwnedVolume(profile);
                var response = Send(action, new JObject { ["target"] = volume.gameObject.GetInstanceIDCompat().ToString(), ["effect"] = effect.Name });
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            }
            Assert.IsTrue(AssetDatabase.Contains(profile));
            UnityEngine.Object component = (UnityEngine.Object)Components(profile)[0];
            Assert.IsTrue(AssetDatabase.Contains(component));
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(component));
            Assert.Contains(component, AssetDatabase.LoadAllAssetsAtPath(path));
            // These are native ownership/path oracles, not a measured render or reload guarantee.
        }

        [Test]
        public void DuplicateInitialEffectCleansOnlyNewGameObjectAndRetainsBorrowedProfile()
        {
            Type effect = RequireEffect();
            string path = assetRoot + "/Duplicate.asset";
            UnityEngine.Object profile = NewProfile(path);
            UnityEngine.Object component = AddNativeEffect(profile, effect);
            AssetDatabase.AddObjectToAsset(component, profile);
            string name = UniqueName();
            var response = Send(
                "volume_create",
                new JObject
                {
                    ["name"] = name,
                    ["profile_path"] = path,
                    ["effects"] = new JArray { new JObject { ["type"] = effect.Name } },
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(ownedScene.GetRootGameObjects().Any(go => go.name == name));
            Assert.AreSame(profile, AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            Assert.AreSame(component, Components(profile)[0]);
            Assert.IsTrue(AssetDatabase.Contains(component));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DuplicateRequestedEffectsRejectBeforeProfileOrSceneMutation(bool existingProfile)
        {
            Type effect = GraphicsHelpers.GetAvailableEffectTypes().FirstOrDefault();
            if (effect == null)
                Assert.Ignore("No concrete Volume effect is installed.");
            string path = assetRoot + "/Nested/Duplicate.asset";
            UnityEngine.Object profile = null;
            byte[] bytes = null;
            string guid = null;
            if (existingProfile)
            {
                AssetDatabase.CreateFolder(assetRoot, "Nested");
                profile = NewProfile(path);
                AssetDatabase.SaveAssets();
                bytes = File.ReadAllBytes(FullPath(path));
                guid = AssetDatabase.AssetPathToGUID(path);
            }
            int profileCount = UnityEngine.Resources.FindObjectsOfTypeAll(GraphicsHelpers.VolumeProfileType).Length;
            var response = Send(
                "volume_create",
                new JObject
                {
                    ["name"] = UniqueName(),
                    ["profile_path"] = path,
                    ["effects"] = new JArray
                    {
                        new JObject { ["type"] = effect.Name },
                        new JObject { ["type"] = effect.Name },
                    },
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0, ownedScene.rootCount);
            Assert.AreEqual(profileCount, UnityEngine.Resources.FindObjectsOfTypeAll(GraphicsHelpers.VolumeProfileType).Length);
            if (existingProfile)
            {
                Assert.AreEqual(0, Components(profile).Count, "A rejected list must not retain its earlier valid effect.");
                Assert.IsFalse(EditorUtility.IsDirty(profile));
                Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(FullPath(path)));
            }
            else
            {
                Assert.IsFalse(File.Exists(FullPath(path)));
                Assert.IsFalse(Directory.Exists(FullPath(assetRoot + "/Nested")));
                Assert.IsTrue(string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path)));
            }
        }

        [Test]
        public void OwnedTransientCleanupDestroysProfileAndItsIndependentEffects()
        {
            Type effect = RequireEffect();
            var profile = ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType);
            ownedTransients.Add(profile);
            UnityEngine.Object component = AddNativeEffect(profile, effect);
            ownedTransients.Add(component);
            Assert.IsFalse(AssetDatabase.Contains(profile));
            Assert.IsFalse(AssetDatabase.Contains(component));
            CleanupHelper().Invoke(null, new object[] { profile });
            Assert.IsTrue(profile == null);
            Assert.IsTrue(component == null);
        }

        [Test]
        public void TransientCleanupRetainsPersistentProfileAndEffect()
        {
            Type effect = RequireEffect();
            string path = assetRoot + "/Retained.asset";
            UnityEngine.Object profile = NewProfile(path);
            UnityEngine.Object component = AddNativeEffect(profile, effect);
            AssetDatabase.AddObjectToAsset(component, profile);
            CleanupHelper().Invoke(null, new object[] { profile });
            Assert.IsTrue(profile != null && component != null);
            Assert.IsTrue(AssetDatabase.Contains(profile));
            Assert.IsTrue(AssetDatabase.Contains(component));
            Assert.AreSame(component, Components(profile)[0]);
        }

        [TestCase("relative")]
        [TestCase("backslash")]
        public void ExistingPathNormalizationRemainsCompatible(string form)
        {
            string path = assetRoot + "/Normalized.asset";
            string requested = form == "relative" ? path.Substring(7, path.Length - 7 - 6) : path.Replace('/', '\\');
            var response = Send("volume_create_profile", new JObject { ["path"] = requested });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(path, response["data"].Value<string>("path"));
            Assert.IsTrue(AssetDatabase.Contains(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path)));
        }

        [Test]
        public void InvalidPathRejectsBeforeOwnedSceneAllocation()
        {
            var response = Send("volume_create", new JObject { ["name"] = UniqueName(), ["profile_path"] = "Assets/../Outside.asset" });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0, ownedScene.rootCount);
        }

        [TestCase("false")]
        [TestCase("0")]
        [TestCase("{}")]
        [TestCase("[]")]
        public void MalformedCreationNameRejectsBeforeOwnedSceneOrProfileMutation(string json)
        {
            string path = assetRoot + "/Fresh/Profile.asset";
            int profileCount = UnityEngine.Resources.FindObjectsOfTypeAll(GraphicsHelpers.VolumeProfileType).Length;
            var response = Send("volume_create", new JObject { ["name"] = JToken.Parse(json), ["profile_path"] = path });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'name'", response.Value<string>("error"));
            Assert.AreEqual(0, ownedScene.rootCount);
            Assert.AreEqual(profileCount, UnityEngine.Resources.FindObjectsOfTypeAll(GraphicsHelpers.VolumeProfileType).Length);
            Assert.IsFalse(Directory.Exists(FullPath(assetRoot + "/Fresh")));
            Assert.IsFalse(File.Exists(FullPath(path)));
            Assert.IsTrue(string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path)));
        }

        [TestCase("volume_create", "profile_path")]
        [TestCase("volume_create_profile", "path")]
        public void ContainerPathRejectsWithParameterErrorBeforeOwnedFolderMutation(string action, string parameter)
        {
            // Keep the serialized malformed token confined even against the old coercion behavior.
            var token = new JObject { ["ownedPath"] = assetRoot + "/Fresh/Profile.asset" };
            var response = Send(action, new JObject { ["name"] = UniqueName(), [parameter] = token });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'" + parameter + "'", response.Value<string>("error"));
            Assert.AreEqual(0, ownedScene.rootCount);
            Assert.IsFalse(Directory.Exists(FullPath(assetRoot + "/Fresh")));
        }

        [Test]
        public void OmittedCreationNameUsesTrackedOwnedDefault()
        {
            Component volume = ReturnedVolume(Send("volume_create", new JObject()));
            Assert.AreEqual("Volume", volume.gameObject.name);
            Assert.AreEqual(1, ownedScene.rootCount);
        }

        private JObject Send(string action, JObject request)
        {
            request["action"] = action;
            string name = request["name"]?.ToString() ?? "Volume";
            try
            {
                return JObject.FromObject(ManageGraphics.HandleCommand(request));
            }
            finally
            {
                foreach (GameObject go in ownedScene.GetRootGameObjects())
                {
                    if (name == null || go.name != name)
                        continue;
                    if (!ownedObjects.Contains(go))
                        ownedObjects.Add(go);
                    Component volume = go.GetComponent(GraphicsHelpers.VolumeType);
                    if (volume != null && Member(volume, "sharedProfile") is UnityEngine.Object profile && !AssetDatabase.Contains(profile))
                    {
                        foreach (UnityEngine.Object component in Components(profile))
                            if (component != null && !ownedTransients.Contains(component))
                                ownedTransients.Add(component);
                        if (!ownedTransients.Contains(profile))
                            ownedTransients.Add(profile);
                    }
                }
            }
        }

        private Component ReturnedVolume(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            int id = response["data"].Value<int>("instanceID");
            GameObject go = ownedObjects.Single(obj => obj != null && obj.GetInstanceIDCompat() == id);
            Assert.AreEqual(ownedScene, go.scene);
            return go.GetComponent(GraphicsHelpers.VolumeType);
        }

        private UnityEngine.Object NewProfile(string path)
        {
            ScriptableObject profile = ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType);
            ownedTransients.Add(profile);
            AssetDatabase.CreateAsset(profile, path);
            Assert.IsTrue(AssetDatabase.Contains(profile));
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(profile));
            return profile;
        }

        private Component OwnedVolume(UnityEngine.Object profile)
        {
            var go = new GameObject(UniqueName());
            ownedObjects.Add(go);
            Assert.AreEqual(ownedScene, go.scene);
            Component volume = go.AddComponent(GraphicsHelpers.VolumeType);
            var field = volume.GetType().GetField("sharedProfile");
            Assert.IsNotNull(field, "Actual Core sharedProfile public field precondition.");
            field.SetValue(volume, profile);
            return volume;
        }

        private static Type RequireEffect()
        {
            Type type = GraphicsHelpers.ResolveVolumeComponentType("Bloom") ?? GraphicsHelpers.GetAvailableEffectTypes().FirstOrDefault();
            if (type == null)
                Assert.Ignore("No concrete Volume effect is installed.");
            return type;
        }

        private static UnityEngine.Object AddNativeEffect(UnityEngine.Object profile, Type effect) =>
            (UnityEngine.Object)
                GraphicsHelpers.VolumeProfileType.GetMethod("Add", new[] { typeof(Type), typeof(bool) }).Invoke(profile, new object[] { effect, true });

        private static IList Components(object profile) => (IList)Member(profile, "components");

        private static MethodInfo CleanupHelper() => typeof(VolumeOps).GetMethod("DestroyTransientProfile", BindingFlags.NonPublic | BindingFlags.Static);

        private static object Member(object obj, string name) => obj.GetType().GetProperty(name)?.GetValue(obj) ?? obj.GetType().GetField(name)?.GetValue(obj);

        private static string FullPath(string path) => Path.Combine(Application.dataPath, path.Substring(7).Replace('/', Path.DirectorySeparatorChar));

        private static string UniqueName() => "__McpVolumeIntegrity_" + Guid.NewGuid().ToString("N");
    }
}
