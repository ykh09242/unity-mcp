using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class GraphicsAssetOwnershipTests
    {
        private readonly PrefabTestSceneFixture scene = new();
        private readonly List<Object> transients = new();
        private readonly HashSet<string> allocatedPaths = new();
        private string assetRootGuid;
        private string packageRootGuid;
        private string assetRoot;
        private string packageRunRoot;
        private Material previousSkybox;
        private RenderPipelineAsset previousDefault;
        private RenderPipelineAsset previousQuality;

        [OneTimeSetUp]
        public void PrepareRunner() => scene.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunner() => scene.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            string id = Guid.NewGuid().ToString("N");
            assetRoot = "Assets/__McpGraphicsOwnership_" + id;
            packageRunRoot = null;
            assetRootGuid = packageRootGuid = null;
            allocatedPaths.Clear();
            transients.Clear();
            previousSkybox = RenderSettings.skybox;
            previousDefault = GraphicsSettings.defaultRenderPipeline;
            previousQuality = QualitySettings.renderPipeline;
            scene.Create("McpGraphicsOwnership_", id);
            Assert.IsFalse(Directory.Exists(Full(assetRoot)));
            Assert.IsTrue(string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(assetRoot)));
            assetRootGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(assetRoot));
            TestContext.Progress.WriteLine("Graphics ownership fixture: " + assetRoot);
            Assert.IsTrue(AssetDatabase.IsValidFolder(assetRoot));
        }

        [TearDown]
        public void TearDown()
        {
            QualitySettings.renderPipeline = previousQuality;
            GraphicsSettings.defaultRenderPipeline = previousDefault;
            RenderSettings.skybox = previousSkybox;
            foreach (Object obj in transients)
                if (obj != null && !EditorUtility.IsPersistent(obj))
                {
                    Undo.ClearUndo(obj);
                    Object.DestroyImmediate(obj);
                }
            scene.Close();
            foreach (string root in new[] { assetRoot, packageRunRoot })
            {
                if (string.IsNullOrEmpty(root) || !AssetDatabase.IsValidFolder(root))
                    continue;
                SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(Full(root)), Full(root));
                SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(Full(root)), Full(root) + ".meta");
                string expectedGuid = root == assetRoot ? assetRootGuid : packageRootGuid;
                Assert.AreEqual(expectedGuid, AssetDatabase.AssetPathToGUID(root), "Preserve a replaced fixture root.");
                Assert.IsNotEmpty(expectedGuid);
                Assert.IsEmpty(Directory.GetDirectories(Full(root)), "Preserve unexpected fixture directories.");
                foreach (string file in Directory.GetFiles(Full(root)))
                {
                    Assert.AreEqual(0, (int)(File.GetAttributes(file) & FileAttributes.ReparsePoint), "Preserve a linked fixture file.");
                    string path = root + "/" + Path.GetFileName(file);
                    if (path.EndsWith(".meta", StringComparison.Ordinal))
                        path = path.Substring(0, path.Length - 5);
                    Assert.IsTrue(allocatedPaths.Contains(path), "Preserve unexpected fixture file: " + file);
                }
                foreach (string guid in AssetDatabase.FindAssets("", new[] { root }))
                foreach (Object obj in AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GUIDToAssetPath(guid)))
                    if (obj != null)
                        Undo.ClearUndo(obj);
                Assert.IsTrue(AssetDatabase.DeleteAsset(root));
            }
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(true, true)]
        public void SkyboxPropertiesRespectActualMaterialOwner(bool package, bool subasset)
        {
            // Given a writable material, optionally a subasset reached through its native object.
            var material = Track(new Material(RequireShader()));
            material.SetFloat("_Glossiness", 0.25f);
            string path = PathFor(package, subasset ? "Owner.asset" : "Skybox.mat");
            if (subasset)
            {
                var owner = Track(new Mesh());
                AssetDatabase.CreateAsset(owner, path);
                AssetDatabase.AddObjectToAsset(material, owner);
                material = PersistSubAsset(owner, material);
            }
            else
                AssetDatabase.CreateAsset(material, path);
            AssetDatabase.SaveAssetIfDirty(material);
            RenderSettings.skybox = material;
            byte[] bytes = File.ReadAllBytes(Full(path));
            int dirty = EditorUtility.GetDirtyCount(material);

            // When modifying the current material, there is no caller-provided path to trust.
            JObject result = Send("skybox_set_properties", new JObject { ["properties"] = new JObject { ["_Glossiness"] = 0.75f } });

            // Then only Assets-owned materials may change, including their persistent owner file.
            Assert.AreEqual(!package, result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(package ? 0.25f : 0.75f, material.GetFloat("_Glossiness"));
            if (package)
            {
                Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(material));
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
            }
        }

        [Test]
        public void PackageSkyboxReferenceRemainsAssignableWithoutEditingItsMaterial()
        {
            // Given a writable package material resolved through its persistent GUID.
            var material = Track(new Material(RequireShader()));
            string path = PathFor(true, "Referenced.mat");
            AssetDatabase.CreateAsset(material, path);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            material = AssetDatabase.LoadAssetAtPath<Material>(path);
            Assert.IsNotNull(material);
            string resolved = AssetDatabase.GUIDToAssetPath(AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(path, resolved);
            byte[] bytes = File.ReadAllBytes(Full(path));
            byte[] metadata = File.ReadAllBytes(Full(path) + ".meta");
            int dirty = EditorUtility.GetDirtyCount(material);
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(material, out string guid, out long localId));

            // When assigning a package reference to the scene's environment settings.
            JObject result = Send("skybox_set_material", new JObject { ["material"] = resolved });

            // Then borrowing is allowed and the package material itself remains unchanged.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(material == RenderSettings.skybox, "The same native material must remain assigned.");
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(RenderSettings.skybox, out string actualGuid, out long actualLocalId));
            Assert.AreEqual(guid, actualGuid);
            Assert.AreEqual(localId, actualLocalId);
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(RenderSettings.skybox));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(path) + ".meta"));
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(material));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PipelineSettingsRespectConfiguredPersistentOwner(bool package)
        {
            // Given an active pipeline in a writable package or the project's Assets.
            Type type = RequireType("UniversalRenderPipelineAsset");
            var renderer = Track(ScriptableObject.CreateInstance(RequireType("UniversalRendererData")));
            AssetDatabase.CreateAsset(renderer, PathFor(false, "Renderer.asset"));
            var pipeline = NewPipeline(renderer, package);
            string path = AssetDatabase.GetAssetPath(pipeline);
            byte[] bytes = File.ReadAllBytes(Full(path));
            var property = type.GetProperty("renderScale");
            Assert.IsNotNull(property);
            float original = (float)property.GetValue(pipeline);
            float value = original > 0.6f ? 0.5f : 0.8f;

            // When the settings route follows the configured pipeline reference.
            JObject result = Send("pipeline_set_settings", new JObject { ["settings"] = new JObject { ["renderScale"] = value } });

            // Then rejection leaves both native state and disk bytes unchanged.
            Assert.AreEqual(!package, result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(package ? original : value, (float)property.GetValue(pipeline));
            if (package)
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
        }

        [TestCase("volume_add_effect", false)]
        [TestCase("volume_add_effect", true)]
        [TestCase("volume_set_effect", false)]
        [TestCase("volume_set_effect", true)]
        [TestCase("volume_remove_effect", false)]
        [TestCase("volume_remove_effect", true)]
        public void VolumeMutationsRespectSharedProfileOwner(string action, bool package)
        {
            // Given a real Core profile with a Bloom subasset when the operation needs it.
            RequireVolumes();
            var profile = Track(ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType));
            string path = PathFor(package, "Profile.asset");
            AssetDatabase.CreateAsset(profile, path);
            Object effect = null;
            if (action != "volume_add_effect")
                effect = AddBloom(profile);
            Component volume = NewVolume(profile);
            AssetDatabase.SaveAssetIfDirty(profile);
            string original = EditorJsonUtility.ToJson(profile);
            byte[] bytes = File.ReadAllBytes(Full(path));
            int dirty = EditorUtility.GetDirtyCount(profile);
            Object[] identities = AssetDatabase.LoadAllAssetsAtPath(path);
            string effectJson = effect != null ? EditorJsonUtility.ToJson(effect) : null;

            // When the action resolves the Volume through a native scene object ID.
            JObject result = Send(
                action,
                new JObject
                {
                    ["target"] = volume.gameObject.GetInstanceIDCompat().ToString(),
                    ["effect"] = "Bloom",
                    ["parameters"] = new JObject { ["intensity"] = 2f },
                }
            );

            // Then package state, override values and subasset identities are preserved.
            Assert.AreEqual(!package, result.Value<bool>("success"), result.ToString());
            if (package)
            {
                Assert.AreEqual(original, EditorJsonUtility.ToJson(profile));
                Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(profile));
                CollectionAssert.AreEqual(identities, AssetDatabase.LoadAllAssetsAtPath(path));
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
                if (effect != null)
                    Assert.AreEqual(effectJson, EditorJsonUtility.ToJson(effect));
            }
            else if (action == "volume_add_effect")
            {
                Assert.AreEqual(1, Components(profile).Count);
                Assert.AreEqual(path, AssetDatabase.GetAssetPath((Object)Components(profile)[0]));
            }
            else if (action == "volume_remove_effect")
                Assert.IsEmpty(Components(profile));
            else
                Assert.AreNotEqual(effectJson, EditorJsonUtility.ToJson(effect));
        }

        [Test]
        public void AssetsProfileCannotMutateBorrowedPackageEffectOverride()
        {
            // Given an Assets profile referring to a Bloom owned by a package profile.
            RequireVolumes();
            var borrowed = Track(ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType));
            string path = PathFor(true, "BorrowedProfile.asset");
            AssetDatabase.CreateAsset(borrowed, path);
            Object bloom = AddBloom(borrowed);
            var profile = Track(ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType));
            AssetDatabase.CreateAsset(profile, PathFor(false, "Profile.asset"));
            Components(profile).Add(bloom);
            Component volume = NewVolume(profile);
            string original = EditorJsonUtility.ToJson(bloom);
            AssetDatabase.SaveAssetIfDirty(borrowed);
            byte[] bytes = File.ReadAllBytes(Full(path));

            // When modifying an effect reached indirectly through the allowed profile.
            JObject result = Send(
                "volume_set_effect",
                new JObject
                {
                    ["target"] = volume.gameObject.GetInstanceIDCompat().ToString(),
                    ["effect"] = "Bloom",
                    ["parameters"] = new JObject { ["intensity"] = 3f },
                }
            );

            // Then the nested object's own owner controls the write.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(original, EditorJsonUtility.ToJson(bloom));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void EditableProfileCanUnlinkBorrowedPackageEffectWithoutChangingItsOwner(bool transient)
        {
            // Given a package-owned Bloom borrowed by an editable persistent or transient profile.
            RequireVolumes();
            var borrowed = Track(ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType));
            string path = PathFor(true, "BorrowedProfile.asset");
            AssetDatabase.CreateAsset(borrowed, path);
            Object bloom = AddBloom(borrowed);
            var profile = Track(ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType));
            if (!transient)
                AssetDatabase.CreateAsset(profile, PathFor(false, "Profile.asset"));
            Components(profile).Add(bloom);
            Component volume = NewVolume(profile);
            AssetDatabase.SaveAssetIfDirty(borrowed);
            string originalProfile = EditorJsonUtility.ToJson(borrowed);
            string originalEffect = EditorJsonUtility.ToJson(bloom);
            byte[] bytes = File.ReadAllBytes(Full(path));
            byte[] metadata = File.ReadAllBytes(Full(path) + ".meta");
            int profileDirty = EditorUtility.GetDirtyCount(borrowed);
            int effectDirty = EditorUtility.GetDirtyCount(bloom);
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(bloom, out string guid, out long localId));

            // When removing only the reference from the target profile.
            JObject result = Send(
                "volume_remove_effect",
                new JObject { ["target"] = volume.gameObject.GetInstanceIDCompat().ToString(), ["effect"] = "Bloom" }
            );

            // Then the reference is unlinked while the borrowed component and owner are preserved.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsEmpty(Components(profile));
            Assert.AreEqual(1, Components(borrowed).Count);
            Assert.IsTrue(bloom == (Object)Components(borrowed)[0], "Unlinking must preserve the borrowed native component.");
            Assert.IsTrue(AssetDatabase.Contains(bloom));
            Assert.IsTrue(AssetDatabase.IsSubAsset(bloom));
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(bloom));
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(bloom, out string actualGuid, out long actualLocalId));
            Assert.AreEqual(guid, actualGuid);
            Assert.AreEqual(localId, actualLocalId);
            Assert.AreEqual(originalProfile, EditorJsonUtility.ToJson(borrowed));
            Assert.AreEqual(originalEffect, EditorJsonUtility.ToJson(bloom));
            Assert.AreEqual(profileDirty, EditorUtility.GetDirtyCount(borrowed));
            Assert.AreEqual(effectDirty, EditorUtility.GetDirtyCount(bloom));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(path) + ".meta"));
        }

        [TestCase("feature_add", false)]
        [TestCase("feature_add", true)]
        [TestCase("feature_remove", false)]
        [TestCase("feature_remove", true)]
        [TestCase("feature_configure", false)]
        [TestCase("feature_configure", true)]
        [TestCase("feature_toggle", false)]
        [TestCase("feature_toggle", true)]
        [TestCase("feature_reorder", false)]
        [TestCase("feature_reorder", true)]
        public void RendererMutationsRespectReferencedRendererOwner(string action, bool package)
        {
            // Given an Assets pipeline referencing a separately owned renderer data asset.
            ScriptableObject renderer = NewRenderer(package);
            Object feature = action == "feature_add" ? null : AddFeature(renderer);
            string path = AssetDatabase.GetAssetPath(renderer);
            AssetDatabase.SaveAssetIfDirty(renderer);
            string original = EditorJsonUtility.ToJson(renderer);
            byte[] bytes = File.ReadAllBytes(Full(path));
            Object[] identities = AssetDatabase.LoadAllAssetsAtPath(path);
            string featureJson = feature != null ? EditorJsonUtility.ToJson(feature) : null;

            // When the renderer is selected indirectly from the active pipeline.
            JObject result = Send(
                action,
                new JObject
                {
                    ["type"] = "FullScreenPassRendererFeature",
                    ["index"] = 0,
                    ["properties"] = new JObject { ["m_Name"] = "Configured owned feature" },
                    ["active"] = false,
                    ["order"] = new JArray(0),
                }
            );

            // Then package-owned renderers reject every structural/property mutation.
            Assert.AreEqual(!package, result.Value<bool>("success"), result.ToString());
            if (package)
            {
                Assert.AreEqual(original, EditorJsonUtility.ToJson(renderer));
                CollectionAssert.AreEqual(identities, AssetDatabase.LoadAllAssetsAtPath(path));
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
                if (feature != null)
                    Assert.AreEqual(featureJson, EditorJsonUtility.ToJson(feature));
            }
            else
            {
                var features = (IList)renderer.GetType().GetProperty("rendererFeatures").GetValue(renderer);
                Assert.AreEqual(action == "feature_remove" ? 0 : 1, features.Count);
                if (action == "feature_configure")
                    Assert.AreEqual("Configured owned feature", feature.name);
                if (action == "feature_toggle")
                    Assert.IsFalse((bool)feature.GetType().GetProperty("isActive").GetValue(feature));
            }
        }

        [TestCase("feature_configure")]
        [TestCase("feature_toggle")]
        public void AssetsRendererCannotMutateBorrowedPackageFeature(string action)
        {
            // Given a package-owned feature in an otherwise editable renderer's list.
            ScriptableObject renderer = NewRenderer(false);
            var owner = Track(ScriptableObject.CreateInstance(RequireType("UniversalRendererData")));
            string path = PathFor(true, "BorrowedRenderer.asset");
            AssetDatabase.CreateAsset(owner, path);
            Object feature = AddFeature(owner);
            using (var serialized = new SerializedObject(renderer))
            {
                var features = serialized.FindProperty("m_RendererFeatures");
                features.arraySize = 1;
                features.GetArrayElementAtIndex(0).objectReferenceValue = feature;
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            AssetDatabase.SaveAssetIfDirty(owner);
            string original = EditorJsonUtility.ToJson(feature);
            byte[] bytes = File.ReadAllBytes(Full(path));

            // When the mutation resolves the borrowed feature by its list index.
            JObject result = Send(
                action,
                new JObject
                {
                    ["index"] = 0,
                    ["properties"] = new JObject { ["m_Name"] = "Must not change" },
                    ["active"] = false,
                }
            );

            // Then checking only the renderer would be insufficient.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(original, EditorJsonUtility.ToJson(feature));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Full(path)));
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [TestCase(false)]
        [TestCase(true)]
        public void LoadedSkyboxRechecksLinkedPersistentOwnerBeforeEditing(bool ancestor)
        {
            // Given a native material loaded before its physical owner entry becomes linked.
            var material = Track(new Material(RequireShader()));
            material.SetFloat("_Glossiness", 0.25f);
            string path = PathFor(false, "Linked.mat");
            AssetDatabase.CreateAsset(material, path);
            RenderSettings.skybox = material;
            string source = ancestor ? Full(assetRoot) : Full(path);
            string target = source + "_owned_target";
            bool moved = false;
            bool linked = false;
            try
            {
                if (ancestor)
                    Directory.Move(source, target);
                else
                    File.Move(source, target);
                moved = true;
                linked =
                    Application.platform == RuntimePlatform.WindowsEditor
                        ? CreateSymbolicLinkW(source, target, (ancestor ? 1 : 0) | 2)
                        : symlink(target, source) == 0;
                if (!linked)
                    Assert.Ignore("Symbolic link creation unavailable: " + Marshal.GetLastWin32Error());
                byte[] bytes = File.ReadAllBytes(ancestor ? Path.Combine(target, "Linked.mat") : target);

                // When the already loaded object is used, no path is re-resolved from input.
                JObject result = Send("skybox_set_properties", new JObject { ["properties"] = new JObject { ["_Glossiness"] = 0.75f } });

                // Then physical containment is checked again before modifying native state.
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(0.25f, material.GetFloat("_Glossiness"));
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(ancestor ? Path.Combine(target, "Linked.mat") : target));
            }
            finally
            {
                if (linked)
                {
                    if (ancestor)
                        Directory.Delete(source);
                    else
                        File.Delete(source);
                }
                if (moved)
                {
                    if (ancestor)
                        Directory.Move(target, source);
                    else
                        File.Move(target, source);
                }
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ToolAddedFeatureRemovalReportsActualCleanupOutcome(bool initialize)
        {
            ScriptableObject renderer = NewRenderer(false);
            string path = AssetDatabase.GetAssetPath(renderer);
            byte[] metadata = File.ReadAllBytes(Full(path) + ".meta");
            JObject added = Send("feature_add", new JObject { ["type"] = "FullScreenPassRendererFeature" });
            Assert.IsTrue(added.Value<bool>("success"), added.ToString());
            JObject survivorAdded = Send("feature_add", new JObject { ["type"] = "RenderObjects", ["name"] = "Unrelated feature" });
            Assert.IsTrue(survivorAdded.Value<bool>("success"), survivorAdded.ToString());
            var features = (IList)renderer.GetType().GetProperty("rendererFeatures").GetValue(renderer);
            Assert.AreEqual(2, features.Count);
            var survivor = (Object)features[1];
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(survivor, out string survivorGuid, out long survivorLocalId));
            string survivorJson = EditorJsonUtility.ToJson(survivor);
            var feature = (Object)features[0];
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(feature, out string guid, out long localId));
            // The native pass is an oracle only: production never depends on URP private fields.
            var passField = feature.GetType().GetField("m_FullScreenPass", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
            Assert.IsNotNull(passField);
            if (initialize)
            {
                if (passField.GetValue(feature) == null)
                    feature.GetType().GetMethod("Create").Invoke(feature, null);
                Assert.IsNotNull(passField.GetValue(feature));
            }
            else
            {
                Assert.IsNull(RenderPipelineManager.currentPipeline, "Exercise editing a configured renderer without a live pipeline.");
                Assert.IsNull(passField.GetValue(feature), "The tool-added feature must exercise URP's uninitialized cleanup path.");
            }

            JObject removed = Send("feature_remove", new JObject { ["index"] = 0 });

            Assert.IsTrue(removed.Value<bool>("success"), removed.ToString());
            features = (IList)renderer.GetType().GetProperty("rendererFeatures").GetValue(renderer);
            Assert.AreEqual(1, features.Count);
            Assert.IsTrue(survivor == (Object)features[0]);
            Assert.AreEqual(survivorJson, EditorJsonUtility.ToJson(survivor));
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(survivor, out string actualSurvivorGuid, out long actualSurvivorLocalId));
            Assert.AreEqual(survivorGuid, actualSurvivorGuid);
            Assert.AreEqual(survivorLocalId, actualSurvivorLocalId);
            Assert.IsTrue(feature == null, "The owned native feature must be destroyed.");
            Assert.IsFalse(EditorUtility.IsDirty(renderer), "The modified owner must be saved even if Dispose fails.");
            using (var serialized = new SerializedObject(renderer))
            {
                Assert.AreEqual(1, serialized.FindProperty("m_RendererFeatures").arraySize);
                Assert.AreEqual(1, serialized.FindProperty("m_RendererFeatureMap").arraySize);
                Assert.AreEqual(survivorLocalId, serialized.FindProperty("m_RendererFeatureMap").GetArrayElementAtIndex(0).longValue);
            }
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            foreach (Object asset in AssetDatabase.LoadAllAssetsAtPath(path))
            {
                Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string actualGuid, out long actualLocalId));
                Assert.IsFalse(actualGuid == guid && actualLocalId == localId, "The removed identity must not remain on disk.");
            }
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(path) + ".meta"));
            if (initialize)
                Assert.IsNull(removed["warnings"]);
            else
            {
                Assert.IsTrue(removed["data"].Value<bool>("removed"));
                Assert.IsFalse(removed["data"].Value<bool>("cleanupSucceeded"));
                Assert.AreEqual("renderer_feature_cleanup_failed", removed["warnings"][0].Value<string>("code"));
                Assert.AreEqual(nameof(NullReferenceException), removed["warnings"][0].Value<string>("exceptionType"));
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RemovedFeatureCleanupRunsOnceAndReportsOnlyExceptionType(bool throws)
        {
            var target = new FeatureCleanupProbe { Throws = throws };
            var unrelated = new FeatureCleanupProbe();
            var dispose = typeof(RendererFeatureOps).GetMethod(
                "DisposeRemovedFeatureResources",
                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic
            );
            Assert.IsNotNull(dispose);
            object result = dispose.Invoke(null, new object[] { target });
            Assert.AreEqual(1, target.DisposeCalls);
            Assert.AreEqual(0, unrelated.DisposeCalls);
            Assert.AreEqual(throws ? nameof(InvalidOperationException) : null, result);
        }

        private sealed class FeatureCleanupProbe : IDisposable
        {
            internal bool Throws;
            internal int DisposeCalls;

            public void Dispose()
            {
                DisposeCalls++;
                if (Throws)
                    throw new InvalidOperationException("Private asset path must not appear in the cleanup response.");
            }
        }

        private T Track<T>(T obj)
            where T : Object
        {
            transients.Add(obj);
            return obj;
        }

        private string PathFor(bool package, string name)
        {
            if (!package)
            {
                string path = assetRoot + "/" + name;
                allocatedPaths.Add(path);
                return path;
            }
            var info = PackageInfo.FindForAssembly(typeof(VolumeOps).Assembly);
            if (info == null || info.source != PackageSource.Embedded)
                Assert.Ignore("The package write regression requires the native runner's isolated embedded MCP package.");
            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(info.resolvedPath), info.resolvedPath);
            string packageRoot = info.assetPath;
            Assert.AreEqual(Path.GetFullPath(Full(packageRoot)), Path.GetFullPath(info.resolvedPath), "Never write into a local/shared/cache package.");
            packageRunRoot ??= packageRoot + "/" + Path.GetFileName(assetRoot);
            if (!AssetDatabase.IsValidFolder(packageRunRoot))
            {
                Assert.IsFalse(Directory.Exists(Full(packageRunRoot)));
                Assert.IsTrue(string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(packageRunRoot)));
                packageRootGuid = AssetDatabase.CreateFolder(packageRoot, Path.GetFileName(packageRunRoot));
                TestContext.Progress.WriteLine("Graphics ownership fixture: " + packageRunRoot);
                Assert.IsTrue(AssetDatabase.IsValidFolder(packageRunRoot));
            }
            string packagePath = packageRunRoot + "/" + name;
            allocatedPaths.Add(packagePath);
            return packagePath;
        }

        private Component NewVolume(Object profile)
        {
            var go = Track(new GameObject("Owned graphics volume"));
            Component volume = go.AddComponent(GraphicsHelpers.VolumeType);
            volume.GetType().GetField("sharedProfile").SetValue(volume, profile);
            return volume;
        }

        private Object AddBloom(Object profile)
        {
            var type = GraphicsHelpers.ResolveVolumeComponentType("Bloom");
            Assert.IsNotNull(type);
            var effect = (Object)
                GraphicsHelpers.VolumeProfileType.GetMethod("Add", new[] { typeof(Type), typeof(bool) }).Invoke(profile, new object[] { type, true });
            Track(effect);
            AssetDatabase.AddObjectToAsset(effect, profile);
            return PersistSubAsset(profile, effect);
        }

        private static T PersistSubAsset<T>(Object owner, T child)
            where T : Object
        {
            string path = AssetDatabase.GetAssetPath(owner);
            // NativeFormatImporter may otherwise choose a different main asset by type priority.
            AssetDatabase.SetMainObject(owner, path);
            EditorUtility.SetDirty(owner);
            EditorUtility.SetDirty(child);
            AssetDatabase.SaveAssetIfDirty(owner);
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(child, out string guid, out long localId));
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            Assert.IsTrue(owner == AssetDatabase.LoadMainAssetAtPath(path));
            foreach (Object candidate in AssetDatabase.LoadAllAssetsAtPath(path))
            {
                if (
                    candidate is T typed
                    && AssetDatabase.TryGetGUIDAndLocalFileIdentifier(candidate, out string actualGuid, out long actualLocalId)
                    && actualGuid == guid
                    && actualLocalId == localId
                )
                {
                    Assert.IsTrue(AssetDatabase.IsSubAsset(typed));
                    return typed;
                }
            }
            Assert.Fail("The imported child must retain its persistent GUID and local file ID.");
            return null;
        }

        private ScriptableObject NewRenderer(bool package)
        {
            var renderer = Track(ScriptableObject.CreateInstance(RequireType("UniversalRendererData")));
            AssetDatabase.CreateAsset(renderer, PathFor(package, "Renderer.asset"));
            NewPipeline(renderer, false);
            return renderer;
        }

        private RenderPipelineAsset NewPipeline(ScriptableObject renderer, bool package)
        {
            var pipeline = (RenderPipelineAsset)Track(ScriptableObject.CreateInstance(RequireType("UniversalRenderPipelineAsset")));
            using (var serialized = new SerializedObject(pipeline))
            {
                var list = serialized.FindProperty("m_RendererDataList");
                list.arraySize = 1;
                list.GetArrayElementAtIndex(0).objectReferenceValue = renderer;
                serialized.FindProperty("m_DefaultRendererIndex").intValue = 0;
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            AssetDatabase.CreateAsset(pipeline, PathFor(package, "Pipeline.asset"));
            UsePipeline(pipeline);
            return pipeline;
        }

        private Object AddFeature(Object renderer)
        {
            var feature = Track(ScriptableObject.CreateInstance(RequireType("FullScreenPassRendererFeature")));
            AssetDatabase.AddObjectToAsset(feature, renderer);
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(feature, out _, out long localId));
            using var serialized = new SerializedObject(renderer);
            var list = serialized.FindProperty("m_RendererFeatures");
            list.arraySize = 1;
            list.GetArrayElementAtIndex(0).objectReferenceValue = feature;
            var map = serialized.FindProperty("m_RendererFeatureMap");
            if (map != null)
            {
                map.arraySize = 1;
                map.GetArrayElementAtIndex(0).longValue = localId;
            }
            serialized.ApplyModifiedPropertiesWithoutUndo();
            return feature;
        }

        private static void UsePipeline(RenderPipelineAsset pipeline)
        {
            QualitySettings.renderPipeline = pipeline;
            GraphicsSettings.defaultRenderPipeline = pipeline;
            Assert.AreSame(pipeline, GraphicsSettings.currentRenderPipeline);
        }

        private static Type RequireType(string name)
        {
            Type type = Type.GetType("UnityEngine.Rendering.Universal." + name + ", Unity.RenderPipelines.Universal.Runtime");
            if (type == null)
                Assert.Ignore("URP type unavailable: " + name);
            return type;
        }

        private static void RequireVolumes()
        {
            if (!GraphicsHelpers.HasVolumeSystem || GraphicsHelpers.ResolveVolumeComponentType("Bloom") == null)
                Assert.Ignore("Core Volume and URP Bloom types are required.");
        }

        private static Shader RequireShader()
        {
            Shader shader = Shader.Find("Standard");
            if (shader == null || shader.FindPropertyIndex("_Glossiness") < 0)
                Assert.Ignore("Built-in Standard shader is required for the material mutation witness.");
            return shader;
        }

        private static IList Components(object profile) => (IList)profile.GetType().GetField("components").GetValue(profile);

        private static string Full(string path) => Path.GetFullPath(Path.Combine(Application.dataPath, "../", path));

        private static JObject Send(string action, JObject request)
        {
            request["action"] = action;
            return JObject.FromObject(ManageGraphics.HandleCommand(request));
        }
    }
}
