using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class UguiProjectionDiagnosticsTests
    {
        private GameObject root;
        private Camera camera;
        private RenderTexture texture;
        private string assetFolder;
        private readonly List<UnityEngine.Object> owned = new List<UnityEngine.Object>();

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("UI07_" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Canvas));
            owned.Add(root);
            root.GetComponent<Canvas>().renderMode = RenderMode.WorldSpace;
            ((RectTransform)root.transform).sizeDelta = new Vector2(2, 2);
            root.transform.position = new Vector3(0, 0, 5);
            var go = new GameObject("UI07Camera_" + Guid.NewGuid().ToString("N"), typeof(Camera));
            owned.Add(go);
            camera = go.GetComponent<Camera>();
            camera.enabled = false;
            camera.nearClipPlane = 0.3f;
            camera.farClipPlane = 30;
            camera.fieldOfView = 60;
            texture = new RenderTexture(320, 240, 24);
            owned.Add(texture);
            Assert.IsTrue(texture.Create(), "Owned graphics prerequisite.");
            camera.targetTexture = texture;
            camera.aspect = 4f / 3f;
        }

        [TearDown]
        public void TearDown()
        {
            if (assetFolder != null)
                AssetDatabase.DeleteAsset(assetFolder);
            assetFolder = null;
            foreach (var value in owned.AsEnumerable().Reverse())
                if (value != null)
                    UnityEngine.Object.DestroyImmediate(value);
            owned.Clear();
        }

        private GameObject Child(string name = "Panel", Transform parent = null)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent ?? root.transform, false);
            ((RectTransform)go.transform).sizeDelta = new Vector2(1, 0.5f);
            return go;
        }

        private JObject Send(JObject options = null, bool explicitCamera = true)
        {
            var request = new JObject { ["action"] = "diagnose_projection", ["target"] = root.GetInstanceIDCompat() };
            if (explicitCamera)
                request["camera"] = camera.gameObject.GetInstanceIDCompat();
            if (options != null)
                foreach (var property in options.Properties())
                    request[property.Name] = property.Value.DeepClone();
            return JObject.FromObject(ManageUGUI.HandleCommand(request));
        }

        private JObject Data(JObject options = null, bool explicitCamera = true)
        {
            var r = Send(options, explicitCamera);
            Assert.IsTrue(r.Value<bool>("success"), r.ToString());
            var d = (JObject)r["data"];
            Assert.AreEqual("ugui_projection_diagnostics", d.Value<string>("kind"));
            Assert.AreEqual(1, d.Value<int>("schema_version"));
            Assert.AreEqual("current_camera_properties", d["evidence"].Value<string>("observation"));
            return d;
        }

        private JObject NativeState(JObject response)
        {
            var canvas = root.GetComponent<Canvas>();
            return new JObject
            {
                ["render_mode"] = canvas.renderMode.ToString(),
                ["world_camera_id"] = canvas.worldCamera == null ? (JToken)JValue.CreateNull() : new JValue(canvas.worldCamera.GetInstanceIDCompat()),
                ["allow_dynamic_resolution"] = camera.allowDynamicResolution,
                ["use_dynamic_scale"] = texture.useDynamicScale,
                ["pixel_width"] = camera.pixelWidth,
                ["scaled_pixel_width"] = camera.scaledPixelWidth,
                ["pixel_height"] = camera.pixelHeight,
                ["scaled_pixel_height"] = camera.scaledPixelHeight,
                ["payload"] = response.DeepClone(),
            };
        }

        private static JObject Node(JObject data, string name) => (JObject)data["nodes"].Single(n => n.Value<string>("name") == name);

        private static string ExportDirectory()
        {
            string configured = Environment.GetEnvironmentVariable("UNITY_MCP_UI07_EXPORT_ROOT");
            if (configured == null)
                return Path.GetTempPath(); // Preserve ordinary non-QA test behavior.
            string project = Path.GetFullPath(Path.GetDirectoryName(Application.dataPath));
            string expected = Path.GetFullPath(Path.Combine(project, "..", "fixture-exports"));
            string actual = Path.GetFullPath(configured);
            Assert.IsTrue(string.Equals(expected, actual, StringComparison.OrdinalIgnoreCase), "Exact owned fixture export directory required.");
            Assert.IsTrue(
                string.Equals(project, Path.GetFullPath(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_PROJECT")), StringComparison.OrdinalIgnoreCase),
                "Exact native QA project binding required."
            );
            Assert.IsTrue(Directory.Exists(actual), "The fresh wrapper must precreate the owned fixture export directory.");
            for (string current = actual; current != null; current = Path.GetDirectoryName(current))
                if (Directory.Exists(current))
                    Assert.IsFalse((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0, "Linked fixture export directory refused.");
            return actual;
        }

        private static void Export(string name, JObject data) =>
            File.WriteAllText(Path.Combine(ExportDirectory(), "ui07-" + name + ".json"), data.ToString(Newtonsoft.Json.Formatting.None));

        [TestCase(false)]
        [TestCase(true)]
        public void AmbiguousProjectionTargetNameOrPathRefusesWithConstantError(bool fullPath)
        {
            string name = "UI07_PRIVATE_LOOKUP_" + Guid.NewGuid().ToString("N");
            var first = Child(name);
            var second = Child(name);
            Assert.AreNotEqual(first.GetInstanceIDCompat(), second.GetInstanceIDCompat());
            Assert.IsTrue(root.scene.IsValid() && root.scene.isLoaded, "Owned loaded scene prerequisite.");
            Assert.AreEqual(2, root.transform.Cast<Transform>().Count(t => t.name == name));
            string target = fullPath ? root.name + "/" + name : name;

            var response = Send(new JObject { ["target"] = target });

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("invalid_or_ambiguous_projection_target", response.Value<string>("error"));
            StringAssert.DoesNotContain(name, response.ToString());
            Assert.Less(response.ToString().Length, 200);
            Export(fullPath ? "refusal-ambiguous-path" : "refusal-ambiguous-name", response);
        }

        [Test]
        public void PersistentPrefabAssetProjectionTargetRefusesWithoutChangingSource()
        {
            string folder = "UI07_Refusal_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder);
            assetFolder = "Assets/" + folder;
            string path = assetFolder + "/Projection.prefab";
            string absolutePath = Path.Combine(Application.dataPath, folder, "Projection.prefab");
            var saved = PrefabUtility.SaveAsPrefabAsset(root, path);
            Assert.IsNotNull(saved, "Owned persistent prefab prerequisite.");
            Assert.IsTrue(EditorUtility.IsPersistent(saved), "The target must be the asset, not its scene source.");
            byte[] before = File.ReadAllBytes(absolutePath),
                meta = File.ReadAllBytes(absolutePath + ".meta");
            string guid = AssetDatabase.AssetPathToGUID(path);
            string cameraJson = EditorJsonUtility.ToJson(camera);
            bool assetDirty = EditorUtility.IsDirty(saved);
            var active = RenderTexture.active;
            var selection = Selection.activeObject;

            var response = Send(new JObject { ["target"] = saved.GetInstanceIDCompat() });

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("invalid_or_ambiguous_projection_target", response.Value<string>("error"));
            StringAssert.DoesNotContain(path, response.ToString());
            Assert.Less(response.ToString().Length, 200);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(absolutePath));
            CollectionAssert.AreEqual(meta, File.ReadAllBytes(absolutePath + ".meta"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(assetDirty, EditorUtility.IsDirty(saved));
            Assert.AreEqual(cameraJson, EditorJsonUtility.ToJson(camera));
            Assert.AreSame(active, RenderTexture.active);
            Assert.AreSame(selection, Selection.activeObject);
            Export("refusal-persistent-asset", response);
        }

        [Test]
        public void WorldCanvasRequiresExplicitViewEvenWithEventCamera()
        {
            root.GetComponent<Canvas>().worldCamera = camera;
            Assert.AreEqual("camera_required", Send(explicitCamera: false).Value<string>("error"));
            var d = Data();
            Assert.AreEqual("explicit_world_view", d["context"].Value<string>("camera_selection"));
            Assert.AreEqual(camera.GetInstanceIDCompat(), d["context"].Value<int>("assigned_camera_id"));
            Export("world", d);
        }

        [Test]
        public void ScreenCameraUsesAssignedCameraAndRejectsForeignOverride()
        {
            var canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera = camera;
            Canvas.ForceUpdateCanvases();
            var assignedResponse = Send(explicitCamera: false);
            Export("screen-camera-assigned-native", NativeState(assignedResponse));
            Assert.AreEqual(RenderMode.ScreenSpaceCamera, canvas.renderMode, "Realized assigned-camera mode prerequisite.");
            Assert.AreSame(camera, canvas.worldCamera, "Realized assigned-camera getter prerequisite.");
            Assert.IsTrue(assignedResponse.Value<bool>("success"), assignedResponse.ToString());
            Assert.AreEqual(camera.GetInstanceIDCompat(), assignedResponse["data"]["context"].Value<int>("camera_id"));
            var other = new GameObject("UI07OtherCamera", typeof(Camera));
            owned.Add(other);
            Assert.AreEqual("camera_canvas_mismatch", Send(new JObject { ["camera"] = other.GetInstanceIDCompat() }).Value<string>("error"));
            canvas.worldCamera = null;
            var clearedResponse = Send(explicitCamera: false);
            Export("screen-camera-cleared-native", NativeState(clearedResponse));
            Assert.IsNull(canvas.worldCamera, "Native cleared-camera getter prerequisite.");
            if (canvas.renderMode == RenderMode.ScreenSpaceCamera)
            {
                Assert.IsTrue(clearedResponse.Value<bool>("success"), clearedResponse.ToString());
                Assert.AreEqual("unavailable", clearedResponse["data"].Value<string>("status"));
                Assert.AreEqual("camera_unassigned", clearedResponse["data"].Value<string>("unavailable_reason"));
            }
            else
            {
                Assert.AreEqual(RenderMode.ScreenSpaceOverlay, canvas.renderMode, "Only the observed supported native fallback mode is admitted.");
                Assert.IsFalse(clearedResponse.Value<bool>("success"));
                Assert.AreEqual("unsupported_projection_canvas_mode", clearedResponse.Value<string>("error"));
            }
        }

        [Test]
        public void OverlayRemainsOnExistingDiagnosticPath()
        {
            root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            Assert.AreEqual("unsupported_projection_canvas_mode", Send().Value<string>("error"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FrontProjectionMatchesIndependentNativeViewport(bool orthographic)
        {
            camera.orthographic = orthographic;
            camera.orthographicSize = 3;
            var panel = Child();
            panel.transform.localRotation = Quaternion.Euler(15, 20, 5);
            var corners = new Vector3[4];
            ((RectTransform)panel.transform).GetWorldCorners(corners);
            Assert.IsTrue(corners.All(p => camera.WorldToViewportPoint(p).z > camera.nearClipPlane));
            var d = Data();
            var node = Node(d, panel.name);
            Assert.AreEqual("inside", node.Value<string>("frustum_relation"));
            var polygon = (JArray)node["clipped_viewport_polygon"];
            Assert.AreEqual(4, polygon.Count);
            foreach (var corner in corners)
            {
                var expected = camera.WorldToViewportPoint(corner);
                Assert.IsTrue(polygon.Any(p => Math.Abs(p[0].Value<double>() - expected.x) < 0.00001 && Math.Abs(p[1].Value<double>() - expected.y) < 0.00001));
            }
            Export(orthographic ? "orthographic" : "perspective", d);
        }

        [Test]
        public void BehindCameraNeverPublishesMirroredScreenBounds()
        {
            root.transform.position = new Vector3(0, 0, -5);
            var d = Data();
            var node = Node(d, root.name);
            Assert.AreEqual("behind", node.Value<string>("camera_plane_relation"));
            Assert.AreEqual("outside", node.Value<string>("frustum_relation"));
            Assert.AreEqual(JTokenType.Null, node["unclipped_viewport_bounds"].Type);
            Assert.AreEqual(0, node["clipped_viewport_polygon"].Count());
            Export("behind", d);
        }

        [Test]
        public void NearCrossingClipsBeforePerspectiveDivide()
        {
            root.transform.position = new Vector3(0, 0, 0.35f);
            root.transform.rotation = Quaternion.Euler(0, 65, 0);
            var corners = new Vector3[4];
            ((RectTransform)root.transform).GetWorldCorners(corners);
            var depths = corners.Select(p => camera.WorldToViewportPoint(p).z).ToArray();
            Assert.Less(depths.Min(), 0);
            Assert.Greater(depths.Max(), camera.nearClipPlane);
            var d = Data();
            var node = Node(d, root.name);
            Assert.AreEqual("crossing", node.Value<string>("camera_plane_relation"));
            Assert.AreEqual("partial", node.Value<string>("frustum_relation"));
            Assert.IsTrue(node["cut_planes"].Any(p => p.Value<string>() == "near"));
            Assert.That(node["clipped_viewport_polygon"].Count(), Is.InRange(3, 10));
            Assert.AreEqual(JTokenType.Null, node["unclipped_viewport_bounds"].Type);
            foreach (var point in node["clipped_viewport_polygon"])
            foreach (var coordinate in point)
                Assert.That(coordinate.Value<double>(), Is.InRange(0d, 1d));
            Export("near-crossing", d);
        }

        [TestCase(0, 0, 50, "far")]
        [TestCase(30, 0, 5, "right")]
        public void FarAndViewportExclusionHaveDifferentPlaneEvidence(float x, float y, float z, string plane)
        {
            root.transform.position = new Vector3(x, y, z);
            var d = Data();
            Assert.AreEqual("outside", Node(d, root.name).Value<string>("frustum_relation"));
            Assert.AreEqual(plane, Node(d, root.name).Value<string>("rejected_by"));
            Export("outside-" + plane, d);
        }

        [Test]
        public void HugeQuadEnclosingViewportDoesNotNeedAnInsideCorner()
        {
            ((RectTransform)root.transform).sizeDelta = new Vector2(100, 100);
            var corners = new Vector3[4];
            ((RectTransform)root.transform).GetWorldCorners(corners);
            Assert.IsFalse(corners.Select(camera.WorldToViewportPoint).Any(p => p.x > 0 && p.x < 1 && p.y > 0 && p.y < 1));
            var d = Data();
            var node = Node(d, root.name);
            Assert.AreEqual("partial", node.Value<string>("frustum_relation"));
            Assert.That(node["clipped_viewport_polygon"].Count(), Is.InRange(4, 10));
            Export("enclosing", d);
        }

        [TestCase(0f)]
        [TestCase(-1f)]
        public void ZeroAndMirroredSizeNeverProduceInvalidNumbers(float scale)
        {
            root.transform.localScale = new Vector3(scale, 1, 1);
            var d = Data();
            Assert.AreEqual(scale == 0 ? "degenerate" : "inside", Node(d, root.name).Value<string>("frustum_relation"));
            StringAssert.DoesNotContain("NaN", d.ToString());
            Export(scale == 0 ? "degenerate" : "mirrored", d);
        }

        [Test]
        public void NonFullTextureViewportKeepsNormalizedGeometryWithoutPixelClaim()
        {
            camera.rect = new Rect(0.2f, 0.1f, 0.5f, 0.6f);
            var before = camera.rect;
            var d = Data();
            var output = d["context"]["output"];
            Assert.AreEqual("render_texture", output.Value<string>("kind"));
            Assert.AreEqual("partial", d.Value<string>("status"));
            Assert.AreEqual("unavailable", output.Value<string>("pixel_status"));
            Assert.AreEqual(JTokenType.Null, output["viewport_pixels"].Type);
            Assert.AreEqual(JTokenType.Null, output["camera_rect_ignored"].Type);
            Assert.AreEqual("inside", Node(d, root.name).Value<string>("frustum_relation"));
            Assert.That(Node(d, root.name)["clipped_viewport_polygon"].Count(), Is.InRange(3, 10));
            Assert.AreEqual(JTokenType.Null, Node(d, root.name)["clipped_target_pixel_bounds"].Type);
            Assert.AreEqual(before, camera.rect);
            Assert.AreSame(texture, camera.targetTexture);
            Export("texture-viewport", d);
        }

        [Test]
        public void MissingSecondaryDisplayKeepsNormalizedGeometryWithoutInventedPixels()
        {
            camera.targetTexture = null;
            camera.targetDisplay = 7;
            Assert.Less(Display.displays.Length, 8, "This fixture requires an absent secondary display, not an activated monitor.");
            var d = Data();
            Assert.AreEqual("partial", d.Value<string>("status"));
            Assert.AreEqual("display", d["context"]["output"].Value<string>("kind"));
            Assert.AreEqual(7, d["context"]["output"].Value<int>("target_display"));
            Assert.AreEqual("unavailable", Node(d, root.name).Value<string>("pixel_status"));
            Assert.AreEqual("inside", Node(d, root.name).Value<string>("frustum_relation"));
            Export("secondary-unavailable", d);
        }

        [Test]
        public void InvalidProjectionMatrixNeverBecomesCleanGeometry()
        {
            camera.projectionMatrix = Matrix4x4.zero;
            var d = Data();
            Assert.AreEqual("unavailable", d.Value<string>("status"));
            Assert.AreEqual(0, d["nodes"].Count());
            Export("invalid-matrix", d);
        }

        [Test]
        public void ObservedScaleIsNotAppliedTwiceAndDisabledNestedCanvasFallsBack()
        {
            root.GetComponent<Canvas>().scaleFactor = 2;
            var nested = Child("DisabledCanvas");
            var canvas = nested.AddComponent<Canvas>();
            canvas.enabled = false;
            var panel = Child("ScaledChild", nested.transform);
            panel.transform.localScale = new Vector3(2, 1, 1);
            var corners = new Vector3[4];
            ((RectTransform)panel.transform).GetWorldCorners(corners);
            var expected = corners.Select(camera.WorldToViewportPoint).ToArray();
            var d = Data();
            var polygon = Node(d, panel.name)["clipped_viewport_polygon"];
            Assert.AreEqual(4, polygon.Count());
            Assert.That(
                polygon.Max(p => p[0].Value<double>()) - polygon.Min(p => p[0].Value<double>()),
                Is.EqualTo(expected.Max(p => p.x) - expected.Min(p => p.x)).Within(0.00001)
            );
        }

        [Test]
        public void ScreenCameraPhysicalSizeScalerUsesObservedWorldCornersOnly()
        {
            var canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera = camera;
            canvas.planeDistance = 5;
            var scalerType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler");
            Assert.IsNotNull(scalerType, "Installed uGUI scaler prerequisite.");
            var scaler = root.AddComponent(scalerType);
            var mode = scalerType.GetProperty("uiScaleMode");
            mode.SetValue(scaler, Enum.Parse(mode.PropertyType, "ConstantPhysicalSize"));
            var unit = scalerType.GetProperty("physicalUnit");
            unit.SetValue(scaler, Enum.Parse(unit.PropertyType, "Points"));
            scalerType.GetProperty("fallbackScreenDPI").SetValue(scaler, 144f);
            scalerType.GetMethod("Handle", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance).Invoke(scaler, null);
            float currentDpi = Screen.dpi;
            float effectiveDpi = currentDpi == 0 ? 144f : currentDpi;
            Assert.Greater(effectiveDpi, 0, "Recognized actual or owned fallback DPI prerequisite.");
            Assert.That(canvas.scaleFactor, Is.EqualTo(effectiveDpi / 72f).Within(0.000001f), "Apply the actual owned engine scaler before observing it.");
            var panel = Child("PhysicalPanel");
            ((RectTransform)panel.transform).sizeDelta = new Vector2(80 / canvas.scaleFactor, 40 / canvas.scaleFactor);
            Canvas.ForceUpdateCanvases();
            Assert.Greater(canvas.scaleFactor, 0, "Actual observed physical-size scaler prerequisite.");
            var corners = new Vector3[4];
            ((RectTransform)panel.transform).GetWorldCorners(corners);
            var expected = corners.Select(camera.WorldToViewportPoint).ToArray();
            Assert.IsTrue(expected.All(v => v.z > 0 && v.x > 0 && v.x < 1 && v.y > 0 && v.y < 1));
            var d = Data(explicitCamera: false);
            Assert.AreEqual(canvas.scaleFactor, d["context"].Value<float>("scale_factor"));
            var polygon = Node(d, panel.name)["clipped_viewport_polygon"];
            Assert.AreEqual(4, polygon.Count());
            foreach (var point in expected)
                Assert.IsTrue(polygon.Any(v => Math.Abs(v[0].Value<double>() - point.x) < 0.00001 && Math.Abs(v[1].Value<double>() - point.y) < 0.00001));
            Export("physical-size", d);
        }

        [Test]
        public void LocalNameLimitDoesNotConsumeSharedCharacterAllowance()
        {
            Child(new string('n', 200000));
            Child("Later");
            var d = Data();
            Assert.IsNotNull(Node(d, "Later"));
            Assert.IsFalse(d["traversal"]["reasons"].Any(v => v.Value<string>() == "max_characters"));
            Assert.That(d["traversal"].Value<int>("characters"), Is.LessThanOrEqualTo(65536));
        }

        [Test]
        public void DeepPhysicalTraversalIsBoundedAcrossPlainTransformGap()
        {
            var gap = new GameObject("PlainGap");
            gap.transform.SetParent(root.transform, false);
            var deep = gap.transform;
            for (int i = 0; i < 160; i++)
                deep = Child("Deep" + i, deep).transform;
            var d = Data(new JObject { ["max_depth"] = 24, ["max_evaluations"] = 200000 });
            Assert.AreEqual("partial", d.Value<string>("status"));
            Assert.IsTrue(d["traversal"]["reasons"].Any(v => v.Value<string>() == "max_depth"));
            Assert.AreEqual(25, d["traversal"].Value<int>("nodes_visited"));
            Assert.AreEqual(24, d["nodes"].Count());
            Assert.AreEqual("/0/0", Node(d, "Deep0").Value<string>("diagnostic_path"));
            Assert.AreEqual(0, Node(d, "Deep0").Value<int>("parent_index"));
            Export("deep-gap", d);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OffCentreAndObliqueCurrentMatricesMatchNativeViewport(bool oblique)
        {
            if (oblique)
                camera.projectionMatrix = camera.CalculateObliqueMatrix(new Vector4(0.1f, 0, -1, -0.3f));
            else
            {
                camera.usePhysicalProperties = true;
                camera.lensShift = new Vector2(0.1f, -0.1f);
            }
            var p = Child("MatrixPanel");
            var corners = new Vector3[4];
            ((RectTransform)p.transform).GetWorldCorners(corners);
            var expected = corners.Select(camera.WorldToViewportPoint).ToArray();
            Assert.IsTrue(expected.All(v => v.x > 0 && v.x < 1 && v.y > 0 && v.y < 1 && v.z > 0));
            var before = camera.projectionMatrix;
            var d = Data();
            var polygon = Node(d, p.name)["clipped_viewport_polygon"];
            Assert.AreEqual(4, polygon.Count());
            foreach (var point in expected)
                Assert.IsTrue(polygon.Any(v => Math.Abs(v[0].Value<double>() - point.x) < 0.00001 && Math.Abs(v[1].Value<double>() - point.y) < 0.00001));
            Assert.AreEqual(before, camera.projectionMatrix);
            Export(oblique ? "oblique" : "lens-shift", d);
        }

        [Test]
        public void PositiveViewportSliverSurvivesWithoutPixelTolerance()
        {
            camera.orthographic = true;
            camera.orthographicSize = 1;
            camera.aspect = 1;
            root.transform.position = new Vector3(1.99999f, 0, 5);
            var corners = new Vector3[4];
            ((RectTransform)root.transform).GetWorldCorners(corners);
            double left = corners.Select(camera.WorldToViewportPoint).Min(v => v.x);
            Assert.Greater(left, 0.9999);
            Assert.Less(left, 1, "Actual observed positive sliver prerequisite.");
            var d = Data();
            var n = Node(d, root.name);
            Assert.AreEqual("partial", n.Value<string>("frustum_relation"));
            Assert.Greater(n["clipped_viewport_polygon"].Max(v => v[0].Value<double>()) - n["clipped_viewport_polygon"].Min(v => v[0].Value<double>()), 0);
            Export("positive-sliver", d);
        }

        [Test]
        public void SurrogateBoundaryAndInactiveFilteringAreExplicit()
        {
            Child(new string('x', 255) + "\U0001F680tail");
            Child("Inactive").SetActive(false);
            Child("Later");
            var d = Data();
            Assert.IsNotNull(Node(d, "Later"));
            Assert.IsFalse(d["nodes"].Any(n => n.Value<string>("name") == "Inactive"));
            var truncated = d["nodes"].Single(n => n.Value<string>("name").StartsWith("xxx", StringComparison.Ordinal));
            Assert.AreEqual(255, truncated.Value<string>("name").Length);
            Assert.IsFalse(char.IsSurrogate(truncated.Value<string>("name").Last()));
            Assert.IsNotNull(Node(Data(new JObject { ["include_inactive"] = true }), "Inactive"));
            Export("surrogates", d);
        }

        [Test]
        public void DynamicPixelRouteIsUnknownWhileNormalizedProjectionRemainsAvailable()
        {
            camera.allowDynamicResolution = true;
            var response = Send();
            Export("dynamic-native", NativeState(response));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var d = (JObject)response["data"];
            bool observedDynamic = camera.allowDynamicResolution;
            Assert.AreEqual(observedDynamic, d["context"].Value<bool>("allow_dynamic_resolution"));
            Assert.AreEqual("inside", Node(d, root.name).Value<string>("frustum_relation"));
            if (observedDynamic)
            {
                Assert.AreEqual("partial", d.Value<string>("status"));
                Assert.AreEqual("unavailable", Node(d, root.name).Value<string>("pixel_status"));
            }
            else
            {
                Assert.AreEqual("complete", d.Value<string>("status"), "Requested dynamic state was not realized; do not manufacture it in the payload.");
                Assert.AreEqual("available", Node(d, root.name).Value<string>("pixel_status"));
                camera.rect = new Rect(0.1f, 0.2f, 0.4f, 0.5f);
                d = Data();
                Assert.AreEqual("partial", d.Value<string>("status"));
                Assert.AreEqual("unavailable", Node(d, root.name).Value<string>("pixel_status"));
                Export("dynamic-unrealized-viewport-control", d);
            }
            Export("dynamic-route", d);
        }

        [Test]
        public void WideDeepTreesAndAllWorkCategoriesStayBounded()
        {
            for (int i = 0; i < 1100; i++)
                Child("Wide" + i);
            var deep = root.transform;
            for (int i = 0; i < 160; i++)
                deep = Child("Deep" + i, deep).transform;
            var d = Data(
                new JObject
                {
                    ["max_nodes"] = 1000,
                    ["max_depth"] = 24,
                    ["max_evaluations"] = 200000,
                }
            );
            Assert.AreEqual("partial", d.Value<string>("status"));
            Assert.LessOrEqual(d["nodes"].Count(), 1000);
            Assert.LessOrEqual(d["traversal"].Value<int>("evaluations"), 200000);
            Assert.AreEqual(
                d["traversal"].Value<int>("evaluations"),
                ((JObject)d["traversal"]["evaluation_counts"]).Properties().Sum(p => p.Value.Value<int>())
            );
            Export("maximum", d);
        }

        [TestCase("max_nodes", 1)]
        [TestCase("max_depth", 0)]
        [TestCase("max_evaluations", 1)]
        [TestCase("max_characters", 1)]
        public void ExhaustionIsPartialAndDoesNotPublishUnchargedProjection(string key, int value)
        {
            Child();
            var d = Data(new JObject { [key] = value });
            Assert.AreEqual("partial", d.Value<string>("status"));
            Assert.IsTrue(d["traversal"].Value<bool>("truncated"));
            Assert.IsTrue(d["traversal"]["reasons"].Any(v => v.Value<string>() == key));
            if (key == "max_evaluations")
                Assert.IsFalse(d["nodes"].Any(n => n["world_corners"]?.Type == JTokenType.Array));
            Export("partial-" + key, d);
        }

        [Test]
        public void WorstCaseUnicodeAndClippedVerticesRespectActualSerializedLimit()
        {
            string unicode = new string('\u754c', 252);
            for (int i = 0; i < 999; i++)
            {
                var child = Child(unicode + i.ToString(System.Globalization.CultureInfo.InvariantCulture));
                ((RectTransform)child.transform).sizeDelta = new Vector2(100, 70);
                child.transform.localPosition = new Vector3(0.1234567f, 0.2345678f, -4.25f);
                child.transform.localRotation = Quaternion.Euler(30, 60, 15);
            }
            var response = Send(
                new JObject
                {
                    ["max_nodes"] = 1000,
                    ["max_depth"] = 24,
                    ["max_evaluations"] = 200000,
                    ["max_characters"] = 262144,
                }
            );
            var proof = new JObject { ["success"] = response.Value<bool>("success"), ["limit_bytes"] = 2 * 1024 * 1024 };
            if (response.Value<bool>("success"))
            {
                var data = (JObject)response["data"];
                int bytes = System.Text.Encoding.UTF8.GetByteCount(data.ToString(Newtonsoft.Json.Formatting.None));
                Assert.LessOrEqual(bytes, 2 * 1024 * 1024);
                Assert.AreEqual(1000, data["traversal"].Value<int>("nodes_visited"));
                Assert.Greater(data["traversal"].Value<int>("characters"), 250000, "Actual Unicode composition prerequisite.");
                Assert.LessOrEqual(data["traversal"].Value<int>("evaluations"), 200000);
                proof["serialized_bytes"] = bytes;
                Export("unicode-vertices", data);
            }
            else
            {
                Assert.AreEqual("projection_artifact_limit", response.Value<string>("error"));
                Assert.Less(response.ToString().Length, 200);
                proof["error"] = response.Value<string>("error");
            }
            File.WriteAllText(Path.Combine(ExportDirectory(), "ui07limit-unicode.json"), proof.ToString());
        }

        [Test]
        public void NativeSerializationGuardRefusesOversizeProducerDataWithConstantError()
        {
            var type = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiProjectionDiagnostics");
            Assert.IsNotNull(type, "The new artifact boundary must be present.");
            var flags = System.Reflection.BindingFlags.NonPublic;
            var optionType = type.GetNestedType("Options", flags);
            var options = Activator.CreateInstance(optionType, true);
            var budgetType = type.GetNestedType("Budget", flags);
            var budget = Activator.CreateInstance(
                budgetType,
                flags | System.Reflection.BindingFlags.Instance,
                null,
                new[] { options },
                System.Globalization.CultureInfo.InvariantCulture
            );
            var nodes = new JArray(new JObject { ["name"] = new string('x', 2 * 1024 * 1024) });
            int bytes = System.Text.Encoding.UTF8.GetByteCount(nodes.ToString(Newtonsoft.Json.Formatting.None));
            Assert.Greater(bytes, 2 * 1024 * 1024, "Measured producer composition, not estimated JSON overhead.");
            var result = type.GetMethod("Finish", flags | System.Reflection.BindingFlags.Static).Invoke(null, new[] { null, nodes, budget, "partial", null });
            var response = JObject.FromObject(result);
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("projection_artifact_limit", response.Value<string>("error"));
            Assert.Less(response.ToString().Length, 200);
            File.WriteAllText(
                Path.Combine(ExportDirectory(), "ui07limit-native-oversize.json"),
                new JObject
                {
                    ["producer_node_bytes"] = bytes,
                    ["limit_bytes"] = 2 * 1024 * 1024,
                    ["error"] = response.Value<string>("error"),
                }.ToString()
            );
        }

        private static readonly object[] InvalidOptions =
        {
            new object[] { "max_nodes", "0" },
            new object[] { "max_nodes", "1001" },
            new object[] { "max_depth", "-1" },
            new object[] { "max_depth", "25" },
            new object[] { "max_evaluations", "0" },
            new object[] { "max_evaluations", "200001" },
            new object[] { "max_characters", "0" },
            new object[] { "max_characters", "262145" },
            new object[] { "max_nodes", "true" },
            new object[] { "max_depth", "1.5" },
            new object[] { "max_nodes", "999999999999999999999999" },
            new object[] { "include_inactive", "1" },
            new object[] { "max__nodes", "false" },
            new object[] { "resolutions", "[]" },
            new object[] { "properties", "{}" },
        };

        [TestCaseSource(nameof(InvalidOptions))]
        public void InvalidOptionsRefuseBeforeMissingTargetLookup(string key, string json)
        {
            var r = Send(new JObject { ["target"] = "MISSING_PRIVATE_SENTINEL", [key] = JToken.Parse(json) });
            Assert.IsFalse(r.Value<bool>("success"));
            Assert.AreEqual("invalid_projection_request", r.Value<string>("error"));
            StringAssert.DoesNotContain("PRIVATE_SENTINEL", r.ToString());
        }

        [Test]
        public void DuplicateAliasesRefuseEvenWhenValuesAreEqual()
        {
            var r = Send(
                new JObject
                {
                    ["max_nodes"] = 10,
                    ["maxNodes"] = 10,
                    ["target"] = "MISSING_PRIVATE_SENTINEL",
                }
            );
            Assert.AreEqual("invalid_projection_request", r.Value<string>("error"));
            StringAssert.DoesNotContain("PRIVATE_SENTINEL", r.ToString());
        }

        [Test]
        public void OwnedPrefabSourceBytesAndGuidsRemainUnchanged()
        {
            string folder = "UI07_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder);
            assetFolder = "Assets/" + folder;
            string path = assetFolder + "/Projection.prefab";
            string absolutePath = Path.Combine(Application.dataPath, folder, "Projection.prefab");
            var saved = PrefabUtility.SaveAsPrefabAsset(root, path);
            Assert.IsNotNull(saved);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(saved);
            owned.Add(instance);
            root = instance;
            byte[] before = File.ReadAllBytes(absolutePath),
                meta = File.ReadAllBytes(absolutePath + ".meta");
            string guid = AssetDatabase.AssetPathToGUID(path);
            string cameraJson = EditorJsonUtility.ToJson(camera);
            string rootJson = EditorJsonUtility.ToJson(root.transform);
            string canvasJson = EditorJsonUtility.ToJson(root.GetComponent<Canvas>());
            bool dirty = root.scene.isDirty,
                assetDirty = EditorUtility.IsDirty(saved);
            int objects = root.transform.childCount;
            var d = Data();
            Assert.IsTrue(Send(new JObject { ["max_nodes"] = 1 }).Value<bool>("success"));
            Assert.IsFalse(Send(new JObject { ["resolutions"] = new JArray() }).Value<bool>("success"));
            CollectionAssert.AreEqual(before, File.ReadAllBytes(absolutePath));
            CollectionAssert.AreEqual(meta, File.ReadAllBytes(absolutePath + ".meta"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(cameraJson, EditorJsonUtility.ToJson(camera));
            Assert.AreEqual(rootJson, EditorJsonUtility.ToJson(root.transform));
            Assert.AreEqual(canvasJson, EditorJsonUtility.ToJson(root.GetComponent<Canvas>()));
            Assert.AreEqual(dirty, root.scene.isDirty);
            Assert.AreEqual(assetDirty, EditorUtility.IsDirty(saved));
            Assert.AreEqual(objects, root.transform.childCount);
            Export("source-invariants", d);
        }

        [Test]
        public void SuccessPartialAndRefusalPreserveAllOwnedSourceState()
        {
            var panel = Child();
            var inputType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.InputField");
            if (inputType != null)
            {
                var input = panel.AddComponent(inputType);
                inputType.GetProperty("text").SetValue(input, "UI07_PRIVATE_SENTINEL");
            }
            string before = EditorJsonUtility.ToJson(camera),
                rect = EditorJsonUtility.ToJson(panel.transform),
                canvas = EditorJsonUtility.ToJson(root.GetComponent<Canvas>());
            bool sceneDirty = root.scene.isDirty,
                componentDirty = EditorUtility.IsDirty(camera);
            var active = RenderTexture.active;
            var selection = Selection.activeObject;
            foreach (
                var options in new[]
                {
                    new JObject(),
                    new JObject { ["max_nodes"] = 1 },
                    new JObject { ["resolutions"] = new JArray() },
                }
            )
                StringAssert.DoesNotContain("UI07_PRIVATE_SENTINEL", Send(options).ToString());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(camera));
            Assert.AreEqual(rect, EditorJsonUtility.ToJson(panel.transform));
            Assert.AreEqual(canvas, EditorJsonUtility.ToJson(root.GetComponent<Canvas>()));
            Assert.AreEqual(sceneDirty, root.scene.isDirty);
            Assert.AreEqual(componentDirty, EditorUtility.IsDirty(camera));
            Assert.AreSame(active, RenderTexture.active);
            Assert.AreSame(selection, Selection.activeObject);
        }
    }
}
