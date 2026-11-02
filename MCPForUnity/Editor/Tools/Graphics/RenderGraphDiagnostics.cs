using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Tools.Graphics
{
    // Observes serialized routes and already-existing pass fields. Never creates renderers or invokes feature callbacks.
    internal static class RenderGraphDiagnostics
    {
        private const string UrpAssembly = ", Unity.RenderPipelines.Universal.Runtime";

        private static Type UrpType(string name) => Type.GetType("UnityEngine.Rendering.Universal." + name + UrpAssembly);

        internal sealed class RendererSelection
        {
            internal ScriptableObject Renderer;
            internal int? Index;
            internal string Source;
            internal Camera Camera;
            internal int? ConfiguredRendererIndex;
            internal bool FellBackToDefault;

            internal object Describe() =>
                new
                {
                    name = Renderer.name,
                    path = AssetDatabase.GetAssetPath(Renderer),
                    instanceId = Renderer.GetInstanceIDCompat(),
                    type = Renderer.GetType().FullName,
                    index = Index,
                    source = Source,
                };

            internal object DescribeCamera() =>
                Camera == null
                    ? null
                    : new
                    {
                        name = Camera.name,
                        instanceId = Camera.GetInstanceIDCompat(),
                        configuredRendererIndex = ConfiguredRendererIndex,
                        effectiveRendererIndex = Index,
                        usesPipelineDefault = ConfiguredRendererIndex == -1,
                        fellBackToDefault = FellBackToDefault,
                    };
        }

        internal static ErrorResponse ResolveRenderer(JObject parameters, bool allowCamera, out RendererSelection selection)
        {
            selection = null;
            var p = new ToolParams(parameters);
            var targetToken = p.GetRaw("target");
            if (targetToken != null && targetToken.Type != JTokenType.Null && targetToken.Type != JTokenType.String)
                return new ErrorResponse("'target' must be a renderer asset path/name/instance ID or camera name/ID string.");
            string target = p.Get("target");
            int? requestedIndex = p.GetInt("renderer_index");
            if (requestedIndex < -1)
                return new ErrorResponse("'renderer_index' must be -1 (pipeline default) or a non-negative pipeline renderer index.");
            if (target != null && requestedIndex.HasValue)
                return new ErrorResponse("Use either 'target' or 'renderer_index', not both.");
            if (target != null && string.IsNullOrWhiteSpace(target))
                return new ErrorResponse("'target' cannot be empty or whitespace.");

            var rendererType = UrpType("ScriptableRendererData");
            if (rendererType == null)
                return new ErrorResponse("URP ScriptableRendererData is unavailable. Requires the Universal Render Pipeline package.");
            var pipeline = GraphicsSettings.currentRenderPipeline;
            bool activeUrp = pipeline != null && UrpType("UniversalRenderPipelineAsset")?.IsInstanceOfType(pipeline) == true;
            if (target != null)
            {
                Object found = null;
                if (target.StartsWith("Assets/", StringComparison.Ordinal) || target.StartsWith("Packages/", StringComparison.Ordinal))
                {
                    string path = AssetPathUtility.GetAssetReferencePath(target, allowPackages: true);
                    found = AssetDatabase.LoadAssetAtPath(path, rendererType);
                }
                else if (int.TryParse(target, out int instanceId))
                    found = GameObjectLookup.ResolveInstanceID(instanceId);
                else
                {
                    if (allowCamera)
                        found = GameObject.Find(target);
                    if (found == null && activeUrp)
                    {
                        using var serialized = new SerializedObject(pipeline);
                        using var list = serialized.FindProperty("m_RendererDataList");
                        if (list != null && list.isArray)
                        {
                            for (int i = 0; i < list.arraySize; i++)
                            {
                                using var item = list.GetArrayElementAtIndex(i);
                                var candidate = item.objectReferenceValue;
                                if (candidate != null && string.Equals(candidate.name, target, StringComparison.Ordinal))
                                {
                                    if (found != null && found != candidate)
                                        return new ErrorResponse("Renderer 'target' name is ambiguous. Use an asset path or instance ID.");
                                    found = candidate;
                                }
                            }
                        }
                    }
                }
                if (found is ScriptableObject renderer && rendererType.IsInstanceOfType(renderer))
                {
                    selection = new RendererSelection { Renderer = renderer, Source = "explicit_asset" };
                    if (activeUrp)
                    {
                        using var serialized = new SerializedObject(pipeline);
                        using var list = serialized.FindProperty("m_RendererDataList");
                        if (list != null && list.isArray)
                            for (int i = 0; i < list.arraySize; i++)
                            {
                                using var item = list.GetArrayElementAtIndex(i);
                                if (item.objectReferenceValue == renderer)
                                {
                                    selection.Index = i;
                                    break;
                                }
                            }
                    }
                    return null;
                }
                Camera camera = found as Camera;
                if (found is GameObject gameObject)
                    camera = gameObject.GetComponent<Camera>();
                if (!allowCamera || camera == null)
                    return new ErrorResponse(
                        allowCamera
                            ? "'target' did not resolve to a Camera or URP renderer data asset."
                            : "'target' did not resolve to a URP renderer data asset."
                    );
                if (!activeUrp)
                    return new ErrorResponse("Camera renderer routing requires an active URP pipeline.");
                var additionalType = UrpType("UniversalAdditionalCameraData");
                var additional = additionalType == null ? null : camera.GetComponent(additionalType);
                int configured = -1;
                if (additional != null)
                {
                    using var serialized = new SerializedObject(additional);
                    using var index = serialized.FindProperty("m_RendererIndex");
                    if (index == null)
                        return new ErrorResponse("Camera renderer route is unknown: m_RendererIndex is unavailable.");
                    configured = index.intValue;
                }
                selection = new RendererSelection
                {
                    Camera = camera,
                    ConfiguredRendererIndex = configured,
                    Source = "camera",
                };
                requestedIndex = configured;
            }
            if (!activeUrp)
                return new ErrorResponse("Renderer selection requires an active URP pipeline or an explicit renderer asset 'target'.");
            selection ??= new RendererSelection { Source = requestedIndex.GetValueOrDefault(-1) == -1 ? "pipeline_default" : "renderer_index" };
            using (var serialized = new SerializedObject(pipeline))
            {
                using var list = serialized.FindProperty("m_RendererDataList");
                using var defaultIndex = serialized.FindProperty("m_DefaultRendererIndex");
                if (list == null || !list.isArray || defaultIndex == null)
                    return new ErrorResponse("Renderer routing is unknown: pipeline renderer list/default index is unavailable.");
                int selectedIndex = requestedIndex ?? -1;
                bool cameraFallback = selection.Camera != null && selectedIndex != -1 && !HasRenderer(list, selectedIndex);
                if (selectedIndex == -1 || cameraFallback)
                    selectedIndex = defaultIndex.intValue;
                if (!HasRenderer(list, selectedIndex))
                    return new ErrorResponse(
                        $"Invalid 'renderer_index' or missing renderer asset at index {selectedIndex}. Available renderer slots: {list.arraySize}."
                    );
                using var element = list.GetArrayElementAtIndex(selectedIndex);
                if (!(element.objectReferenceValue is ScriptableObject renderer) || !rendererType.IsInstanceOfType(renderer))
                    return new ErrorResponse($"Renderer asset at 'renderer_index' {selectedIndex} is not URP ScriptableRendererData.");
                selection.Renderer = renderer;
                selection.Index = selectedIndex;
                selection.FellBackToDefault = cameraFallback;
            }
            return null;
        }

        private static bool HasRenderer(SerializedProperty list, int index)
        {
            if (index < 0 || index >= list.arraySize)
                return false;
            using var element = list.GetArrayElementAtIndex(index);
            return element.objectReferenceValue != null;
        }

        internal static object GetDiagnostics(JObject parameters)
        {
            var pipeline = GraphicsSettings.currentRenderPipeline;
            var urpType = UrpType("UniversalRenderPipelineAsset");
            bool activeUrp = pipeline != null && urpType?.IsInstanceOfType(pipeline) == true;
            var p = new ToolParams(parameters);
            RendererSelection selection = null;
            if (activeUrp || p.GetRaw("target")?.Type is not (null or JTokenType.Null) || p.GetRaw("renderer_index")?.Type is not (null or JTokenType.Null))
            {
                var error = ResolveRenderer(parameters, true, out selection);
                if (error != null)
                    return error;
            }
            string version = ObservePackageVersion(urpType);
            return new
            {
                success = true,
                message = "Read-only renderer and RenderGraph observations. Feature execution is not established.",
                data = new
                {
                    experimental = true,
                    unityVersion = Application.unityVersion,
                    pipeline = RenderPipelineUtility.GetActivePipeline().ToString(),
                    pipelineAsset = pipeline == null
                        ? null
                        : new
                        {
                            name = pipeline.name,
                            path = AssetDatabase.GetAssetPath(pipeline),
                            type = pipeline.GetType().FullName,
                        },
                    urpPackageVersion = version,
                    urpPackageVersionStatus = version != null ? "observed"
                    : urpType == null ? "not_installed"
                    : "unknown",
                    renderGraph = ObserveGraphMode(activeUrp),
                    renderer = selection?.Describe(),
                    camera = selection?.DescribeCamera(),
                    features = selection == null ? new List<object>() : ObserveFeatures(selection.Renderer),
                    execution = "unknown",
                },
            };
        }

        internal static string ObservePackageVersion(Type urpType) => urpType == null ? null : PackageInfo.FindForAssembly(urpType.Assembly)?.version;

        private static object ObserveGraphMode(bool activeUrp)
        {
            bool? compatibility = null;
            string source = activeUrp ? "unavailable" : "URP is not active";
            var settingsType = UrpType("RenderGraphSettings");
            if (activeUrp && settingsType != null)
            {
                var getter = typeof(GraphicsSettings)
                    .GetMethods(BindingFlags.Public | BindingFlags.Static)
                    .FirstOrDefault(m =>
                        m.Name == "GetRenderPipelineSettings"
                        && m.IsGenericMethodDefinition
                        && m.GetGenericArguments().Length == 1
                        && m.GetParameters().Length == 0
                    );
                var property = settingsType.GetProperty("enableRenderCompatibilityMode", BindingFlags.Public | BindingFlags.Instance);
                if (getter != null && property?.PropertyType == typeof(bool))
                {
                    try
                    {
                        var settings = getter.MakeGenericMethod(settingsType).Invoke(null, null);
                        if (settings != null)
                        {
                            compatibility = (bool)property.GetValue(settings);
                            source = "GraphicsSettings.GetRenderPipelineSettings<RenderGraphSettings>().enableRenderCompatibilityMode";
                        }
                    }
                    catch (Exception error) when (error is TargetInvocationException || error is ArgumentException || error is MemberAccessException)
                    {
                        source = "settings observation unavailable: " + error.GetType().Name;
                    }
                }
            }
            return DescribeGraphMode(activeUrp, compatibility, source);
        }

        internal static object DescribeGraphMode(bool activeUrp, bool? compatibility, string source)
        {
            return new
            {
                mode = !activeUrp ? "not_applicable"
                : compatibility.HasValue ? (compatibility.Value ? "compatibility" : "enabled")
                : "unknown",
                enabled = compatibility.HasValue ? !compatibility.Value : (bool?)null,
                compatibilityMode = compatibility,
                source,
                execution = "unknown",
            };
        }

        private static List<object> ObserveFeatures(ScriptableObject renderer)
        {
            var result = new List<object>();
            using var serialized = new SerializedObject(renderer);
            using var list = serialized.FindProperty("m_RendererFeatures");
            if (list == null || !list.isArray)
                return result;
            var basePass = UrpType("ScriptableRenderPass");
            for (int i = 0; i < list.arraySize; i++)
            {
                using var element = list.GetArrayElementAtIndex(i);
                var feature = element.objectReferenceValue as ScriptableObject;
                if (feature == null)
                {
                    result.Add(new { index = i, missing = true });
                    continue;
                }
                using var featureSerialized = new SerializedObject(feature);
                using var active = featureSerialized.FindProperty("m_Active");
                var passes = new List<object>();
                bool missingImplementation = false;
                bool implementationObserved = false;
                for (var type = feature.GetType(); basePass != null && type != null && type != typeof(ScriptableObject); type = type.BaseType)
                {
                    foreach (var field in type.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                    {
                        if (!basePass.IsAssignableFrom(field.FieldType))
                            continue;
                        var pass = field.GetValue(feature);
                        bool? implemented = pass == null ? null : HasRecordRenderGraphOverride(pass.GetType(), basePass);
                        missingImplementation |= implemented == false;
                        implementationObserved |= implemented == true;
                        passes.Add(
                            new
                            {
                                field = field.Name,
                                type = (pass?.GetType() ?? field.FieldType).FullName,
                                instanceObserved = pass != null,
                                recordRenderGraphOverride = implemented,
                            }
                        );
                    }
                }
                result.Add(
                    new
                    {
                        index = i,
                        name = feature.name,
                        type = feature.GetType().FullName,
                        isActive = active == null ? (bool?)null : active.boolValue,
                        compatibility = new
                        {
                            status = missingImplementation ? "missing_record_render_graph_override"
                            : implementationObserved ? "implementation_observed"
                            : "unknown",
                            passes,
                            execution = "unknown",
                            note = "Direct pass fields only. Overrides do not prove enqueueing or execution; absent overrides can have no effect in RenderGraph mode.",
                        },
                    }
                );
            }
            return result;
        }

        internal static bool? HasRecordRenderGraphOverride(Type passType, Type basePassType)
        {
            var baseMethod = basePassType
                .GetMethods(BindingFlags.Instance | BindingFlags.Public)
                .FirstOrDefault(m => m.Name == "RecordRenderGraph" && m.IsVirtual && m.GetParameters().Length == 2);
            if (baseMethod == null || !basePassType.IsAssignableFrom(passType))
                return null;
            var method = passType.GetMethod(
                baseMethod.Name,
                BindingFlags.Instance | BindingFlags.Public,
                null,
                baseMethod.GetParameters().Select(p => p.ParameterType).ToArray(),
                null
            );
            return method != null && method.DeclaringType != baseMethod.DeclaringType && method.GetBaseDefinition() == baseMethod.GetBaseDefinition();
        }
    }
}
