using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Vfx;
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
    public class VfxPrefabPersistenceTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private string assetRoot;
        private string assetRootGuid;
        private GameObject instance;
        private GameObject prefab;
        private Material originalMaterial;
        private Material replacementMaterial;
        private Object[] previousSelection;
        private Object previousActiveObject;

        [OneTimeSetUp]
        public void PrepareRunner() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunner() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            assetRootGuid = null;
            instance = null;
            previousSelection = Selection.objects;
            previousActiveObject = Selection.activeObject;
            string suffix = Guid.NewGuid().ToString("N");
            sceneFixture.Create("McpVfxPrefab_", suffix);
            assetRoot = "Assets/__McpVfxPrefab_" + suffix;
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, assetRoot.Substring(7))));
            assetRootGuid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring(7));
            Assert.IsNotEmpty(assetRootGuid);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                ClearInstanceUndo();
                sceneFixture.Close();
                if (!string.IsNullOrEmpty(assetRootGuid))
                {
                    StringAssert.StartsWith("Assets/__McpVfxPrefab_", assetRoot);
                    Assert.AreEqual(assetRootGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
                }
            }
            finally
            {
                Selection.objects = previousSelection;
                Selection.activeObject = previousActiveObject;
            }
        }

        [TestCase("line_set_positions", "{positions:[[9,8,7],[6,5,4],[3,2,1]]}", false)]
        [TestCase("line_add_position", "{position:[9,8,7]}", false)]
        [TestCase("line_set_position", "{index:0,position:[9,8,7]}", false)]
        [TestCase("line_clear", "{}", false)]
        [TestCase("line_set_width", "{startWidth:3,endWidth:4}", false)]
        [TestCase("line_set_color", "{startColor:[0,1,0,1],endColor:[0,0,1,0.5]}", false)]
        [TestCase("line_set_properties", "{positions:[[9,8,7]],loop:true,useWorldSpace:false,sortingOrder:13,materialPath:'REPLACEMENT'}", false)]
        [TestCase("line_set_material", "{materialPath:'REPLACEMENT'}", false)]
        [TestCase("line_set_material", "{}", false)]
        [TestCase("line_set_width", "{width:3}", true)]
        [TestCase("line_create_line", "{start:[9,8,7],end:[6,5,4],width:3}", false)]
        [TestCase("line_create_circle", "{segments:4,width:3}", false)]
        [TestCase("line_create_arc", "{segments:4,width:3}", false)]
        [TestCase("line_create_bezier", "{segments:4,start:[9,8,7],end:[6,5,4],width:3}", false)]
        [TestCase("trail_set_time", "{time:9}", false)]
        [TestCase("trail_set_width", "{startWidth:3,endWidth:4}", false)]
        [TestCase("trail_set_color", "{startColor:[0,1,0,1],endColor:[0,0,1,0.5]}", false)]
        [TestCase("trail_set_properties", "{time:9,startWidth:3,minVertexDistance:0.75,emitting:false,sortingOrder:13,materialPath:'REPLACEMENT'}", false)]
        [TestCase("trail_set_material", "{materialPath:'REPLACEMENT'}", false)]
        [TestCase("trail_set_material", "{}", false)]
        [TestCase("trail_set_time", "{time:9}", true)]
        public void PublicWritesPersistPrefabOverridesThroughUndoRedoAndSceneReopen(string action, string json, bool missingMaterial)
        {
            CreateInstance(missingMaterial);
            bool isLine = action.StartsWith("line_", StringComparison.Ordinal);
            Renderer renderer = FindRenderer(instance, isLine);
            JObject before = Snapshot(renderer);
            string beforeMaterial = MaterialIdentity(renderer.sharedMaterial);
            var properties = JObject.Parse(json);
            if (properties.Value<string>("materialPath") == "REPLACEMENT")
                properties["materialPath"] = AssetDatabase.GetAssetPath(replacementMaterial);

            Undo.IncrementCurrentGroup();
            JObject response = JObject.FromObject(
                ManageVFX.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = renderer.gameObject.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["properties"] = properties,
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            JObject after = Snapshot(renderer);
            Assert.IsFalse(JToken.DeepEquals(before, after), "The public action must change the saved prefab instance.");
            string afterMaterial = MaterialIdentity(renderer.sharedMaterial);
            Assert.IsNotNull(afterMaterial);
            if (properties["materialPath"] != null)
                Assert.AreEqual(MaterialIdentity(replacementMaterial), afterMaterial);
            else if (!missingMaterial && !action.EndsWith("_set_material", StringComparison.Ordinal))
                Assert.AreEqual(MaterialIdentity(originalMaterial), afterMaterial);

            // Persist the direct tool write before Undo/Redo can record prefab changes itself.
            Assert.IsTrue(EditorSceneManager.SaveScene(instance.scene));
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            Undo.PerformUndo();
            AssertSnapshot(before, Snapshot(renderer));
            Assert.AreEqual(beforeMaterial, MaterialIdentity(renderer.sharedMaterial));
            Undo.PerformRedo();
            AssertSnapshot(after, Snapshot(renderer));
            Assert.AreEqual(afterMaterial, MaterialIdentity(renderer.sharedMaterial));

            ClearInstanceUndo();
            // Reopen the pre-Undo save; discard the later in-memory Undo/Redo dirtiness.
            sceneFixture.ClearDirtiness();
            Scene reopened = sceneFixture.ReopenSavedScene();
            instance = reopened.GetRootGameObjects().Single();
            renderer = FindRenderer(instance, isLine);
            Assert.IsTrue(PrefabUtility.IsPartOfPrefabInstance(renderer));
            AssertSnapshot(after, Snapshot(renderer));
            Assert.AreEqual(afterMaterial, MaterialIdentity(renderer.sharedMaterial));
            AssertSnapshot(before, Snapshot(FindRenderer(prefab, isLine)));
        }

        private void CreateInstance(bool missingMaterial)
        {
            Shader shader = RenderPipelineUtility.ResolveShader("Standard");
            if (shader == null || !shader.isSupported)
                Assert.Ignore("An active-pipeline-compatible shader is required.");
            originalMaterial = new Material(shader);
            if (RenderPipelineUtility.IsMaterialInvalidForActivePipeline(originalMaterial, out string reason))
            {
                Object.DestroyImmediate(originalMaterial);
                Assert.Ignore("A usable material is required: " + reason);
            }
            AssetDatabase.CreateAsset(originalMaterial, assetRoot + "/Original.mat");
            replacementMaterial = new Material(shader);
            AssetDatabase.CreateAsset(replacementMaterial, assetRoot + "/Replacement.mat");
            var source = new GameObject("VfxPrefabInstance");
            try
            {
                var lineObject = new GameObject("Line");
                lineObject.transform.SetParent(source.transform);
                var line = lineObject.AddComponent<LineRenderer>();
                line.positionCount = 2;
                line.SetPositions(new[] { Vector3.zero, Vector3.one });
                line.startWidth = 1;
                line.endWidth = 2;
                line.sharedMaterial = missingMaterial ? null : originalMaterial;
                var trailObject = new GameObject("Trail");
                trailObject.transform.SetParent(source.transform);
                var trail = trailObject.AddComponent<TrailRenderer>();
                trail.time = 5;
                trail.startWidth = 1;
                trail.endWidth = 2;
                trail.sharedMaterial = missingMaterial ? null : originalMaterial;
                prefab = PrefabUtility.SaveAsPrefabAsset(source, assetRoot + "/Vfx.prefab");
                Assert.IsNotNull(prefab);
            }
            finally
            {
                Object.DestroyImmediate(source);
            }
            instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab, SceneManager.GetActiveScene());
            Assert.IsTrue(EditorSceneManager.SaveScene(instance.scene, assetRoot + "/" + instance.scene.name + ".unity"));
        }

        private static Renderer FindRenderer(GameObject target, bool isLine) =>
            isLine ? (Renderer)target.GetComponentInChildren<LineRenderer>() : target.GetComponentInChildren<TrailRenderer>();

        private static JObject Snapshot(Renderer renderer)
        {
            var state = new JObject { ["material"] = MaterialIdentity(renderer.sharedMaterial), ["sortingOrder"] = renderer.sortingOrder };
            if (renderer is LineRenderer line)
            {
                var points = new Vector3[line.positionCount];
                line.GetPositions(points);
                state["positions"] = new JArray(points.Select(p => new JArray(p.x, p.y, p.z)));
                state["loop"] = line.loop;
                state["worldSpace"] = line.useWorldSpace;
                state["startWidth"] = line.startWidth;
                state["endWidth"] = line.endWidth;
                state["startColor"] = ColorValues(line.startColor);
                state["endColor"] = ColorValues(line.endColor);
            }
            else if (renderer is TrailRenderer trail)
            {
                state["time"] = trail.time;
                state["minVertexDistance"] = trail.minVertexDistance;
                state["emitting"] = trail.emitting;
                state["startWidth"] = trail.startWidth;
                state["endWidth"] = trail.endWidth;
                state["startColor"] = ColorValues(trail.startColor);
                state["endColor"] = ColorValues(trail.endColor);
            }
            return state;
        }

        private static JArray ColorValues(Color color) => new JArray(color.r, color.g, color.b, color.a);

        private static string MaterialIdentity(Material material)
        {
            if (material == null)
                return null;
            // Asset identity survives native wrapper recreation during import, Undo and scene reload.
            Assert.IsTrue(
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(material, out string guid, out long localId),
                "Material must have a saved asset identity."
            );
            return guid + ":" + localId;
        }

        private static void AssertSnapshot(JToken expected, JToken actual)
        {
            if (expected is JValue value && value.Type == JTokenType.Float)
                Assert.That(actual.Value<double>(), Is.EqualTo(value.Value<double>()).Within(0.00001), expected.Path);
            else if (expected is JContainer container)
            {
                Assert.AreEqual(container.Count, ((JContainer)actual).Count, expected.Path);
                if (expected is JObject obj)
                    foreach (JProperty property in obj.Properties())
                        AssertSnapshot(property.Value, actual[property.Name]);
                else
                    for (int i = 0; i < container.Count; i++)
                        AssertSnapshot(expected[i], actual[i]);
            }
            else
                Assert.IsTrue(JToken.DeepEquals(expected, actual), expected.Path + ": " + actual);
        }

        private void ClearInstanceUndo()
        {
            if (instance == null)
                return;
            foreach (Component component in instance.GetComponentsInChildren<Component>())
                Undo.ClearUndo(component);
            Undo.ClearUndo(instance);
        }
    }
}
