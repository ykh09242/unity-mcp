using System;
using System.IO;
using System.Security.Cryptography;
using MCPForUnity.Editor.Tools.Graphics;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using Object = UnityEngine.Object;

namespace VerifiedWorkflows
{
    public static class UrpCameraFixture
    {
        private const string ScenePath = "Assets/RendererBinding.unity";
        private const string CameraName = "FixtureCamera";
        private const int Size = 128;

        public static void Run()
        {
            string output = FixtureRun.OpenOutput("urp");
            var visible = ScriptableObject.CreateInstance<UniversalRendererData>();
            AssetDatabase.CreateAsset(visible, "Assets/VisibleRenderer.asset");
            var hidden = ScriptableObject.CreateInstance<UniversalRendererData>();
            hidden.opaqueLayerMask = 0;
            AssetDatabase.CreateAsset(hidden, "Assets/HiddenRenderer.asset");
            var pipeline = UniversalRenderPipelineAsset.Create(visible);
            AssetDatabase.CreateAsset(pipeline, "Assets/FixturePipeline.asset");
            using (var serialized = new SerializedObject(pipeline))
            {
                var renderers = serialized.FindProperty("m_RendererDataList");
                renderers.arraySize = 2;
                renderers.GetArrayElementAtIndex(1).objectReferenceValue = hidden;
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }
            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var camera = new GameObject(CameraName).AddComponent<Camera>();
            camera.transform.position = new Vector3(0, 0, -5);
            camera.orthographic = true;
            camera.orthographicSize = 2;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.blue;
            camera.allowHDR = false;
            camera.allowMSAA = false;
            camera.gameObject.AddComponent<UniversalAdditionalCameraData>().SetRenderer(1);
            var quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
            var shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null)
                throw new InvalidOperationException("The URP Unlit shader was not imported.");
            var material = new Material(shader);
            material.SetColor("_BaseColor", Color.red);
            AssetDatabase.CreateAsset(material, "Assets/FixtureSurface.mat");
            quad.GetComponent<MeshRenderer>().sharedMaterial = material;
            AssetDatabase.SaveAssets();
            if (!EditorSceneManager.SaveScene(scene, ScenePath))
                throw new InvalidOperationException("Could not save the fixture camera.");

            var broken = Capture(output, "unexpected", 1, hidden, false);
            camera = GameObject.Find(CameraName).GetComponent<Camera>();
            camera.GetComponent<UniversalAdditionalCameraData>().SetRenderer(-1);
            EditorSceneManager.MarkSceneDirty(camera.gameObject.scene);
            if (!EditorSceneManager.SaveScene(camera.gameObject.scene))
                throw new InvalidOperationException("Could not save the camera repair.");
            var repaired = Capture(output, "repaired", -1, visible, true);

            File.WriteAllText(
                Path.Combine(output, "renderer-bindings.json"),
                new JObject
                {
                    ["unity_version"] = Application.unityVersion,
                    ["scene"] = ScenePath,
                    ["scene_guid"] = AssetDatabase.AssetPathToGUID(ScenePath),
                    ["expected_renderer_index"] = 0,
                    ["graphics_device_type"] = SystemInfo.graphicsDeviceType.ToString(),
                    ["evidence"] = SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null ? "saved_camera_route" : "saved_camera_route_and_frame",
                    ["unexpected"] = broken,
                    ["repaired"] = repaired,
                }.ToString()
            );
        }

        private static JObject Capture(string output, string label, int configured, UniversalRendererData renderer, bool shouldShowQuad)
        {
            EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            var camera = GameObject.Find(CameraName).GetComponent<Camera>();
            var response = JObject.FromObject(ManageGraphics.HandleCommand(new JObject { ["action"] = "pipeline_get_diagnostics", ["target"] = CameraName }));
            int expectedIndex = configured == -1 ? 0 : configured;
            if (
                !response.Value<bool>("success")
                || response.SelectToken("data.camera.configuredRendererIndex")?.Value<int>() != configured
                || response.SelectToken("data.camera.effectiveRendererIndex")?.Value<int>() != expectedIndex
                || response.SelectToken("data.renderer.path")?.Value<string>() != AssetDatabase.GetAssetPath(renderer)
            )
                throw new InvalidOperationException("Unexpected camera diagnostic: " + response);

            var result = new JObject
            {
                ["diagnostics"] = response,
                ["renderer_opaque_layer_mask"] = renderer.opaqueLayerMask.value,
                ["camera_layer_mask"] = camera.cullingMask,
            };
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
            {
                result["capture"] = new JObject { ["status"] = "unavailable", ["reason"] = "null_graphics_device" };
                return result;
            }

            var request = new UniversalRenderPipeline.SingleCameraRequest();
            request.destination = RenderTexture.GetTemporary(Size, Size, 24, RenderTextureFormat.ARGB32);
            var previous = RenderTexture.active;
            var pixels = new Texture2D(Size, Size, TextureFormat.RGB24, false);
            try
            {
                RenderPipeline.SubmitRenderRequest(camera, request);
                RenderTexture.active = request.destination;
                pixels.ReadPixels(new Rect(0, 0, Size, Size), 0, 0);
                pixels.Apply();
                Color32 center = pixels.GetPixel(Size / 2, Size / 2);
                Color32 corner = pixels.GetPixel(0, 0);
                bool background = corner.b > 200 && corner.r < 25 && corner.g < 25;
                bool expectedCenter = shouldShowQuad ? center.r > 200 && center.g < 25 && center.b < 25 : center.b > 200 && center.r < 25 && center.g < 25;
                string image = label + ".png";
                byte[] png = pixels.EncodeToPNG();
                File.WriteAllBytes(Path.Combine(output, image), png);
                if (!background || !expectedCenter)
                    throw new InvalidOperationException($"Unexpected rendered {label} pixels: center {center}, corner {corner}.");
                using var hash = SHA256.Create();
                result["capture"] = new JObject
                {
                    ["status"] = "captured",
                    ["path"] = image,
                    ["width"] = Size,
                    ["height"] = Size,
                    ["sha256"] = BitConverter.ToString(hash.ComputeHash(png)).Replace("-", "").ToLowerInvariant(),
                    ["center_rgb"] = new JArray(center.r, center.g, center.b),
                    ["corner_rgb"] = new JArray(corner.r, corner.g, corner.b),
                };
                return result;
            }
            finally
            {
                RenderTexture.active = previous;
                RenderTexture.ReleaseTemporary(request.destination);
                Object.DestroyImmediate(pixels);
            }
        }
    }
}
