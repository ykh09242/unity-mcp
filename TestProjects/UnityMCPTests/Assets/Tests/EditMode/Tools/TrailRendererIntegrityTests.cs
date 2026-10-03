using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Vfx;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnityTests.EditMode.Tools
{
    public class TrailRendererIntegrityTests
    {
        private GameObject _object;
        private TrailRenderer _trail;
        private Material _material;

        [SetUp]
        public void SetUp()
        {
            _object = new GameObject("TrailRendererIntegrity_" + System.Guid.NewGuid().ToString("N"));
            _trail = _object.AddComponent<TrailRenderer>();
            _trail.time = 7;
            _trail.startWidth = 1;
            _trail.endWidth = 2;
            _trail.sharedMaterial = null;
        }

        [TearDown]
        public void TearDown()
        {
            if (_object != null) Object.DestroyImmediate(_object);
            if (_material != null) Object.DestroyImmediate(_material);
        }

        private JObject Send(string action, JObject properties)
        {
            return JObject.FromObject(ManageVFX.HandleCommand(new JObject
            {
                ["action"] = action, ["target"] = _object.GetInstanceIDCompat(),
                ["search_method"] = "by_id", ["properties"] = properties
            }));
        }

        private void AssignUsableMaterial()
        {
            var shader = RenderPipelineUtility.ResolveShader("Sprites/Default");
            if (shader == null || !shader.isSupported)
                Assert.Ignore("No supported trail shader available for the valid-material control.");
            _material = new Material(shader);
            if (RenderPipelineUtility.IsMaterialInvalidForActivePipeline(_material, out _))
                Assert.Ignore("Resolved shader is incompatible with the active pipeline.");
            _trail.sharedMaterial = _material;
        }

        [TestCase("'bad'")]
        [TestCase("null")]
        [TestCase("{}")]
        [TestCase("NaN")]
        [TestCase("Infinity")]
        [TestCase("1e100")]
        public void InvalidTimeDoesNotAssignMaterialOrChangeDuration(string json)
        {
            JObject response = Send("trail_set_time", new JObject { ["time"] = JToken.Parse(json) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(_trail.sharedMaterial);
            Assert.AreEqual(7, _trail.time);
        }

        [TestCase("{emitting:'bad'}")]
        [TestCase("{end_width:'bad'}")]
        [TestCase("{rendering_layer_mask:-1}")]
        [TestCase("{num_cap_vertices:'bad'}")]
        [TestCase("{sorting_order:'bad'}")]
        [TestCase("{min_vertex_distance:NaN}")]
        public void LatePropertyFailurePreservesEarlierTrailValuesAndMaterial(string json)
        {
            var properties = JObject.Parse(json);
            properties["time"] = 2;
            properties["width"] = 3;
            JObject response = Send("trail_set_properties", properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(7, _trail.time);
            Assert.AreEqual(1, _trail.startWidth);
            Assert.AreEqual(2, _trail.endWidth);
            Assert.IsNull(_trail.sharedMaterial);
        }

        [TestCase("{width:3,end_width:'bad'}")]
        [TestCase("{width:3,width_multiplier:Infinity}")]
        public void LateWidthFailurePreservesWidthsAndMaterial(string json)
        {
            JObject response = Send("trail_set_width", JObject.Parse(json));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(1, _trail.startWidth);
            Assert.AreEqual(2, _trail.endWidth);
            Assert.IsNull(_trail.sharedMaterial);
        }

        [TestCase("{}", 5f)]
        [TestCase("{time:0}", 0f)]
        [TestCase("{time:'2.5'}", 2.5f)]
        [TestCase("{time:true}", 1f)]
        public void CompatibleTimeInputsKeepDefaultZeroAndConversions(string json, float expected)
        {
            AssignUsableMaterial();
            JObject response = Send("trail_set_time", JObject.Parse(json));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(expected, _trail.time);
            Assert.AreSame(_material, _trail.sharedMaterial);
        }

        [Test]
        public void FalseAndZeroBulkFieldsRemainAccepted()
        {
            AssignUsableMaterial();
            JObject response = Send("trail_set_properties", JObject.Parse(
                "{time:0,width:0,emitting:false,autodestruct:false,sorting_order:0,rendering_layer_mask:0}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0, _trail.time);
            Assert.IsFalse(_trail.emitting);
            Assert.IsFalse(_trail.autodestruct);
            Assert.AreEqual(0u, _trail.renderingLayerMask);
        }

        [Test]
        public void CommonPreparationDoesNotMutateAndCapturesParsedValues()
        {
            var changes = new List<string>();
            var properties = JObject.Parse("{receiveShadows:false,sortingOrder:3,renderingLayerMask:0}");
            bool shadows = _trail.receiveShadows;
            int order = _trail.sortingOrder;
            var apply = RendererHelpers.PrepareCommonRendererProperties(_trail, properties, changes);
            Assert.AreEqual(shadows, _trail.receiveShadows);
            Assert.AreEqual(order, _trail.sortingOrder);
            Assert.IsNull(_trail.sharedMaterial);
            CollectionAssert.AreEqual(new[] { "receiveShadows", "sortingOrder", "renderingLayerMask" }, changes);
            properties["sortingOrder"] = "bad";
            apply();
            Assert.IsFalse(_trail.receiveShadows);
            Assert.AreEqual(3, _trail.sortingOrder);
            Assert.AreEqual(0u, _trail.renderingLayerMask);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExistingLineAndParticleHelperInputsRemainCompatible(bool particle)
        {
            var other = new GameObject("CommonRendererControl");
            try
            {
                Renderer renderer;
                if (particle)
                {
                    other.AddComponent<ParticleSystem>();
                    renderer = other.GetComponent<ParticleSystemRenderer>();
                }
                else renderer = other.AddComponent<LineRenderer>();
                var changes = new List<string>();
                RendererHelpers.ApplyCommonRendererProperties(renderer, JObject.Parse(
                    "{shadowCastingMode:'bad',receiveShadows:'false',sortingOrder:'0',renderingLayerMask:0}"), changes);
                Assert.IsFalse(renderer.receiveShadows);
                Assert.AreEqual(0, renderer.sortingOrder);
                CollectionAssert.AreEqual(new[] { "receiveShadows", "sortingOrder", "renderingLayerMask" }, changes);
                Assert.Catch(() => RendererHelpers.ApplyCommonRendererProperties(renderer, JObject.Parse(
                    "{receiveShadows:true,renderingLayerMask:-1}"), new List<string>()));
                Assert.IsFalse(renderer.receiveShadows);
            }
            finally { Object.DestroyImmediate(other); }
        }
    }
}
