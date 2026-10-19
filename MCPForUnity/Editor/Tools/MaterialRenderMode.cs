using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnity.Editor.Tools
{
    // Explicit, narrow adapter for the Built-in StandardShaderGUI blend-mode contract.
    // Rules agree in UnityCsReference 2021.3 and 6000.0 StandardShaderGUI.SetupMaterialWithBlendMode.
    internal static class MaterialRenderMode
    {
        private static readonly string[] AlphaKeywords = { "_ALPHATEST_ON", "_ALPHABLEND_ON", "_ALPHAPREMULTIPLY_ON" };
        private static readonly string[] Modes = { "opaque", "cutout", "fade", "transparent" };
        private static readonly string[] Properties = { "_Mode", "_SrcBlend", "_DstBlend", "_ZWrite" };

        internal static Action<Material> Prepare(Material source, JToken value, JObject patch)
        {
            string unsupported = UnsupportedReason(source);
            if (unsupported != null)
                throw new ArgumentException(unsupported);
            if (value.Type != JTokenType.String)
                throw new ArgumentException("render_mode must be opaque, cutout, fade or transparent.");
            string mode = value.Value<string>();
            int index = Array.IndexOf(Modes, mode);
            if (index < 0)
                throw new ArgumentException("render_mode must be opaque, cutout, fade or transparent.");
            if (patch.Property("render_queue") != null)
                throw new ArgumentException("render_mode explicitly resets the queue; render_queue cannot coexist in the same patch.");
            if (patch["keywords"] is JObject keywords)
                foreach (string keyword in AlphaKeywords)
                    if (keywords.Property(keyword) != null)
                        throw new ArgumentException($"render_mode owns '{keyword}'; a simultaneous keyword edit is ambiguous.");

            var declared = new LocalKeyword[AlphaKeywords.Length];
            for (int i = 0; i < AlphaKeywords.Length; i++)
            {
                declared[i] = source.shader.keywordSpace.FindKeyword(AlphaKeywords[i]);
                if (!declared[i].isValid)
                    throw new ArgumentException($"Standard preset requires declared keyword '{AlphaKeywords[i]}'.");
                if (declared[i].isOverridable && Shader.IsKeywordEnabled(AlphaKeywords[i]) && index != i + 1)
                    throw new ArgumentException($"Global override for '{AlphaKeywords[i]}' conflicts with the preset. Global keyword mutation is unsupported.");
            }
            bool opaque = index == 0;
            bool cutout = index == 1;
            bool fade = index == 2;
            string renderType =
                opaque ? ""
                : cutout ? "TransparentCutout"
                : "Transparent";
            int sourceBlend = fade ? (int)BlendMode.SrcAlpha : (int)BlendMode.One;
            int destinationBlend = opaque || cutout ? (int)BlendMode.Zero : (int)BlendMode.OneMinusSrcAlpha;
            int queue =
                opaque ? -1
                : cutout ? (int)RenderQueue.AlphaTest
                : (int)RenderQueue.Transparent;
            return material =>
            {
                material.SetFloat("_Mode", index);
                material.SetFloat("_SrcBlend", sourceBlend);
                material.SetFloat("_DstBlend", destinationBlend);
                material.SetFloat("_ZWrite", opaque || cutout ? 1 : 0);
                material.SetOverrideTag("RenderType", renderType);
                for (int i = 0; i < declared.Length; i++)
                    material.SetKeyword(declared[i], index == i + 1);
                material.renderQueue = queue;
            };
        }

        internal static JObject Describe(Material material, JObject properties)
        {
            string unsupported = UnsupportedReason(material);
            var alpha = new JObject();
            foreach (string keyword in AlphaKeywords)
            {
                var declared = material.shader.keywordSpace.FindKeyword(keyword);
                if (declared.isValid)
                    alpha[keyword] = material.IsKeywordEnabled(declared);
            }
            return new JObject
            {
                ["status"] = unsupported == null ? "supported" : "unsupported",
                ["adapter"] = unsupported == null ? "builtin_standard" : null,
                ["supported_modes"] = unsupported == null ? new JArray(Modes) : new JArray(),
                ["properties"] = properties,
                ["render_type"] = material.GetTag("RenderType", false, ""),
                ["alpha_keywords"] = alpha,
                ["queue_behavior"] = "Explicit presets reset to shader default (-1) for opaque, 2450 for cutout, or 3000 for fade/transparent.",
                ["reason"] =
                    unsupported
                    ?? "Explicit preset support only. Raw edits do not automatically reconcile rendering mode; no ShaderGUI or global keyword mutation is performed.",
                ["support_matrix"] = new JObject
                {
                    ["builtin_standard"] = "BuiltIn pipeline and exact Standard or Standard (Specular setup) shader with required declarations",
                    ["urp"] = "unsupported; no installed URP package/version adapter is available",
                    ["hdrp"] = "unsupported; no installed HDRP package/version adapter is available",
                    ["custom"] = "unsupported",
                },
            };
        }

        private static string UnsupportedReason(Material material)
        {
            if (RenderPipelineUtility.GetActivePipeline() != RenderPipelineUtility.PipelineKind.BuiltIn)
                return "render_mode presets support the Built-in pipeline only. URP, HDRP and custom pipeline adapters are unsupported.";
            if (material.shader.name != "Standard" && material.shader.name != "Standard (Specular setup)")
                return "render_mode presets require Standard or Standard (Specular setup). Other shaders support raw state edits only.";
            foreach (string property in Properties)
            {
                int index = material.shader.FindPropertyIndex(property);
                if (index < 0 || material.shader.GetPropertyType(index) != ShaderPropertyType.Float)
                    return $"Standard preset requires declared float property '{property}'.";
            }
            foreach (string keyword in AlphaKeywords)
                if (!material.shader.keywordSpace.FindKeyword(keyword).isValid)
                    return $"Standard preset requires declared keyword '{keyword}'.";
            return null;
        }
    }
}
