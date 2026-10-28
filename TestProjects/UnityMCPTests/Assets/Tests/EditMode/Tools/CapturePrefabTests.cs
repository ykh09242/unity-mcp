using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class CapturePrefabTests
    {
        private string _folder;
        private readonly PrefabTestSceneFixture _sceneFixture = new PrefabTestSceneFixture();

        [SetUp]
        public void SetUp()
        {
            _sceneFixture.PrepareRunnerBootstrap();
            _sceneFixture.Create("CapturePrefab_", Guid.NewGuid().ToString("N"));
            string name = "CapturePrefabTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", name);
            _folder = "Assets/" + name;
        }

        [TearDown]
        public void TearDown()
        {
            if (_folder != null)
                AssetDatabase.DeleteAsset(_folder);
            _sceneFixture.Close();
            _sceneFixture.RestoreRunnerBootstrap();
        }

        [Test]
        public void UiCaptureProducesPixelsAndPreservesSceneAndPrefab()
        {
            Type imageType = Type.GetType("UnityEngine.UI.Image, UnityEngine.UI");
            Assert.NotNull(imageType, "UI integration profile is required.");
            var root = new GameObject("Panel", typeof(RectTransform));
            string path = _folder + "/Panel.prefab";
            try
            {
                var rect = (RectTransform)root.transform;
                rect.sizeDelta = new Vector2(64, 64);
                var component = root.AddComponent(imageType);
                imageType.GetProperty("color").SetValue(component, Color.red);
                PrefabUtility.SaveAsPrefabAsset(root, path);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
            AssertCapture(path, "ui_canvas");
        }

        [Test]
        public void RendererCaptureProducesPixelsAndPreservesSceneAndPrefab()
        {
            var root = GameObject.CreatePrimitive(PrimitiveType.Cube);
            string path = _folder + "/Cube.prefab";
            try
            {
                var material = new Material(Shader.Find("Unlit/Color")) { color = Color.red };
                AssetDatabase.CreateAsset(material, _folder + "/Red.mat");
                root.GetComponent<Renderer>().sharedMaterial = material;
                PrefabUtility.SaveAsPrefabAsset(root, path);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
            AssertCapture(path, "renderer_bounds_front");
        }

        [TestCase(128, 128, "MatchWidthOrHeight", 0f, 40f, 32f)]
        [TestCase(128, 128, "MatchWidthOrHeight", 1f, 20f, 16f)]
        [TestCase(128, 128, "MatchWidthOrHeight", 0.5f, 28.284f, 22.627f)]
        [TestCase(512, 128, "MatchWidthOrHeight", 0f, 10f, 8f)]
        [TestCase(512, 128, "MatchWidthOrHeight", 0.5f, 14.142f, 11.314f)]
        [TestCase(128, 128, "Expand", 0f, 20f, 16f)]
        [TestCase(128, 128, "Shrink", 0f, 40f, 32f)]
        public void CanvasReferenceResolutionUsesRequestedViewport(
            int referenceWidth,
            int referenceHeight,
            string matchMode,
            float match,
            float expectedWidth,
            float expectedHeight
        )
        {
            string path = CreateCanvasPrefab(scaler =>
            {
                SetUiProperty(scaler, "uiScaleMode", "ScaleWithScreenSize");
                SetUiProperty(scaler, "referenceResolution", new Vector2(referenceWidth, referenceHeight));
                SetUiProperty(scaler, "screenMatchMode", matchMode);
                SetUiProperty(scaler, "matchWidthOrHeight", match);
            });
            AssertPixelBounds(path, 256, 128, expectedWidth, expectedHeight);
        }

        [TestCase(0.75f, 15f, 12f)]
        [TestCase(2.5f, 50f, 40f)]
        public void CanvasConstantPixelScalePreservesAuthoredSize(float scale, float expectedWidth, float expectedHeight)
        {
            string path = CreateCanvasPrefab(scaler =>
            {
                SetUiProperty(scaler, "uiScaleMode", "ConstantPixelSize");
                SetUiProperty(scaler, "scaleFactor", scale);
            });
            AssertPixelBounds(path, 256, 128, expectedWidth, expectedHeight);
        }

        [Test]
        public void CanvasPhysicalScaleUsesDpiAndAuthoredUnits()
        {
            string path = CreateCanvasPrefab(scaler =>
            {
                SetUiProperty(scaler, "uiScaleMode", "ConstantPhysicalSize");
                SetUiProperty(scaler, "physicalUnit", "Points");
                SetUiProperty(scaler, "fallbackScreenDPI", 144f);
            });
            float scale = (Screen.dpi == 0 ? 144f : Screen.dpi) / 72f;
            AssertPixelBounds(path, 512, 512, 20 * scale, 16 * scale);
        }

        [Test]
        public void CanvasReferencePixelDensityPreservesSlicedSpriteBorders()
        {
            var texture = new Texture2D(24, 24, TextureFormat.RGBA32, false, false) { filterMode = FilterMode.Point };
            for (int y = 0; y < 24; y++)
            for (int x = 0; x < 24; x++)
                texture.SetPixel(x, y, x >= 8 && x < 16 && y >= 8 && y < 16 ? Color.green : Color.red);
            texture.Apply();
            AssetDatabase.CreateAsset(texture, _folder + "/Borders.asset");
            var sprite = Sprite.Create(texture, new Rect(0, 0, 24, 24), Vector2.one * 0.5f, 100f, 0, SpriteMeshType.FullRect, Vector4.one * 8f);
            AssetDatabase.AddObjectToAsset(sprite, texture);
            string path = CreateCanvasPrefab(
                scaler =>
                {
                    SetUiProperty(scaler, "scaleFactor", 2f);
                    SetUiProperty(scaler, "referencePixelsPerUnit", 200f);
                },
                image =>
                {
                    ((RectTransform)image.transform).sizeDelta = new Vector2(64, 64);
                    SetUiProperty(image, "color", Color.white);
                    SetUiProperty(image, "sprite", sprite);
                    SetUiProperty(image, "type", "Sliced");
                }
            );
            _sceneFixture.ClearDirtiness();
            var captured = CapturePixels(path, "ui_canvas", 256, 256);
            try
            {
                RectInt green = PixelBounds(captured, color => color.g > 200 && color.r < 40);
                Assert.That(
                    green.width,
                    Is.EqualTo(64).Within(2),
                    "Reference pixel density must preserve the authored 16-unit borders before the 2x Canvas scale."
                );
                Assert.That(green.height, Is.EqualTo(64).Within(2));
            }
            finally
            {
                Object.DestroyImmediate(captured);
            }
        }

        [TestCase(64, 2048, 100f, 100f, 100f)]
        [TestCase(2048, 64, 100f, 100f, 100f)]
        [TestCase(64, 2048, 4f, 2f, 200f)]
        [TestCase(2048, 64, 4f, 2f, 200f)]
        [TestCase(64, 2048, 0.02f, 0.02f, 0.02f)]
        public void ExtremeAspectRendererCaptureRetainsCompleteForeground(int width, int height, float x, float y, float z)
        {
            var root = GameObject.CreatePrimitive(PrimitiveType.Cube);
            root.transform.localScale = new Vector3(x, y, z);
            root.GetComponent<Renderer>().sharedMaterial = CreateMaterial("Unlit/Color", Color.red);
            string path = SavePrefab(root, "Extreme");
            _sceneFixture.ClearDirtiness();
            var captured = CapturePixels(path, "renderer_bounds_front", width, height);
            try
            {
                RectInt bounds = PixelBounds(captured, IsRed);
                float pixelsPerUnit = Mathf.Min(width / x, height / y) / 1.1f;
                Assert.That(bounds.width, Is.EqualTo(x * pixelsPerUnit).Within(2));
                Assert.That(bounds.height, Is.EqualTo(y * pixelsPerUnit).Within(2));
                Assert.Greater(bounds.xMin, 0);
                Assert.Greater(bounds.yMin, 0);
                Assert.Less(bounds.xMax, width);
                Assert.Less(bounds.yMax, height);
            }
            finally
            {
                Object.DestroyImmediate(captured);
            }
        }

        [TestCase(64, 2048)]
        [TestCase(2048, 64)]
        public void ExtremeAspectCaptureIncludesNearAndFarGeometry(int width, int height)
        {
            var root = new GameObject("Depth range");
            for (int index = 0; index < 2; index++)
            {
                var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                cube.transform.SetParent(root.transform, false);
                cube.transform.localPosition = new Vector3(index == 0 ? -1 : 1, 0, index == 0 ? -100 : 100);
                cube.GetComponent<Renderer>().sharedMaterial = CreateMaterial("Unlit/Color", index == 0 ? Color.red : Color.green);
            }
            string path = SavePrefab(root, "Depth");
            _sceneFixture.ClearDirtiness();
            var captured = CapturePixels(path, "renderer_bounds_front", width, height);
            try
            {
                Assert.Greater(PixelBounds(captured, IsRed).width, 0, "Nearest geometry is clipped.");
                Assert.Greater(PixelBounds(captured, color => color.g > 200 && color.r < 40).width, 0, "Farthest geometry is clipped.");
            }
            finally
            {
                Object.DestroyImmediate(captured);
            }
        }

        [TestCase(117, 124)]
        [TestCase(124, 117)]
        [TestCase(2048, 64)]
        public void CaptureUsesExactRequestedPixelsIndependentlyOfEditorDpi(int width, int height)
        {
            string path = CreateCanvasPrefab(scaler => SetUiProperty(scaler, "scaleFactor", 1f));
            _sceneFixture.ClearDirtiness();
            var captured = CapturePixels(path, "ui_canvas", width, height);
            Object.DestroyImmediate(captured);
        }

        [TestCase(64)]
        [TestCase(128)]
        [TestCase(192)]
        public void RendererMidtonesAreDisplayEncodedInPng(byte value)
        {
            var root = GameObject.CreatePrimitive(PrimitiveType.Quad);
            root.GetComponent<Renderer>().sharedMaterial = CreateMaterial("Unlit/Color", new Color32(value, value, value, 255));
            string path = SavePrefab(root, "Gray");
            _sceneFixture.ClearDirtiness();
            var captured = CapturePixels(path, "renderer_bounds_front", 128, 128);
            try
            {
                AssertGray(captured, 64, 64, value);
            }
            finally
            {
                Object.DestroyImmediate(captured);
            }
        }

        [TestCase(64)]
        [TestCase(128)]
        [TestCase(192)]
        public void UiMidtonesAreDisplayEncodedInPng(byte value)
        {
            string path = CreateCanvasPrefab(
                scaler => SetUiProperty(scaler, "scaleFactor", 1f),
                image => SetUiProperty(image, "color", (Color)new Color32(value, value, value, 255))
            );
            _sceneFixture.ClearDirtiness();
            var captured = CapturePixels(path, "ui_canvas", 128, 128);
            try
            {
                AssertGray(captured, 64, 64, value);
            }
            finally
            {
                Object.DestroyImmediate(captured);
            }
        }

        [Test]
        public void SrgbTextureSamplesKeepTheirDisplayEncodedValues()
        {
            var source = new Texture2D(3, 1, TextureFormat.RGBA32, false, false) { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Clamp };
            source.SetPixels32(new[] { new Color32(64, 64, 64, 255), new Color32(128, 128, 128, 255), new Color32(192, 192, 192, 255) });
            source.Apply();
            AssetDatabase.CreateAsset(source, _folder + "/Steps.asset");
            var root = GameObject.CreatePrimitive(PrimitiveType.Quad);
            var material = CreateMaterial("Unlit/Texture", Color.white);
            material.mainTexture = source;
            root.GetComponent<Renderer>().sharedMaterial = material;
            string path = SavePrefab(root, "TextureSteps");
            _sceneFixture.ClearDirtiness();
            var captured = CapturePixels(path, "renderer_bounds_front", 128, 128);
            try
            {
                AssertGray(captured, 29, 64, 64);
                AssertGray(captured, 64, 64, 128);
                AssertGray(captured, 99, 64, 192);
            }
            finally
            {
                Object.DestroyImmediate(captured);
            }
        }

        private static void AssertGray(Texture2D texture, int x, int y, byte expected)
        {
            Color32 sample = texture.GetPixel(x, y);
            string context = $"{QualitySettings.activeColorSpace} PNG at ({x},{y}); expected sRGB byte {expected}, actual {sample}.";
            Assert.That(sample.r, Is.EqualTo(expected).Within(2), context);
            Assert.That(sample.g, Is.EqualTo(expected).Within(2), context);
            Assert.That(sample.b, Is.EqualTo(expected).Within(2), context);
        }

        private Material CreateMaterial(string shaderName, Color color)
        {
            var shader = Shader.Find(shaderName);
            Assert.NotNull(shader, "Built-in graphics integration profile is required: " + shaderName);
            var material = new Material(shader);
            if (material.HasProperty("_Color"))
                material.color = color;
            AssetDatabase.CreateAsset(material, _folder + "/Material_" + Guid.NewGuid().ToString("N") + ".mat");
            return material;
        }

        private string CreateCanvasPrefab(Action<Component> configureScaler, Action<Component> configureImage = null)
        {
            var root = new GameObject("Scaled Canvas", typeof(RectTransform), typeof(Canvas));
            try
            {
                root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
                Type scalerType = Type.GetType("UnityEngine.UI.CanvasScaler, UnityEngine.UI");
                Type imageType = Type.GetType("UnityEngine.UI.Image, UnityEngine.UI");
                Assert.NotNull(scalerType, "uGUI integration profile is required.");
                Assert.NotNull(imageType, "uGUI integration profile is required.");
                configureScaler(root.AddComponent(scalerType));
                var panel = new GameObject("Child", typeof(RectTransform));
                var rect = (RectTransform)panel.transform;
                rect.SetParent(root.transform, false);
                rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
                rect.sizeDelta = new Vector2(20, 16);
                var image = panel.AddComponent(imageType);
                SetUiProperty(image, "color", Color.red);
                configureImage?.Invoke(image);
                string path = _folder + "/Canvas.prefab";
                PrefabUtility.SaveAsPrefabAsset(root, path);
                return path;
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        private static void SetUiProperty(Component component, string name, object value)
        {
            var property = component.GetType().GetProperty(name);
            Assert.NotNull(property, name);
            property.SetValue(component, property.PropertyType.IsEnum ? Enum.Parse(property.PropertyType, (string)value) : value);
        }

        private string SavePrefab(GameObject root, string name)
        {
            try
            {
                string path = _folder + "/" + name + ".prefab";
                PrefabUtility.SaveAsPrefabAsset(root, path);
                return path;
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        private static bool IsRed(Color32 color) => color.r > 200 && color.g < 40;

        private static RectInt PixelBounds(Texture2D texture, Predicate<Color32> matches)
        {
            var pixels = texture.GetPixels32();
            int minX = texture.width,
                minY = texture.height,
                maxX = -1,
                maxY = -1;
            for (int y = 0; y < texture.height; y++)
            for (int x = 0; x < texture.width; x++)
                if (matches(pixels[y * texture.width + x]))
                {
                    minX = Mathf.Min(minX, x);
                    minY = Mathf.Min(minY, y);
                    maxX = Mathf.Max(maxX, x);
                    maxY = Mathf.Max(maxY, y);
                }
            Assert.GreaterOrEqual(maxX, 0, "Expected foreground pixels are missing.");
            return new RectInt(minX, minY, maxX - minX + 1, maxY - minY + 1);
        }

        private void AssertPixelBounds(string path, int width, int height, float expectedWidth, float expectedHeight)
        {
            _sceneFixture.ClearDirtiness();
            var captured = CapturePixels(path, "ui_canvas", width, height);
            try
            {
                RectInt bounds = PixelBounds(captured, IsRed);
                Assert.That(bounds.width, Is.EqualTo(expectedWidth).Within(2));
                Assert.That(bounds.height, Is.EqualTo(expectedHeight).Within(2));
                Assert.That(bounds.center.x, Is.EqualTo(width / 2f).Within(1));
                Assert.That(bounds.center.y, Is.EqualTo(height / 2f).Within(1));
            }
            finally
            {
                Object.DestroyImmediate(captured);
            }
        }

        private void AssertCapture(string path, string framing)
        {
            _sceneFixture.ClearDirtiness();
            AssertCaptureInCurrentScene(path, framing);
        }

        private static void AssertCaptureInCurrentScene(string path, string framing)
        {
            var texture = CapturePixels(path, framing, 128, 128);
            try
            {
                Assert.IsTrue(IsRed(texture.GetPixel(64, 64)), "Foreground must be rendered, not an empty preview.");
            }
            finally
            {
                Object.DestroyImmediate(texture);
            }
        }

        private static Texture2D CapturePixels(string path, string framing, int width, int height)
        {
            byte[] original = File.ReadAllBytes(path);
            var active = SceneManager.GetActiveScene();
            bool dirty = active.isDirty;
            int sceneCount = EditorSceneManager.previewSceneCount;
            var selection = Selection.activeObject;
            var renderTarget = RenderTexture.active;
            JObject result = JObject.FromObject(
                CapturePrefab.HandleCommand(
                    new JObject
                    {
                        ["prefab_path"] = path,
                        ["width"] = width,
                        ["height"] = height,
                    }
                )
            );
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.AreEqual(framing, (string)result["data"]["framing"]);
            Assert.IsTrue((bool)result["data"]["cleanup_completed"]);
            Assert.AreEqual(width, (int)result["data"]["width"]);
            Assert.AreEqual(height, (int)result["data"]["height"]);
            Assert.AreEqual(sceneCount, EditorSceneManager.previewSceneCount);
            Assert.AreEqual(active, SceneManager.GetActiveScene());
            Assert.AreEqual(dirty, active.isDirty);
            Assert.AreSame(selection, Selection.activeObject);
            Assert.AreSame(renderTarget, RenderTexture.active);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
            byte[] png = Convert.FromBase64String((string)result["data"]["imageBase64"]);
            var texture = new Texture2D(1, 1);
            try
            {
                Assert.IsTrue(texture.LoadImage(png));
                Assert.AreEqual(width, texture.width);
                Assert.AreEqual(height, texture.height);
                string evidence = Path.Combine(
                    "Library",
                    "MCPForUnity",
                    "TestEvidence",
                    "capture_" + QualitySettings.activeColorSpace,
                    TestContext.CurrentContext.Test.ID + ".png"
                );
                Directory.CreateDirectory(Path.GetDirectoryName(evidence));
                File.WriteAllBytes(evidence, png);
                TestContext.WriteLine(
                    $"PNG: {evidence}; color space={QualitySettings.activeColorSpace}; Editor DPI={EditorGUIUtility.pixelsPerPoint}; {width}x{height}"
                );
                return texture;
            }
            catch
            {
                Object.DestroyImmediate(texture);
                throw;
            }
        }

        [Test]
        public void FailureAfterInstantiationCleansPreviewScene()
        {
            var root = new GameObject("Empty");
            string path = _folder + "/Empty.prefab";
            try
            {
                PrefabUtility.SaveAsPrefabAsset(root, path);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
            int before = EditorSceneManager.previewSceneCount;
            var result = JObject.FromObject(CapturePrefab.HandleCommand(new JObject { ["prefab_path"] = path }));
            Assert.IsFalse((bool)result["success"]);
            Assert.IsTrue((bool)result["data"]["cleanup_completed"]);
            Assert.AreEqual(before, EditorSceneManager.previewSceneCount);
        }

        [TestCase("../outside.prefab", 128)]
        [TestCase("Assets/Missing.prefab", 4096)]
        [TestCase("Assets/Missing.prefab", 128)]
        public void InvalidRequestDoesNotCreatePreview(string path, int width)
        {
            int before = EditorSceneManager.previewSceneCount;
            var result = JObject.FromObject(CapturePrefab.HandleCommand(new JObject { ["prefab_path"] = path, ["width"] = width }));
            Assert.IsFalse((bool)result["success"]);
            Assert.AreEqual(before, EditorSceneManager.previewSceneCount);
        }
    }
}
