using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public partial class UguiTmpLayoutDiagnosticsTests
    {
        [UnityTest]
        public IEnumerator LatinAndCjkClippingChangesAcrossTwoRenderedViewports()
        {
            string fixtureRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
            string evidence = Path.Combine(fixtureRoot, "exports");
            Assert.IsTrue(Directory.Exists(evidence), "The owned fixture requires an export directory.");
            Assert.IsFalse(File.GetAttributes(evidence).HasFlag(FileAttributes.ReparsePoint));
            var cameraObject = new GameObject("Verified layout camera", typeof(Camera));
            owned.Add(cameraObject);
            var camera = cameraObject.GetComponent<Camera>();
            camera.enabled = false;
            camera.orthographic = true;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.black;
            camera.allowHDR = camera.allowMSAA = false;
            root.transform.position = new Vector3(0, 0, 5);
            var latin = ViewportLabel("English", "Claim all available rewards from your inventory", 46f);
            var cjk = ViewportLabel("Korean", "한국어漢字 한국어漢字 한국어漢字", -46f);
            var measurements = new JArray();
            foreach (var viewport in new[] { new Vector2Int(320, 240), new Vector2Int(960, 480) })
            {
                var target = new RenderTexture(viewport.x, viewport.y, 24);
                owned.Add(target);
                camera.targetTexture = target;
                camera.orthographicSize = target.height / 2f;
                camera.aspect = (float)target.width / target.height;
                var rootRect = (RectTransform)root.transform;
                rootRect.sizeDelta = new Vector2(target.width, target.height);
                yield return null;
                Render(latin);
                Render(cjk);
                camera.Render();
                Assert.AreEqual(viewport, new Vector2Int(camera.pixelWidth, camera.pixelHeight));
                Assert.IsTrue(target.IsCreated());
                byte[] png = ReadViewport(target);
                string capture = $"verified-layout-{target.width}x{target.height}.png";
                WriteEvidence(Path.Combine(evidence, capture), png);
                var run = Data(Call());
                bool shouldClip = viewport.x == 320;
                var labels = new JArray(ObserveLabel(run, latin, "en", shouldClip, target.width), ObserveLabel(run, cjk, "ko", shouldClip, target.width));
                measurements.Add(
                    new JObject
                    {
                        ["evidence"] = "observed_render_texture",
                        ["viewport"] = new JArray(camera.pixelWidth, camera.pixelHeight),
                        ["labels"] = labels,
                        ["capture"] = capture,
                        ["capture_sha256"] = Digest(png),
                    }
                );
                camera.targetTexture = null;
            }

            root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            var projection = Data(
                JObject.FromObject(
                    ManageUGUI.HandleCommand(
                        new JObject
                        {
                            ["action"] = "diagnose_device_profiles",
                            ["target"] = root.GetInstanceIDCompat(),
                            ["device_profiles"] = new JArray(
                                new JObject
                                {
                                    ["source"] = "virtual",
                                    ["width"] = 390,
                                    ["height"] = 844,
                                    ["dpi"] = 460,
                                    ["safe_area"] = new JArray(0, 47, 390, 763),
                                    ["orientation"] = "portrait",
                                }
                            ),
                        }
                    )
                )
            );
            Assert.AreEqual("dpi_geometry_estimate", (string)projection["policy"]["measurement"]);
            Assert.AreEqual("not_observed", (string)projection["policy"]["rendering"]);
            Assert.AreEqual("virtual", (string)projection["profiles"][0]["source"]);
            Assert.AreEqual("owned_overlay_preview", (string)projection["profiles"][0]["route"]);
            var receipt = new JObject
            {
                ["unity_version"] = Application.unityVersion,
                ["graphics_device"] = SystemInfo.graphicsDeviceType.ToString(),
                ["fixture_font_sha256"] = Digest(File.ReadAllBytes(nativeFontPath)),
                ["measurements"] = measurements,
                ["device_profile"] = projection,
            };
            using var output = new StreamWriter(new FileStream(Path.Combine(evidence, "verified-localized-layout.json"), FileMode.CreateNew));
            output.Write(receipt.ToString(Formatting.Indented));
        }

        private Component ViewportLabel(string name, string contents, float y)
        {
            Component label = Text(contents, render: false);
            label.name = name;
            var rect = (RectTransform)label.transform;
            rect.anchorMin = new Vector2(0, .5f);
            rect.anchorMax = new Vector2(1, .5f);
            rect.sizeDelta = new Vector2(-32, 48);
            rect.anchoredPosition = new Vector2(0, y);
            var overflow = textType.GetProperty("overflowMode");
            overflow.SetValue(label, Enum.Parse(overflow.PropertyType, "Truncate"));
            return label;
        }

        private JObject ObserveLabel(JObject data, Component label, string locale, bool shouldClip, int viewportWidth)
        {
            // The product reads already-generated metrics; the oracle independently uses the native TMP public values.
            var node = Node(data, label);
            Assert.AreEqual("observed", (string)node["status"], node.ToString());
            Assert.AreEqual(viewportWidth - 32, (float)node["text_rect"][2], 0.01f);
            int index = Get<int>(label, "firstOverflowCharacterIndex");
            Assert.AreEqual(index, (int)node["first_overflow_index"], locale);
            Assert.AreEqual(shouldClip, (bool)node["native_overflow"], locale + ": " + node);
            Assert.AreEqual(shouldClip, Get<bool>(label, "isTextTruncated"), locale);
            Assert.AreEqual(shouldClip, (bool)node["native_truncated"], locale);
            if (locale == "ko")
                Assert.Greater((int)node["fallback_characters"], 0, "Actual CJK fallback glyphs must be used.");
            return new JObject
            {
                ["locale"] = locale,
                ["expected_clipped"] = shouldClip,
                ["observed_layout"] = node.DeepClone(),
            };
        }

        private static byte[] ReadViewport(RenderTexture target)
        {
            var previous = RenderTexture.active;
            var pixels = new Texture2D(target.width, target.height, TextureFormat.RGBA32, false);
            try
            {
                RenderTexture.active = target;
                pixels.ReadPixels(new Rect(0, 0, target.width, target.height), 0, 0);
                pixels.Apply();
                int row = target.height / 2 * target.width;
                var colors = pixels.GetPixels32();
                bool IsText(Color32 pixel) => pixel.r > 128 && pixel.g < 96 && pixel.b < 96;
                Assert.Greater(colors.Take(row).Count(IsText), 10, "CJK glyph pixels were rendered.");
                Assert.Greater(colors.Skip(row).Count(IsText), 10, "Latin glyph pixels were rendered.");
                return pixels.EncodeToPNG();
            }
            finally
            {
                RenderTexture.active = previous;
                UnityEngine.Object.DestroyImmediate(pixels);
            }
        }

        private static string Digest(byte[] data)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "").ToLowerInvariant();
        }

        private static void WriteEvidence(string file, byte[] bytes)
        {
            using var output = new FileStream(file, FileMode.CreateNew);
            output.Write(bytes, 0, bytes.Length);
        }
    }
}
