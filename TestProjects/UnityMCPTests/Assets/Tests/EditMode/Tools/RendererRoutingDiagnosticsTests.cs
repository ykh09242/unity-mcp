using System;
using System.IO;
using System.Reflection;
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
    public class RendererRoutingDiagnosticsTests
    {
        private string root;
        private ScriptableObject first;
        private ScriptableObject second;
        private RenderPipelineAsset pipeline;
        private RenderPipelineAsset originalDefault;
        private RenderPipelineAsset originalQuality;
        private GameObject cameraObject;
        private ScriptableObject feature;
        private bool captured;

        [SetUp]
        public void SetUp()
        {
            feature = null;
            var rendererType = Type.GetType("UnityEngine.Rendering.Universal.UniversalRendererData, Unity.RenderPipelines.Universal.Runtime");
            var pipelineType = Type.GetType("UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset, Unity.RenderPipelines.Universal.Runtime");
            if (rendererType == null || pipelineType == null)
                Assert.Ignore("URP is optional; renderer routing requires an installed URP package.");
            root = "Assets/__McpRendererDiagnostics_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(root));
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
            first = ScriptableObject.CreateInstance(rendererType);
            second = ScriptableObject.CreateInstance(rendererType);
            AssetDatabase.CreateAsset(first, root + "/First.asset");
            AssetDatabase.CreateAsset(second, root + "/Second.asset");
            pipeline = (RenderPipelineAsset)ScriptableObject.CreateInstance(pipelineType);
            using (var serialized = new SerializedObject(pipeline))
            {
                var renderers = serialized.FindProperty("m_RendererDataList");
                renderers.arraySize = 2;
                renderers.GetArrayElementAtIndex(0).objectReferenceValue = first;
                renderers.GetArrayElementAtIndex(1).objectReferenceValue = second;
                serialized.FindProperty("m_DefaultRendererIndex").intValue = 1;
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            originalDefault = GraphicsSettings.defaultRenderPipeline;
            originalQuality = QualitySettings.renderPipeline;
            captured = true;
            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;
            AssetDatabase.SaveAssetIfDirty(first);
            AssetDatabase.SaveAssetIfDirty(second);
        }

        [TearDown]
        public void TearDown()
        {
            if (cameraObject != null)
                Object.DestroyImmediate(cameraObject);
            if (captured)
            {
                QualitySettings.renderPipeline = originalQuality;
                GraphicsSettings.defaultRenderPipeline = originalDefault;
                captured = false;
            }
            (feature as IDisposable)?.Dispose();
            if (pipeline != null)
                Object.DestroyImmediate(pipeline);
            if (!string.IsNullOrEmpty(root) && AssetDatabase.IsValidFolder(root))
                Assert.IsTrue(AssetDatabase.DeleteAsset(root));
        }

        private static string FullPath(string assetPath) => Path.GetFullPath(Path.Combine(Application.dataPath, "..", assetPath));

        private static JObject Run(string action, JObject extra = null)
        {
            var parameters = extra ?? new JObject();
            parameters["action"] = action;
            return JObject.FromObject(ManageGraphics.HandleCommand(parameters));
        }

        [Test]
        public void DefaultRendererUsesNonzeroPipelineDefault()
        {
            var response = Run("feature_list");
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(root + "/Second.asset", response.SelectToken("data.renderer.path")?.Value<string>());
            Assert.AreEqual(1, response.SelectToken("data.renderer.index")?.Value<int>());
        }

        [TestCase("renderer_index")]
        [TestCase("rendererIndex")]
        public void RendererSelectionDoesNotConsumeFeatureIndex(string key)
        {
            var response = Run("feature_list", new JObject { [key] = 0, ["index"] = 7 });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(root + "/First.asset", response.SelectToken("data.renderer.path")?.Value<string>());
        }

        [Test]
        public void ExplicitRendererAssetSelectsRequestedAsset()
        {
            var response = Run("feature_list", new JObject { ["target"] = root + "/First.asset" });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(root + "/First.asset", response.SelectToken("data.renderer.path")?.Value<string>());
        }

        [Test]
        public void DiagnosticsObserveDisabledFeatureAndExistingPassWithoutRecreatingIt()
        {
            var type = Type.GetType("UnityEngine.Rendering.Universal.FullScreenPassRendererFeature, Unity.RenderPipelines.Universal.Runtime");
            Assert.IsNotNull(type, "The URP17 regression fixture includes FullScreenPassRendererFeature.");
            feature = ScriptableObject.CreateInstance(type);
            feature.name = "__McpObservedFeature";
            AssetDatabase.AddObjectToAsset(feature, second);
            type.GetMethod("Create").Invoke(feature, null);
            type.GetMethod("SetActive").Invoke(feature, new object[] { false });
            using (var serialized = new SerializedObject(second))
            {
                var features = serialized.FindProperty("m_RendererFeatures");
                features.arraySize = 1;
                features.GetArrayElementAtIndex(0).objectReferenceValue = feature;
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            var passField = type.GetField("m_FullScreenPass", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(passField);
            var passBefore = passField.GetValue(feature);
            Assert.IsNotNull(passBefore);
            string before = EditorJsonUtility.ToJson(feature);
            var response = Run("pipeline_get_diagnostics");
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(response.SelectToken("data.features[0].isActive").Value<bool>());
            Assert.AreEqual("implementation_observed", response.SelectToken("data.features[0].compatibility.status").Value<string>());
            Assert.AreEqual("unknown", response.SelectToken("data.features[0].compatibility.execution").Value<string>());
            Assert.AreSame(passBefore, passField.GetValue(feature));
            Assert.AreEqual(before, EditorJsonUtility.ToJson(feature));
            Assert.IsNotEmpty(response.SelectToken("data.urpPackageVersion").Value<string>());
        }

        [Test]
        public void ExplicitRendererAssetCanBeInspectedWhenUrpIsInactive()
        {
            GraphicsSettings.defaultRenderPipeline = null;
            QualitySettings.renderPipeline = null;
            var response = Run("feature_list", new JObject { ["target"] = root + "/First.asset" });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(root + "/First.asset", response.SelectToken("data.renderer.path")?.Value<string>());
        }

        [TestCase(-2)]
        [TestCase(99)]
        public void InvalidRendererIndexRejectsWithoutFallback(int index)
        {
            var response = Run("feature_list", new JObject { ["renderer_index"] = index });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("renderer_index", response.Value<string>("error"));
        }

        [Test]
        public void InvalidExplicitTargetDoesNotFallBackToDefault()
        {
            var response = Run("feature_list", new JObject { ["target"] = root + "/Missing.asset" });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("target", response.Value<string>("error"));
        }

        [Test]
        public void ConflictingTargetAndRendererIndexReject()
        {
            var response = Run("feature_list", new JObject { ["target"] = root + "/First.asset", ["renderer_index"] = 1 });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
        }

        [TestCase(0, 0, false)]
        [TestCase(-1, 1, false)]
        [TestCase(99, 1, true)]
        public void DiagnosticsReportCameraOverrideAndFallbackWithoutCreatingComponents(int configured, int effective, bool fallback)
        {
            cameraObject = new GameObject("__McpDiagnosticCamera", typeof(Camera));
            var additionalType = Type.GetType("UnityEngine.Rendering.Universal.UniversalAdditionalCameraData, Unity.RenderPipelines.Universal.Runtime");
            var additional = cameraObject.AddComponent(additionalType);
            using (var serialized = new SerializedObject(additional))
            {
                serialized.FindProperty("m_RendererIndex").intValue = configured;
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            var before = EditorJsonUtility.ToJson(additional);
            var response = Run("pipeline_get_diagnostics", new JObject { ["target"] = cameraObject.name });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(configured, response.SelectToken("data.camera.configuredRendererIndex")?.Value<int>());
            Assert.AreEqual(effective, response.SelectToken("data.renderer.index")?.Value<int>());
            Assert.AreEqual(fallback, response.SelectToken("data.camera.fellBackToDefault")?.Value<bool>());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(additional));
        }

        [Test]
        public void DiagnosticsPreserveAssetsDirtyFlagsAndBareCameraComponents()
        {
            cameraObject = new GameObject("__McpBareDiagnosticCamera", typeof(Camera));
            int componentCount = cameraObject.GetComponents<Component>().Length;
            string pipelineBefore = EditorJsonUtility.ToJson(pipeline);
            byte[] firstBefore = File.ReadAllBytes(FullPath(root + "/First.asset"));
            byte[] secondBefore = File.ReadAllBytes(FullPath(root + "/Second.asset"));
            bool firstDirty = EditorUtility.IsDirty(first);
            bool secondDirty = EditorUtility.IsDirty(second);
            var response = Run("pipeline_get_diagnostics", new JObject { ["target"] = cameraObject.name });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(componentCount, cameraObject.GetComponents<Component>().Length);
            Assert.AreEqual(pipelineBefore, EditorJsonUtility.ToJson(pipeline));
            CollectionAssert.AreEqual(firstBefore, File.ReadAllBytes(FullPath(root + "/First.asset")));
            CollectionAssert.AreEqual(secondBefore, File.ReadAllBytes(FullPath(root + "/Second.asset")));
            Assert.AreEqual(firstDirty, EditorUtility.IsDirty(first));
            Assert.AreEqual(secondDirty, EditorUtility.IsDirty(second));
            Assert.AreEqual("unknown", response.SelectToken("data.execution")?.Value<string>());
        }
    }

    public class GraphicsDiagnosticsObservationTests
    {
        public class BasePass
        {
            public virtual void RecordRenderGraph(int graph, string data) { }
        }

        public class InheritedPass : BasePass { }

        public class GraphPass : BasePass
        {
            public override void RecordRenderGraph(int graph, string data) { }
        }

        public class OverloadPass : BasePass
        {
            public void RecordRenderGraph(string unrelated) { }
        }

        [TestCase(typeof(InheritedPass), false)]
        [TestCase(typeof(GraphPass), true)]
        [TestCase(typeof(OverloadPass), false)]
        public void RecordRenderGraphOverrideRequiresCorrectVirtualSignature(Type pass, bool expected)
        {
            var type = typeof(ManageGraphics).Assembly.GetType("MCPForUnity.Editor.Tools.Graphics.RenderGraphDiagnostics");
            Assert.IsNotNull(type);
            var method = type.GetMethod("HasRecordRenderGraphOverride", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            Assert.AreEqual(expected, method.Invoke(null, new object[] { pass, typeof(BasePass) }));
        }

        [TestCase(false, "enabled", true)]
        [TestCase(true, "compatibility", false)]
        [TestCase(null, "unknown", null)]
        public void GraphModeDistinguishesObservedCompatibilityFromUnavailableSettings(bool? compatibility, string expectedMode, bool? expectedEnabled)
        {
            var type = typeof(ManageGraphics).Assembly.GetType("MCPForUnity.Editor.Tools.Graphics.RenderGraphDiagnostics");
            Assert.IsNotNull(type);
            var method = type.GetMethod("DescribeGraphMode", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            var result = JObject.FromObject(method.Invoke(null, new object[] { true, compatibility, "test observation" }));
            Assert.AreEqual(expectedMode, result.Value<string>("mode"));
            Assert.AreEqual(expectedEnabled, result.Value<bool?>("enabled"));
            Assert.AreEqual(compatibility, result.Value<bool?>("compatibilityMode"));
            Assert.AreEqual("unknown", result.Value<string>("execution"));
        }

        [Test]
        public void UnregisteredAssemblyDoesNotFabricateUrpPackageVersion()
        {
            var type = typeof(ManageGraphics).Assembly.GetType("MCPForUnity.Editor.Tools.Graphics.RenderGraphDiagnostics");
            Assert.IsNotNull(type);
            var method = type.GetMethod("ObservePackageVersion", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            Assert.IsNull(method.Invoke(null, new object[] { null }));
            Assert.IsNull(method.Invoke(null, new object[] { typeof(object) }));
        }

        [Test]
        public void AbsentGraphApiRemainsUnknown()
        {
            var type = typeof(ManageGraphics).Assembly.GetType("MCPForUnity.Editor.Tools.Graphics.RenderGraphDiagnostics");
            Assert.IsNotNull(type);
            var method = type.GetMethod("HasRecordRenderGraphOverride", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNull(method.Invoke(null, new object[] { typeof(object), typeof(object) }));
        }

        [Test]
        public void DiagnosticsWithoutActiveUrpSucceedWithoutGuessingVersionOrGraphMode()
        {
            var originalDefault = GraphicsSettings.defaultRenderPipeline;
            var originalQuality = QualitySettings.renderPipeline;
            try
            {
                GraphicsSettings.defaultRenderPipeline = null;
                QualitySettings.renderPipeline = null;
                var response = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "pipeline_get_diagnostics" }));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual("not_applicable", response.SelectToken("data.renderGraph.mode")?.Value<string>());
                Assert.AreEqual(JTokenType.Null, response.SelectToken("data.renderGraph.enabled")?.Type);
                Assert.AreEqual("unknown", response.SelectToken("data.execution")?.Value<string>());
            }
            finally
            {
                QualitySettings.renderPipeline = originalQuality;
                GraphicsSettings.defaultRenderPipeline = originalDefault;
            }
        }
    }
}
