using System;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class RendererFeatureCreationIntegrityTests
    {
        private string assetRoot;
        private string folderGuid;
        private string rendererPath;
        private ScriptableObject renderer;
        private RenderPipelineAsset pipeline;
        private RenderPipelineAsset originalDefault;
        private RenderPipelineAsset originalQuality;
        private bool ownsFolder;
        private bool captured;
        private string featureType;

        [SetUp]
        public void SetUp()
        {
            captured = ownsFolder = false;
            renderer = null;
            pipeline = null;
            var rendererType = Type.GetType("UnityEngine.Rendering.Universal.UniversalRendererData, Unity.RenderPipelines.Universal.Runtime");
            var pipelineType = Type.GetType("UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset, Unity.RenderPipelines.Universal.Runtime");
            var feature = Type.GetType("UnityEngine.Rendering.Universal.FullScreenPassRendererFeature, Unity.RenderPipelines.Universal.Runtime");
            if (rendererType == null || pipelineType == null || feature == null)
                Assert.Ignore("URP renderer, pipeline and FullScreenPass feature types are required.");
            featureType = feature.Name;
            assetRoot = "Assets/__McpFeatureCreation_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsFalse(Directory.Exists(FullPath(assetRoot)));
            folderGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(assetRoot));
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(folderGuid));
            ownsFolder = true;
            rendererPath = assetRoot + "/Renderer.asset";
            renderer = ScriptableObject.CreateInstance(rendererType);
            AssetDatabase.CreateAsset(renderer, rendererPath);
            Assert.IsTrue(AssetDatabase.Contains(renderer));
            pipeline = (RenderPipelineAsset)ScriptableObject.CreateInstance(pipelineType);
            using (var serialized = new SerializedObject(pipeline))
            {
                var renderers = serialized.FindProperty("m_RendererDataList");
                var index = serialized.FindProperty("m_DefaultRendererIndex");
                Assert.IsNotNull(renderers);
                Assert.IsNotNull(index);
                renderers.arraySize = 1;
                renderers.GetArrayElementAtIndex(0).objectReferenceValue = renderer;
                index.intValue = 0;
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            originalDefault = GraphicsSettings.defaultRenderPipeline;
            originalQuality = QualitySettings.renderPipeline;
            captured = true;
            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;
            Assert.AreSame(pipeline, GraphicsSettings.currentRenderPipeline);
            AssetDatabase.SaveAssetIfDirty(renderer);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (captured)
                {
                    QualitySettings.renderPipeline = originalQuality;
                    GraphicsSettings.defaultRenderPipeline = originalDefault;
                    captured = false;
                }
                if (pipeline != null)
                {
                    Undo.ClearUndo(pipeline);
                    Object.DestroyImmediate(pipeline);
                }
                if (ownsFolder)
                {
                    Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(folderGuid));
                    foreach (Object obj in AssetDatabase.LoadAllAssetsAtPath(rendererPath))
                        if (obj != null)
                            Undo.ClearUndo(obj);
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
                    ownsFolder = false;
                }
            }
            finally
            {
                pipeline = null;
                renderer = null;
            }
        }

        [TestCase("name", "false")]
        [TestCase("name", "0")]
        [TestCase("name", "{}")]
        [TestCase("name", "[]")]
        [TestCase("material", "false")]
        [TestCase("material", "0")]
        [TestCase("material", "{}")]
        [TestCase("material", "[]")]
        [TestCase("properties", "false")]
        [TestCase("properties", "0")]
        [TestCase("properties", "[]")]
        [TestCase("properties", "\"{}\"")]
        public void MalformedInitialFieldRejectsBeforeOwnedRendererMutation(string parameter, string json)
        {
            byte[] bytes = File.ReadAllBytes(FullPath(rendererPath));
            string guid = AssetDatabase.AssetPathToGUID(rendererPath);
            Object[] identities = AssetDatabase.LoadAllAssetsAtPath(rendererPath);
            bool dirty = EditorUtility.IsDirty(renderer);
            var response = JObject.FromObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "feature_add",
                        ["type"] = featureType,
                        [parameter] = JToken.Parse(json),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'" + parameter + "'", response.Value<string>("error"));
            CollectionAssert.AreEqual(identities, AssetDatabase.LoadAllAssetsAtPath(rendererPath));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(FullPath(rendererPath)));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(rendererPath));
            Assert.AreEqual(dirty, EditorUtility.IsDirty(renderer));
            using var serialized = new SerializedObject(renderer);
            Assert.AreEqual(0, serialized.FindProperty("m_RendererFeatures").arraySize);
            Assert.IsFalse(Directory.Exists(FullPath(assetRoot + "/Fresh")));
        }

        [TestCase("false")]
        [TestCase("0")]
        [TestCase("{}")]
        [TestCase("[]")]
        public void MalformedFeatureTypeRejectsWithParameterError(string json)
        {
            var response = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "feature_add", ["type"] = JToken.Parse(json) }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'type'", response.Value<string>("error"));
            Assert.AreEqual(1, AssetDatabase.LoadAllAssetsAtPath(rendererPath).Length);
        }

        [Test]
        public void MissingInitialMaterialRejectsBeforeOwnedRendererMutation()
        {
            AssertCreationRejectedWithoutMutation(new JObject { ["material"] = assetRoot + "/Missing.mat" }, "material");
        }

        [Test]
        public void UnknownInitialPropertyRejectsBeforeOwnedRendererMutation()
        {
            AssertCreationRejectedWithoutMutation(new JObject { ["properties"] = new JObject { ["missing_setting"] = true } }, "missing_setting");
        }

        [Test]
        public void MixedInitialPropertiesRejectBeforeOwnedRendererMutation()
        {
            AssertCreationRejectedWithoutMutation(
                new JObject
                {
                    ["properties"] = new JObject { ["m_Active"] = false, ["missing_setting"] = true },
                },
                "missing_setting"
            );
        }

        [Test]
        public void ValidInitialPropertiesPersistWithCreatedFeature()
        {
            var response = JObject.FromObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "feature_add",
                        ["type"] = featureType,
                        ["properties"] = new JObject { ["m_Active"] = false },
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { "m_Active" }, response["data"]["changed"].Values<string>());
            Assert.IsEmpty(response["data"]["failed"]);
            AssetDatabase.ImportAsset(rendererPath, ImportAssetOptions.ForceUpdate);
            var reloaded = AssetDatabase.LoadAssetAtPath<ScriptableObject>(rendererPath);
            using var serialized = new SerializedObject(reloaded);
            var features = serialized.FindProperty("m_RendererFeatures");
            Assert.AreEqual(1, features.arraySize);
            var feature = features.GetArrayElementAtIndex(0).objectReferenceValue;
            Assert.IsNotNull(feature);
            Assert.AreEqual(rendererPath, AssetDatabase.GetAssetPath(feature));
            using var featureProperties = new SerializedObject(feature);
            Assert.IsFalse(featureProperties.FindProperty("m_Active").boolValue);
        }

        [Test]
        public void ValidInitialMaterialPersistsWithCreatedFeature()
        {
            var shader = Shader.Find("Hidden/InternalErrorShader");
            Assert.IsNotNull(shader);
            var material = new Material(shader);
            string materialPath = assetRoot + "/Pass.mat";
            AssetDatabase.CreateAsset(material, materialPath);
            var response = JObject.FromObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "feature_add",
                        ["type"] = featureType,
                        ["material"] = materialPath,
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { "material" }, response["data"]["changed"].Values<string>());
            AssetDatabase.ImportAsset(rendererPath, ImportAssetOptions.ForceUpdate);
            using var serialized = new SerializedObject(AssetDatabase.LoadAssetAtPath<ScriptableObject>(rendererPath));
            var feature = serialized.FindProperty("m_RendererFeatures").GetArrayElementAtIndex(0).objectReferenceValue;
            Assert.IsNotNull(feature);
            using var featureProperties = new SerializedObject(feature);
            var materialProperty = featureProperties.FindProperty("m_PassMaterial") ?? featureProperties.FindProperty("passMaterial");
            Assert.IsNotNull(materialProperty);
            Assert.AreEqual(materialPath, AssetDatabase.GetAssetPath(materialProperty.objectReferenceValue));
        }

        [Test]
        public void ConfigurePreservesChangedAndFailedFieldContract()
        {
            var added = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "feature_add", ["type"] = featureType }));
            Assert.IsTrue(added.Value<bool>("success"), added.ToString());
            var response = JObject.FromObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "feature_configure",
                        ["index"] = 0,
                        ["properties"] = new JObject { ["m_Active"] = false, ["missing_setting"] = true },
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { "m_Active" }, response["data"]["changed"].Values<string>());
            CollectionAssert.AreEqual(new[] { "missing_setting" }, response["data"]["failed"].Values<string>());
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void ConfigureWithoutAcceptedPropertiesPreservesPendingEdits(bool rejected, bool initiallyDirty)
        {
            var added = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "feature_add", ["type"] = featureType }));
            Assert.IsTrue(added.Value<bool>("success"), added.ToString());
            Object feature;
            using (var serialized = new SerializedObject(renderer))
                feature = serialized.FindProperty("m_RendererFeatures").GetArrayElementAtIndex(0).objectReferenceValue;
            Assert.IsNotNull(feature);
            Assert.IsFalse(EditorUtility.IsDirty(renderer));
            Assert.IsFalse(EditorUtility.IsDirty(feature));
            byte[] bytes = File.ReadAllBytes(FullPath(rendererPath));
            if (initiallyDirty)
            {
                renderer.name += "_PendingOwnerEdit";
                feature.name += "_PendingFeatureEdit";
                EditorUtility.SetDirty(renderer);
                EditorUtility.SetDirty(feature);
            }
            string rendererName = renderer.name;
            string featureName = feature.name;
            int rendererDirtyCount = EditorUtility.GetDirtyCount(renderer);
            int featureDirtyCount = EditorUtility.GetDirtyCount(feature);
            var properties = rejected ? new JObject { ["missing_setting"] = true } : new JObject();
            var response = JObject.FromObject(
                ManageGraphics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "feature_configure",
                        ["index"] = 0,
                        ["properties"] = properties,
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsEmpty(response["data"]["changed"]);
            CollectionAssert.AreEqual(rejected ? new[] { "missing_setting" } : Array.Empty<string>(), response["data"]["failed"].Values<string>());
            Assert.AreEqual(rendererName, renderer.name);
            Assert.AreEqual(featureName, feature.name);
            Assert.AreEqual(initiallyDirty, EditorUtility.IsDirty(renderer));
            Assert.AreEqual(initiallyDirty, EditorUtility.IsDirty(feature));
            Assert.AreEqual(rendererDirtyCount, EditorUtility.GetDirtyCount(renderer));
            Assert.AreEqual(featureDirtyCount, EditorUtility.GetDirtyCount(feature));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(FullPath(rendererPath)), "A no-write request must not persist pending owner or subasset edits.");
        }

        [Test]
        public void SaveBoundaryFailureReportsRetainedFeatureIdentity()
        {
            var hook = typeof(ManageGraphics)
                .Assembly.GetType("MCPForUnity.Editor.Tools.Graphics.RendererFeatureOps")
                ?.GetField("BeforeFeatureSave", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(hook);
            JObject response;
            try
            {
                hook.SetValue(null, new Action(() => throw new InvalidOperationException("Owned save boundary fault.")));
                response = JObject.FromObject(
                    ManageGraphics.HandleCommand(
                        new JObject
                        {
                            ["action"] = "feature_add",
                            ["type"] = featureType,
                            ["properties"] = new JObject { ["m_Active"] = false },
                        }
                    )
                );
            }
            finally
            {
                hook.SetValue(null, null);
            }
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            var data = response["data"];
            Assert.AreEqual("partial_or_uncertain", data.Value<string>("state"));
            Assert.IsTrue(data.Value<bool>("feature_created"));
            Assert.IsTrue(data.Value<bool>("renderer_link_completed"));
            Assert.IsFalse(data.Value<bool>("save_completed"));
            Assert.AreEqual("save", data.Value<string>("failed_operation"));
            Assert.AreEqual(nameof(InvalidOperationException), data.Value<string>("exception_type"));
            CollectionAssert.AreEqual(new[] { "m_Active" }, data["prepared"].Values<string>());
            Assert.AreEqual(2, AssetDatabase.LoadAllAssetsAtPath(rendererPath).Length);
            using (var serialized = new SerializedObject(renderer))
            {
                var features = serialized.FindProperty("m_RendererFeatures");
                Assert.AreEqual(1, features.arraySize);
                var feature = features.GetArrayElementAtIndex(0).objectReferenceValue;
                Assert.IsNotNull(feature);
                Assert.AreEqual(data.Value<int>("instanceId"), feature.GetInstanceIDCompat());
            }
            AssetDatabase.SaveAssetIfDirty(renderer);
            AssetDatabase.ImportAsset(rendererPath, ImportAssetOptions.ForceUpdate);
            using var persisted = new SerializedObject(AssetDatabase.LoadAssetAtPath<ScriptableObject>(rendererPath));
            Assert.AreEqual(1, persisted.FindProperty("m_RendererFeatures").arraySize);
        }

        private void AssertCreationRejectedWithoutMutation(JObject request, params string[] failed)
        {
            byte[] bytes = File.ReadAllBytes(FullPath(rendererPath));
            string guid = AssetDatabase.AssetPathToGUID(rendererPath);
            Object[] identities = AssetDatabase.LoadAllAssetsAtPath(rendererPath);
            bool dirty = EditorUtility.IsDirty(renderer);
            request["action"] = "feature_add";
            request["type"] = featureType;
            var response = JObject.FromObject(ManageGraphics.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("rejected", response["data"].Value<string>("state"));
            Assert.IsFalse(response["data"].Value<bool>("feature_created"));
            CollectionAssert.AreEqual(failed, response["data"]["failed"].Values<string>());
            CollectionAssert.AreEqual(identities, AssetDatabase.LoadAllAssetsAtPath(rendererPath));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(FullPath(rendererPath)));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(rendererPath));
            Assert.AreEqual(dirty, EditorUtility.IsDirty(renderer));
            using (var serialized = new SerializedObject(renderer))
                Assert.AreEqual(0, serialized.FindProperty("m_RendererFeatures").arraySize);
            AssetDatabase.ImportAsset(rendererPath, ImportAssetOptions.ForceUpdate);
            Assert.AreEqual(1, AssetDatabase.LoadAllAssetsAtPath(rendererPath).Length);
            using var persisted = new SerializedObject(AssetDatabase.LoadAssetAtPath<ScriptableObject>(rendererPath));
            Assert.AreEqual(0, persisted.FindProperty("m_RendererFeatures").arraySize);
        }

        private string FullPath(string path)
        {
            Assert.IsTrue(path == assetRoot || path.StartsWith(assetRoot + "/", StringComparison.Ordinal));
            return Path.Combine(Application.dataPath, path.Substring(7).Replace('/', Path.DirectorySeparatorChar));
        }
    }
}
