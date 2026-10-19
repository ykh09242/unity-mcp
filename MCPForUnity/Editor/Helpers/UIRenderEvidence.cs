using System.Collections.Generic;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Evidence about the full saved image, never a claim that UI is present.</summary>
    internal static class UIRenderEvidence
    {
        internal static Dictionary<string, object> Describe(Texture2D texture, string source, string readiness, int requested, int observed)
        {
            var pixels = texture.GetPixels32(0);
            int nonzeroAlpha = 0;
            for (int i = 0; i < pixels.Length; i++)
                if (pixels[i].a != 0)
                    nonzeroAlpha++;
            return new Dictionary<string, object>
            {
                { "capture_source", source },
                { "full_game_view", source == "game_view" },
                { "readiness", readiness },
                { "warmup_updates_requested", requested },
                { "warmup_updates_observed", observed },
                { "ui_repaint_completion", "not_observed" },
                { "hasContent", nonzeroAlpha != 0 },
                {
                    "content_check",
                    new Dictionary<string, object>
                    {
                        { "method", "full_alpha_scan" },
                        { "meaning", "image_alpha_occupancy" },
                        { "pixel_basis", "full_resolution_saved_png" },
                        { "pixels_examined", pixels.Length },
                        { "nonzero_alpha_pixels", nonzeroAlpha },
                        { "alpha_occupancy", pixels.Length == 0 ? 0d : (double)nonzeroAlpha / pixels.Length },
                        { "ui_presence", "not_determined" },
                    }
                },
            };
        }
    }
}
