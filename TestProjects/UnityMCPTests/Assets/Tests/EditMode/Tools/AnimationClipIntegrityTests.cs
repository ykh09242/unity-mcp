using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class AnimationClipCollisionAsset : ScriptableObject { public int marker = 42; }

    // This audit compiles these regressions; native animation/asset behavior is not executed here.
    public class AnimationClipIntegrityTests
    {
        private string assetRoot;
        private bool ownsAssetRoot;
        private List<GameObject> ownedObjects;

        [SetUp]
        public void SetUp()
        {
            assetRoot = "Assets/__McpAnimationClipIntegrity_" + Guid.NewGuid().ToString("N");
            ownsAssetRoot = false;
            ownedObjects = new List<GameObject>();
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                foreach (var gameObject in ownedObjects)
                    if (gameObject != null) UnityEngine.Object.DestroyImmediate(gameObject);
            }
            finally
            {
                if (ownsAssetRoot)
                {
                    Assert.IsTrue(assetRoot.StartsWith("Assets/__McpAnimationClipIntegrity_", StringComparison.Ordinal));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Delete only the exact owned unique folder.");
                    ownsAssetRoot = false;
                }
            }
        }

        private void EnsureOwnedRoot()
        {
            if (ownsAssetRoot) return;
            string guid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            ownsAssetRoot = !string.IsNullOrEmpty(guid);
            Assert.IsTrue(ownsAssetRoot, "Capture successful ownership before any later assertion.");
        }

        private static JObject Call(JObject parameters) => JObject.FromObject(ManageAnimation.HandleCommand(parameters));
        private JObject CreateRequest(bool preset = false) => new JObject
        {
            ["action"] = preset ? "clip_create_preset" : "clip_create",
            ["clip_path"] = assetRoot + "/Fixture.anim", ["preset"] = "bounce"
        };

        private AnimationClip CreateClip()
        {
            EnsureOwnedRoot();
            var result = Call(CreateRequest());
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(assetRoot + "/Fixture.anim");
            Assert.IsNotNull(clip);
            Assert.IsTrue(EditorUtility.IsPersistent(clip));
            AnimationUtility.SetEditorCurve(clip, EditorCurveBinding.FloatCurve("", typeof(Transform), "localPosition.x"),
                new AnimationCurve(new Keyframe(0, 42), new Keyframe(1, 43)));
            AnimationUtility.SetAnimationEvents(clip, new[] { new AnimationEvent { time = 0.2f, functionName = "Existing" } });
            AssetDatabase.SaveAssets();
            return clip;
        }

        private static JObject Snapshot(AnimationClip clip)
        {
            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            var curves = new JArray();
            foreach (var binding in AnimationUtility.GetCurveBindings(clip).OrderBy(b => b.path + "|" + b.propertyName))
            {
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                var keys = new JArray();
                foreach (var key in curve.keys)
                    keys.Add(new JArray(key.time, key.value, key.inTangent, key.outTangent, key.inWeight, key.outWeight));
                curves.Add(new JObject { ["path"] = binding.path, ["property"] = binding.propertyName, ["type"] = binding.type.FullName, ["keys"] = keys });
            }
            var events = new JArray();
            foreach (var e in AnimationUtility.GetAnimationEvents(clip))
                events.Add(new JObject { ["time"] = e.time, ["function"] = e.functionName, ["float"] = e.floatParameter, ["int"] = e.intParameter, ["string"] = e.stringParameter });
            return new JObject
            {
                ["frameRate"] = clip.frameRate, ["legacy"] = clip.legacy,
                ["loop"] = settings.loopTime, ["stop"] = settings.stopTime, ["curves"] = curves, ["events"] = events
            };
        }

        [TestCase("length")]
        [TestCase("frame_rate")]
        [TestCase("loop")]
        public void MalformedCreateSettingDoesNotCreateFolder(string setting)
        {
            var request = CreateRequest();
            request[setting] = "bad";
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageAnimation\\] Action 'clip_create' failed:"));
            var result = Call(request);
            ownsAssetRoot = AssetDatabase.IsValidFolder(assetRoot); // Retain cleanup ownership if this regression returns.
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsFalse(ownsAssetRoot);
        }

        [Test]
        public void UnknownPresetDoesNotCreateFolder()
        {
            var request = CreateRequest(true);
            request["preset"] = "unknown";
            var result = Call(request);
            ownsAssetRoot = AssetDatabase.IsValidFolder(assetRoot);
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsFalse(ownsAssetRoot);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExistingNonClipAssetIsPreserved(bool preset)
        {
            EnsureOwnedRoot();
            var original = ScriptableObject.CreateInstance<AnimationClipCollisionAsset>();
            try
            {
                string path = assetRoot + "/Fixture.anim";
                AssetDatabase.CreateAsset(original, path);
                AssetDatabase.SaveAssets();
                Assert.IsTrue(EditorUtility.IsPersistent(original), "The collision fixture must establish a real existing native asset.");
                string guid = AssetDatabase.AssetPathToGUID(path);
                var result = Call(CreateRequest(preset));
                Assert.IsFalse(result.Value<bool>("success"));
                Assert.AreSame(original, AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
                Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
                Assert.AreEqual(42, original.marker);
            }
            finally
            {
                if (original != null && !EditorUtility.IsPersistent(original)) UnityEngine.Object.DestroyImmediate(original);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExistingClipIsPreserved(bool preset)
        {
            var clip = CreateClip();
            var before = Snapshot(clip);
            string guid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(clip));
            var result = Call(CreateRequest(preset));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot(clip)));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(clip)));
        }

        [TestCase("clip_set_curve", "string")]
        [TestCase("clip_set_curve", "short-array")]
        [TestCase("clip_set_curve", "null")]
        [TestCase("clip_add_curve", "string")]
        [TestCase("clip_add_curve", "short-array")]
        [TestCase("clip_add_curve", "null")]
        public void MalformedMixedKeyframeRowsPreserveWholeClip(string action, string badShape)
        {
            var clip = CreateClip();
            var before = Snapshot(clip);
            JToken malformed = badShape == "string" ? new JValue("bad") : badShape == "short-array" ? (JToken)new JArray(0) : JValue.CreateNull();
            var result = Call(new JObject
            {
                ["action"] = action, ["clip_path"] = AssetDatabase.GetAssetPath(clip), ["property_path"] = "localPosition.x",
                ["keys"] = new JArray(new JArray(0, 1), malformed)
            });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot(clip)), "Preserve actual curves, tangents, events and settings.");
        }

        [Test]
        public void ValidScalarDefaultsAndShorthandExtrasRemainSupported()
        {
            var clip = CreateClip();
            var result = Call(new JObject
            {
                ["action"] = "clip_set_curve", ["clip_path"] = AssetDatabase.GetAssetPath(clip), ["property_path"] = "localPosition.x",
                ["keys"] = new JArray(new JArray(0, 1, 99), new JObject { ["time"] = 1, ["value"] = 2, ["in_tangent"] = "3" }, new JObject { ["time"] = 2 })
            });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var curve = AnimationUtility.GetEditorCurve(clip, EditorCurveBinding.FloatCurve("", typeof(Transform), "localPosition.x"));
            Assert.AreEqual(3, curve.length);
            Assert.AreEqual(1, curve.keys[0].value);
            Assert.AreEqual(3, curve.keys[1].inTangent);
            Assert.AreEqual(0, curve.keys[2].value);
        }

        [Test]
        public void LateInvalidVectorPreservesWholeClip()
        {
            var clip = CreateClip();
            var before = Snapshot(clip);
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageAnimation\\] Action 'clip_set_vector_curve' failed:"));
            var result = Call(new JObject
            {
                ["action"] = "clip_set_vector_curve", ["clip_path"] = AssetDatabase.GetAssetPath(clip), ["property"] = "localPosition",
                ["keys"] = new JArray(new JObject { ["time"] = 0, ["value"] = new JArray(1, 2, 3) }, new JObject { ["time"] = 1, ["value"] = new JArray(4, "bad", 6) })
            });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot(clip)));
        }

        [Test]
        public void LateInvalidEventPreservesWholeClip()
        {
            var clip = CreateClip();
            var before = Snapshot(clip);
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageAnimation\\] Action 'clip_add_event' failed:"));
            var result = Call(new JObject
            {
                ["action"] = "clip_add_event", ["clip_path"] = AssetDatabase.GetAssetPath(clip), ["function_name"] = "New",
                ["float_parameter"] = 1, ["int_parameter"] = "bad"
            });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot(clip)));
        }

        [TestCase("bounce")]
        [TestCase("rotate")]
        [TestCase("pulse")]
        [TestCase("fade")]
        [TestCase("shake")]
        [TestCase("hover")]
        [TestCase("spin")]
        [TestCase("sway")]
        [TestCase("bob")]
        [TestCase("wiggle")]
        [TestCase("blink")]
        [TestCase("slide_in")]
        [TestCase("elastic")]
        [TestCase("grow")]
        [TestCase("shrink")]
        public void ValidPresetCreatesPersistentNonLegacyCurves(string preset)
        {
            EnsureOwnedRoot();
            var request = CreateRequest(true);
            request["preset"] = preset.ToUpperInvariant();
            request["loop"] = false;
            var result = Call(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(assetRoot + "/Fixture.anim");
            Assert.IsNotNull(clip);
            Assert.IsTrue(EditorUtility.IsPersistent(clip));
            Assert.IsFalse(clip.legacy);
            Assert.IsFalse(AnimationUtility.GetAnimationClipSettings(clip).loopTime);
            Assert.Greater(AnimationUtility.GetCurveBindings(clip).Length, 0);
        }
    }
}
