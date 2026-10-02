using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Vfx;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class LineRendererIntegrityTests
    {
        private GameObject root;
        private LineRenderer line;
        private Material material;
        private UnityEngine.Object[] previousSelection;
        private UnityEngine.Object previousActiveObject;
        private readonly Vector3[] originalPositions = { new Vector3(1, 2, 3), new Vector3(4, 5, 6) };

        [SetUp]
        public void SetUp()
        {
            previousSelection = Selection.objects;
            previousActiveObject = Selection.activeObject;
            root = new GameObject("__McpLineIntegrity_" + Guid.NewGuid().ToString("N"));
            line = root.AddComponent<LineRenderer>();
            line.sharedMaterial = null;
            line.positionCount = originalPositions.Length;
            line.SetPositions(originalPositions);
            line.startWidth = 2;
            line.endWidth = 4;
            line.loop = false;
        }

        [TearDown]
        public void TearDown()
        {
            if (root != null) UnityEngine.Object.DestroyImmediate(root);
            if (material != null) UnityEngine.Object.DestroyImmediate(material);
            Selection.objects = previousSelection;
            Selection.activeObject = previousActiveObject;
        }

        [TestCase("line_set_positions", "{}")]
        [TestCase("line_set_positions", "{positions:null}")]
        [TestCase("line_set_positions", "{positions:'bad'}")]
        [TestCase("line_set_positions", "{positions:[[7,8,9],[1,2]]}")]
        [TestCase("line_set_positions", "{positions:[[1,'bad',3]]}")]
        [TestCase("line_set_positions", "{positions:[null]}")]
        [TestCase("line_add_position", "{position:[1,2]}")]
        [TestCase("line_add_position", "{position:{x:1,y:2,z:'bad'}}")]
        [TestCase("line_set_position", "{index:0,position:'bad'}")]
        [TestCase("line_set_position", "{index:9,position:[1,2,3]}")]
        [TestCase("line_set_position", "{position:[1,2,3]}")]
        [TestCase("line_set_position", "{index:'bad',position:[1,2,3]}")]
        [TestCase("line_set_properties", "{positions:123,loop:true}")]
        [TestCase("line_set_properties", "{positions:[[7,8,9],[1]],materialPath:'Assets/__MissingLineMaterial.mat',loop:true}")]
        [TestCase("line_set_properties", "{positionCount:-1,loop:true}")]
        [TestCase("line_set_properties", "{positionCount:'bad'}")]
        [TestCase("line_set_properties", "{positions:[[9,9,9]],loop:'bad'}")]
        [TestCase("line_set_properties", "{positions:[[9,9,9]],loop:true,sortingOrder:'bad'}")]
        [TestCase("line_set_properties", "{positionCount:0,receiveShadows:false,renderingLayerMask:-1}")]
        [TestCase("line_set_width", "{width:3,endWidth:'bad'}")]
        [TestCase("line_set_width", "{startWidth:0,endWidth:-2,widthMultiplier:'bad'}")]
        [TestCase("line_set_width", "{width:'NaN'}")]
        [TestCase("line_set_width", "{width:'Infinity'}")]
        [TestCase("line_set_width", "{width:1e100}")]
        [TestCase("line_set_width", "{width:3,startWidth:'NaN'}")]
        [TestCase("line_set_width", "{width:3,endWidth:'-Infinity'}")]
        [TestCase("line_set_width", "{width:3,widthMultiplier:'Infinity'}")]
        public void InvalidWritePreservesPositionsAndDoesNotAssignMaterial(string action, string properties)
        {
            int dirtyCount = EditorUtility.GetDirtyCount(line);
            var response = Call(action, JObject.Parse(properties));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(line.sharedMaterial);
            CollectionAssert.AreEqual(originalPositions, Positions(line));
            Assert.AreEqual(2f, line.startWidth);
            Assert.AreEqual(4f, line.endWidth);
            Assert.IsFalse(line.loop);
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(line));
        }

        [Test]
        public void InvalidLateVectorPreservesExistingMaterial()
        {
            AssignUsableMaterial();
            var response = Call("line_set_positions", JObject.Parse("{positions:[[9,9,9],[1]]}"));
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreSame(material, line.sharedMaterial);
            CollectionAssert.AreEqual(originalPositions, Positions(line));
        }

        [TestCase("line_set_positions")]
        [TestCase("line_set_properties")]
        public void EmptyPositionsClearsAndPreservesExistingMaterial(string action)
        {
            AssignUsableMaterial();
            var response = Call(action, JObject.Parse("{positions:[],positionCount:7,loop:false}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0, line.positionCount);
            Assert.AreSame(material, line.sharedMaterial);
        }

        [Test]
        public void ValidVectorFormatsPreserveExtrasNumericStringsZeroAndNegativeCoordinates()
        {
            AssignUsableMaterial();
            Assert.IsTrue(Call("line_set_positions", JObject.Parse("{positions:[[0,'-2',3,99],{x:4,y:0,z:-6}]}" )).Value<bool>("success"));
            CollectionAssert.AreEqual(new[] { new Vector3(0, -2, 3), new Vector3(4, 0, -6) }, Positions(line));
        }

        [TestCase("line_add_position", "{}")]
        [TestCase("line_add_position", "{position:null}")]
        [TestCase("line_set_position", "{index:'0'}")]
        [TestCase("line_set_position", "{index:0,position:null}")]
        public void OmittedOrNullSinglePositionRetainsZeroDefault(string action, string properties)
        {
            AssignUsableMaterial();
            Assert.IsTrue(Call(action, JObject.Parse(properties)).Value<bool>("success"));
            int index = action == "line_add_position" ? 2 : 0;
            Assert.AreEqual(action == "line_add_position" ? 3 : 2, line.positionCount);
            Assert.AreEqual(Vector3.zero, line.GetPosition(index));
        }

        [TestCase("{loop:false,useWorldSpace:false}")]
        [TestCase("{positions:null,positionCount:7}")]
        public void OmittedAndNullBulkPositionsRetainExistingPositions(string properties)
        {
            AssignUsableMaterial();
            Assert.IsTrue(Call("line_set_properties", JObject.Parse(properties)).Value<bool>("success"));
            CollectionAssert.AreEqual(originalPositions, Positions(line));
        }

        [Test]
        public void ValidWidthOverridesRetainZeroNegativeAndNumericStringValues()
        {
            AssignUsableMaterial();
            Assert.IsTrue(Call("line_set_width", JObject.Parse("{width:3,start_width:0,endWidth:'-2'}")).Value<bool>("success"));
            Assert.AreEqual(0f, line.startWidth);
            Assert.AreEqual(-2f, line.endWidth);
            Assert.IsTrue(Call("line_set_width", JObject.Parse("{width_multiplier:0,widthCurve:null}")).Value<bool>("success"));
            Assert.AreEqual(0f, line.widthMultiplier);
            Assert.IsNotNull(line.widthCurve);
            Assert.AreSame(material, line.sharedMaterial);
        }

        [Test]
        public void InvalidComponentIndexPreservesTheExistingLine()
        {
            Assert.IsFalse(Call("line_clear", JObject.Parse("{component_index:9}")).Value<bool>("success"));
            CollectionAssert.AreEqual(originalPositions, Positions(line));
            Assert.IsNull(line.sharedMaterial);
        }

        private JObject Call(string action, JObject properties)
        {
            return JObject.FromObject(ManageVFX.HandleCommand(new JObject
            {
                ["action"] = action,
                ["target"] = root.GetInstanceID(),
                ["searchMethod"] = "by_id",
                ["properties"] = properties
            }));
        }

        private void AssignUsableMaterial()
        {
            Shader shader = RenderPipelineUtility.ResolveShader("Standard");
            if (shader == null || !shader.isSupported) Assert.Ignore("An active-pipeline-compatible shader is required.");
            material = new Material(shader);
            if (RenderPipelineUtility.IsMaterialInvalidForActivePipeline(material, out string reason))
                Assert.Ignore("A usable material is required: " + reason);
            line.sharedMaterial = material;
        }

        private static Vector3[] Positions(LineRenderer renderer)
        {
            var positions = new Vector3[renderer.positionCount];
            renderer.GetPositions(positions);
            return positions;
        }
    }
}
