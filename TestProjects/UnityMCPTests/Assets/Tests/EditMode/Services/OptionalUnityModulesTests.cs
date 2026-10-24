using System;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools.Physics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class OptionalUnityModulesTests
    {
        [Test]
        public void PhysicsCompatibilityMatchesCompiledModules()
        {
            Assert.AreEqual(OptionalUnityModules.Physics3D, OptionalUnityModules.Physics3DType != null);
            Assert.AreEqual(OptionalUnityModules.Physics2D, OptionalUnityModules.Physics2DType != null);
            Assert.AreEqual(OptionalUnityModules.Physics3D && OptionalUnityModules.Physics2D, OptionalUnityModules.PhysicsTools);
            JObject result = JObject.FromObject(ManagePhysics.HandleCommand(new JObject { ["action"] = "ping" }));
            Assert.AreEqual(OptionalUnityModules.PhysicsTools, result.Value<bool>("success"), result.ToString());
            if (!OptionalUnityModules.PhysicsTools)
            {
                StringAssert.Contains("physics_modules_unavailable", result.Value<string>("error"));
                Assert.AreEqual("module_unavailable", result["data"]["code"].Value<string>());
            }
            if (!OptionalUnityModules.Physics3D)
            {
                Assert.IsNull(UnityPhysicsCompat.GetPhysicsAutoSyncTransforms());
                Assert.AreEqual(UnityPhysicsCompat.SimulationMode.Unknown, UnityPhysicsCompat.GetPhysicsSimulationMode());
                Assert.IsFalse(UnityPhysicsCompat.TrySetPhysicsAutoSyncTransforms(true));
            }
            if (!OptionalUnityModules.Physics2D)
            {
                Assert.IsNull(UnityPhysicsCompat.GetPhysics2DAutoSyncTransforms());
                Assert.IsFalse(UnityPhysicsCompat.TrySetPhysics2DAutoSyncTransforms(true));
            }
        }

        [Test]
        public void ModuleReadinessIsExplicitWithoutClaimingNativeReadiness()
        {
            JObject features = FeatureReadiness.Capture();
            Assert.AreEqual(OptionalUnityModules.PhysicsTools, features["physics"]["supported"].Value<bool>());
            Assert.AreEqual(OptionalUnityModules.ScreenCapture, features["screen_capture"]["supported"].Value<bool>());
            Assert.AreNotEqual(true, features["physics"]["ready"].Value<bool?>());
            Assert.AreNotEqual(true, features["screen_capture"]["ready"].Value<bool?>());
            Assert.AreEqual("physics", FeatureReadiness.FeatureForTool("query_physics"));
            Assert.IsNull(FeatureReadiness.FeatureForTool("manage_scene"));
        }

        [Test]
        public void MissingCaptureModuleFailsBeforeCallingNativeCapture()
        {
            if (OptionalUnityModules.ScreenCapture)
                Assert.Ignore("Absence behavior is exercised by the metadata-absent compilation matrix and module-absent projects.");
            var error = Assert.Throws<NotSupportedException>(() => OptionalUnityModules.CaptureScreenshotAsTexture());
            StringAssert.Contains("com.unity.modules.screencapture", error.Message);
            error = Assert.Throws<NotSupportedException>(() => OptionalUnityModules.CaptureScreenshot("unused.png"));
            StringAssert.Contains("screen_capture_module_unavailable", error.Message);
            error = Assert.Throws<NotSupportedException>(() => ScreenshotCapturer.Begin(1, texture => { }));
            StringAssert.Contains("screen_capture_module_unavailable", error.Message);
        }
    }
}
