using System;
using System.IO;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ConfigurationPixelProbe : MonoBehaviour, IPlayScenarioStateProvider
    {
        public Material Surface;
        public string MaterialPath;
        private IDisposable registration;
        private bool measured;

        [Serializable]
        private sealed class Sample
        {
            public string job_id;
            public string scene;
            public string material;
            public string sample;
            public string method;
            public string device;
            public string color_space;
            public int[] rgba;
        }

        private void OnEnable() => registration = PlayScenarioStateRegistry.Register("render.sampled", this);

        private void OnDisable() => registration?.Dispose();

        private void Start()
        {
            if (SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null || Surface == null || Surface.mainTexture == null)
                return;
            string[] args = Environment.GetCommandLineArgs();
            int request = Array.IndexOf(args, "--mcp-scenario-request");
            if (request < 0 || request + 1 >= args.Length)
                return;
            var target = RenderTexture.GetTemporary(8, 8, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.Linear);
            var previous = RenderTexture.active;
            bool srgbWrite = GL.sRGBWrite;
            var readback = new Texture2D(1, 1, TextureFormat.RGBA32, false, true);
            try
            {
                GL.sRGBWrite = false;
                Graphics.Blit(Surface.mainTexture, target, Surface);
                RenderTexture.active = target;
                readback.ReadPixels(new Rect(4, 4, 1, 1), 0, 0, false);
                readback.Apply(false);
                Color32 pixel = readback.GetPixels32()[0];
                var sample = new Sample
                {
                    job_id = Path.GetFileName(Path.GetDirectoryName(args[request + 1])),
                    scene = gameObject.scene.path,
                    material = MaterialPath,
                    sample = "center-linear-rgba8",
                    method = "render_texture_read_pixels",
                    device = SystemInfo.graphicsDeviceType.ToString(),
                    color_space = QualitySettings.activeColorSpace.ToString(),
                    rgba = new int[] { pixel.r, pixel.g, pixel.b, pixel.a },
                };
                Debug.Log("MCP_CONFIGURATION_PIXEL " + JsonUtility.ToJson(sample));
                measured = true;
            }
            finally
            {
                RenderTexture.active = previous;
                GL.sRGBWrite = srgbWrite;
                RenderTexture.ReleaseTemporary(target);
                Destroy(readback);
            }
        }

        public bool TryRead(out PlayScenarioStateValue value)
        {
            value = PlayScenarioStateValue.FromBoolean(measured);
            return true;
        }
    }
}
