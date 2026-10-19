using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.AssetGen.Import;
using MCPForUnity.Editor.Services.Blender;
using MCPForUnity.Editor.Tools.AssetGen;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Blender
{
    /// <summary>
    /// Lets the Unity Editor talk straight to the BlenderMCP addon socket, so a Blender → Unity
    /// handoff is one call (export → import through the shared model pipeline → place → normalize)
    /// instead of an AI-orchestrated multi-step dance. Also drives the Blender Bridge panel in the
    /// Asset Gen tab and the Window/MCP for Unity/Blender Bridge menu. Carries no API keys.
    /// Socket and git work runs on the thread pool; Unity API calls happen after the await, back on
    /// the editor thread (the bridge awaits handlers on Unity's synchronization context).
    /// </summary>
    [McpForUnityTool(
        "blender_bridge",
        AutoRegister = false,
        RequiresExplicitConsent = true,
        Group = "asset_gen",
        Description = "Bridge to a running Blender with the BlenderMCP addon: status, scene/object info, viewport "
            + "screenshot, run Python in Blender, import a model (export → import → place → normalize), "
            + "check the blender-mcp checkout for updates, and sync its addon into Blender."
    )]
    public static class BlenderBridgeTool
    {
        // Internal readers let isolated tests avoid changing Editor preferences.
        internal static Func<bool> ConsentGrantedOverride = null;
        internal static Func<BlenderEndpoint> EndpointOverride = null;

        private static bool ConsentGranted => ConsentGrantedOverride?.Invoke() ?? MCPServiceLocator.ToolConsent.GetStatus("blender_bridge").HasGrant;

        private static BlenderEndpoint Endpoint => EndpointOverride?.Invoke() ?? BlenderBridgePrefs.Endpoint;

        private static readonly string[] ValidActions =
        {
            "status",
            "scene_info",
            "object_info",
            "screenshot",
            "run_python",
            "import_model",
            "compare_screenshot",
            "setup_bloom",
            "check_updates",
            "sync_addon",
        };

        private const string NotConfiguredMessage =
            "The blender-mcp checkout is not set. Open Window > MCP for Unity > Generative > Blender Bridge " + "and pick the folder that contains addon.py.";

        /// <summary>Compatibility entry point for callers without cooperative cancellation.</summary>
        public static Task<object> HandleCommand(JObject @params) => HandleCommand(@params, CancellationToken.None);

        /// <summary>Dispatches bridge actions; requested cancellation propagates with the caller token.</summary>
        public static async Task<object> HandleCommand(JObject @params, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");
            // Panel/menu callers and direct registry dispatch must enforce the same grant.
            if (!ConsentGranted || !MCPServiceLocator.ToolDiscovery.IsToolEnabled("blender_bridge"))
                return new ErrorResponse(
                    "blender_consent_required",
                    "Enable blender_bridge explicitly in the Unity Editor before using Blender bridge operations, "
                        + "including Python execution and addon synchronization."
                );

            var p = new ToolParams(@params);
            string action = (p.Get("action") ?? "status").Trim().ToLowerInvariant();
            int timeout = Math.Max(5, Math.Min(3600, p.GetInt("timeout_seconds", 180) ?? 180));

            try
            {
                switch (action)
                {
                    case "status":
                        return await StatusAsync(cancellationToken);
                    case "scene_info":
                    {
                        JToken scene = await BlenderSocketClient.SendAsync(Endpoint, "get_scene_info", null, timeout, cancellationToken);
                        cancellationToken.ThrowIfCancellationRequested();
                        return new SuccessResponse("Retrieved Blender scene info.", scene);
                    }
                    case "object_info":
                    {
                        string objectName = p.Get("object_name");
                        if (string.IsNullOrWhiteSpace(objectName))
                            return new ErrorResponse("'object_name' is required for object_info.");
                        JToken info = await BlenderSocketClient.SendAsync(
                            Endpoint,
                            "get_object_info",
                            new JObject { ["object_name"] = objectName },
                            timeout,
                            cancellationToken
                        );
                        cancellationToken.ThrowIfCancellationRequested();
                        return new SuccessResponse($"Retrieved info for '{objectName}'.", info);
                    }
                    case "screenshot":
                        return await ScreenshotAsync(p, timeout, cancellationToken);
                    case "run_python":
                    {
                        string code = p.Get("code");
                        if (string.IsNullOrWhiteSpace(code))
                            return new ErrorResponse("'code' is required for run_python.");
                        string stdout = await BlenderSocketClient.RunPythonAsync(Endpoint, code, timeout, cancellationToken);
                        cancellationToken.ThrowIfCancellationRequested();
                        return new SuccessResponse("Executed Python in Blender.", new { stdout });
                    }
                    case "import_model":
                        return await ImportModelAsync(p, timeout, cancellationToken);
                    case "compare_screenshot":
                        return await CompareScreenshotAsync(p, timeout, cancellationToken);
                    case "setup_bloom":
                    {
                        JObject bloom = await SetupBloomAsync(cancellationToken);
                        cancellationToken.ThrowIfCancellationRequested();
                        return ToBloomResponse(bloom);
                    }
                    case "check_updates":
                        return await CheckUpdatesAsync(cancellationToken);
                    case "sync_addon":
                        cancellationToken.ThrowIfCancellationRequested();
                        return SyncAddon(p.GetBool("force", false));
                    default:
                        return new ErrorResponse($"Unknown action '{action}'. Valid: {string.Join(", ", ValidActions)}.");
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (BlenderUnavailableException e)
            {
                cancellationToken.ThrowIfCancellationRequested();
                return new ErrorResponse(e.Message);
            }
            catch (Exception e)
            {
                cancellationToken.ThrowIfCancellationRequested();
                return new ErrorResponse(SecretRedactor.Scrub($"blender_bridge '{action}' failed: {e.Message}"));
            }
        }

        // ------------------------------------------------------------------ status

        /// <summary>Probes the addon socket and reports checkout / installed-addon state without failing.</summary>
        private static async Task<object> StatusAsync(CancellationToken cancellationToken)
        {
            BlenderEndpoint endpoint = Endpoint;
            string forkAddon = BlenderBridgePrefs.ForkAddonPath;
            string installedAddon = BlenderBridgePrefs.InstalledAddonPath;
            bool forkConfigured = BlenderBridgePrefs.IsForkConfigured;
            string forkPath = BlenderBridgePrefs.ForkPath;
            bool blenderInstalled = BlenderDetection.IsInstalled();

            bool reachable = false;
            string error = null;
            JToken scene = null;
            try
            {
                scene = await BlenderSocketClient.SendAsync(endpoint, "get_scene_info", null, 10, cancellationToken);
                cancellationToken.ThrowIfCancellationRequested();
                reachable = true;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception e)
            {
                error = SecretRedactor.Scrub(e.Message);
            }

            cancellationToken.ThrowIfCancellationRequested();
            string forkMd5 = forkAddon != null && File.Exists(forkAddon) ? FileMd5(forkAddon) : null;
            string installedMd5 = installedAddon != null && File.Exists(installedAddon) ? FileMd5(installedAddon) : null;

            var data = new JObject
            {
                ["blender_reachable"] = reachable,
                ["blender_installed"] = blenderInstalled,
                ["endpoint"] = endpoint.ToString(),
                ["error"] = reachable ? null : error,
                ["scene_name"] = scene?["name"],
                ["object_count"] = scene?["object_count"],
                ["fork_configured"] = forkConfigured,
                ["fork_path"] = forkPath,
                ["fork_addon_found"] = forkMd5 != null,
                ["installed_addon_path"] = installedAddon,
                ["installed_addon_found"] = installedMd5 != null,
                ["addon_in_sync"] = forkMd5 != null && installedMd5 != null && forkMd5 == installedMd5,
            };
            string msg = reachable ? $"Blender reachable. Scene '{scene?["name"]}' with {scene?["object_count"]} objects." : "Blender not reachable.";
            cancellationToken.ThrowIfCancellationRequested();
            return new SuccessResponse(msg, data);
        }

        // -------------------------------------------------------------- screenshot

        /// <summary>Asks Blender for an offscreen viewport render and optionally copies it under Assets/.</summary>
        private static async Task<object> ScreenshotAsync(ToolParams p, int timeout, CancellationToken cancellationToken)
        {
            int requestedSize = p.GetInt("max_size", 1000) ?? 1000;
            ScreenshotUtility.ValidateMaxResolution(requestedSize);
            int maxSize = Math.Max(64, requestedSize);
            string outputFolder = p.Get("output_folder");

            string assetsRelativeFolder = null;
            if (!string.IsNullOrWhiteSpace(outputFolder))
            {
                // Canonicalizes and rejects anything that does not resolve under Assets/ (e.g. "Assets/../x").
                if (!AssetGenPaths.NormalizeOutputFolder(outputFolder, out assetsRelativeFolder, out string folderError))
                    return new ErrorResponse(folderError);
            }

            cancellationToken.ThrowIfCancellationRequested();
            using var files = new CaptureFiles(ProjectRoot());
            string file = files.PathFor("blender_viewport.png");

            JToken result = await BlenderSocketClient.SendAsync(
                Endpoint,
                "get_viewport_screenshot",
                new JObject
                {
                    ["max_size"] = maxSize,
                    ["filepath"] = file,
                    ["format"] = "png",
                },
                timeout,
                cancellationToken
            );
            cancellationToken.ThrowIfCancellationRequested();

            if (!File.Exists(file))
                return new ErrorResponse($"Blender did not write a screenshot to {file}. Response: {result}");

            string assetPath = null;
            if (assetsRelativeFolder != null)
            {
                cancellationToken.ThrowIfCancellationRequested();
                assetPath = CopyCaptureAsset(file, assetsRelativeFolder, files, cancellationToken);
            }

            cancellationToken.ThrowIfCancellationRequested();
            files.Retain(file);
            if (assetPath != null)
                files.Retain(AssetGenPaths.ToAbsolute(assetPath));
            return new SuccessResponse(
                "Captured Blender viewport.",
                new JObject
                {
                    ["path"] = file,
                    ["asset_path"] = assetPath,
                    ["width"] = result?["width"],
                    ["height"] = result?["height"],
                    ["method"] = result?["method"],
                }
            );
        }

        // ------------------------------------------------------------ import_model

        /// <summary>Exports from Blender, imports through the shared normalization policy, then places the resulting asset.</summary>
        private static async Task<object> ImportModelAsync(ToolParams p, int timeout, CancellationToken cancellationToken)
        {
            string fmt = (p.Get("format") ?? "glb").Trim().ToLowerInvariant();
            if (fmt != "glb" && fmt != "fbx")
                return new ErrorResponse("'format' must be glb or fbx.");

            string[] names = p.GetStringArray("object_names")?.Where(n => !string.IsNullOrWhiteSpace(n)).ToArray();
            bool selectionOnly = p.GetBool("selection_only", false);
            bool applyModifiers = p.GetBool("apply_modifiers", true);
            bool place = p.GetBool("place_in_scene", true);
            Vector3 position = ParsePosition(p.GetRaw("position"));
            if (fmt == "glb" && !ModelImportPipeline.IsGltfastAvailable())
                return new ErrorResponse("GLB import requires glTFast. Install it from the MCP for Unity → Dependencies tab, or choose FBX output.");
            float target = Mathf.Max(0f, p.GetFloat("target_size", 0f) ?? 0f);
            string outputFolder = AssetGenPaths.ResolveOutputFolder(p.Get("output_folder"), "Imported");
            string animationType = p.Get("animation_type");
            bool autoAnimate = p.GetBool("auto_animate", true);
            bool savePrefab = p.GetBool("save_prefab", false);
            bool ensureBloom = p.GetBool("ensure_bloom", false);

            string name = p.Get("name");
            if (string.IsNullOrWhiteSpace(name))
                name = names != null && names.Length == 1 ? names[0] : "BlenderModel";
            name = SanitizeName(name);

            // 1. Export from Blender to a temp file (off the editor thread).
            string exportDir = Path.Combine(Path.GetTempPath(), "BlenderBridge");
            cancellationToken.ThrowIfCancellationRequested();
            Directory.CreateDirectory(exportDir);
            string exportPath = Path.Combine(exportDir, $"{name}_{UniqueSuffix()}.{fmt}").Replace('\\', '/');

            if (File.Exists(exportPath) || Directory.Exists(exportPath))
                throw new IOException("The Blender export path already exists.");

            // Own the unique path before waiting. A remote exporter may still recreate it after cleanup.
            using var export = new TemporaryBlenderExport(exportPath);
            string stdout = await ExportFromBlenderAsync(Endpoint, exportPath, names, selectionOnly, applyModifiers, fmt, timeout, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();

            if (!File.Exists(exportPath))
                return new ErrorResponse($"Blender did not produce {exportPath}. Blender output: {Truncate(stdout, 800)}");

            // 2. Import through the shared pipeline (staging under Assets/, glTFast/FBX, material setup).
            // target_size 0 is passed through: the pipeline only rescales when it is > 0.
            var importParams = new JObject
            {
                ["sourcePath"] = exportPath,
                ["name"] = name,
                ["targetSize"] = target,
            };
            if (!string.IsNullOrWhiteSpace(outputFolder))
                importParams["outputFolder"] = outputFolder;
            if (!string.IsNullOrWhiteSpace(animationType))
                importParams["animationType"] = animationType;

            // The public importer only accepts Assets-contained sources. Stage this bridge's
            // own export there without broadening that boundary to arbitrary local files.
            string stagedExport = StageExportForImport(exportPath, cancellationToken);
            JObject importResult;
            try
            {
                importParams["sourcePath"] = stagedExport;
                cancellationToken.ThrowIfCancellationRequested();
                // Once admitted, the shared synchronous import may finish. Keep its published asset.
                importResult = JObject.FromObject(ImportModelFile.HandleCommand(importParams));
            }
            finally
            {
                try
                {
                    File.Delete(stagedExport);
                    Directory.Delete(Path.GetDirectoryName(stagedExport));
                }
                catch (Exception ex) when (cancellationToken.IsCancellationRequested)
                {
                    McpLog.Warn($"Could not remove canceled Blender staging directory ({ex.GetType().Name}).");
                }
            }
            cancellationToken.ThrowIfCancellationRequested();
            if (!(importResult.Value<bool?>("success") ?? false))
                return MapImportFailure(importResult);

            string assetPath = importResult["data"]?["asset_path"]?.ToString();
            var data = new JObject
            {
                ["asset_path"] = assetPath,
                ["asset_guid"] = importResult["data"]?["asset_guid"],
                ["export_path"] = exportPath,
                ["format"] = fmt,
                ["blender_output"] = Truncate(stdout, 400),
                ["placed"] = false,
            };

            cancellationToken.ThrowIfCancellationRequested();
            if (!place || string.IsNullOrEmpty(assetPath))
                return export.Preserve(new SuccessResponse($"Imported {assetPath} (not placed).", data));

            cancellationToken.ThrowIfCancellationRequested();
            string sourceAssetPath = NormalizedModelVariant.ResolveSourceAssetPath(assetPath);
            if (fmt == "fbx" && autoAnimate)
                ConfigureFbxClipLooping(sourceAssetPath, cancellationToken);

            // 3. Place the pipeline's verified output. AutoNormalize and nonpositive targets must
            // have identical meaning with or without placement; never rescale a second time here.
            cancellationToken.ThrowIfCancellationRequested();
            GameObject go = PlaceImportedModelWithCancellation(assetPath, name, position, SceneManager.GetActiveScene(), cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            if (go == null)
            {
                data["note"] = "Asset has no GameObject root to instantiate.";
                return export.Preserve(new SuccessResponse($"Imported {assetPath} but could not instantiate it.", data));
            }
            cancellationToken.ThrowIfCancellationRequested();
            Undo.RegisterCreatedObjectUndo(go, "Import from Blender");

            cancellationToken.ThrowIfCancellationRequested();
            EditorSceneManager.MarkSceneDirty(go.scene);
            cancellationToken.ThrowIfCancellationRequested();
            Selection.activeGameObject = go;

            data["placed"] = true;
            data["game_object"] = go.name;
            data["scene"] = go.scene.name;
            data["scale_factor_applied"] = 1f; // Placement applied no additional scale.
            if (TryGetWorldBounds(go, out Bounds b1))
            {
                data["bounds_size"] = new JArray(b1.size.x, b1.size.y, b1.size.z);
                data["bounds_center"] = new JArray(b1.center.x, b1.center.y, b1.center.z);
            }

            // 4. Optional finishing touches: play imported clips, keep a prefab, make emission glow.
            cancellationToken.ThrowIfCancellationRequested();
            JObject animation = SetupAnimationWithCancellation(go, assetPath, name, autoAnimate, null, cancellationToken);
            if (animation != null)
                data["animation"] = animation;
            cancellationToken.ThrowIfCancellationRequested();
            if (savePrefab)
                data["prefab_path"] = SavePrefabWithCancellation(go, assetPath, name, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            if (ensureBloom && HasEmissiveMaterial(go))
                data["bloom"] = await SetupBloomAsync(cancellationToken);

            cancellationToken.ThrowIfCancellationRequested();
            return export.Preserve(new SuccessResponse($"Imported {assetPath} and placed '{go.name}' in the scene.", data));
        }

        /// <summary>
        /// Asks the addon to export. Prefers its first-class <c>export_scene</c> command (validated
        /// parameters, no code execution); addons that predate it answer "Unknown command type", in
        /// which case the equivalent Python is sent through <c>execute_code</c>.
        /// </summary>
        private static async Task<string> ExportFromBlenderAsync(
            BlenderEndpoint endpoint,
            string exportPath,
            string[] names,
            bool selectionOnly,
            bool applyModifiers,
            string fmt,
            int timeout,
            CancellationToken cancellationToken
        )
        {
            var args = new JObject
            {
                ["filepath"] = exportPath,
                ["format"] = fmt,
                ["object_names"] = names == null ? new JArray() : new JArray(names.Cast<object>().ToArray()),
                ["selection_only"] = selectionOnly,
                ["apply_modifiers"] = applyModifiers,
            };
            try
            {
                JToken result = await BlenderSocketClient.SendAsync(endpoint, "export_scene", args, timeout, cancellationToken);
                cancellationToken.ThrowIfCancellationRequested();
                if (result?["error"] != null)
                    throw new BlenderCommandException($"Blender export_scene failed: {result["error"]}");
                return result?.ToString(Formatting.None) ?? string.Empty;
            }
            catch (BlenderCommandException e) when (e.Message.Contains("Unknown command type"))
            {
                cancellationToken.ThrowIfCancellationRequested();
                string script = BuildExportScript(exportPath, names, selectionOnly, applyModifiers, fmt);
                string output = await BlenderSocketClient.RunPythonAsync(endpoint, script, timeout, cancellationToken);
                cancellationToken.ThrowIfCancellationRequested();
                return output;
            }
        }

        /// <summary>
        /// Builds the Python that Blender runs to export on addons without <c>export_scene</c>. All caller-controlled values travel inside one
        /// JSON document embedded as a single Python string literal, so names with quotes or
        /// placeholder-looking text can never alter the program.
        /// </summary>
        internal static string BuildExportScript(string outPath, string[] names, bool selectionOnly, bool applyModifiers, string fmt)
        {
            var config = new JObject
            {
                ["out"] = outPath,
                ["names"] = names == null ? new JArray() : new JArray(names.Cast<object>().ToArray()),
                ["selection_only"] = selectionOnly,
                ["apply_modifiers"] = applyModifiers,
                ["format"] = fmt,
            };
            // JSON string escaping is a subset of Python string-literal escaping, so the encoded
            // document is a valid double-quoted Python literal.
            string configLiteral = JsonConvert.ToString(config.ToString(Formatting.None));

            const string template =
                @"
import bpy, os, json
cfg = json.loads(__CFG__)
out = cfg['out']
names = cfg['names']
selection_only = cfg['selection_only']
apply_mods = cfg['apply_modifiers']
fmt = cfg['format']
os.makedirs(os.path.dirname(out), exist_ok=True)
try:
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
except Exception:
    pass
use_sel = False
if names:
    missing = [n for n in names if bpy.data.objects.get(n) is None]
    if missing:
        raise Exception('Objects not found in Blender: ' + ', '.join(missing))
    bpy.ops.object.select_all(action='DESELECT')
    for n in names:
        o = bpy.data.objects[n]
        o.select_set(True)
        for c in o.children_recursive:
            c.select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects[names[0]]
    use_sel = True
elif selection_only:
    if not bpy.context.selected_objects:
        raise Exception('Nothing is selected in Blender and no object_names were given.')
    use_sel = True
if fmt == 'glb':
    bpy.ops.export_scene.gltf(filepath=out, export_format='GLB', use_selection=use_sel,
                              use_active_scene=True, export_apply=apply_mods,
                              export_animations=True, export_skins=True, export_morph=True,
                              export_yup=True)
else:
    bpy.ops.export_scene.fbx(filepath=out, use_selection=use_sel, apply_unit_scale=True,
                             bake_space_transform=apply_mods, use_mesh_modifiers=apply_mods,
                             path_mode='COPY', embed_textures=True)
print(json.dumps({'path': out, 'bytes': os.path.getsize(out), 'selection_only': use_sel,
                  'exported': [o.name for o in (bpy.context.selected_objects if use_sel else bpy.context.scene.objects)]}))
";
            // Verbatim strings take the checkout's line endings; normalize so the script (and tests) are
            // byte-identical whether the file was checked out with LF or CRLF.
            string body = template.Replace("\r\n", "\n");
            int at = body.IndexOf("__CFG__", StringComparison.Ordinal);
            return body.Substring(0, at) + configLiteral + body.Substring(at + "__CFG__".Length);
        }

        // ---------------------------------------------------------- finishing touches

        /// <summary>Marks every clip in an FBX as looping so the imported animation cycles once a controller drives it.</summary>
        private static void ConfigureFbxClipLooping(string assetPath, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (!(AssetImporter.GetAtPath(assetPath) is ModelImporter importer))
                return;
            ModelImporterClipAnimation[] clips = importer.clipAnimations.Length > 0 ? importer.clipAnimations : importer.defaultClipAnimations;
            if (clips.Length == 0)
                return;
            foreach (ModelImporterClipAnimation clip in clips)
            {
                cancellationToken.ThrowIfCancellationRequested();
                clip.loopTime = true;
                cancellationToken.ThrowIfCancellationRequested();
                if (importer.animationType == ModelImporterAnimationType.Legacy)
                    clip.wrapMode = WrapMode.Loop;
            }
            cancellationToken.ThrowIfCancellationRequested();
            importer.clipAnimations = clips;
            cancellationToken.ThrowIfCancellationRequested();
            importer.SaveAndReimport();
            cancellationToken.ThrowIfCancellationRequested();
        }

        /// <summary>
        /// Configures looping playback with the animation system used by the imported clips.
        /// </summary>
        internal static JObject SetupAnimation(GameObject go, string assetPath, string name, bool autoAnimate, AnimationClip[] clips = null) =>
            SetupAnimationWithCancellation(go, assetPath, name, autoAnimate, clips, CancellationToken.None);

        private static JObject SetupAnimationWithCancellation(
            GameObject go,
            string assetPath,
            string name,
            bool autoAnimate,
            AnimationClip[] clips,
            CancellationToken cancellationToken
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (!autoAnimate)
                return null;
            clips = (
                clips
                ?? AssetDatabase.LoadAllAssetRepresentationsAtPath(NormalizedModelVariant.ResolveSourceAssetPath(assetPath)).OfType<AnimationClip>().ToArray()
            )
                .Where(c => c != null && !c.name.StartsWith("__preview__", StringComparison.Ordinal))
                .ToArray();
            cancellationToken.ThrowIfCancellationRequested();
            if (clips.Length == 0)
                return null;
            bool legacy = clips[0].legacy;
            if (clips.Any(c => c.legacy != legacy))
                throw new InvalidOperationException("Imported clips mix Legacy and Mecanim animation types.");

            if (legacy)
            {
                cancellationToken.ThrowIfCancellationRequested();
                UnityEngine.Animation animation = go.GetComponentInChildren<UnityEngine.Animation>(true) ?? go.AddComponent<UnityEngine.Animation>();
                AnimationClip defaultClip = clips.Contains(animation.clip) ? animation.clip : clips[0];
                foreach (AnimationClip clip in clips)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    clip.wrapMode = WrapMode.Loop;
                    cancellationToken.ThrowIfCancellationRequested();
                    animation.AddClip(clip, clip.name);
                    cancellationToken.ThrowIfCancellationRequested();
                    EditorUtility.SetDirty(clip);
                }
                cancellationToken.ThrowIfCancellationRequested();
                animation.clip = defaultClip;
                cancellationToken.ThrowIfCancellationRequested();
                animation.wrapMode = WrapMode.Loop;
                cancellationToken.ThrowIfCancellationRequested();
                animation.playAutomatically = true;
                cancellationToken.ThrowIfCancellationRequested();
                animation.enabled = true;
                cancellationToken.ThrowIfCancellationRequested();
                EditorUtility.SetDirty(animation);
                cancellationToken.ThrowIfCancellationRequested();
                AssetDatabase.SaveAssets();
                cancellationToken.ThrowIfCancellationRequested();
                return new JObject
                {
                    ["system"] = "legacy",
                    ["clips"] = new JArray(clips.Select(c => c.name).Cast<object>().ToArray()),
                    ["default_clip"] = defaultClip.name,
                    ["animation_on"] = animation.gameObject.name,
                    ["loops"] = true,
                };
            }

            cancellationToken.ThrowIfCancellationRequested();
            Animator animator = go.GetComponentInChildren<Animator>(true) ?? go.AddComponent<Animator>();
            string controllerPath = AssetDatabase.GenerateUniqueAssetPath(
                Path.Combine(Path.GetDirectoryName(assetPath) ?? "Assets", $"{name}_Controller.controller").Replace('\\', '/')
            );
            cancellationToken.ThrowIfCancellationRequested();
            var controller = UnityEditor.Animations.AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            cancellationToken.ThrowIfCancellationRequested();
            var stateMachine = controller.layers[0].stateMachine;
            foreach (AnimationClip clip in clips)
            {
                AnimationClipSettings settings = AnimationUtility.GetAnimationClipSettings(clip);
                if (!settings.loopTime)
                {
                    settings.loopTime = true;
                    cancellationToken.ThrowIfCancellationRequested();
                    AnimationUtility.SetAnimationClipSettings(clip, settings);
                }
                cancellationToken.ThrowIfCancellationRequested();
                var state = stateMachine.AddState(clip.name);
                cancellationToken.ThrowIfCancellationRequested();
                state.motion = clip;
                cancellationToken.ThrowIfCancellationRequested();
                if (stateMachine.defaultState == null)
                    stateMachine.defaultState = state;
            }
            cancellationToken.ThrowIfCancellationRequested();
            animator.runtimeAnimatorController = controller;
            cancellationToken.ThrowIfCancellationRequested();
            EditorUtility.SetDirty(controller);
            cancellationToken.ThrowIfCancellationRequested();
            EditorUtility.SetDirty(animator);
            cancellationToken.ThrowIfCancellationRequested();
            AssetDatabase.SaveAssets();
            cancellationToken.ThrowIfCancellationRequested();
            return new JObject
            {
                ["system"] = "mecanim",
                ["clips"] = new JArray(clips.Select(c => c.name).Cast<object>().ToArray()),
                ["controller_path"] = controllerPath,
                ["animator_on"] = animator.gameObject.name,
                ["loops"] = true,
            };
        }

        /// <summary>Saves the placed instance as a prefab under &lt;asset folder&gt;/Prefabs and connects the instance to it.</summary>
        private static string SavePrefab(GameObject go, string assetPath, string name) =>
            SavePrefabWithCancellation(go, assetPath, name, CancellationToken.None);

        private static string SavePrefabWithCancellation(GameObject go, string assetPath, string name, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string parent = (Path.GetDirectoryName(assetPath) ?? "Assets").Replace('\\', '/');
            string dir = $"{parent}/Prefabs";
            using var folders = new AssetFolderScope();
            cancellationToken.ThrowIfCancellationRequested();
            folders.EnsureFolder(dir);
            cancellationToken.ThrowIfCancellationRequested();
            string prefabPath = AssetDatabase.GenerateUniqueAssetPath($"{dir}/{name}.prefab");
            AssetPathUtility.GetFullAssetPath(prefabPath);
            cancellationToken.ThrowIfCancellationRequested();
            var saved = PrefabUtility.SaveAsPrefabAssetAndConnect(go, prefabPath, InteractionMode.AutomatedAction);
            if (saved != null)
                folders.Complete(); // Keep a published prefab even if this admitted save observed cancellation.
            cancellationToken.ThrowIfCancellationRequested();
            if (saved == null)
                throw new IOException($"Failed to save prefab at '{prefabPath}'.");
            return prefabPath;
        }

        /// <summary>True when any material on the hierarchy has a non-black emission (glTFast or URP Lit naming).</summary>
        private static bool HasEmissiveMaterial(GameObject go)
        {
            foreach (Renderer r in go.GetComponentsInChildren<Renderer>(true))
            {
                foreach (Material m in r.sharedMaterials)
                {
                    if (m == null)
                        continue;
                    if (m.HasProperty("emissiveFactor") && m.GetColor("emissiveFactor").maxColorComponent > 0.01f)
                        return true;
                    if (m.HasProperty("_EmissionColor") && m.IsKeywordEnabled("_EMISSION") && m.GetColor("_EmissionColor").maxColorComponent > 0.01f)
                        return true;
                }
            }
            return false;
        }

        /// <summary>
        /// Makes emissive materials actually glow: enables post-processing on the main camera and ensures a
        /// global Volume with a Bloom override exists (reusing the scene's global volume when it has one).
        /// Goes through manage_graphics so the Volume/Bloom reflection lives in one place.
        /// </summary>
        private static async Task<JObject> SetupBloomAsync(CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var result = new JObject();
            Type volumeType = Type.GetType("UnityEngine.Rendering.Volume, Unity.RenderPipelines.Core.Runtime");
            if (volumeType == null)
            {
                result["message"] = "Volume system (URP/HDRP) not available; nothing to do.";
                return result;
            }

            Camera cam = Camera.main;
            Type camDataType = Type.GetType("UnityEngine.Rendering.Universal.UniversalAdditionalCameraData, Unity.RenderPipelines.Universal.Runtime");
            if (cam != null && camDataType != null)
            {
                cancellationToken.ThrowIfCancellationRequested();
                Component camData = cam.GetComponent(camDataType) ?? cam.gameObject.AddComponent(camDataType);
                System.Reflection.PropertyInfo post = camDataType.GetProperty("renderPostProcessing");
                if (post != null && !(bool)post.GetValue(camData))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    post.SetValue(camData, true);
                    cancellationToken.ThrowIfCancellationRequested();
                    EditorUtility.SetDirty(camData);
                    result["camera_post_processing_enabled"] = cam.name;
                }
            }

            System.Reflection.PropertyInfo isGlobal = volumeType.GetProperty("isGlobal");
            System.Reflection.FieldInfo sharedProfile = volumeType.GetField("sharedProfile");
            Component globalVolume = null;
            foreach (UnityEngine.Object v in UnityFindObjectsCompat.FindAll(volumeType))
            {
                var comp = v as Component;
                if (comp == null || (isGlobal != null && !(bool)isGlobal.GetValue(comp)))
                    continue;
                globalVolume ??= comp;
                object profile = sharedProfile?.GetValue(comp);
                var components = profile?.GetType().GetField("components")?.GetValue(profile) as System.Collections.IEnumerable;
                if (components != null && components.Cast<object>().Any(c => c != null && c.GetType().Name == "Bloom"))
                {
                    result["volume"] = comp.gameObject.name;
                    result["message"] = $"Bloom already present on global volume '{comp.gameObject.name}'.";
                    return result;
                }
            }

            JObject graphics;
            if (globalVolume != null)
            {
                graphics = JObject.FromObject(
                    await CommandRegistry.InvokeCommandAsync(
                        "manage_graphics",
                        new JObject
                        {
                            ["action"] = "volume_add_effect",
                            ["target"] = globalVolume.gameObject.name,
                            ["effect"] = "Bloom",
                        },
                        cancellationToken
                    )
                );
                cancellationToken.ThrowIfCancellationRequested();
                if (graphics.Value<bool?>("success") ?? false)
                {
                    await CommandRegistry.InvokeCommandAsync(
                        "manage_graphics",
                        new JObject
                        {
                            ["action"] = "volume_set_effect",
                            ["target"] = globalVolume.gameObject.name,
                            ["effect"] = "Bloom",
                            ["parameters"] = new JObject { ["intensity"] = 1.0f, ["threshold"] = 0.9f },
                        },
                        cancellationToken
                    );
                }
                result["volume"] = globalVolume.gameObject.name;
            }
            else
            {
                graphics = JObject.FromObject(
                    await CommandRegistry.InvokeCommandAsync(
                        "manage_graphics",
                        new JObject
                        {
                            ["action"] = "volume_create",
                            ["name"] = "Global Volume (Blender Bridge)",
                            ["is_global"] = true,
                            ["effects"] = new JArray(
                                new JObject
                                {
                                    ["type"] = "Bloom",
                                    ["intensity"] = 1.0f,
                                    ["threshold"] = 0.9f,
                                }
                            ),
                        },
                        cancellationToken
                    )
                );
                result["volume"] = "Global Volume (Blender Bridge)";
            }
            cancellationToken.ThrowIfCancellationRequested();
            bool ok = graphics.Value<bool?>("success") ?? false;
            result["message"] = ok ? $"Bloom added to '{result["volume"]}'." : $"Could not add Bloom: {graphics["error"] ?? graphics["message"]}";
            result["success"] = ok;
            return result;
        }

        /// <summary>Maps the Bloom setup report to a tool response: an error when manage_graphics could not add Bloom.</summary>
        internal static object ToBloomResponse(JObject bloom)
        {
            string message = bloom?["message"]?.ToString() ?? "Bloom configured.";
            bool failed = bloom != null && bloom.Value<bool?>("success") == false;
            return failed ? new ErrorResponse(message) : new SuccessResponse(message, bloom);
        }

        // -------------------------------------------------------- compare_screenshot

        /// <summary>Puts a Blender viewport capture and a Unity capture of a placed object side by side in one PNG.</summary>
        private static async Task<object> CompareScreenshotAsync(ToolParams p, int timeout, CancellationToken cancellationToken)
        {
            string target = p.Get("game_object");
            if (string.IsNullOrWhiteSpace(target))
                return new ErrorResponse("'game_object' is required for compare_screenshot.");
            if (GameObject.Find(target) == null)
                return new ErrorResponse($"GameObject '{target}' not found in the open scene.");
            int requestedSize = p.GetInt("max_size", 800) ?? 800;
            ScreenshotUtility.ValidateMaxResolution(requestedSize);
            int maxSize = Math.Max(128, requestedSize);
            string outputFolder = p.Get("output_folder");
            string assetsRelativeFolder = null;
            if (
                !string.IsNullOrWhiteSpace(outputFolder) && !AssetGenPaths.NormalizeOutputFolder(outputFolder, out assetsRelativeFolder, out string folderError)
            )
                return new ErrorResponse(folderError);

            cancellationToken.ThrowIfCancellationRequested();
            using var files = new CaptureFiles(ProjectRoot());
            string blenderPng = files.PathFor("blender.png");
            string unityPng = files.PathFor("unity.png");
            await BlenderSocketClient.SendAsync(
                Endpoint,
                "get_viewport_screenshot",
                new JObject
                {
                    ["max_size"] = maxSize,
                    ["filepath"] = blenderPng,
                    ["format"] = "png",
                },
                timeout,
                cancellationToken
            );
            cancellationToken.ThrowIfCancellationRequested();
            if (!File.Exists(blenderPng))
                return new ErrorResponse("Blender did not write a viewport screenshot.");

            JObject shot = JObject.FromObject(
                await CommandRegistry.InvokeCommandAsync(
                    "manage_scene",
                    new JObject
                    {
                        ["action"] = "screenshot",
                        ["view_target"] = target,
                        ["max_resolution"] = maxSize,
                        ["fileName"] = Path.GetFileName(unityPng),
                        ["output_folder"] = Path.GetDirectoryName(unityPng),
                    },
                    cancellationToken
                )
            );
            cancellationToken.ThrowIfCancellationRequested();
            string reportedUnityPath = shot["data"]?["fullPath"]?.ToString();
            if (!(shot.Value<bool?>("success") ?? false) || string.IsNullOrEmpty(reportedUnityPath) || !File.Exists(unityPng))
                return new ErrorResponse($"Unity screenshot failed: {shot["error"] ?? shot["message"]}");
            if (Path.GetFullPath(reportedUnityPath) != Path.GetFullPath(unityPng))
                return new ErrorResponse("Unity screenshot did not return the owned intermediate path.");

            string outPath = files.PathFor("comparison.png");
            cancellationToken.ThrowIfCancellationRequested();
            (int width, int height) = CompositeSideBySide(blenderPng, unityPng, outPath);

            string assetPath = null;
            if (assetsRelativeFolder != null)
            {
                cancellationToken.ThrowIfCancellationRequested();
                assetPath = CopyCaptureAsset(outPath, assetsRelativeFolder, files, cancellationToken);
            }

            cancellationToken.ThrowIfCancellationRequested();
            files.Retain(blenderPng);
            files.Retain(outPath);
            if (assetPath != null)
                files.Retain(AssetGenPaths.ToAbsolute(assetPath));
            return new SuccessResponse(
                "Composited Blender (left) and Unity (right).",
                new JObject
                {
                    ["path"] = outPath,
                    ["asset_path"] = assetPath,
                    ["width"] = width,
                    ["height"] = height,
                    ["blender_path"] = blenderPng,
                    ["game_object"] = target,
                }
            );
        }

        /// <summary>Private per-call staging; only explicitly returned files survive disposal.</summary>
        internal sealed class CaptureFiles : IDisposable
        {
            private readonly string project;
            private readonly string directory;
            private readonly OutputFolderScope folders;
            private readonly HashSet<string> owned;
            private readonly HashSet<string> retained;

            public CaptureFiles(string projectRoot)
            {
                project = SafePathUtility.ResolveWithinRoot(projectRoot, projectRoot);
                var comparer = Path.DirectorySeparatorChar == '\\' ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal;
                owned = new HashSet<string>(comparer);
                retained = new HashSet<string>(comparer);
                directory = SafePathUtility.ResolveWithinRoot(project, Path.Combine("Library", "BlenderBridge", Guid.NewGuid().ToString("N")));
                folders = new OutputFolderScope(project);
                try
                {
                    folders.EnsureParentDirectory(Path.Combine(directory, "capture.png"));
                }
                catch
                {
                    folders.Dispose();
                    throw;
                }
            }

            public string PathFor(string name)
            {
                if (string.IsNullOrEmpty(name) || name != Path.GetFileName(name) || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
                    throw new ArgumentException("A simple capture filename is required.");
                string path = SafePathUtility.ResolveWithinRoot(project, Path.Combine(directory, name));
                if (File.Exists(path) || Directory.Exists(path))
                    throw new IOException("Capture output already exists.");
                owned.Add(path);
                return path.Replace('\\', '/');
            }

            public void TrackCreated(string path) => owned.Add(SafePathUtility.ResolveWithinRoot(project, path));

            public void Retain(string path)
            {
                path = SafePathUtility.ResolveWithinRoot(project, path);
                if (!owned.Contains(path))
                    throw new InvalidOperationException("Cannot retain an unowned capture.");
                retained.Add(path);
            }

            public void Dispose()
            {
                foreach (string ownedPath in owned)
                {
                    if (retained.Contains(ownedPath))
                        continue;
                    try
                    {
                        string path = SafePathUtility.ResolveWithinRoot(project, ownedPath);
                        if (!File.Exists(path))
                            continue;
                        string relative = path.Substring(project.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Length + 1)
                            .Replace('\\', '/');
                        if (ScreenshotUtility.IsUnderAssets(relative))
                        {
                            AssetDatabase.DeleteAsset(relative);
                            if (File.Exists(path))
                                File.Delete(path);
                        }
                        else
                            File.Delete(path);
                    }
                    catch (Exception error) when (error is IOException || error is UnauthorizedAccessException || error is InvalidOperationException)
                    {
                        McpLog.Warn("Could not clean an owned Blender capture: " + error.Message);
                    }
                }
                folders.Dispose();
            }
        }

        private static string CopyCaptureAsset(string source, string folder, CaptureFiles files, CancellationToken cancellationToken)
        {
            byte[] png = ReadCapturePng(source);
            string name = Path.GetFileNameWithoutExtension(source) + "_" + UniqueSuffix();
            var output = ScreenshotUtility.PrepareCaptureResult(name, 1, true, folder, false);
            string meta = SafePathUtility.ResolveWithinRoot(ProjectRoot(), output.FullPath + ".meta");
            if (File.Exists(meta))
                throw new IOException("Capture metadata already exists.");
            cancellationToken.ThrowIfCancellationRequested();
            ScreenshotUtility.WriteCaptureBytes(output.FullPath, png);
            files.TrackCreated(output.FullPath);
            cancellationToken.ThrowIfCancellationRequested();
            AssetDatabase.ImportAsset(output.ProjectRelativePath, ImportAssetOptions.ForceSynchronousImport);
            return output.ProjectRelativePath;
        }

        private const long MaxCapturePngBytes = ScreenshotUtility.MaxCapturePixels * 4 + 1024 * 1024;

        private static byte[] ReadCapturePng(string path)
        {
            path = SafePathUtility.ResolveWithinRoot(ProjectRoot(), path);
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            long length = stream.Length;
            if (length < 33 || length > MaxCapturePngBytes)
                throw new IOException("Capture PNG exceeds its encoded byte budget or has no complete header.");
            byte[] header = new byte[33];
            ReadExactly(stream, header, 0, header.Length);
            byte[] prefix = { 137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82 };
            for (int i = 0; i < prefix.Length; i++)
                if (header[i] != prefix[i])
                    throw new IOException("Capture must have a valid PNG IHDR header.");
            uint width = ReadBigEndian(header, 16),
                height = ReadBigEndian(header, 20);
            if (width > int.MaxValue || height > int.MaxValue)
                throw new ArgumentException("Capture PNG dimensions exceed their budget.");
            ScreenshotUtility.ValidateFrameDimensions((int)width, (int)height);
            byte[] bytes = new byte[(int)length];
            Buffer.BlockCopy(header, 0, bytes, 0, header.Length);
            ReadExactly(stream, bytes, header.Length, bytes.Length - header.Length);
            if (stream.ReadByte() != -1)
                throw new IOException("Capture PNG changed while reading.");
            return bytes;
        }

        private static uint ReadBigEndian(byte[] bytes, int offset) =>
            ((uint)bytes[offset] << 24) | ((uint)bytes[offset + 1] << 16) | ((uint)bytes[offset + 2] << 8) | bytes[offset + 3];

        private static void ReadExactly(Stream stream, byte[] bytes, int offset, int count)
        {
            while (count > 0)
            {
                int read = stream.Read(bytes, offset, count);
                if (read == 0)
                    throw new IOException("Capture PNG ended before its declared size.");
                offset += read;
                count -= read;
            }
        }

        /// <summary>Scales both images to the smaller height and writes them side by side. Returns the output size.</summary>
        internal static (int Width, int Height) CompositeSideBySide(string leftPng, string rightPng, string outPath)
        {
            Texture2D left = null,
                right = null;
            Texture2D l = null,
                r = null,
                outTex = null;
            try
            {
                left = LoadPng(leftPng);
                right = LoadPng(rightPng);
                int h = Math.Min(left.height, right.height);
                l = ScaleToHeight(left, h);
                r = ScaleToHeight(right, h);
                ScreenshotUtility.ValidateFrameDimensions(checked(l.width + r.width), h);
                outTex = new Texture2D(l.width + r.width, h, TextureFormat.RGBA32, false);
                outTex.SetPixels(0, 0, l.width, h, l.GetPixels());
                outTex.SetPixels(l.width, 0, r.width, h, r.GetPixels());
                outTex.Apply();
                ScreenshotUtility.WriteCaptureBytes(outPath, outTex.EncodeToPNG());
                return (outTex.width, h);
            }
            finally
            {
                foreach (Texture2D t in new[] { outTex, l == left ? null : l, r == right ? null : r, left, right })
                    if (t != null)
                        UnityEngine.Object.DestroyImmediate(t);
            }
        }

        /// <summary>Decodes a PNG from disk into a readable texture.</summary>
        private static Texture2D LoadPng(string path)
        {
            byte[] png = ReadCapturePng(path);
            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            try
            {
                if (!ImageConversion.LoadImage(tex, png))
                    throw new IOException($"Could not decode PNG: {path}");
                ScreenshotUtility.ValidateFrameDimensions(tex.width, tex.height);
                return tex;
            }
            catch
            {
                UnityEngine.Object.DestroyImmediate(tex);
                throw;
            }
        }

        /// <summary>Bilinear resample to a target height, keeping the aspect ratio. Returns the source when already right.</summary>
        private static Texture2D ScaleToHeight(Texture2D src, int height)
        {
            if (src.height == height)
                return src;
            int width = Mathf.Max(1, Mathf.RoundToInt(src.width * (float)height / src.height));
            var dst = new Texture2D(width, height, TextureFormat.RGBA32, false);
            try
            {
                var pixels = new Color[width * height];
                for (int y = 0; y < height; y++)
                for (int x = 0; x < width; x++)
                    pixels[y * width + x] = src.GetPixelBilinear((x + 0.5f) / width, (y + 0.5f) / height);
                dst.SetPixels(pixels);
                dst.Apply();
                return dst;
            }
            catch
            {
                // The caller only owns a destination after this method returns successfully.
                UnityEngine.Object.DestroyImmediate(dst);
                throw;
            }
        }

        // ----------------------------------------------------------- check_updates

        /// <summary>Fetches the checkout's remotes on the thread pool and reports how far behind it is.</summary>
        private static async Task<object> CheckUpdatesAsync(CancellationToken cancellationToken)
        {
            if (!BlenderBridgePrefs.IsForkConfigured)
                return new ErrorResponse(NotConfiguredMessage);
            string fork = BlenderBridgePrefs.ForkPath;
            string forkAddon = BlenderBridgePrefs.ForkAddonPath;
            string installedAddon = BlenderBridgePrefs.InstalledAddonPath;
            string gitPath = Path.Combine(fork, ".git");
            if (!Directory.Exists(gitPath) && !File.Exists(gitPath))
                return new ErrorResponse($"'{fork}' is not a git checkout (no .git entry); check_updates needs one.");

            cancellationToken.ThrowIfCancellationRequested();
            object result = await Task.Run(() => CheckUpdatesBlocking(fork, forkAddon, installedAddon), cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            return result;
        }

        /// <summary>Git and file work for check_updates; touches no Unity API so it can run on any thread.</summary>
        internal static object CheckUpdatesBlocking(string fork, string forkAddon, string installedAddon)
        {
            if (!TryGit(fork, "--version", out _, out string gitErr, 10000))
                return new ErrorResponse($"git is not available: {gitErr}");

            TryGit(fork, "log -1 --format=%h%x09%ad%x09%s --date=short", out string head, out _);
            TryGit(fork, "status --porcelain", out string porcelain, out _);
            TryGit(fork, "remote", out string remotesRaw, out _);
            var remotes = remotesRaw.Split(new[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries).Select(r => r.Trim()).Where(r => r.Length > 0).ToList();

            var perRemote = new JArray();
            int totalBehind = 0;
            foreach (
                string remote in remotes.OrderBy(r =>
                    r == "upstream" ? 0
                    : r == "origin" ? 1
                    : 2
                )
            )
            {
                var entry = new JObject { ["remote"] = remote };
                TryGit(fork, $"remote get-url -- {QuoteGitArgument(remote)}", out string url, out _);
                entry["url"] = RedactRemoteUrl(url.Trim());

                bool fetched = TryGit(fork, $"fetch --quiet -- {QuoteGitArgument(remote)}", out _, out string fetchErr, 90000);
                entry["fetched"] = fetched;
                if (!fetched)
                    entry["fetch_error"] = Truncate(fetchErr, 300);

                string branch = "main";
                if (
                    TryGit(fork, $"symbolic-ref --short {QuoteGitArgument("refs/remotes/" + remote + "/HEAD")}", out string sym, out _)
                    && sym.Trim().StartsWith(remote + "/", StringComparison.Ordinal)
                )
                    branch = sym.Trim().Substring(remote.Length + 1);
                else if (
                    !TryGit(fork, $"rev-parse --verify --quiet {QuoteGitArgument("refs/remotes/" + remote + "/main")}", out _, out _)
                    && TryGit(fork, $"rev-parse --verify --quiet {QuoteGitArgument("refs/remotes/" + remote + "/master")}", out _, out _)
                )
                    branch = "master";
                entry["branch"] = branch;

                if (
                    TryGit(
                        fork,
                        $"rev-list --left-right --count {QuoteGitArgument("HEAD...refs/remotes/" + remote + "/" + branch)}",
                        out string counts,
                        out string cErr
                    )
                )
                {
                    var parts = counts.Trim().Split('\t', ' ');
                    int ahead = parts.Length > 0 && int.TryParse(parts[0], out int a) ? a : 0;
                    int behind = parts.Length > 1 && int.TryParse(parts[1], out int bb) ? bb : 0;
                    entry["local_ahead"] = ahead;
                    entry["behind"] = behind;
                    if (remote == "upstream" || remotes.Count == 1)
                        totalBehind += behind;

                    TryGit(
                        fork,
                        $"log --format=%h%x20%ad%x20%s --date=short -n 20 {QuoteGitArgument("HEAD..refs/remotes/" + remote + "/" + branch)}",
                        out string log,
                        out _
                    );
                    entry["new_commits"] = new JArray(log.Split(new[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries));
                }
                else
                {
                    entry["error"] = Truncate(cErr, 300);
                }
                perRemote.Add(entry);
            }

            string forkMd5 = File.Exists(forkAddon) ? FileMd5(forkAddon) : null;
            string installedMd5 = installedAddon != null && File.Exists(installedAddon) ? FileMd5(installedAddon) : null;
            bool addonInSync = forkMd5 != null && forkMd5 == installedMd5;

            int dirty = porcelain.Split(new[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries).Length;
            var recommendations = new List<string>();
            if (totalBehind > 0)
                recommendations.Add($"Checkout is {totalBehind} commit(s) behind upstream: merge upstream into it.");
            if (!addonInSync)
                recommendations.Add("Installed Blender addon differs from the checkout's addon.py: run sync_addon, then restart Blender.");
            if (dirty > 0)
                recommendations.Add($"Checkout has {dirty} uncommitted change(s).");
            if (recommendations.Count == 0)
                recommendations.Add("Everything is up to date.");

            var data = new JObject
            {
                ["fork_path"] = fork,
                ["head"] = head.Trim(),
                ["uncommitted_changes"] = dirty,
                ["remotes"] = perRemote,
                ["fork_addon_md5"] = forkMd5,
                ["installed_addon_path"] = installedAddon,
                ["installed_addon_md5"] = installedMd5,
                ["addon_in_sync"] = addonInSync,
                ["recommendations"] = new JArray(recommendations),
            };
            return new SuccessResponse(string.Join(" ", recommendations), data);
        }

        // -------------------------------------------------------------- sync_addon

        /// <summary>Copies the checkout's addon.py over Blender's installed copy, keeping a .bak of the old file.</summary>
        private static object SyncAddon(bool force)
        {
            if (!BlenderBridgePrefs.IsForkConfigured)
                return new ErrorResponse(NotConfiguredMessage);
            string src = BlenderBridgePrefs.ForkAddonPath;
            string dst = BlenderBridgePrefs.InstalledAddonPath;
            if (!File.Exists(src))
                return new ErrorResponse($"addon.py not found in the checkout at {src}.");
            if (dst == null)
                return new ErrorResponse("Could not locate Blender's user addons directory. Set it in Window > MCP for Unity > Generative > Blender Bridge.");

            string srcMd5 = FileMd5(src);
            string dstMd5 = File.Exists(dst) ? FileMd5(dst) : null;
            if (!force && srcMd5 == dstMd5)
                return new SuccessResponse(
                    "Installed addon already matches the checkout; nothing copied.",
                    new
                    {
                        source = src,
                        destination = dst,
                        md5 = srcMd5,
                        copied = false,
                    }
                );

            Directory.CreateDirectory(Path.GetDirectoryName(dst));
            string backup = null;
            if (File.Exists(dst))
            {
                backup = dst + ".bak";
                File.Copy(dst, backup, true);
            }
            File.Copy(src, dst, true);

            return new SuccessResponse(
                "Copied addon.py into Blender. Restart Blender (or Reload Scripts) and press 'Connect to MCP server' again.",
                new
                {
                    source = src,
                    destination = dst,
                    backup,
                    previous_md5 = dstMd5,
                    new_md5 = srcMd5,
                    copied = true,
                }
            );
        }

        // ------------------------------------------------------------------ helpers

        /// <summary>Owns only an acknowledged bridge export until a successful response exposes its path.</summary>
        internal sealed class TemporaryBlenderExport : IDisposable
        {
            private readonly string path;
            private bool preserved;

            internal TemporaryBlenderExport(string path) => this.path = path;

            internal object Preserve(object response)
            {
                preserved = true;
                return response;
            }

            public void Dispose()
            {
                if (preserved)
                    return;
                try
                {
                    File.Delete(path);
                }
                catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
                {
                    McpLog.Warn($"Could not remove failed Blender export ({ex.GetType().Name}).");
                }
            }
        }

        internal static string StageExportForImport(string exportPath, CancellationToken cancellationToken = default)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string relative = "Assets/.BlenderBridge/" + Guid.NewGuid().ToString("N") + "/" + Path.GetFileName(exportPath);
            string staged = AssetGenPaths.ToAbsolute(relative);
            string stagingDirectory = Path.GetDirectoryName(staged);
            string stagingRoot = Path.GetDirectoryName(stagingDirectory);
            bool ownsRoot = false;
            bool ownsDirectory = false;
            try
            {
                if (!Directory.Exists(stagingRoot))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    Directory.CreateDirectory(stagingRoot);
                    ownsRoot = true;
                }
                if (Directory.Exists(stagingDirectory) || File.Exists(stagingDirectory))
                    throw new IOException("The Blender staging directory already exists.");
                cancellationToken.ThrowIfCancellationRequested();
                Directory.CreateDirectory(stagingDirectory);
                ownsDirectory = true;
                staged = AssetGenPaths.ToAbsolute(relative);
                cancellationToken.ThrowIfCancellationRequested();
                File.Copy(exportPath, staged);
                cancellationToken.ThrowIfCancellationRequested();
                return staged;
            }
            catch
            {
                try
                {
                    staged = AssetGenPaths.ToAbsolute(relative);
                    if (ownsDirectory)
                    {
                        File.Delete(staged);
                        Directory.Delete(stagingDirectory);
                    }
                    if (ownsRoot)
                        Directory.Delete(stagingRoot);
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Could not remove failed Blender staging directory ({ex.GetType().Name}).");
                }
                throw;
            }
        }

        internal static string QuoteGitArgument(string value)
        {
            var quoted = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\\')
                {
                    slashes++;
                    continue;
                }
                quoted.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
                quoted.Append(c);
                slashes = 0;
            }
            quoted.Append('\\', slashes * 2);
            return quoted.Append('"').ToString();
        }

        /// <summary>Runs one git command with no prompts; false on non-zero exit, timeout, or missing git.</summary>
        private static bool TryGit(string workingDir, string args, out string stdout, out string stderr, int timeoutMs = 30000)
        {
            stdout = string.Empty;
            stderr = string.Empty;
            try
            {
                var elapsed = Stopwatch.StartNew();
                var psi = new ProcessStartInfo("git", args)
                {
                    WorkingDirectory = workingDir,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8,
                };
                psi.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0";
                using var proc = Process.Start(psi);
                if (proc == null)
                {
                    stderr = "failed to start git";
                    return false;
                }
                var outTask = proc.StandardOutput.ReadToEndAsync();
                var errTask = proc.StandardError.ReadToEndAsync();
                if (!proc.WaitForExit(timeoutMs))
                {
                    try
                    {
                        proc.Kill();
                    }
                    catch { /* already gone */ }
                    stderr = $"git timed out after {timeoutMs} ms";
                    return false;
                }
                int remainingMs = Math.Max(0, timeoutMs - (int)elapsed.ElapsedMilliseconds);
                if (!Task.WaitAll(new Task[] { outTask, errTask }, remainingMs))
                {
                    stderr = $"git output timed out after {timeoutMs} ms";
                    return false;
                }
                // Git diagnostics may echo credential-bearing URLs (including redirects),
                // helper output or bare tokens. Do not send them to MCP clients or editor logs.
                // Drain both pipes, but only expose stdout from a successful command.
                string output = outTask.Result;
                _ = errTask.Result;
                if (proc.ExitCode != 0)
                {
                    stderr = $"git exited with code {proc.ExitCode}. Check the checkout and remote configuration locally.";
                    return false;
                }
                stdout = output;
                return true;
            }
            catch (Exception)
            {
                stderr = "Unable to run git. Check that git is installed and the checkout is accessible.";
                return false;
            }
        }

        /// <summary>
        /// Timestamp plus a random tail for temp file names, so concurrent commands (menu + MCP) started
        /// in the same second never share a screenshot or export path.
        /// </summary>
        private static string UniqueSuffix() => $"{DateTime.Now:yyyyMMdd_HHmmss}_{Guid.NewGuid().ToString("N").Substring(0, 8)}";

        /// <summary>
        /// Strips user info, query and fragment from a remote URL so credentials embedded as
        /// https://user:token@host/..., ?token=... or #access_token=... never reach the MCP response
        /// or the editor log. scp-style git@host:path is returned unchanged (no secret there).
        /// </summary>
        internal static string RedactRemoteUrl(string url)
        {
            if (string.IsNullOrEmpty(url))
                return url ?? string.Empty;
            if (Uri.TryCreate(url, UriKind.Absolute, out Uri uri) && !string.IsNullOrEmpty(uri.Host))
            {
                bool clean = string.IsNullOrEmpty(uri.UserInfo) && string.IsNullOrEmpty(uri.Query) && string.IsNullOrEmpty(uri.Fragment);
                if (clean)
                    return url;
                return new UriBuilder(uri)
                {
                    UserName = string.Empty,
                    Password = string.Empty,
                    Query = string.Empty,
                    Fragment = string.Empty,
                }.Uri.ToString();
            }
            // Not a parseable absolute URI: drop anything after ? or # and mask a user:secret@ prefix.
            int cut = url.IndexOfAny(new[] { '?', '#' });
            if (cut >= 0)
                url = url.Substring(0, cut);
            int scheme = url.IndexOf("://", StringComparison.Ordinal);
            int at = url.IndexOf('@');
            if (scheme >= 0 && at > scheme)
                return url.Substring(0, scheme + 3) + "***@" + url.Substring(at + 1);
            return url;
        }

        /// <summary>Absolute project folder (parent of Assets/), forward slashes.</summary>
        private static string ProjectRoot() => Path.GetDirectoryName(Application.dataPath).Replace('\\', '/');

        /// <summary>Lower-case hex MD5 of a file; shared with the Asset Gen panel's addon-sync indicator.</summary>
        internal static string FileMd5(string path)
        {
            using var md5 = MD5.Create();
            using var fs = File.OpenRead(path);
            return BitConverter.ToString(md5.ComputeHash(fs)).Replace("-", "").ToLowerInvariant();
        }

        /// <summary>Makes a caller-supplied name safe to use as a file and GameObject name.</summary>
        private static string SanitizeName(string raw)
        {
            var invalid = Path.GetInvalidFileNameChars();
            var sb = new StringBuilder();
            foreach (char c in raw.Trim())
                sb.Append(invalid.Contains(c) || c == '/' || c == '\\' ? '_' : c);
            string s = sb.ToString().Trim('_', '.', ' ');
            return string.IsNullOrEmpty(s) ? "BlenderModel" : s;
        }

        /// <summary>Reads a [x, y, z] position from a JSON array or a stringified array; origin when absent.</summary>
        private static Vector3 ParsePosition(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
                return Vector3.zero;
            JArray arr = token as JArray;
            if (arr == null && token.Type == JTokenType.String)
            {
                try
                {
                    arr = JArray.Parse(token.ToString());
                }
                catch (Newtonsoft.Json.JsonException ex)
                {
                    throw new ArgumentException("Position must contain three finite numeric components.", ex);
                }
            }
            if (arr == null || arr.Count < 3)
                throw new ArgumentException("Position must contain three finite numeric components.");
            try
            {
                return new Vector3(arr[0].ReadScalar<float>(), arr[1].ReadScalar<float>(), arr[2].ReadScalar<float>());
            }
            catch (ArgumentException ex)
            {
                throw new ArgumentException("Position must contain three finite numeric components.", ex);
            }
        }

        internal static object MapImportFailure(JObject response) =>
            new ErrorResponse($"Import failed: {response["error"] ?? response["message"]}", response["data"]?.DeepClone());

        internal static GameObject PlaceImportedModel(string assetPath, string name, Vector3 position, Scene destinationScene) =>
            PlaceImportedModelWithCancellation(assetPath, name, position, destinationScene, CancellationToken.None);

        private static GameObject PlaceImportedModelWithCancellation(
            string assetPath,
            string name,
            Vector3 position,
            Scene destinationScene,
            CancellationToken cancellationToken
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (prefab == null)
                return null;
            cancellationToken.ThrowIfCancellationRequested();
            var go = PrefabUtility.InstantiatePrefab(prefab, destinationScene) as GameObject;
            if (go == null)
            {
                cancellationToken.ThrowIfCancellationRequested();
                go = UnityEngine.Object.Instantiate(prefab);
                cancellationToken.ThrowIfCancellationRequested();
                SceneManager.MoveGameObjectToScene(go, destinationScene);
            }
            cancellationToken.ThrowIfCancellationRequested();
            go.name = name;
            cancellationToken.ThrowIfCancellationRequested();
            go.transform.position = position;
            cancellationToken.ThrowIfCancellationRequested();
            return go;
        }

        /// <summary>World-space default-pose mesh envelope, using the same bounded measurement as import.</summary>
        private static bool TryGetWorldBounds(GameObject go, out Bounds bounds) =>
            ModelHierarchyBounds.TryMeasure(go, go.transform.localToWorldMatrix, out bounds, out _);

        /// <summary>Trims and caps a string for inclusion in messages.</summary>
        private static string Truncate(string s, int max)
        {
            if (string.IsNullOrEmpty(s))
                return s ?? string.Empty;
            s = s.Trim();
            return s.Length <= max ? s : s.Substring(0, max) + "…";
        }
    }
}
