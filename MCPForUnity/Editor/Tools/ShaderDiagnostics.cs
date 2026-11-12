using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Rendering;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    internal static class ShaderDiagnostics
    {
        private static readonly string[] VariantFields = { "shader_type", "platform", "build_target", "subshader_index", "pass_index", "keywords" };

        internal static object Handle(JObject parameters, bool compile)
        {
            try
            {
                if (parameters["path"]?.Type != JTokenType.String)
                    throw new ArgumentException("A full Assets .shader or .shadergraph path is required.");
                string path = AssetPathUtility.GetContainedAssetPath(parameters.Value<string>("path"));
                string extension = Path.GetExtension(path);
                if (
                    !string.Equals(extension, ".shader", StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(extension, ".shadergraph", StringComparison.OrdinalIgnoreCase)
                )
                    throw new ArgumentException("Shader diagnostics require a .shader or .shadergraph asset.");
                if (compile)
                    return Compile(path, parameters["variant"] as JObject);
                var shader = LoadShader(path);
                var data = ShaderUtil.GetShaderData(shader);
                var passes = new List<object>();
                bool truncated = false;
                for (int subshaderIndex = 0; subshaderIndex < data.SubshaderCount; subshaderIndex++)
                {
                    var subshader = data.GetSubshader(subshaderIndex);
                    for (int passIndex = 0; passIndex < subshader.PassCount; passIndex++)
                    {
                        if (passes.Count == 100)
                        {
                            truncated = true;
                            break;
                        }
                        string name = subshader.GetPass(passIndex).Name ?? "";
                        passes.Add(
                            new
                            {
                                subshader_index = subshaderIndex,
                                pass_index = passIndex,
                                name = name.Length > 256 ? name.Substring(0, 256) : name,
                            }
                        );
                    }
                    if (truncated)
                        break;
                }
                return new SuccessResponse(
                    "Shader diagnostics observed.",
                    new
                    {
                        path,
                        compilation = ManageShader.CollectCompilationEvidence(path),
                        subshader_count = data.SubshaderCount,
                        passes,
                        passes_truncated = truncated,
                        shader_types = Enum.GetNames(typeof(ShaderType)),
                        compiler_platforms = Enum.GetNames(typeof(ShaderCompilerPlatform)),
                    }
                );
            }
            catch (Exception error)
            {
                return new ErrorResponse("Shader diagnostics unavailable: " + error.GetBaseException().Message);
            }
        }

        private static Shader LoadShader(string path) =>
            AssetDatabase.LoadAssetAtPath<Shader>(path) ?? throw new ArgumentException("No imported Shader object is available at the selected path.");

        private static object Compile(string path, JObject variant)
        {
            if (variant == null || variant.Properties().Any(property => !VariantFields.Contains(property.Name)))
                throw new ArgumentException("variant requires shader_type, platform, build_target and optional subshader_index, pass_index, keywords.");
            var stage = ReadEnum<ShaderType>(variant, "shader_type");
            var platform = ReadEnum<ShaderCompilerPlatform>(variant, "platform");
            var target = ReadEnum<BuildTarget>(variant, "build_target");
            if (Convert.ToInt32(target) < 0 || Convert.ToInt32(platform) < 0)
                throw new ArgumentException("A concrete compiler platform and build target are required.");
            int subshaderIndex = ReadIndex(variant, "subshader_index");
            int passIndex = ReadIndex(variant, "pass_index");
            var keywords = variant["keywords"] == null ? new JArray() : variant["keywords"] as JArray;
            if (
                keywords == null
                || keywords.Count > 64
                || keywords.Any(value => value.Type != JTokenType.String || !Regex.IsMatch(value.Value<string>(), @"\A[A-Za-z_][A-Za-z0-9_]{0,127}\z"))
            )
                throw new ArgumentException("keywords must contain at most 64 identifiers of 1-128 characters.");
            var keywordNames = keywords.Values<string>().ToArray();
            if (keywordNames.Distinct(StringComparer.Ordinal).Count() != keywordNames.Length)
                throw new ArgumentException("keywords must be unique.");
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                throw new InvalidOperationException("compile_variant requires idle Edit Mode.");
            var shader = LoadShader(path);
            var data = ShaderUtil.GetShaderData(shader);
            if (subshaderIndex >= data.SubshaderCount)
                throw new ArgumentException("subshader_index is outside the imported shader.");
            var subshader = data.GetSubshader(subshaderIndex);
            if (passIndex >= subshader.PassCount)
                throw new ArgumentException("pass_index is outside the selected subshader.");
            // One explicit variant only. Unity owns its compiler/cache lifetime.
            var result = subshader.GetPass(passIndex).CompileVariant(stage, keywordNames, platform, target);
            int bytes = result.ShaderData?.Length ?? 0;
            string outcome =
                !result.Success ? "failed"
                : bytes == 0 ? "no_program"
                : "success";
            var evidence = ManageShader.BuildCompilationEvidence(result.Messages ?? Array.Empty<ShaderMessage>(), !result.Success, false, null);
            evidence["scope"] = "selected_shader_variant";
            evidence["limitations"] =
                "This result covers only the selected runtime subshader, pass, stage, keywords, compiler platform and build target. Empty bytecode means no program was compiled.";
            return new SuccessResponse(
                "Shader variant compilation observed.",
                new
                {
                    path,
                    outcome,
                    bytecode_size = bytes,
                    variant = new
                    {
                        shader_type = stage.ToString(),
                        platform = platform.ToString(),
                        build_target = target.ToString(),
                        subshader_index = subshaderIndex,
                        pass_index = passIndex,
                        keywords = keywordNames,
                    },
                    compilation = evidence,
                }
            );
        }

        private static T ReadEnum<T>(JObject variant, string key)
            where T : struct, Enum
        {
            var token = variant[key];
            if (
                token?.Type != JTokenType.String
                || !Enum.GetNames(typeof(T)).Contains(token.Value<string>())
                || !Enum.TryParse(token.Value<string>(), out T value)
            )
                throw new ArgumentException(key + " must be an exact Unity enum name.");
            return value;
        }

        private static int ReadIndex(JObject variant, string key)
        {
            var token = variant[key];
            if (token == null)
                return 0;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int value) || value < 0 || value > 4095)
                throw new ArgumentException(key + " must be an integer from 0 to 4095.");
            return value;
        }
    }
}
