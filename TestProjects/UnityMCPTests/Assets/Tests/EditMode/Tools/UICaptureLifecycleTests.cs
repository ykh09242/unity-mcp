using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class UICaptureLifecycleTests
    {
        private readonly List<Texture2D> allocated = new();
        private bool ownsState;

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
