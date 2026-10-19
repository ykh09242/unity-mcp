using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Vfx;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class VfxExposedPropertiesTests
    {
        private readonly PrefabTestSceneFixture scene = new PrefabTestSceneFixture();
        private string folder;
        private string folderGuid;
        private GameObject target;
        private Component effect;
        private Object graphAsset;
        private Texture2D firstTexture;
        private Texture2D nextTexture;
        private RenderPipelineAsset pipeline;
        private RenderPipelineAsset previousGraphics;
        private RenderPipelineAsset previousQuality;
        private PropertyInfo graphicsProperty;
        private bool graphicsAssigned;
        private bool qualityAssigned;
        private const BindingFlags PublicInstance = BindingFlags.Public | BindingFlags.Instance;

        private static Type FindType(string name) => AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType(name)).FirstOrDefault(t => t != null);

        private static object Invoke(object instance, string method, params object[] args)
        {
            MethodInfo selected = instance.GetType().GetMethods(PublicInstance).Single(m => m.Name == method && m.GetParameters().Length == args.Length);
            return selected.Invoke(instance, args);
        }

        private static object Extension(string type, string method, object instance) =>
            FindType("UnityEditor.VFX." + type).GetMethod(method, BindingFlags.Public | BindingFlags.Static).Invoke(null, new[] { instance });

        private JObject Send(string action, JObject properties = null) =>
            JObject.FromObject(
                ManageVFX.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = target.GetInstanceIDCompat(),
                        ["search_method"] = "by_id",
                        ["properties"] = properties,
                    }
                )
            );

        private JObject Row(string name, JObject options = null)
        {
            var properties = options ?? new JObject();
            properties["parameter"] = name;
            JObject result = Send("vfx_get_exposed_properties", properties);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            return ((JArray)result["data"]["properties"]).OfType<JObject>().Single();
        }

        [SetUp]
        public void Setup()
        {
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssetPath("Packages/com.unity.visualeffectgraph");
            if (package == null)
                Assert.Ignore("Optional VFX Graph package is absent; positive native fixture requires VFX17.0.4 and URP17.0.4.");
            Assert.AreEqual("17.0.4", package.version, "The internal test graph authoring adapter is verified specifically against bundled VFX17.0.4.");
            Type pipelineType = FindType("UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset");
            Assert.IsNotNull(pipelineType, "The owned native fixture requires the matching optional URP package prepared by root.");
            scene.PrepareRunnerBootstrap();
            scene.Create("VfxExposed_", Guid.NewGuid().ToString("N"));
            folder = "Assets/VfxExposed_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            Assert.IsNotEmpty(folderGuid);
            // Create a real owned pipeline through the package's public factory. Both active
            // pipeline slots are restored by exact identity; shipping defaults are never saved.
            MethodInfo createPipeline = pipelineType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Single(m => m.Name == "Create" && m.GetParameters().Length == 1);
            pipeline = (RenderPipelineAsset)createPipeline.Invoke(null, new object[] { null });
            var serialized = new SerializedObject(pipeline);
            Object renderer = serialized.FindProperty("m_RendererDataList").GetArrayElementAtIndex(0).objectReferenceValue;
            AssetDatabase.CreateAsset(renderer, folder + "/Renderer.asset");
            AssetDatabase.CreateAsset(pipeline, folder + "/Pipeline.asset");
            graphicsProperty = typeof(GraphicsSettings).GetProperty("defaultRenderPipeline") ?? typeof(GraphicsSettings).GetProperty("renderPipelineAsset");
            previousGraphics = (RenderPipelineAsset)graphicsProperty.GetValue(null);
            previousQuality = QualitySettings.renderPipeline;
            graphicsProperty.SetValue(null, pipeline);
            graphicsAssigned = true;
            QualitySettings.renderPipeline = pipeline;
            qualityAssigned = true;
            Assert.AreSame(pipeline, GraphicsSettings.currentRenderPipeline);
            firstTexture = new Texture2D(2, 2);
            nextTexture = new Texture2D(2, 2);
            AssetDatabase.CreateAsset(firstTexture, folder + "/First.asset");
            AssetDatabase.CreateAsset(nextTexture, folder + "/Next.asset");
            string path = folder + "/Owned.vfx";
            // A real graph containing only exposed CPU expressions exercises the runtime
            // schema without unrelated GPU particle shader-template I/O. The package compiler
            // explicitly registers every exposed parameter through its implicit CPU context.
            Type assetFactory = FindType("UnityEditor.VisualEffectAssetEditorUtility");
            Assert.IsNotNull(assetFactory, "Bundled VFX17.0.4 factory type UnityEditor.VisualEffectAssetEditorUtility must be loaded.");
            MethodInfo createGraph = assetFactory.GetMethod("CreateNewAsset", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(createGraph);
            graphAsset = (Object)createGraph.Invoke(null, new object[] { path });
            Assert.IsNotNull(graphAsset, "The package's real graph factory must create/import the owned asset.");
            object resource = Extension("VisualEffectObjectExtensions", "GetResource", graphAsset);
            object graph = Extension("VisualEffectResourceExtensions", "GetOrCreateGraph", resource);
            AddParameter(graph, "OwnedFloat", typeof(float), 1.25f, true);
            AddParameter(graph, "OwnedTexture", typeof(Texture2D), firstTexture, true);
            AddParameter(graph, "OwnedCurve", typeof(AnimationCurve), AnimationCurve.Linear(0, 1, 1, 2), true);
            var gradient = new Gradient();
            gradient.SetKeys(
                new[] { new GradientColorKey(Color.red, 0), new GradientColorKey(Color.blue, 1) },
                new[] { new GradientAlphaKey(1, 0), new GradientAlphaKey(1, 1) }
            );
            AddParameter(graph, "OwnedGradient", typeof(Gradient), gradient, true);
            AddParameter(graph, "HiddenFloat", typeof(float), 9f, false);
            Extension("VisualEffectResourceExtensions", "WriteAssetWithSubAssets", resource);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            graphAsset = AssetDatabase.LoadMainAssetAtPath(path);
            target = new GameObject("OwnedVfx");
            Type effectType = FindType("UnityEngine.VFX.VisualEffect");
            effect = target.AddComponent(effectType);
            effectType.GetProperty("visualEffectAsset").SetValue(effect, graphAsset);
            foreach (
                var pair in new[]
                {
                    ("OwnedFloat", "HasFloat"),
                    ("OwnedTexture", "HasTexture"),
                    ("OwnedCurve", "HasAnimationCurve"),
                    ("OwnedGradient", "HasGradient"),
                }
            )
                Assert.IsTrue(
                    (bool)effectType.GetMethod(pair.Item2, new[] { typeof(string) }).Invoke(effect, new object[] { pair.Item1 }),
                    "The real imported graph must expose " + pair.Item1
                );
        }

        private static void AddParameter(object graph, string name, Type type, object value, bool exposed)
        {
            Type parameterType = FindType("UnityEditor.VFX.VFXParameter");
            var parameter = ScriptableObject.CreateInstance(parameterType);
            Invoke(parameter, "Init", type);
            Invoke(parameter, "SetSettingValue", "m_ExposedName", name);
            Invoke(parameter, "SetSettingValue", "m_Exposed", exposed);
            parameterType.GetProperty("value").SetValue(parameter, value);
            Invoke(graph, "AddChild", parameter, -1, true);
        }

        [TearDown]
        public void Cleanup()
        {
            bool graphicsOwned = !graphicsAssigned || ReferenceEquals(graphicsProperty.GetValue(null), pipeline);
            bool qualityOwned = !qualityAssigned || ReferenceEquals(QualitySettings.renderPipeline, pipeline);
            if (qualityAssigned && qualityOwned)
                QualitySettings.renderPipeline = previousQuality;
            if (graphicsAssigned && graphicsOwned)
                graphicsProperty.SetValue(null, previousGraphics);
            graphicsAssigned = qualityAssigned = false;
            scene.Close();
            scene.RestoreRunnerBootstrap();
            if (folderGuid != null)
            {
                Assert.AreEqual(folder, AssetDatabase.GUIDToAssetPath(folderGuid));
                Assert.IsTrue(AssetDatabase.DeleteAsset(folder));
            }
            folder = folderGuid = null;
            Assert.IsTrue(graphicsOwned && qualityOwned, "An external pipeline settings writer was preserved; fixture does not overwrite external settings.");
        }

        [Test]
        public void ListsRealSchemaAndCompleteValuesWithoutDirtyingNativeObjects()
        {
            // Given
            int dirty = EditorUtility.GetDirtyCount(effect);
            string guid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(graphAsset));
            // When
            JObject result = Send("vfx_get_exposed_properties");
            // Then
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var rows = ((JArray)result["data"]["properties"]).OfType<JObject>().ToArray();
            foreach (string name in new[] { "OwnedFloat", "OwnedTexture", "OwnedCurve", "OwnedGradient" })
            {
                var row = rows.Single(r => r.Value<string>("name") == name);
                Assert.AreEqual("available", row.Value<string>("value_status"), row.ToString());
                Assert.AreEqual("unavailable", row.Value<string>("override_status"));
                Assert.IsTrue(row.Value<bool>("getter_supported"));
                Assert.IsNotEmpty((JArray)row["setter_actions"]);
            }
            Assert.AreEqual(guid, result["data"]["asset"].Value<string>("guid"));
            Assert.IsNotEmpty(result["data"].Value<string>("asset_revision"));
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(effect));
        }

        [TestCase("float")]
        [TestCase("texture")]
        [TestCase("curve")]
        [TestCase("gradient")]
        public void ExistingSetterReadbackAndUndoPreserveGraphIdentity(string kind)
        {
            // Given
            string name = "Owned" + char.ToUpperInvariant(kind[0]) + kind.Substring(1);
            JToken before = Row(name)["value"].DeepClone();
            string guid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(graphAsset));
            var properties = new JObject { ["parameter"] = name };
            if (kind == "float")
                properties["value"] = 3.75;
            if (kind == "texture")
                properties["texture_path"] = AssetDatabase.GetAssetPath(nextTexture);
            if (kind == "curve")
                properties["curve"] = JObject.Parse("{keys:[{time:0,value:4},{time:1,value:6}]}");
            if (kind == "gradient")
                properties["gradient"] = JObject.Parse(
                    "{colorKeys:[{time:0,color:[0,1,0,1]},{time:1,color:[1,1,0,1]}],alphaKeys:[{time:0,alpha:1},{time:1,alpha:0.5}]}"
                );
            // When
            JObject changed = Send("vfx_set_" + kind, properties);
            // Then
            Assert.IsTrue(changed.Value<bool>("success"), changed.ToString());
            JObject after = Row(name);
            Assert.AreEqual("available", after.Value<string>("value_status"));
            Assert.IsFalse(JToken.DeepEquals(before, after["value"]));
            if (kind == "float")
                Assert.AreEqual(3.75f, after.Value<float>("value"));
            if (kind == "texture")
                Assert.AreEqual(AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(nextTexture)), after["value"].Value<string>("guid"));
            if (kind == "curve")
                Assert.AreEqual(6f, after["value"]["keys"][1].Value<float>("value"));
            if (kind == "gradient")
                Assert.AreEqual(0.5f, after["value"]["alpha_keys"][1].Value<float>("alpha"));
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.IsTrue(JToken.DeepEquals(before, Row(name)["value"]), "Undo must restore the native value.");
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(graphAsset)));
        }

        [Test]
        public void ExactNonexposedNameIsExplicitAndKeyBudgetsNeverReturnPartialKeys()
        {
            // Given
            var filter = new JObject { ["parameter"] = "HiddenFloat" };
            // When
            JObject missing = Send("vfx_get_exposed_properties", filter);
            // Then
            Assert.IsTrue(missing.Value<bool>("success"), missing.ToString());
            Assert.AreEqual("not_exposed", missing["data"].Value<string>("filter_status"));
            Assert.IsEmpty((JArray)missing["data"]["properties"]);
            var curve = Row("OwnedCurve", new JObject { ["max_curve_keys"] = 0 });
            Assert.AreEqual("key_limit", curve.Value<string>("value_status"));
            Assert.AreEqual(JTokenType.Null, curve["value"].Type);
            Assert.Greater(curve.Value<int>("key_count"), 0);
            var gradient = Row("OwnedGradient", new JObject { ["max_gradient_keys"] = 0 });
            Assert.AreEqual("key_limit", gradient.Value<string>("value_status"));
            Assert.AreEqual(JTokenType.Null, gradient["value"].Type);
        }

        [Test]
        public void StablePagesCoverEveryExposedNameAndTinyMetadataBudgetRefuses()
        {
            // Given
            JObject all = Send("vfx_get_exposed_properties");
            Assert.IsTrue(all.Value<bool>("success"), all.ToString());
            var expected = ((JArray)all["data"]["properties"]).Select(p => p.Value<string>("name")).ToArray();
            var actual = new List<string>();
            // When
            for (int page = 1; ; page++)
            {
                JObject result = Send("vfx_get_exposed_properties", new JObject { ["page_size"] = 1, ["page_number"] = page });
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                actual.AddRange(((JArray)result["data"]["properties"]).Select(p => p.Value<string>("name")));
                if (!result["data"].Value<bool>("has_more"))
                    break;
                Assert.Less(page, 100);
            }
            // Then
            CollectionAssert.AreEqual(expected, actual);
            Assert.AreEqual(actual.Count, actual.Distinct().Count());
            var limited = Send("vfx_get_exposed_properties", new JObject { ["max_characters"] = 1024 });
            Assert.IsFalse(limited.Value<bool>("success"), limited.ToString());
        }
    }
}
