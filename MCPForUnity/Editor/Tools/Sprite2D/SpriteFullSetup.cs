using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.Sprite2D
{
    internal static class SpriteFullSetup
    {
        /// <summary>
        /// params:
        ///   path             - sprite texture path (required)
        ///   cols             - grid columns (required)
        ///   rows             - grid rows (default 1)
        ///   frame_width      - alternative to cols: explicit frame size
        ///   frame_height     - alternative to rows: explicit frame size
        ///   clips            - [{name, start_frame, end_frame, fps, loop}];
        ///                      omitted means every frame becomes one clip named animation_name
        ///   animation_name   - used when clips is omitted (default: the file name)
        ///   controller_path  - default: the sprite's own folder
        ///   overwrite        - bool (default false)
        ///   add_to_scene     - add an Animator to a target GameObject
        ///   scene_target     - GameObject name
        /// </summary>
        public static object Run(JObject @params, SpriteDiagnosticBuilder diagnostics)
        {
            if (!SpriteParams.TryReadAssetPath(@params, "path", out string path, out string pathError))
                return diagnostics.Fail("BAD_PARAM", pathError);

            if (
                !SpriteParams.TryReadBool(@params, "overwrite", false, out bool overwrite, out string flagError)
                || !SpriteParams.TryReadBool(@params, "add_to_scene", false, out bool addToScene, out flagError)
            )
                return diagnostics.Fail("BAD_PARAM", flagError);

            if (
                !SpriteParams.TryReadString(
                    @params,
                    "output_dir",
                    Path.GetDirectoryName(path)?.Replace('\\', '/') ?? "Assets",
                    out string outputDir,
                    out pathError
                )
            )
                return diagnostics.Fail("BAD_PARAM", pathError);
            if (!SpriteParams.TryReadAssetPath(new JObject { ["output_dir"] = outputDir }, "output_dir", out outputDir, out pathError))
                return diagnostics.Fail("BAD_PARAM", pathError);

            if (
                !SpriteParams.TryReadString(
                    @params,
                    "controller_path",
                    $"{outputDir}/{Path.GetFileNameWithoutExtension(path)}_Controller.controller",
                    out string controllerPath,
                    out pathError
                )
            )
                return diagnostics.Fail("BAD_PARAM", pathError);
            if (!SpriteControllerBuilder.TryResolveControllerPath(controllerPath, out controllerPath, out pathError))
                return diagnostics.Fail("BAD_PARAM", pathError);

            var clipsToken = @params["clips"] as JArray;
            if (@params["clips"] != null && @params["clips"].Type != JTokenType.Null && clipsToken == null)
                return diagnostics.Fail("BAD_PARAM", "'clips' must be an array of clip definitions.");

            string animName = null;
            if (
                (clipsToken == null || clipsToken.Count == 0)
                && !SpriteParams.TryReadString(@params, "animation_name", Path.GetFileNameWithoutExtension(path), out animName, out pathError)
            )
                return diagnostics.Fail("BAD_PARAM", pathError);

            if (clipsToken == null || clipsToken.Count == 0)
            {
                clipsToken = new JArray(
                    new JObject
                    {
                        ["name"] = animName,
                        ["start_frame"] = 0,
                        ["fps"] = 12,
                    }
                );
            }

            string sceneTarget = null;
            if (addToScene)
            {
                if (!SpriteParams.TryReadString(@params, "scene_target", null, out sceneTarget, out pathError))
                    return diagnostics.Fail("BAD_PARAM", pathError);
                ResolveSceneTarget(sceneTarget, diagnostics);
                if (diagnostics.HasErrors)
                    return Stop("add_to_scene", diagnostics);
            }

            string refusedStep = PreflightDestinations(clipsToken, outputDir, controllerPath, overwrite, PredictSpriteCount(@params), diagnostics);
            if (refusedStep != null)
                return Stop(refusedStep, diagnostics);

            // ── Step 1: Slice ──────────────────────────────────────────────────

            SpriteImportSetup.SliceSheet(@params, diagnostics);
            if (diagnostics.HasErrors)
                return Stop("slice_sheet", diagnostics);

            // ── Step 2: Clips ──────────────────────────────────────────────────

            var clips = SpriteClipBuilder.CreateClips(path, clipsToken, outputDir, overwrite, diagnostics);
            // A repeated run skips every clip, and the controller step then failed with "No valid
            // clips loaded.", which named neither the cause nor the way past it.
            if (clips.Count == 0 && diagnostics.Build().Count(d => d.code == "CLIP_EXISTS") == clipsToken.Count)
                diagnostics.AddError(
                    "ALL_CLIPS_EXIST",
                    "Every requested clip already exists, so no clip was written and full_setup stopped before the controller step.",
                    "Set overwrite=true to replace the clips and the controller.",
                    "Call setup_controller with the existing .anim paths, which the CLIP_EXISTS warnings name, and overwrite=true if the controller already exists."
                );
            if (diagnostics.HasErrors)
                return Stop("setup_clips", diagnostics);

            // ── Step 3: Controller ─────────────────────────────────────────────

            var controller = SpriteControllerBuilder.BuildController(
                clips.Select(c => (c.name, c.path, (bool?)c.loop)),
                controllerPath,
                overwrite,
                diagnostics
            );
            if (diagnostics.HasErrors)
                return Stop("setup_controller", diagnostics);

            // ── Step 4: Add to scene ───────────────────────────────────────────

            if (addToScene)
            {
                // Imports can invoke project callbacks; re-resolve at the actual attachment
                // boundary rather than trusting a target observed before the asset writes.
                var go = ResolveSceneTarget(sceneTarget, diagnostics);
                if (go != null)
                {
                    var asset = AssetDatabase.LoadAssetAtPath<UnityEditor.Animations.AnimatorController>(controller.path);
                    if (asset != null)
                    {
                        // `??` compares references and never sees Unity's overloaded ==, so
                        // AddComponent was skipped and the next line threw. Measured: this path
                        // never once worked.
                        var animator = go.GetComponent<UnityEngine.Animator>();
                        if (animator == null)
                        {
                            UnityEditor.Undo.RecordObject(go, "Add Animator Component");
                            animator = UnityEditor.Undo.AddComponent<UnityEngine.Animator>(go);
                        }
                        // The clips bind to SpriteRenderer.m_Sprite: without one the Animator
                        // plays into nothing and the call still reports success.
                        if (go.GetComponent<UnityEngine.SpriteRenderer>() == null)
                        {
                            UnityEditor.Undo.AddComponent<UnityEngine.SpriteRenderer>(go);
                            diagnostics.AddWarning(
                                "SCENE_SPRITE_RENDERER_ADDED",
                                $"'{sceneTarget}' had no SpriteRenderer, so one was added for the clips to drive."
                            );
                        }
                        // Recorded and dirtied like the sibling controller_assign path.
                        UnityEditor.Undo.RecordObject(animator, "Assign AnimatorController");
                        animator.runtimeAnimatorController = asset;
                        if (PrefabUtility.IsPartOfPrefabInstance(animator))
                            PrefabUtility.RecordPrefabInstancePropertyModifications(animator);
                        EditorUtility.SetDirty(animator);
                        EditorUtility.SetDirty(go);
                        if (go.scene.IsValid() && go.scene.isLoaded)
                            UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(go.scene);
                    }
                    else
                    {
                        diagnostics.AddError(
                            "SCENE_CONTROLLER_NOT_LOADED",
                            $"The controller at '{controller.path}' could not be loaded, so '{sceneTarget}' was left unchanged.",
                            "Check the controller_path in the response."
                        );
                    }
                }
            }

            // Shaped like the other three steps' refusals rather than like a success with the
            // flag flipped: a step-4 failure used to be the one refusal in the tool carrying
            // neither 'step' nor 'message', leaving the reason only inside the diagnostics
            // array. The asset fields stay on it, because by this point steps 1-3 have written
            // and the caller needs to know what is already on disk.
            if (diagnostics.HasErrors)
                return new
                {
                    success = false,
                    step = "add_to_scene",
                    message = diagnostics.FirstError,
                    sprite_path = path,
                    controller_path = controller.path,
                    state_count = controller.stateCount,
                    clip_count = clips.Count,
                    diagnostics = diagnostics.Build(),
                };

            return new
            {
                success = true,
                sprite_path = path,
                controller_path = controller.path,
                state_count = controller.stateCount,
                clip_count = clips.Count,
                diagnostics = diagnostics.Build(),
            };
        }

        private static UnityEngine.GameObject ResolveSceneTarget(string sceneTarget, SpriteDiagnosticBuilder diagnostics)
        {
            if (string.IsNullOrWhiteSpace(sceneTarget))
            {
                diagnostics.AddError(
                    "SCENE_TARGET_MISSING",
                    "'add_to_scene' is true but 'scene_target' is empty.",
                    "Pass 'scene_target' with the GameObject name.",
                    "Set add_to_scene=false."
                );
                return null;
            }

            // Shared lookup includes inactive objects and refuses duplicate names.
            var matches = GameObjectLookup.SearchGameObjects("by_name", sceneTarget, includeInactive: true);
            if (matches.Count > 1)
            {
                diagnostics.AddError(
                    "SCENE_TARGET_AMBIGUOUS",
                    $"{matches.Count} GameObjects are named '{sceneTarget}'; nothing was attached.",
                    "Rename the target or pick a unique name."
                );
                return null;
            }
            var go = matches.Count == 1 ? GameObjectLookup.FindById(matches[0]) : null;
            if (go == null)
                diagnostics.AddError(
                    "SCENE_TARGET_NOT_FOUND",
                    $"GameObject '{sceneTarget}' not found in scene.",
                    "Check GameObject name or open the correct scene first."
                );
            return go;
        }

        // Inspect predictable output refusals before the first importer or asset mutation.
        // The builders keep their checks too: project import callbacks can change the paths.
        private static int? PredictSpriteCount(JObject parameters)
        {
            // Explicit columns/rows fix the successful slice count without importing. If a
            // dimension is derived from source pixels, leave the count unknown here.
            if (
                !SpriteParams.TryReadWholeNumber(parameters, "cols", 0, out int cols, out _)
                || !SpriteParams.TryReadWholeNumber(parameters, "rows", 0, out int rows, out _)
                || !SpriteParams.TryReadWholeNumber(parameters, "frame_height", 0, out int frameHeight, out _)
            )
                return null;
            if ((parameters["rows"] == null || parameters["rows"].Type == JTokenType.Null) && frameHeight <= 0)
                rows = 1;
            long count = (long)cols * rows;
            return cols > 0 && rows > 0 && count <= int.MaxValue ? (int?)count : null;
        }

        private static string PreflightDestinations(
            JArray clips,
            string outputDir,
            string controllerPath,
            bool overwrite,
            int? spriteCount,
            SpriteDiagnosticBuilder diagnostics
        )
        {
            if (!AssetGenPaths.TryGetAssetsFolder(outputDir, out _))
            {
                diagnostics.AddError("BAD_PARAM", "'output_dir' must name a folder under Assets without an existing file at the destination or an ancestor.");
                return "setup_clips";
            }
            string controllerDir = Path.GetDirectoryName(controllerPath)?.Replace('\\', '/') ?? "Assets";
            if (!AssetGenPaths.TryGetAssetsFolder(controllerDir, out _))
            {
                diagnostics.AddError("BAD_PARAM", "'controller_path' must have a folder parent without an existing file at any ancestor.");
                return "setup_controller";
            }

            var plannedPaths = new List<string>();
            var skippedClips = new SpriteDiagnosticBuilder();
            int existingClips = 0;
            int folderClips = 0;
            bool uncertainClips = false;
            foreach (var clip in clips)
            {
                (string name, string path, int startFrame, int frameCount, float fps, bool loop, float duration) definition;
                if (spriteCount.HasValue)
                {
                    if (!SpriteClipDefinition.TryRead(clip, spriteCount.Value, outputDir, skippedClips, out definition))
                        continue;
                }
                else
                {
                    // With an unknown slice count, reject a path conflict only for a clip
                    // eligible at every possible positive count. Range/default-end and duration
                    // validity are monotonic between these endpoints; uncertain clips stay with
                    // the builders' execution checks rather than blocking a valid skipped clip.
                    var predictionDiagnostics = new SpriteDiagnosticBuilder();
                    if (
                        !SpriteClipDefinition.TryRead(clip, 1, outputDir, predictionDiagnostics, out definition)
                        || !SpriteClipDefinition.TryRead(clip, int.MaxValue, outputDir, predictionDiagnostics, out _)
                    )
                    {
                        uncertainClips = true;
                        continue;
                    }
                }
                string name = definition.name;
                string clipPath = definition.path;
                if (Directory.Exists(AssetPathUtility.GetFullAssetPath(clipPath)))
                {
                    folderClips++;
                    skippedClips.AddWarning(
                        "CLIP_BAD_PATH",
                        $"Clip '{name}': '{clipPath}' is a folder - skipped.",
                        "Choose a different clip name or output_dir."
                    );
                    continue;
                }
                if (!overwrite && AssetExists(clipPath))
                {
                    existingClips++;
                    skippedClips.AddWarning(
                        "CLIP_EXISTS",
                        $"Clip '{name}': an asset already exists at '{clipPath}' - skipped.",
                        "Set overwrite=true to replace it."
                    );
                    continue;
                }
                plannedPaths.Add(clipPath);
            }
            // A skipped destination can coexist with valid new clips. Preserve that successful
            // partial setup and let the builder emit its warnings exactly once at execution.
            if (!uncertainClips && plannedPaths.Count == 0 && existingClips + folderClips > 0)
            {
                foreach (var skipped in skippedClips.Build())
                    diagnostics.AddWarning(skipped.code, skipped.message, skipped.fix_options);
                if (existingClips == clips.Count)
                    diagnostics.AddError(
                        "ALL_CLIPS_EXIST",
                        "Every requested clip already exists, so full_setup stopped before changing the importer or assets.",
                        "Set overwrite=true to replace the clips and the controller.",
                        "Call setup_controller with the existing .anim paths, which the CLIP_EXISTS warnings name, and overwrite=true if the controller already exists."
                    );
                else
                    diagnostics.AddError(
                        "NO_CLIPS",
                        "No requested clip has a writable destination; full_setup stopped before changing the importer or assets.",
                        "Choose a different clip name or output_dir, or set overwrite=true for existing files."
                    );
                return "setup_clips";
            }

            plannedPaths.Add(controllerPath);
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            for (int i = 0; i < plannedPaths.Count; i++)
            {
                for (int j = i + 1; j < plannedPaths.Count; j++)
                {
                    string left = plannedPaths[i];
                    string right = plannedPaths[j];
                    if (left.StartsWith(right + "/", comparison) || right.StartsWith(left + "/", comparison))
                    {
                        diagnostics.AddError(
                            "OUTPUT_PATH_CONFLICT",
                            $"Requested outputs '{left}' and '{right}' cannot both be files.",
                            "Choose distinct output paths without a file ancestor."
                        );
                        return "setup_controller";
                    }
                }
            }
            if (!overwrite && AssetExists(controllerPath))
            {
                diagnostics.AddError("CONTROLLER_EXISTS", $"An asset already exists at '{controllerPath}'.", "Set overwrite=true to replace it.");
                return "setup_controller";
            }
            return null;
        }

        private static bool AssetExists(string path) =>
            File.Exists(AssetPathUtility.GetFullAssetPath(path))
            || AssetDatabase.LoadMainAssetAtPath(path) != null
            || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));

        private static object Stop(string step, SpriteDiagnosticBuilder diagnostics) =>
            new
            {
                success = false,
                step,
                message = diagnostics.FirstError,
                diagnostics = diagnostics.Build(),
            };
    }
}
