using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Vfx;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public class VfxGraphIntegrityTests
    {
        private GameObject _object;
        private Component _effect;

        [SetUp]
        public void SetUp()
        {
            _object = new GameObject("VfxGraphIntegrity_" + Guid.NewGuid().ToString("N"));
            Type type = Type.GetType("UnityEngine.VFX.VisualEffect, UnityEngine.VFXModule");
            if (type != null) _effect = _object.AddComponent(type);
        }

        [TearDown]
        public void TearDown()
        {
            if (_object != null) UnityEngine.Object.DestroyImmediate(_object);
        }

        private JObject Send(string action, JObject properties)
        {
            return JObject.FromObject(ManageVFX.HandleCommand(new JObject
            {
                ["action"] = action, ["target"] = _object.GetInstanceID(),
                ["search_method"] = "by_id", ["properties"] = properties
            }));
        }

        private void RequireEnabledGraph()
        {
            JObject response = Send("vfx_integrity_unknown_action", new JObject());
            if (response["message"]?.ToString().Contains("not installed") == true)
                Assert.Ignore("Production UNITY_VFX_GRAPH/package branch is unavailable.");
            Assert.IsNotNull(_effect, "UnityEngine.VFX.VisualEffect is unavailable.");
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("Unknown vfx action", response["message"]?.ToString());
        }

        private T GetProperty<T>(string name)
            => (T)_effect.GetType().GetProperty(name).GetValue(_effect);

        [Test]
        public void PublicAvailabilityHasExplicitEnabledOrUnavailableResponse()
        {
            JObject response = Send("vfx_integrity_unknown_action", new JObject());
            if (response["message"]?.ToString().Contains("not installed") == true)
                Assert.IsFalse(response.Value<bool>("success"));
            else
            {
                Assert.IsNotNull(_effect);
                Assert.IsFalse(response.Value<bool>("success"));
                StringAssert.Contains("Unknown vfx action", response["message"]?.ToString());
            }
        }

        [TestCase("float")]
        [TestCase("int")]
        [TestCase("bool")]
        [TestCase("vector2")]
        [TestCase("vector3")]
        [TestCase("vector4")]
        [TestCase("color")]
        [TestCase("gradient")]
        [TestCase("curve")]
        [TestCase("texture")]
        [TestCase("mesh")]
        public void MissingExposedParameterRejectsBeforeNativeOverride(string kind)
        {
            RequireEnabledGraph();
            var properties = new JObject
            {
                ["parameter"] = "Missing_" + Guid.NewGuid().ToString("N"),
                ["value"] = kind == "bool" ? (JToken)new JValue(false)
                    : kind.StartsWith("vector") || kind == "color" ? new JArray(1, 2, 3, 4)
                    : new JValue(0),
                ["texture_path"] = "Assets/AbsentTexture.png",
                ["mesh_path"] = "Assets/AbsentMesh.asset"
            };
            int dirtyCount = UnityEditor.EditorUtility.GetDirtyCount(_effect);
            JObject response = Send("vfx_set_" + kind, properties);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("Parameter", response["message"]?.ToString());
            Assert.AreEqual(dirtyCount, UnityEditor.EditorUtility.GetDirtyCount(_effect));
        }

        [TestCase("NaN")]
        [TestCase("Infinity")]
        [TestCase("-Infinity")]
        [TestCase("1e100")]
        public void NonfinitePlaybackRatePreservesNativeState(string token)
        {
            RequireEnabledGraph();
            float original = GetProperty<float>("playRate");
            JObject response = Send("vfx_set_playback_speed", new JObject { ["play_rate"] = JToken.Parse(token) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(original, GetProperty<float>("playRate"));
        }

        [TestCase("{}", 1f)]
        [TestCase("{play_rate:0}", 0f)]
        [TestCase("{play_rate:'2.5'}", 2.5f)]
        [TestCase("{play_rate:true}", 1f)]
        public void PlaybackDefaultZeroAndExistingConversionsRemainAccepted(string json, float expected)
        {
            RequireEnabledGraph();
            JObject response = Send("vfx_set_playback_speed", JObject.Parse(json));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(expected, GetProperty<float>("playRate"));
        }

        [TestCase("{}", true)]
        [TestCase("{seed:0,reset_seed_on_play:false}", false)]
        public void SeedDefaultZeroAndFalseRemainAccepted(string json, bool reset)
        {
            RequireEnabledGraph();
            JObject response = Send("vfx_set_seed", JObject.Parse(json));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(0u, GetProperty<uint>("startSeed"));
            Assert.AreEqual(reset, GetProperty<bool>("resetSeedOnPlay"));
        }

        [Test]
        public void PauseTogglesOwnedComponentInBothDirections()
        {
            RequireEnabledGraph();
            bool original = GetProperty<bool>("pause");
            JObject first = Send("vfx_pause", new JObject());
            Assert.IsTrue(first.Value<bool>("success"));
            Assert.AreEqual(!original, GetProperty<bool>("pause"));
            JObject second = Send("vfx_pause", new JObject());
            Assert.IsTrue(second.Value<bool>("success"));
            Assert.AreEqual(original, GetProperty<bool>("pause"));
        }

        [Test]
        public void EventWithoutNameFailsWithoutSending()
        {
            RequireEnabledGraph();
            JObject response = Send("vfx_send_event", new JObject());
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("Event name required", response["message"]?.ToString());
        }

        [TestCase(2)]
        [TestCase(3)]
        [TestCase(4)]
        public void ExistingDimensionParsersAcceptArraysAndObjects(int dimensions)
        {
            var array = new JArray(2, 3, 4, 5);
            var properties = JObject.Parse("{x:2,y:3,z:4,w:5}");
            if (dimensions == 2)
            {
                Assert.AreEqual(new Vector2(2, 3), VectorParsing.ParseVector2(array).Value);
                Assert.AreEqual(new Vector2(2, 3), VectorParsing.ParseVector2(properties).Value);
                Assert.AreEqual(new Vector2(2, 3), VectorParsing.ParseVector2(new JArray(2, 3)).Value);
                Assert.AreEqual(new Vector2(2, 3), VectorParsing.ParseVector2(JObject.Parse("{x:2,y:3}")).Value);
            }
            else if (dimensions == 3)
            {
                Assert.AreEqual(new Vector3(2, 3, 4), VectorParsing.ParseVector3(array).Value);
                Assert.AreEqual(new Vector3(2, 3, 4), VectorParsing.ParseVector3(properties).Value);
                Assert.AreEqual(new Vector3(2, 3, 4), VectorParsing.ParseVector3(new JArray(2, 3, 4)).Value);
                Assert.AreEqual(new Vector3(2, 3, 4), VectorParsing.ParseVector3(JObject.Parse("{x:2,y:3,z:4}")).Value);
            }
            else
            {
                Assert.AreEqual(new Vector4(2, 3, 4, 5), VectorParsing.ParseVector4(array).Value);
                Assert.AreEqual(new Vector4(2, 3, 4, 5), VectorParsing.ParseVector4(properties).Value);
            }
        }
    }
}
