using System;
using System.Collections;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageAudioContractTests
    {
        private static JObject Send(JObject request) => JObject.FromObject(ManageAudio.HandleCommand(request));

        private static JObject Request(string action, GameObject target) =>
            new JObject
            {
                ["action"] = action,
                ["target"] = target.GetInstanceIDCompat(),
                ["searchMethod"] = "by_id",
            };

        [TestCase("play")]
        [TestCase("stop")]
        public void EditModeRejectsBeforeChangingAssignedClip(string action)
        {
            Assert.IsFalse(EditorApplication.isPlaying);
            var go = new GameObject("AudioEditMode_" + Guid.NewGuid().ToString("N"));
            AudioClip clip = AudioClip.Create("OwnedAudioClip", 32, 1, 8000, false);
            try
            {
                var source = go.AddComponent<AudioSource>();
                source.playOnAwake = false;
                source.clip = clip;
                var result = Send(Request(action, go));
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                StringAssert.Contains("Play mode", result.Value<string>("error"));
                Assert.AreSame(clip, source.clip);
            }
            finally
            {
                Object.DestroyImmediate(go);
                Object.DestroyImmediate(clip);
            }
        }

        [TestCase("{\"action\":\"pause\",\"target\":\"Music\"}", "action")]
        [TestCase("{\"action\":\"play\",\"target\":false}", "target")]
        [TestCase("{\"action\":\"play\",\"target\":2147483648}", "range")]
        [TestCase("{\"action\":\"play\",\"target\":\"Music\",\"searchMethod\":\"typo\"}", "search_method")]
        [TestCase("{\"action\":\"play\",\"target\":\"Music\",\"clip\":false}", "clip")]
        [TestCase("{\"action\":\"play\",\"target\":\"Music\",\"clip\":\"\"}", "clip")]
        [TestCase("{\"action\":\"play\",\"target\":\"Music\",\"clip\":\"Assets/../bad.wav\"}", "segment")]
        [TestCase("{\"action\":\"stop\",\"target\":\"Music\",\"clip\":\"Assets/Audio/new.wav\"}", "only supported")]
        public void MalformedRequestIsRejectedAtItsPrecondition(string json, string diagnostic)
        {
            var result = Send(JObject.Parse(json));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains(diagnostic, result.Value<string>("error"));
        }

        [UnityTest]
        public IEnumerator PlayModeAssignedClipPlayAndStopIssueRequests()
        {
            yield return new EnterPlayMode();
            var go = new GameObject("AudioRuntime_" + Guid.NewGuid().ToString("N"));
            AudioClip clip = AudioClip.Create("OwnedRuntimeClip", 32, 1, 8000, false);
            try
            {
                var source = go.AddComponent<AudioSource>();
                source.playOnAwake = false;
                source.mute = true;
                source.clip = clip;
                foreach (string action in new[] { "play", "stop" })
                {
                    var request = Request(action, go);
                    request["clip"] = JValue.CreateNull();
                    var result = Send(request);
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                    StringAssert.Contains("request issued", result.Value<string>("message"));
                    Assert.AreEqual(go.GetInstanceIDCompat(), result["data"].Value<int>("instanceID"));
                    Assert.AreSame(clip, source.clip);
                }
            }
            finally
            {
                Object.DestroyImmediate(go);
                Object.DestroyImmediate(clip);
            }
        }

        [UnityTest]
        public IEnumerator PlayModeMissingClipAndInactivePlayPreserveAssignedReference()
        {
            yield return new EnterPlayMode();
            var go = new GameObject("AudioValidation_" + Guid.NewGuid().ToString("N"));
            AudioClip clip = AudioClip.Create("OwnedValidationClip", 32, 1, 8000, false);
            try
            {
                var source = go.AddComponent<AudioSource>();
                source.playOnAwake = false;
                source.mute = true;
                source.clip = clip;
                var request = Request("play", go);
                request["clip"] = "Assets/__missing_audio_" + Guid.NewGuid().ToString("N") + ".wav";
                var result = Send(request);
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                StringAssert.Contains("AudioClip was not found", result.Value<string>("error"));
                Assert.AreSame(clip, source.clip);

                source.enabled = false;
                result = Send(request);
                StringAssert.Contains("enabled", result.Value<string>("error"));
                Assert.AreSame(clip, source.clip);
                source.enabled = true;
                go.SetActive(false);
                result = Send(request);
                StringAssert.Contains("active", result.Value<string>("error"));
                Assert.AreSame(clip, source.clip);

                source.clip = null;
                source.enabled = false;
                result = Send(Request("stop", go));
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.IsNull(source.clip);
            }
            finally
            {
                Object.DestroyImmediate(go);
                Object.DestroyImmediate(clip);
            }
        }

        [UnityTest]
        public IEnumerator PlayModeExplicitNumericNameAndPathSelectTheIntendedSource()
        {
            yield return new EnterPlayMode();
            var unrelated = new GameObject("AudioUnrelated_" + Guid.NewGuid().ToString("N"));
            var named = new GameObject(unrelated.GetInstanceIDCompat().ToString());
            var root = new GameObject("AudioRoot_" + Guid.NewGuid().ToString("N"));
            try
            {
                unrelated.AddComponent<AudioSource>().playOnAwake = false;
                named.AddComponent<AudioSource>().playOnAwake = false;
                var request = Request("stop", named);
                request["target"] = named.name;
                request["searchMethod"] = "by_name";
                var result = Send(request);
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(named.GetInstanceIDCompat(), result["data"].Value<int>("instanceID"));

                named.transform.SetParent(root.transform);
                request["target"] = root.name + "/" + named.name;
                request.Remove("searchMethod");
                request["search_method"] = "by_path";
                result = Send(request);
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(named.GetInstanceIDCompat(), result["data"].Value<int>("instanceID"));
                Object.DestroyImmediate(named.GetComponent<AudioSource>());
                result = Send(request);
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                StringAssert.Contains("no AudioSource", result.Value<string>("error"));
                Assert.IsNull(named.GetComponent<AudioSource>());
            }
            finally
            {
                Object.DestroyImmediate(unrelated);
                Object.DestroyImmediate(root);
                if (named != null)
                    Object.DestroyImmediate(named);
            }
        }

        [UnityTearDown]
        public IEnumerator ExitPlayModeAfterTest()
        {
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }
    }
}
