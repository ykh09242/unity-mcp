using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>One static asset preview. Project initialization and locale selection are not assumed.</summary>
    [McpForUnityTool(
        "capture_prefab",
        Group = "prefabs",
        Description = "Capture a prefab asset as a PNG in an isolated preview: root RectTransform UI or renderer bounds, 64..2048 pixels. Requires Edit Mode and graphics; no runtime initialization."
    )]
    public static class CapturePrefab
    {
        public static object HandleCommand(JObject parameters)
        {
            PreviewRenderUtility preview = null;
            Texture2D pixels = null;
            RenderTexture target = null;
            Scene scene = default;
            byte[] png = null;
            string error = null;
            string path = null;
            string framing = null;
            JObject uiContext = null;
            var limitations = new List<string>
            {
                "Static preview; runtime initialization, animation, button interaction and locale switching are not performed. Prefab edit-time callbacks may execute.",
            };
            int width = 0,
                height = 0;
            bool cleanupCompleted = false;
            try
            {
                if (
                    parameters == null
                    || parameters.Properties().Any(p => p.Name != "prefab_path" && p.Name != "width" && p.Name != "height" && p.Name != "ui_parent")
                )
                    throw new ArgumentException("Expected prefab_path, width, height and optional ui_parent only.");
                path = parameters["prefab_path"]?.Type == JTokenType.String ? (string)parameters["prefab_path"] : null;
                if (
                    string.IsNullOrEmpty(path)
                    || path.Length > 4096
                    || !(path.StartsWith("Assets/", StringComparison.Ordinal) || path.StartsWith("Packages/", StringComparison.Ordinal))
                    || !path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)
                    || path.Contains('\\')
                    || path.Split('/').Any(p => p == "" || p == "." || p == "..")
                )
                    throw new ArgumentException("prefab_path must be a project-relative .prefab asset path.");
                width = Dimension(parameters, "width", 1024);
                height = Dimension(parameters, "height", 1024);
                if (
                    EditorApplication.isPlayingOrWillChangePlaymode
                    || EditorStateCache.GetActualIsCompiling()
                    || EditorApplication.isUpdating
                    || EditorStateCache.IsDomainReloadPending
                )
                    return new ErrorResponse("editor_not_ready", new { retry_after_ms = 500 });
                if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                    return new ErrorResponse("graphics_unavailable");
                var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
                if (asset == null || !PrefabUtility.IsPartOfPrefabAsset(asset))
                    throw new ArgumentException("Prefab asset was not found.");
                var uiParent = PrefabCaptureContext.ResolveParent(parameters["ui_parent"], asset);

                preview = new PreviewRenderUtility();
                var camera = preview.camera;
                scene = camera.gameObject.scene;
                var instance = preview.InstantiatePrefabInScene(asset);
                // Preview scenes have their own culling mask; the active scene mask produces blank captures.
                camera.overrideSceneCullingMask = EditorSceneManager.GetSceneCullingMask(scene);
                camera.clearFlags = CameraClearFlags.SolidColor;
                camera.backgroundColor = new Color(0.12f, 0.12f, 0.12f, 1f);
                camera.allowHDR = false;
                camera.allowMSAA = false;
                camera.orthographic = true;
                camera.aspect = (float)width / height;
                camera.nearClipPlane = 0.01f;
                camera.farClipPlane = 10000f;
                camera.cullingMask = -1;
                preview.ambientColor = Color.gray;

                bool opened = false;
                RenderTexture previewTarget = null;
                try
                {
                    // Begin/End retain Unity's preview lighting and render-state ownership. Its point-sized
                    // scratch target is not the output: a pixel-sized target avoids fractional Editor DPI rounding.
                    preview.BeginPreview(new Rect(0, 0, 1, 1), GUIStyle.none);
                    opened = true;
                    previewTarget = camera.targetTexture;
                    // PreviewRenderUtility uses a linear UNorm target, then converts in EndStaticPreview.
                    // Request sRGB writes here instead, so ReadPixels/PNG receive display-encoded bytes in
                    // Linear projects. Gamma projects do not perform the conversion (RenderTextureReadWrite).
                    target = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB)
                    {
                        hideFlags = HideFlags.HideAndDontSave,
                        antiAliasing = 1,
                    };
                    if (!target.Create())
                        throw new InvalidOperationException("Could not create the prefab capture render target.");
                    camera.targetTexture = target;
                    camera.pixelRect = new Rect(0, 0, width, height);
                    camera.aspect = (float)width / height;

                    if (instance.transform is RectTransform rect)
                    {
                        framing = "ui_canvas";
                        uiContext = PrefabCaptureContext.Frame(preview, instance, rect, camera, width, height, uiParent, limitations);
                    }
                    else
                    {
                        framing = "renderer_bounds_front";
                        FrameRenderers(instance, camera);
                    }
                    Canvas.ForceUpdateCanvases();
                    preview.Render(allowScriptableRenderPipeline: true, updatefov: false);
                    RenderTexture.active = target;
                    pixels = new Texture2D(width, height, TextureFormat.RGBA32, false, false);
                    pixels.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                    pixels.Apply();
                    png = pixels.EncodeToPNG();
                }
                finally
                {
                    if (opened)
                    {
                        camera.targetTexture = previewTarget;
                        preview.EndPreview();
                    }
                }
            }
            catch (Exception exception)
            {
                error = exception is ArgumentException || exception is InvalidOperationException ? exception.Message : "Prefab capture failed.";
            }
            finally
            {
                try
                {
                    if (pixels != null)
                        Object.DestroyImmediate(pixels);
                    if (target != null)
                        Object.DestroyImmediate(target);
                    preview?.Cleanup();
                    cleanupCompleted = !scene.IsValid();
                }
                catch (Exception)
                {
                    error = "Prefab preview cleanup failed.";
                }
            }
            if (error != null || png == null || !cleanupCompleted)
                return new ErrorResponse("prefab_capture_failed", new { message = error, cleanup_completed = cleanupCompleted });
            return new SuccessResponse(
                "Prefab preview captured.",
                new
                {
                    prefab_path = path,
                    width,
                    height,
                    framing,
                    ui_context = uiContext,
                    cleanup_completed = cleanupCompleted,
                    imageBase64 = Convert.ToBase64String(png),
                    limitations,
                }
            );
        }

        private static void FrameRenderers(GameObject instance, Camera camera)
        {
            var renderers = instance.GetComponentsInChildren<Renderer>().Where(r => r.enabled).ToArray();
            if (renderers.Length == 0)
                throw new InvalidOperationException("Prefab has no active renderers or root RectTransform to frame.");
            var bounds = renderers[0].bounds;
            foreach (var renderer in renderers.Skip(1))
                bounds.Encapsulate(renderer.bounds);
            float extent = Mathf.Max(0.01f, Mathf.Max(bounds.extents.y, bounds.extents.x / camera.aspect));
            camera.orthographicSize = extent * 1.1f;
            // Orthographic X/Y framing does not require moving the camera farther away. Place it using
            // depth alone, with both clip planes enclosing the final camera-space bounds plus padding.
            float padding = Mathf.Max(0.1f, bounds.size.z * 0.05f);
            float distance = bounds.extents.z + padding;
            camera.transform.SetPositionAndRotation(bounds.center - Vector3.forward * distance, Quaternion.identity);
            camera.nearClipPlane = padding * 0.5f;
            camera.farClipPlane = distance + bounds.extents.z + padding;
        }

        private static int Dimension(JObject parameters, string name, int fallback)
        {
            var value = parameters[name];
            if (value == null)
                return fallback;
            if (value.Type != JTokenType.Integer || (long)value < 64 || (long)value > 2048)
                throw new ArgumentException(name + " must be an integer within 64..2048.");
            return (int)value;
        }
    }
}
