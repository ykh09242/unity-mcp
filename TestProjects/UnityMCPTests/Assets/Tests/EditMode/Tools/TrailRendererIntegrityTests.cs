using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Vfx;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    public class TrailRendererIntegrityTests
    {
        private readonly PrefabTestSceneFixture _sceneFixture = new PrefabTestSceneFixture();
        private GameObject _object;
        private TrailRenderer _trail;
        private Material _material;

        [OneTimeSetUp]
        public void OneTimeSetUp() => _sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => _sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            _material = null;
            _sceneFixture.Create("McpTrailIntegrity_", System.Guid.NewGuid().ToString("N"));
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
            if (_object != null)
                Object.DestroyImmediate(_object);
            if (_material != null)
                Object.DestroyImmediate(_material);
            _sceneFixture.Close();
        }

        private JObject Send(string action, JObject properties)
        {
            return JObject.FromObject(
                ManageVFX.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = _object.GetInstanceIDCompat(),
                        ["search_method"] = "by_id",
                        ["properties"] = properties,
                    }
                )
            );
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
        [TestCase("true")]
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
        [TestCase("{time:null}", 5f)]
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
            JObject response = Send(
                "trail_set_properties",
                JObject.Parse("{time:0,width:0,emitting:false,autodestruct:false,sorting_order:0,rendering_layer_mask:0}")
            );
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
                else
                    renderer = other.AddComponent<LineRenderer>();
                var changes = new List<string>();
                RendererHelpers.ApplyCommonRendererProperties(
                    renderer,
                    JObject.Parse("{receiveShadows:'false',sortingOrder:'0',renderingLayerMask:0}"),
                    changes
                );
                Assert.IsFalse(renderer.receiveShadows);
                Assert.AreEqual(0, renderer.sortingOrder);
                CollectionAssert.AreEqual(new[] { "receiveShadows", "sortingOrder", "renderingLayerMask" }, changes);
                Assert.Catch(() =>
                    RendererHelpers.ApplyCommonRendererProperties(renderer, JObject.Parse("{receiveShadows:true,renderingLayerMask:-1}"), new List<string>())
                );
                Assert.IsFalse(renderer.receiveShadows);
            }
            finally
            {
                Object.DestroyImmediate(other);
            }
        }

        [TestCase("{color:[1,'bad',0,1]}", false)]
        [TestCase("{startColor:[1,0,0,1],endColor:[0,'bad',0,1]}", false)]
        [TestCase("{startColor:[1,0,0,1],endColor:[0,'bad',0,1]}", true)]
        [TestCase("{color:[1,0,0,1],gradient:{colorKeys:[{color:[0,'bad',0,1],time:0}]}}", false)]
        public void InvalidColorPreservesColorsMaterialAndDirtyCount(string json, bool existingMaterial)
        {
            if (existingMaterial)
                AssignUsableMaterial();
            _sceneFixture.ClearDirtiness();
            Color start = _trail.startColor;
            Color end = _trail.endColor;
            GradientColorKey[] colors = _trail.colorGradient.colorKeys;
            GradientAlphaKey[] alphas = _trail.colorGradient.alphaKeys;
            int dirty = EditorUtility.GetDirtyCount(_trail);
            JObject response = Send("trail_set_color", JObject.Parse(json));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(start, _trail.startColor);
            Assert.AreEqual(end, _trail.endColor);
            CollectionAssert.AreEqual(colors, _trail.colorGradient.colorKeys);
            CollectionAssert.AreEqual(alphas, _trail.colorGradient.alphaKeys);
            Assert.AreSame(_material, _trail.sharedMaterial);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(_trail));
            Assert.IsFalse(SceneManager.GetActiveScene().isDirty);
        }

        [TestCase("{position:[1,'bad',3]}")]
        [TestCase("{position:'bad'}")]
        public void InvalidEmitPreservesPositionsMaterialAndDirtyCount(string json)
        {
            _trail.AddPosition(new Vector3(1, 2, 3));
            _trail.AddPosition(new Vector3(4, 5, 6));
            _sceneFixture.ClearDirtiness();
            int count = _trail.positionCount;
            var positions = new Vector3[count];
            _trail.GetPositions(positions);
            int dirty = EditorUtility.GetDirtyCount(_trail);
            JObject response = Send("trail_emit", JObject.Parse(json));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(_trail.sharedMaterial);
            Assert.AreEqual(count, _trail.positionCount);
            var after = new Vector3[count];
            _trail.GetPositions(after);
            CollectionAssert.AreEqual(positions, after);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(_trail));
            Assert.IsFalse(SceneManager.GetActiveScene().isDirty);
        }

        [Test]
        public void ValidColorAndEmitKeepFadeAndVectorConversions()
        {
            AssignUsableMaterial();
            JObject response = Send("trail_set_color", JObject.Parse("{color:[1,0,0,0.5]}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual((Color)(Color32)new Color(1, 0, 0, 0.5f), _trail.startColor);
            Assert.AreEqual(new Color(1, 0, 0, 0), _trail.endColor);
            Assert.IsTrue(Send("trail_set_color", JObject.Parse("{color:[1,0,0,1],endColor:[0,1,0,1]}")).Value<bool>("success"));
            Assert.AreEqual(Color.green, _trail.endColor);
            int count = _trail.positionCount;
            response = Send("trail_emit", JObject.Parse("{position:[0,'-2',3]}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(count + 1, _trail.positionCount);
            Assert.AreEqual(new Vector3(0, -2, 3), _trail.GetPosition(count));
            Assert.AreSame(_material, _trail.sharedMaterial);
            response = Send("trail_set_color", JObject.Parse("{color:[0,1,0,1],gradient:{startColor:[0,0,1,1],endColor:[1,0,0,0]}}"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(Color.blue, _trail.startColor);
            Assert.AreEqual(new Color(1, 0, 0, 0), _trail.endColor);
            Assert.IsTrue(Send("trail_set_color", JObject.Parse("{color:null}")).Value<bool>("success"));
            Assert.AreEqual(Color.white, _trail.startColor);
            Assert.AreEqual(new Color(1, 1, 1, 0), _trail.endColor);
        }
    }
}
