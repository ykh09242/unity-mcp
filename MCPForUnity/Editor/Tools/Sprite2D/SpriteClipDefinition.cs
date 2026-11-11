using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Sprite2D
{
    /// <summary>Read-only clip eligibility shared by setup preflight and actual clip creation.</summary>
    internal static class SpriteClipDefinition
    {
        internal static bool TryRead(
            JToken clipToken,
            int spriteCount,
            string outputDir,
            SpriteDiagnosticBuilder diagnostics,
            out (string name, string path, int startFrame, int frameCount, float fps, bool loop, float duration) definition
        )
        {
            definition = default;
            // Measured: a non-object clips entry threw InvalidCastException on a typed cast.
            if (!(clipToken is JObject clipDef))
            {
                diagnostics.AddWarning("CLIP_NOT_AN_OBJECT", "A clips entry is not an object - skipped.", "Each clip must be an object with a 'name'.");
                return false;
            }

            if (!SpriteParams.TryReadString(clipDef, "name", null, out string clipName, out string nameError))
            {
                diagnostics.AddWarning("CLIP_BAD_NAME", $"{nameError} - skipped.", "Give the clip a string name.");
                return false;
            }
            if (string.IsNullOrEmpty(clipName))
            {
                diagnostics.AddWarning("CLIP_NO_NAME", "Clip name is missing — skipped.", "Add a 'name' field to each clip definition.");
                return false;
            }

            // Measured: "nested/walk" either threw from CreateAsset or, where the folder
            // existed, wrote the clip outside output_dir.
            if (clipName.Contains("/") || clipName.Contains("\\"))
            {
                diagnostics.AddWarning(
                    "CLIP_BAD_NAME",
                    $"Clip '{clipName}': the name cannot contain a path separator - skipped.",
                    "Remove '..' and path separators from the clip name."
                );
                return false;
            }

            // Sequential, not chained with ||: a short-circuited call leaves its out
            // parameter unassigned and the second value is used below.
            int endFrame = spriteCount - 1;
            bool rangeOk = SpriteParams.TryReadWholeNumber(clipDef, "start_frame", 0, out int startFrame, out string frameError);
            if (rangeOk)
                rangeOk = SpriteParams.TryReadWholeNumber(clipDef, "end_frame", spriteCount - 1, out endFrame, out frameError);
            if (!rangeOk)
            {
                diagnostics.AddWarning(
                    "CLIP_BAD_RANGE",
                    $"Clip '{clipName}': {frameError} - skipped.",
                    "start_frame and end_frame must be whole numbers within a sprite index."
                );
                return false;
            }
            if (endFrame > spriteCount - 1)
            {
                // Skip/Take clamps silently: an end_frame past the last sprite produced a
                // shorter clip and reported success.
                diagnostics.AddWarning(
                    "CLIP_BAD_RANGE",
                    $"Clip '{clipName}': end_frame {endFrame} is past the last sprite index {spriteCount - 1} - skipped.",
                    $"This sheet has {spriteCount} sprites, so end_frame must be at most {spriteCount - 1}."
                );
                return false;
            }
            if (startFrame < 0 || endFrame < startFrame)
            {
                // Skip yields everything for a negative count: start_frame=-2 with
                // end_frame=3 wrote frames 0..5 as a success.
                diagnostics.AddWarning(
                    "CLIP_BAD_RANGE",
                    $"Clip '{clipName}': frame range [{startFrame},{endFrame}] is invalid - skipped.",
                    "start_frame must be 0 or more, and end_frame must not be below start_frame."
                );
                return false;
            }
            // `fps <= 0f` is false for NaN, so a NaN rate wrote a clip of NaN keyframe
            // times and reported success.
            if (!SpriteParams.TryReadFiniteFloat(clipDef, "fps", 12f, out float fps, out string fpsError))
            {
                diagnostics.AddWarning("CLIP_BAD_FPS", $"Clip '{clipName}': {fpsError} - skipped.", "Leave fps out to use the default of 12.");
                return false;
            }
            if (fps <= 0f)
            {
                // Times are i / fps, so a non-positive rate puts every key at infinity.
                diagnostics.AddWarning(
                    "CLIP_BAD_FPS",
                    $"Clip '{clipName}': fps must be greater than 0, got {fps} - skipped.",
                    "Leave fps out to use the default of 12."
                );
                return false;
            }

            int frameCount = endFrame - startFrame + 1;
            float duration = frameCount / fps;
            // A finite positive rate can still overflow the key times and response duration.
            if (float.IsInfinity(duration))
            {
                diagnostics.AddWarning(
                    "CLIP_BAD_FPS",
                    $"Clip '{clipName}': fps is too small for {frameCount} frames - skipped.",
                    "Increase fps so the clip duration is finite."
                );
                return false;
            }

            var entry = SpriteNamingDetector.Detect(clipName);
            if (!SpriteParams.TryReadBool(clipDef, "loop", entry.Loop, out bool loop, out string loopError))
            {
                diagnostics.AddWarning("CLIP_BAD_LOOP", $"Clip '{clipName}': {loopError} - skipped.", "Leave loop out to let the clip name decide.");
                return false;
            }

            if (frameCount <= 2)
                diagnostics.AddWarning("LOW_FRAME_COUNT", $"Clip '{clipName}' has only {frameCount} frame(s) — animation may not be visible.");

            // Refusals come before the allocation: a `new AnimationClip` that never becomes
            // an asset leaks.
            if (!SpriteParams.TryReadAssetPath(new JObject { ["path"] = $"{outputDir}/{clipName}.anim" }, "path", out string clipPath, out _))
            {
                diagnostics.AddWarning(
                    "CLIP_BAD_NAME",
                    $"Clip '{clipName}': the name cannot be used as a file name - skipped.",
                    "Remove '..', path separators and characters like : * ? \" < > | from the clip name."
                );
                return false;
            }
            definition = (clipName, clipPath, startFrame, frameCount, fps, loop, duration);
            return true;
        }
    }
}
