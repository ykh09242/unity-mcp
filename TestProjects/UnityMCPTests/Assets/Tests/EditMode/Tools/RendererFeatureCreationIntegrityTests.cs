using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools.Graphics;
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
            AssetDatabase.SaveAssets();
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

        private string FullPath(string path)
        {
            Assert.IsTrue(path == assetRoot || path.StartsWith(assetRoot + "/", StringComparison.Ordinal));
            return Path.Combine(Application.dataPath, path.Substring(7).Replace('/', Path.DirectorySeparatorChar));
        }
    }
}
