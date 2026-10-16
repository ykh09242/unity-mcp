using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class UICaptureLifecycleTests
    {
        private readonly List<Texture2D> allocated = new();
        private bool ownsState;

        private sealed class CaptureFixture : IDisposable
        {
            internal readonly GameObject Target = new("CaptureOwner_" + Guid.NewGuid().ToString("N"), typeof(UIDocument));
            internal readonly GameObject Other = new("CaptureOther_" + Guid.NewGuid().ToString("N"), typeof(UIDocument));
            internal readonly string Folder = "Temp/UICaptureOwnership_" + Guid.NewGuid().ToString("N");
            internal string FullFolder => Path.Combine(Path.GetDirectoryName(Application.dataPath), Folder);

            internal JObject Request() =>
                new JObject
                {
                    ["action"] = "render_ui",
                    ["target"] = Target.name,
                    ["component_type"] = "ui_document",
                    ["output_folder"] = Folder,
                    ["file_name"] = "owner.png",
                    ["include_image"] = true,
                    ["max_resolution"] = 16,
                };

            public void Dispose()
            {
                Invoke("CleanupPendingCapture");
                Object.DestroyImmediate(Target);
                Object.DestroyImmediate(Other);
                if (Directory.Exists(FullFolder))
                    Directory.Delete(FullFolder, true);
            }
        }

        [SetUp]
        public void SetUp()
        {
            ownsState = false;
            if (Read<bool>("s_pendingCaptureStarted") || Read<bool>("s_pendingCaptureDone") || Read<Texture2D>("s_pendingCaptureTex") != null)
                Assert.Ignore("An unowned UI capture is pending.");
            ownsState = true;
        }

        [TearDown]
        public void TearDown()
        {
            if (ownsState)
                Invoke("CleanupPendingCapture");
            foreach (var texture in allocated)
                if (texture != null)
                    Object.DestroyImmediate(texture);
            allocated.Clear();
        }

        [TestCase(PlayModeStateChange.ExitingPlayMode)]
        [TestCase(PlayModeStateChange.EnteredEditMode)]
        [TestCase(PlayModeStateChange.ExitingEditMode)]
        public void PlayTransitionReleasesUnconsumedResult(PlayModeStateChange state)
        {
            int generation = (int)Invoke("BeginPendingCapture");
            var texture = Allocate();
            Invoke("CompletePendingCapture", generation, texture);
            Assert.AreSame(texture, Read<Texture2D>("s_pendingCaptureTex"));
            Assert.IsTrue(Read<bool>("s_pendingCaptureDone"));
            Invoke("OnPlayModeStateChanged", state);
            Assert.IsTrue(texture == null, "The owned native texture must be destroyed.");
            Assert.IsNull(Read<Texture2D>("s_pendingCaptureTex"));
            Assert.IsFalse(Read<bool>("s_pendingCaptureDone"));
            Assert.IsFalse(Read<bool>("s_pendingCaptureStarted"));
        }

        [Test]
        public void LateCallbackAfterCleanupReleasesItsTexture()
        {
            int generation = (int)Invoke("BeginPendingCapture");
            Invoke("CleanupPendingCapture");
            var texture = Allocate();
            Invoke("CompletePendingCapture", generation, texture);
            Assert.IsTrue(texture == null);
            Assert.IsNull(Read<Texture2D>("s_pendingCaptureTex"));
            Assert.IsFalse(Read<bool>("s_pendingCaptureDone"));
        }

        [Test]
        public void OldCallbackCannotReplaceANewerCapture()
        {
            int oldGeneration = (int)Invoke("BeginPendingCapture");
            int currentGeneration = (int)Invoke("BeginPendingCapture");
            var oldTexture = Allocate();
            Invoke("CompletePendingCapture", oldGeneration, oldTexture);
            Assert.IsTrue(oldTexture == null);
            Assert.IsTrue(Read<bool>("s_pendingCaptureStarted"));
            Assert.IsFalse(Read<bool>("s_pendingCaptureDone"));
            var currentTexture = Allocate();
            Invoke("CompletePendingCapture", currentGeneration, currentTexture);
            Assert.AreSame(currentTexture, Read<Texture2D>("s_pendingCaptureTex"));
            Assert.IsTrue(Read<bool>("s_pendingCaptureDone"));
            Assert.IsFalse(Read<bool>("s_pendingCaptureStarted"));
        }

        [Test]
        public void NullCompletionRemainsAnAvailableFailureForTheNextPoll()
        {
            int generation = (int)Invoke("BeginPendingCapture");
            Invoke("CompletePendingCapture", generation, null);
            Assert.IsNull(Read<Texture2D>("s_pendingCaptureTex"));
            Assert.IsTrue(Read<bool>("s_pendingCaptureDone"));
            Assert.IsFalse(Read<bool>("s_pendingCaptureStarted"));
        }

        [UnityTest]
        public IEnumerator ForeignRequestCannotConsumeOrReattributeCompletedCapture()
        {
            yield return new EnterPlayMode();
            using var fixture = new CaptureFixture();
            var ownerRequest = fixture.Request();
            var started = Send(ownerRequest);
            Assert.IsTrue(started.Value<bool>("success"), started.ToString());
            var texture = Allocate();
            texture.SetPixel(0, 0, Color.red);
            texture.Apply();
            CompleteNativeCapture(texture);
            var foreignRequest = fixture.Request();
            foreignRequest["target"] = fixture.Other.name;
            foreignRequest["file_name"] = "foreign.png";

            var foreign = Send(foreignRequest);

            Assert.IsFalse(foreign.Value<bool>("success"), "A separate request must not consume the owner's completed frame: " + foreign);
            Assert.IsTrue(texture != null, "The owner's frame must remain available.");
            Assert.IsFalse(File.Exists(Path.Combine(fixture.FullFolder, "foreign.png")));
            var resumed = Send(Poll(started));
            Assert.IsTrue(resumed.Value<bool>("success"), resumed.ToString());
            Assert.AreEqual(fixture.Target.name, resumed["data"].Value<string>("gameObject"));
            Assert.AreEqual(Path.Combine(fixture.FullFolder, "owner.png").Replace('\\', '/'), resumed["data"].Value<string>("fullPath").Replace('\\', '/'));
            Assert.AreEqual(2, resumed["data"].Value<int>("width"));
            Assert.AreEqual(2, resumed["data"].Value<int>("height"));
            Assert.IsTrue(texture == null);
            var decoded = Allocate();
            Assert.IsTrue(decoded.LoadImage(File.ReadAllBytes(Path.Combine(fixture.FullFolder, "owner.png"))));
            Assert.That(decoded.GetPixel(0, 0).r, Is.EqualTo(1).Within(0.01f));
            Assert.That(decoded.GetPixel(0, 0).g, Is.EqualTo(0).Within(0.01f));
            Assert.AreEqual(started["data"].Value<string>("capture_id"), resumed["data"].Value<string>("capture_id"));
        }

        [UnityTest]
        public IEnumerator IdenticalSettingsWithoutIdAreIndependentRequests()
        {
            yield return new EnterPlayMode();
            using var fixture = new CaptureFixture();
            var request = fixture.Request();
            var first = Send(request);
            var pending = Send(Poll(first));
            Assert.IsTrue(pending.Value<bool>("success"), pending.ToString());
            Assert.IsTrue(pending["data"].Value<bool>("pending"));
            var texture = Allocate();
            CompleteNativeCapture(texture);

            var independent = Send(request);

            Assert.IsFalse(independent.Value<bool>("success"), "Identical settings do not identify a capture owner: " + independent);
            Assert.IsTrue(texture != null);
            Assert.IsTrue(Send(Poll(first)).Value<bool>("success"));
            var second = Send(request);
            Assert.IsTrue(second.Value<bool>("success"), second.ToString());
            Assert.AreNotEqual(first["data"].Value<string>("capture_id"), second["data"].Value<string>("capture_id"));
            var cancel = Poll(second);
            cancel["cancel_capture"] = true;
            Assert.IsTrue(Send(cancel).Value<bool>("success"));
        }

        [UnityTest]
        public IEnumerator ChangedOwnerSettingsAndForeignIdPreserveResult()
        {
            yield return new EnterPlayMode();
            using var fixture = new CaptureFixture();
            var started = Send(fixture.Request());
            var texture = Allocate();
            CompleteNativeCapture(texture);
            var changedSettings = new[]
            {
                new JObject { ["target"] = fixture.Other.name },
                new JObject { ["output_folder"] = fixture.Folder + "_other" },
                new JObject { ["file_name"] = "changed.png" },
                new JObject { ["width"] = 320 },
                new JObject { ["include_image"] = false },
                new JObject { ["max_resolution"] = 32 },
                new JObject { ["component_type"] = "panel_renderer" },
                new JObject { ["capture_id"] = Guid.NewGuid().ToString("N"), ["cancel_capture"] = true },
            };
            foreach (var changed in changedSettings)
            {
                var poll = Poll(started);
                poll.Merge(changed);
                var response = Send(poll);
                Assert.IsFalse(response.Value<bool>("success"), "Changed ownership/settings must be rejected: " + response);
                Assert.AreSame(texture, Read<Texture2D>("s_pendingCaptureTex"));
                Assert.IsFalse(Directory.Exists(fixture.FullFolder), "Rejected polls must not write output.");
                Assert.IsFalse(Directory.Exists(fixture.FullFolder + "_other"));
            }
            Assert.IsTrue(Send(Poll(started)).Value<bool>("success"));
        }

        [UnityTest]
        public IEnumerator CamelCaseCaptureSettingsAcceptEquivalentSnakeCasePollAndRejectChanges()
        {
            yield return new EnterPlayMode();
            using var fixture = new CaptureFixture();
            var request = fixture.Request();
            foreach (var key in new[] { "include_image", "max_resolution", "file_name", "output_folder" })
                request.Remove(key);
            request["includeImage"] = true;
            request["maxResolution"] = 1;
            request["fileName"] = "camel-preview";
            request["outputFolder"] = fixture.Folder;
            var started = Send(request);
            Assert.IsTrue(started.Value<bool>("success"), started.ToString());
            var texture = Allocate();
            CompleteNativeCapture(texture);
            foreach (
                var changed in new[]
                {
                    new JObject { ["includeImage"] = false },
                    new JObject { ["maxResolution"] = 2 },
                    new JObject { ["fileName"] = "changed.png" },
                    new JObject { ["outputFolder"] = fixture.Folder + "_other" },
                }
            )
            {
                var poll = Poll(started);
                poll.Merge(changed);
                Assert.IsFalse(Send(poll).Value<bool>("success"), "Changed camelCase settings must not consume the frame: " + changed);
                Assert.AreSame(texture, Read<Texture2D>("s_pendingCaptureTex"));
                Assert.IsFalse(Directory.Exists(fixture.FullFolder));
                Assert.IsFalse(Directory.Exists(fixture.FullFolder + "_other"));
            }
            var equivalent = Poll(started);
            equivalent["include_image"] = true;
            equivalent["max_resolution"] = 1;
            equivalent["file_name"] = "camel-preview.png";
            equivalent["output_folder"] = fixture.FullFolder;

            var result = Send(equivalent);

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("camel-preview.png", Path.GetFileName(result["data"].Value<string>("fullPath")));
            Assert.IsNotEmpty(result["data"].Value<string>("imageBase64"));
            Assert.AreEqual(1, result["data"].Value<int>("imageWidth"));
            Assert.AreEqual(1, result["data"].Value<int>("imageHeight"));
        }

        [UnityTest]
        public IEnumerator MixedAliasPrecedenceIsSharedByCaptureStartAndPoll()
        {
            yield return new EnterPlayMode();
            using var fixture = new CaptureFixture();
            var request = fixture.Request();
            request["include_image"] = false;
            request["includeImage"] = true;
            request["max_resolution"] = JValue.CreateNull();
            request["maxResolution"] = 1;
            request["fileName"] = "ignored.png";
            request["outputFolder"] = fixture.Folder + "_ignored";
            var started = Send(request);
            Assert.IsTrue(started.Value<bool>("success"), started.ToString());
            var equivalent = Poll(started);
            equivalent["include_image"] = true;
            equivalent["max_resolution"] = 1;
            equivalent["file_name"] = "owner.png";
            equivalent["output_folder"] = fixture.FullFolder;
            var pending = Send(equivalent);
            Assert.IsTrue(pending.Value<bool>("success"), "Both requests resolve to the same legacy effective settings: " + pending);
            Assert.IsTrue(pending["data"].Value<bool>("pending"));
            var texture = Allocate();
            CompleteNativeCapture(texture);
            var changed = Poll(started);
            changed["max_resolution"] = JValue.CreateNull();
            changed["maxResolution"] = 2;
            Assert.IsFalse(Send(changed).Value<bool>("success"), "A changed effective fallback value must not consume the frame.");
            Assert.AreSame(texture, Read<Texture2D>("s_pendingCaptureTex"));
            Assert.IsFalse(Directory.Exists(fixture.FullFolder));

            var result = Send(Poll(started));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("owner.png", Path.GetFileName(result["data"].Value<string>("fullPath")));
            Assert.IsNotEmpty(result["data"].Value<string>("imageBase64"));
            Assert.AreEqual(1, result["data"].Value<int>("imageWidth"));
            Assert.IsFalse(Directory.Exists(fixture.FullFolder + "_ignored"));
        }

        [UnityTest]
        public IEnumerator CancellationInvalidatesOnlyItsOwnCaptureAndLateCallback()
        {
            yield return new EnterPlayMode();
            using var fixture = new CaptureFixture();
            var first = Send(fixture.Request());
            int oldGeneration = Read<int>("s_pendingCaptureGeneration");
            var cancel = Poll(first);
            cancel["cancel_capture"] = true;
            Assert.IsTrue(Send(cancel).Value<bool>("success"));
            var second = Send(fixture.Request());
            var late = Allocate();
            Invoke("CompletePendingCapture", oldGeneration, late);
            Assert.IsTrue(late == null);
            Assert.IsTrue(Read<bool>("s_pendingCaptureStarted"));
            var current = Allocate();
            CompleteNativeCapture(current);

            Assert.IsFalse(Send(cancel).Value<bool>("success"), "An old owner cannot cancel a newer result.");
            Assert.AreSame(current, Read<Texture2D>("s_pendingCaptureTex"));
            var cancelCurrent = Poll(second);
            cancelCurrent["cancel_capture"] = true;
            Assert.IsTrue(Send(cancelCurrent).Value<bool>("success"));
            Assert.IsTrue(current == null);
            Assert.IsFalse(Directory.Exists(fixture.FullFolder));
        }

        [UnityTest]
        public IEnumerator ExpiredCompletedResultReleasesTextureAndCannotClaimNewCapture()
        {
            yield return new EnterPlayMode();
            using var fixture = new CaptureFixture();
            var first = Send(fixture.Request());
            int oldGeneration = Read<int>("s_pendingCaptureGeneration");
            var texture = Allocate();
            CompleteNativeCapture(texture);
            var expiry = typeof(ManageUI).GetField("s_pendingCaptureExpiresAt", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(expiry, "Completed frames require bounded retention.");
            expiry.SetValue(null, EditorApplication.timeSinceStartup - 1);
            Invoke("ExpirePendingCapture");
            Assert.IsTrue(texture == null);
            var second = Send(fixture.Request());
            Assert.IsTrue(second.Value<bool>("success"), second.ToString());
            Assert.IsFalse(Send(Poll(first)).Value<bool>("success"));
            var late = Allocate();
            Invoke("CompletePendingCapture", oldGeneration, late);
            Assert.IsTrue(late == null);
            Assert.IsTrue(Read<bool>("s_pendingCaptureStarted"));
        }

        [UnityTest]
        public IEnumerator FailedCompletionRemainsOwnedUntilItsMatchingPoll()
        {
            yield return new EnterPlayMode();
            using var fixture = new CaptureFixture();
            var first = Send(fixture.Request());
            CompleteNativeCapture(null);

            Assert.IsFalse(Send(fixture.Request()).Value<bool>("success"));
            Assert.IsTrue(Read<bool>("s_pendingCaptureDone"), "A foreign request must not consume the owner's failure.");
            var owner = Send(Poll(first));
            Assert.IsFalse(owner.Value<bool>("success"));
            StringAssert.Contains("timed out", owner.Value<string>("error"));
            Assert.IsFalse(Read<bool>("s_pendingCaptureDone"));
            Assert.IsTrue(Send(fixture.Request()).Value<bool>("success"));
        }

        [UnityTearDown]
        public IEnumerator ExitPlayModeAfterTest()
        {
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }

        private static JObject Send(JObject request) => JObject.FromObject(ManageUI.HandleCommand(request));

        private static JObject Poll(JObject started) => new JObject { ["action"] = "render_ui", ["capture_id"] = started["data"]?["capture_id"]?.DeepClone() };

        private static void CompleteNativeCapture(Texture2D texture)
        {
            var capturers = Resources.FindObjectsOfTypeAll<ScreenshotCapturer>();
            Assert.AreEqual(1, capturers.Length, "The tool should own exactly one native screenshot capturer.");
            typeof(ScreenshotCapturer)
                .GetMethod("Complete", BindingFlags.NonPublic | BindingFlags.Instance)
                .Invoke(capturers[0], new object[] { texture, false });
        }

        private Texture2D Allocate()
        {
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            allocated.Add(texture);
            return texture;
        }

        private static T Read<T>(string name) => (T)typeof(ManageUI).GetField(name, BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);

        private static object Invoke(string name, params object[] arguments) =>
            typeof(ManageUI).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, arguments);
    }
}
