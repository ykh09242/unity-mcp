using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

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

            if (!SpriteParams.TryReadString(@params, "output_dir", null, out string outputDir, out string dirError))
                return diagnostics.Fail("BAD_PARAM", dirError);

            var clips = CreateClips(path, clipsToken, outputDir, overwrite, diagnostics);
            if (diagnostics.HasErrors)
                return diagnostics.Fail();

            return new
            {
                success = true,
                sprite_path = path,
                clip_count = clips.Count,
                clips,
                diagnostics = diagnostics.Build(),
            };
        }

        /// <summary>`path` is already sanitized; `outputDir` null means the sprite's own folder.</summary>
        internal static List<SpriteClipInfo> CreateClips(string path, JArray clipsToken, string outputDir, bool overwrite, SpriteDiagnosticBuilder diagnostics)
        {
            var created = new List<SpriteClipInfo>();

            var allSprites = AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().OrderBy(s => NaturalSortKey(s.name)).ToArray();

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
                if (!SpriteClipDefinition.TryRead(clipToken, allSprites.Length, outputDir, diagnostics, out var definition))
                    continue;
                var (clipName, clipPath, startFrame, frameCount, fps, loop, duration) = definition;

                using var folders = new AssetFolderScope();
                folders.EnsureFolder(outputDir);

                string fullClipPath = AssetPathUtility.GetFullAssetPath(clipPath);
                if (Directory.Exists(fullClipPath))
                {
                    diagnostics.AddWarning(
                        "CLIP_BAD_PATH",
                        $"Clip '{clipName}': '{clipPath}' is a folder - skipped.",
                        "Choose a different clip name or output_dir."
                    );
                    continue;
                }
                if (
                    !overwrite
                    && (
                        File.Exists(fullClipPath)
                        || AssetDatabase.LoadMainAssetAtPath(clipPath) != null
                        || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(clipPath, AssetPathToGUIDOptions.OnlyExistingAssets))
                    )
                )
                {
                    // Measured: an unrelated clip at this path was replaced by a request carrying
                    // no overwrite field. Same policy as the controller builder: destruction
                    // needs authorisation.
                    diagnostics.AddWarning(
                        "CLIP_EXISTS",
                        $"Clip '{clipName}': an asset already exists at '{clipPath}' - skipped.",
                        "Set overwrite=true to replace it.",
                        "Choose a different clip name or output_dir."
                    );
                    continue;
                }

                var clip = new AnimationClip();
                try
                {
                    clip.frameRate = fps;

                    var binding = new EditorCurveBinding
                    {
                        type = typeof(SpriteRenderer),
                        path = "",
                        propertyName = "m_Sprite",
                    };

                    var keyframes = new ObjectReferenceKeyframe[frameCount];
                    for (int i = 0; i < frameCount; i++)
                    {
                        keyframes[i] = new ObjectReferenceKeyframe { time = i / fps, value = allSprites[startFrame + i] };
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
                        diagnostics.AddWarning(
                            "CLIP_WRITE_FAILED",
                            $"Clip '{clipName}': Unity did not write '{clipPath}' - skipped.",
                            "Check the Unity console for the AssetDatabase error."
                        );
                        continue;
                    }

                    created.Add(
                        new SpriteClipInfo
                        {
                            name = clipName,
                            path = clipPath,
                            frame_count = frameCount,
                            fps = fps,
                            loop = loop,
                            duration = duration,
                        }
                    );
                    folders.Complete();
                }
                finally
                {
                    if (clip != null && !AssetDatabase.Contains(clip))
                        Object.DestroyImmediate(clip);
                }
            }

            // CreateAsset serialized each owned clip before it entered the result list.
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
                    while (i < name.Length && char.IsDigit(name[i]))
                        i++;
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
    }
}
