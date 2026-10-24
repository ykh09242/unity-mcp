using System;
using UnityEngine;

namespace MCPForUnity.Runtime.Helpers
{
    /// <summary>Compiled module boundaries. These symbols come from each owning asmdef's package version defines.</summary>
    public static class OptionalUnityModules
    {
        public static bool Physics3D =>
#if MCP_FOR_UNITY_PHYSICS
            true;
#else
            false;
#endif
        public static bool Physics2D =>
#if MCP_FOR_UNITY_PHYSICS2D
            true;
#else
            false;
#endif
        public static bool ScreenCapture =>
#if MCP_FOR_UNITY_SCREEN_CAPTURE
            true;
#else
            false;
#endif
        public static Type Physics3DType => Physics3D ? Type.GetType("UnityEngine.Physics, UnityEngine.PhysicsModule") : null;
        public static Type Physics2DType => Physics2D ? Type.GetType("UnityEngine.Physics2D, UnityEngine.Physics2DModule") : null;
        public static bool PhysicsTools => Physics3D && Physics2D;
        public static string PhysicsUnavailableReason =>
            PhysicsTools
                ? null
                : "physics_modules_unavailable: physics tools require com.unity.modules.physics and com.unity.modules.physics2d. Missing: "
                    + (!Physics3D ? "com.unity.modules.physics " : "")
                    + (!Physics2D ? "com.unity.modules.physics2d" : "");
        public const string ScreenCaptureUnavailableReason =
            "screen_capture_module_unavailable: Game View capture requires com.unity.modules.screencapture; camera and Scene View rendering remain available.";

        public static Texture2D CaptureScreenshotAsTexture(int superSize = 1)
        {
#if MCP_FOR_UNITY_SCREEN_CAPTURE
            return UnityEngine.ScreenCapture.CaptureScreenshotAsTexture(superSize);
#else
            throw new NotSupportedException(ScreenCaptureUnavailableReason);
#endif
        }

        public static void CaptureScreenshot(string filename, int superSize = 1)
        {
#if MCP_FOR_UNITY_SCREEN_CAPTURE
            UnityEngine.ScreenCapture.CaptureScreenshot(filename, superSize);
#else
            throw new NotSupportedException(ScreenCaptureUnavailableReason);
#endif
        }
    }
}
