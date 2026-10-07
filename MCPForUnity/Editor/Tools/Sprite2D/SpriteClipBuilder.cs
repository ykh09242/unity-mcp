using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnity.Editor.Tools.Sprite2D
{
    internal class SpriteClipInfo
    {
        public string name;
        public string path;
        public int frame_count;
        public float fps;
        public bool loop;
        public float duration;
    }

    internal static class SpriteClipBuilder
    {
        /// <summary>
        /// Builds AnimationClips out of sliced sprites and saves them as .anim assets.
        /// params:
        ///   path         - sprite texture asset path
        ///   clips        - [{name, start_frame, end_frame, fps (opt, def=12), loop (opt)}]
        ///   output_dir   - where the clips are written (default: the sprite's own folder)
        ///   overwrite    - bool (default false); an existing clip is kept unless this is true
        /// </summary>
        public static object SetupClips(JObject @params, SpriteDiagnosticBuilder diagnostics)
        {
            if (!SpriteParams.TryReadAssetPath(@params, "path", out string path, out string pathError))
                return diagnostics.Fail("BAD_PARAM", pathError);

            var clipsToken = @params["clips"] as JArray;
            if (clipsToken == null || clipsToken.Count == 0)
                return diagnostics.Fail("BAD_PARAM", "'clips' array is required.");

            if (!SpriteParams.TryReadBool(@params, "overwrite", false, out bool overwrite, out string overwriteError))
                return diagnostics.Fail("BAD_PARAM", overwriteError);

            var clips = CreateClips(path, clipsToken, @params["output_dir"]?.ToString(), overwrite, diagnostics);
            if (diagnostics.HasErrors)
                return diagnostics.Fail();

            return new
            {
                success     = true,
                sprite_path = path,
                clip_count  = clips.Count,
                clips,
                diagnostics = diagnostics.Build(),
            };
        }

        /// <summary>`path` is already sanitized; `outputDir` null means the sprite's own folder.</summary>
        internal static List<SpriteClipInfo> CreateClips(string path, JArray clipsToken, string outputDir,
                                                          bool overwrite, SpriteDiagnosticBuilder diagnostics)
        {
            var created = new List<SpriteClipInfo>();

            var allSprites = AssetDatabase.LoadAllAssetsAtPath(path)
                .OfType<Sprite>()
                .OrderBy(s => NaturalSortKey(s.name))
                .ToArray();

            if (allSprites.Length == 0)
            {
                diagnostics.AddError("NOT_FOUND", $"No sprites found at '{path}'. Run slice_sheet first.");
                return created;
            }

            outputDir = outputDir ?? Path.GetDirectoryName(path)?.Replace('\\', '/') ?? "Assets";

            if (!SpriteParams.TryReadAssetPath(new JObject { ["output_dir"] = outputDir }, "output_dir", out outputDir, out string dirError))
            {
                diagnostics.AddError("BAD_PARAM", dirError);
                return created;
            }
            foreach (JToken clipToken in clipsToken)
            {
                // Measured: a non-object clips entry threw InvalidCastException on a typed cast.
                if (!(clipToken is JObject clipDef))
                {
                    diagnostics.AddWarning("CLIP_NOT_AN_OBJECT", "A clips entry is not an object - skipped.", "Each clip must be an object with a 'name'.");
                    continue;
                }

                string clipName = clipDef["name"]?.ToString();
                if (string.IsNullOrEmpty(clipName))
                { diagnostics.AddWarning("CLIP_NO_NAME", "Clip name is missing — skipped.", "Add a 'name' field to each clip definition."); continue; }

                // Measured: "nested/walk" either threw from CreateAsset or, where the folder
                // existed, wrote the clip outside output_dir.
                if (clipName.Contains("/") || clipName.Contains("\\"))
                {
                    diagnostics.AddWarning("CLIP_BAD_NAME", $"Clip '{clipName}': the name cannot contain a path separator - skipped.", "Remove '..' and path separators from the clip name.");
                    continue;
                }

                // Sequential, not chained with ||: a short-circuited call leaves its out
                // parameter unassigned and the second value is used below.
                int endFrame = allSprites.Length - 1;
                bool rangeOk = SpriteParams.TryReadWholeNumber(clipDef, "start_frame", 0, out int startFrame, out string frameError);
                if (rangeOk) rangeOk = SpriteParams.TryReadWholeNumber(clipDef, "end_frame", allSprites.Length - 1, out endFrame, out frameError);
                if (!rangeOk)
                {
                    diagnostics.AddWarning("CLIP_BAD_RANGE", $"Clip '{clipName}': {frameError} - skipped.", "start_frame and end_frame must be whole numbers within a sprite index.");
                    continue;
                }
                if (endFrame > allSprites.Length - 1)
                {
                    // Skip/Take clamps silently: an end_frame past the last sprite produced a
                    // shorter clip and reported success.
                    diagnostics.AddWarning("CLIP_BAD_RANGE", $"Clip '{clipName}': end_frame {endFrame} is past the last sprite index {allSprites.Length - 1} - skipped.", $"This sheet has {allSprites.Length} sprites, so end_frame must be at most {allSprites.Length - 1}.");
                    continue;
                }
                if (startFrame < 0 || endFrame < startFrame)
                {
                    // Skip yields everything for a negative count: start_frame=-2 with
                    // end_frame=3 wrote frames 0..5 as a success.
                    diagnostics.AddWarning("CLIP_BAD_RANGE", $"Clip '{clipName}': frame range [{startFrame},{endFrame}] is invalid - skipped.", "start_frame must be 0 or more, and end_frame must not be below start_frame.");
                    continue;
                }
                // `fps <= 0f` is false for NaN, so a NaN rate wrote a clip of NaN keyframe
                // times and reported success.
                if (!SpriteParams.TryReadFiniteFloat(clipDef, "fps", 12f, out float fps, out string fpsError))
                {
                    diagnostics.AddWarning("CLIP_BAD_FPS", $"Clip '{clipName}': {fpsError} - skipped.", "Leave fps out to use the default of 12.");
                    continue;
                }
                if (fps <= 0f)
                {
                    // Times are i / fps, so a non-positive rate puts every key at infinity.
                    diagnostics.AddWarning("CLIP_BAD_FPS", $"Clip '{clipName}': fps must be greater than 0, got {fps} - skipped.", "Leave fps out to use the default of 12.");
                    continue;
                }

                int frameCount = endFrame - startFrame + 1;
                float duration = frameCount / fps;
                // A finite positive rate can still overflow the key times and response duration.
                if (float.IsInfinity(duration))
                {
                    diagnostics.AddWarning("CLIP_BAD_FPS", $"Clip '{clipName}': fps is too small for {frameCount} frames - skipped.", "Increase fps so the clip duration is finite.");
                    continue;
                }

                var entry      = SpriteNamingDetector.Detect(clipName);
                if (!SpriteParams.TryReadBool(clipDef, "loop", entry.Loop, out bool loop, out string loopError))
                {
                    diagnostics.AddWarning("CLIP_BAD_LOOP", $"Clip '{clipName}': {loopError} - skipped.", "Leave loop out to let the clip name decide.");
                    continue;
                }

                if (frameCount <= 2)
                    diagnostics.AddWarning("LOW_FRAME_COUNT", $"Clip '{clipName}' has only {frameCount} frame(s) — animation may not be visible.");

                // Refusals come before the allocation: a `new AnimationClip` that never becomes
                // an asset leaks.
                if (!SpriteParams.TryReadAssetPath(new JObject { ["path"] = $"{outputDir}/{clipName}.anim" },
                    "path", out string clipPath, out _))
                {
                    diagnostics.AddWarning("CLIP_BAD_NAME", $"Clip '{clipName}': the name cannot be used as a file name - skipped.", "Remove '..', path separators and characters like : * ? \" < > | from the clip name.");
                    continue;
                }

                if (!AssetDatabase.IsValidFolder(outputDir))
                    CreateFolders(outputDir);

                string fullClipPath = AssetPathUtility.GetFullAssetPath(clipPath);
                if (Directory.Exists(fullClipPath))
                {
                    diagnostics.AddWarning("CLIP_BAD_PATH", $"Clip '{clipName}': '{clipPath}' is a folder - skipped.", "Choose a different clip name or output_dir.");
                    continue;
                }
                if (!overwrite && (File.Exists(fullClipPath)
                    || AssetDatabase.LoadMainAssetAtPath(clipPath) != null
                    || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(clipPath, AssetPathToGUIDOptions.OnlyExistingAssets))))
                {
                    // Measured: an unrelated clip at this path was replaced by a request carrying
                    // no overwrite field. Same policy as the controller builder: destruction
                    // needs authorisation.
                    diagnostics.AddWarning("CLIP_EXISTS", $"Clip '{clipName}': an asset already exists at '{clipPath}' - skipped.", "Set overwrite=true to replace it.", "Choose a different clip name or output_dir.");
                    continue;
                }

                var clip = new AnimationClip();
                try
                {
                    clip.frameRate = fps;

                    var binding = new EditorCurveBinding
                    {
                        type         = typeof(SpriteRenderer),
                        path         = "",
                        propertyName = "m_Sprite",
                    };

                    var keyframes = new ObjectReferenceKeyframe[frameCount];
                    for (int i = 0; i < frameCount; i++)
                    {
                        keyframes[i] = new ObjectReferenceKeyframe
                        {
                            time  = i / fps,
                            value = allSprites[startFrame + i],
                        };
                    }

                    AnimationUtility.SetObjectReferenceCurve(clip, binding, keyframes);

                    var settings = AnimationUtility.GetAnimationClipSettings(clip);
                    settings.loopTime = loop;
                    AnimationUtility.SetAnimationClipSettings(clip, settings);

                    // CreateAsset replaces an existing asset itself; deleting first left nothing at
                    // the path when the replacement failed to be written. Reference equality, not a
                    // null check: a failed replacement leaves the old asset loadable at the path.
                    AssetDatabase.CreateAsset(clip, clipPath);
                    if (AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath) != clip)
                    {
                        diagnostics.AddWarning("CLIP_WRITE_FAILED", $"Clip '{clipName}': Unity did not write '{clipPath}' - skipped.", "Check the Unity console for the AssetDatabase error.");
                        continue;
                    }

                    created.Add(new SpriteClipInfo
                    {
                        name        = clipName,
                        path        = clipPath,
                        frame_count = frameCount,
                        fps         = fps,
                        loop        = loop,
                        duration    = duration,
                    });
                }
                finally
                {
                    if (clip != null && !AssetDatabase.Contains(clip))
                        Object.DestroyImmediate(clip);
                }
            }

            if (created.Count > 0)
                AssetDatabase.SaveAssets();
            return created;
        }

        // Plain string sort puts hero_10 before hero_2, which reorders the animation.
        private static string NaturalSortKey(string name)
        {
            var sb = new System.Text.StringBuilder();
            int i = 0;
            while (i < name.Length)
            {
                if (char.IsDigit(name[i]))
                {
                    int start = i;
                    while (i < name.Length && char.IsDigit(name[i])) i++;
                    // Left-pad the run of digits so a lexicographic sort compares them numerically.
                    sb.Append(name.Substring(start, i - start).PadLeft(10, '0'));
                }
                else
                {
                    sb.Append(name[i++]);
                }
            }
            return sb.ToString();
        }

        /// <summary>Creates an asset folder and any missing parents above it.</summary>
        internal static void CreateFolders(string path)
        {
            path = AssetPathUtility.GetContainedAssetPath(path);
            if (AssetDatabase.IsValidFolder(path))
                return;
            string parent = Path.GetDirectoryName(path)?.Replace('\\', '/') ?? "Assets";
            if (!AssetDatabase.IsValidFolder(parent))
                CreateFolders(parent);
            string folderName = Path.GetFileName(path);
            if (!string.IsNullOrEmpty(folderName))
            {
                AssetPathUtility.GetFullAssetPath(path);
                AssetDatabase.CreateFolder(parent, folderName);
            }
        }
    }
}
