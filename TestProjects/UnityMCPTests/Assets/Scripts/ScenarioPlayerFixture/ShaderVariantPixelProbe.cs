using System;
using System.IO;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ShaderVariantPixelProbe : MonoBehaviour
    {
        public const string Keyword = "MCP_REQUIRED_TINT";
        public Camera View;
        public Renderer Surface;
        public string MaterialGuid;

        [Serializable]
        private sealed class Observation
        {
            public string buildGuid,
                graphicsApi,
                device,
                scene,
                camera,
                material,
                materialGuid,
                shader,
                keyword;
            public bool keywordEnabled;
            public bool editor;
            public int[] region,
                rgba;
            public int[] background;
        }

        private void Start()
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "--variant-output");
            if (index < 0)
                return;
            int exit = 1;
            try
            {
                if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                    throw new InvalidOperationException("Actual graphics are required for the shader comparison.");
                Capture(args[index + 1], "player.json");
                exit = 0;
            }
            catch (Exception exception)
            {
                Debug.LogException(exception);
            }
            Application.Quit(exit);
        }

        public void Capture(string output, string evidenceFile)
        {
            var target = RenderTexture.GetTemporary(64, 64, 16, RenderTextureFormat.ARGB32, RenderTextureReadWrite.Linear);
            var active = RenderTexture.active;
            var previous = View.targetTexture;
            var readback = new Texture2D(64, 64, TextureFormat.RGBA32, false, true);
            try
            {
                View.targetTexture = target;
                View.Render();
                RenderTexture.active = target;
                readback.ReadPixels(new Rect(0, 0, 64, 64), 0, 0, false);
                readback.Apply(false);
                Color32[] pixels = readback.GetPixels32();
                int[] region = new int[8 * 8 * 4];
                for (int y = 0; y < 8; y++)
                {
                    for (int x = 0; x < 8; x++)
                    {
                        Color32 pixel = pixels[(28 + y) * 64 + 28 + x];
                        int i = 4 * (y * 8 + x);
                        region[i] = pixel.r;
                        region[i + 1] = pixel.g;
                        region[i + 2] = pixel.b;
                        region[i + 3] = pixel.a;
                    }
                }
                var material = Surface.sharedMaterial;
                Color32 corner = pixels[0];
                var data = new Observation
                {
                    buildGuid = Application.buildGUID,
                    editor = Application.isEditor,
                    graphicsApi = SystemInfo.graphicsDeviceType.ToString(),
                    device = SystemInfo.graphicsDeviceName,
                    scene = gameObject.scene.path,
                    camera = View.name,
                    material = material.name,
                    materialGuid = MaterialGuid,
                    shader = material.shader.name,
                    keyword = Keyword,
                    keywordEnabled = material.IsKeywordEnabled(Keyword),
                    region = new[] { 28, 28, 8, 8 },
                    rgba = region,
                    background = new int[] { corner.r, corner.g, corner.b, corner.a },
                };
                using (var file = new FileStream(Path.Combine(output, "frame.png"), FileMode.CreateNew))
                {
                    byte[] png = readback.EncodeToPNG();
                    file.Write(png, 0, png.Length);
                }
                using var stream = new FileStream(Path.Combine(output, evidenceFile), FileMode.CreateNew);
                using var writer = new StreamWriter(stream);
                writer.Write(JsonUtility.ToJson(data, true));
            }
            finally
            {
                RenderTexture.active = active;
                View.targetTexture = previous;
                RenderTexture.ReleaseTemporary(target);
                if (Application.isPlaying)
                    Destroy(readback);
                else
                    DestroyImmediate(readback);
            }
        }
    }
}
