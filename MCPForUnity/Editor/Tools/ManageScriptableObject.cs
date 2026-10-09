using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Single tool for ScriptableObject workflows:
    /// - action=create: create a ScriptableObject asset (and optionally apply patches)
    /// - action=modify: apply serialized property patches to an existing asset
    ///
    /// Patching is performed via SerializedObject/SerializedProperty paths (Unity-native), not reflection.
    /// </summary>
    [McpForUnityTool("manage_scriptable_object", AutoRegister = false, Group = "scripting_ext")]
    public static class ManageScriptableObject
    {
        private const string CodeCompilingOrReloading = "compiling_or_reloading";
        private const string CodeInvalidParams = "invalid_params";
        private const string CodeTypeNotFound = "type_not_found";
        private const string CodeInvalidFolderPath = "invalid_folder_path";
        private const string CodeTargetNotFound = "target_not_found";
        private const string CodeAssetCreateFailed = "asset_create_failed";

        // Growth limits do not prevent edits/shrinks of pre-existing larger arrays.
        private const int MaxArrayGrowthSize = 1_048_576;
        private const long MaxRequestArrayGrowth = 2_097_152;
        private const long MaxGrowthInspectionWork = 4_194_304;
        private const long MaxRequestCopiedCharacters = 33_554_432; // 64 MiB of UTF-16 character payload.

        private static readonly HashSet<string> ValidActions = new(StringComparer.OrdinalIgnoreCase)
        {
            // NOTE: Action strings are normalized by NormalizeAction() (lowercased, '_'/'-' removed),
            // so we only need the canonical normalized forms here.
            "create",
            "createso",
            "modify",
            "modifyso",
        };

        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
            {
                return new ErrorResponse(CodeInvalidParams);
            }

            if (EditorStateCache.GetActualIsCompiling() || EditorApplication.isUpdating)
            {
                // Unity is transient; treat as retryable on the client side.
                return new ErrorResponse(CodeCompilingOrReloading, new { hint = "retry" });
            }

            // Allow JSON-string parameters for objects/arrays.
            JsonUtil.CoerceJsonStringParameter(@params, "target");
            CoerceJsonStringArrayParameter(@params, "patches");

            string actionRaw = @params["action"]?.ToString();
            if (string.IsNullOrWhiteSpace(actionRaw))
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'action' is required.", validActions = ValidActions.ToArray() });
            }

            string action = NormalizeAction(actionRaw);
            if (!ValidActions.Contains(action))
            {
                return new ErrorResponse(CodeInvalidParams, new { message = $"Unknown action: '{actionRaw}'.", validActions = ValidActions.ToArray() });
            }

            if (IsCreateAction(action))
            {
                return HandleCreate(@params);
            }

            return HandleModify(@params);
        }

        private static object HandleCreate(JObject @params)
        {
            var patchesToken = @params["patches"];
            if (patchesToken != null && patchesToken.Type != JTokenType.Null && patchesToken is not JArray)
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'patches' must be an array." });
            }
            if (patchesToken is JArray createPatches)
            {
                for (int i = 0; i < createPatches.Count; i++)
                {
                    if (createPatches[i] is not JObject patch)
                        return new ErrorResponse(CodeInvalidParams, new { message = $"Patch at index {i} must be an object." });
                    string op = patch["op"]?.ToString()?.Trim();
                    if (
                        !string.IsNullOrEmpty(op)
                        && !string.Equals(op, "set", StringComparison.OrdinalIgnoreCase)
                        && !string.Equals(op, "array_resize", StringComparison.OrdinalIgnoreCase)
                    )
                        return new ErrorResponse(CodeInvalidParams, new { message = $"Unknown patch operation: '{op}'." });
                    if (
                        (string.IsNullOrEmpty(op) || string.Equals(op, "set", StringComparison.OrdinalIgnoreCase))
                        && patch["value"] == null
                        && patch["ref"] == null
                    )
                        return new ErrorResponse(CodeInvalidParams, new { message = $"Patch at index {i} requires 'value' or 'ref'." });
                    if (
                        string.Equals(op, "array_resize", StringComparison.OrdinalIgnoreCase)
                        && (patch["value"] == null || patch["value"].Type == JTokenType.Null)
                    )
                        return new ErrorResponse(CodeInvalidParams, new { message = $"Patch at index {i} requires integer 'value'." });
                }
            }
            var typeNameToken = @params["typeName"] ?? @params["type_name"];
            var folderPathToken = @params["folderPath"] ?? @params["folder_path"];
            var assetNameToken = @params["assetName"] ?? @params["asset_name"];
            string typeName = typeNameToken?.ToString();
            string folderPath = folderPathToken?.ToString();
            string assetName = assetNameToken?.ToString();
            bool overwrite = @params["overwrite"]?.ReadScalar<bool?>() ?? false;

            if (typeNameToken != null && typeNameToken.Type != JTokenType.Null && typeNameToken.Type != JTokenType.String)
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'typeName' must be a string." });
            }

            if (string.IsNullOrWhiteSpace(typeName))
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'typeName' is required." });
            }

            if (folderPathToken != null && folderPathToken.Type != JTokenType.Null && folderPathToken.Type != JTokenType.String)
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'folderPath' must be a string." });
            }

            if (string.IsNullOrWhiteSpace(folderPath))
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'folderPath' is required." });
            }

            if (assetNameToken != null && assetNameToken.Type != JTokenType.Null && assetNameToken.Type != JTokenType.String)
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'assetName' must be a string." });
            }

            if (string.IsNullOrWhiteSpace(assetName))
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'assetName' is required." });
            }

            if (assetName.Contains("/") || assetName.Contains("\\"))
            {
                return new ErrorResponse(CodeInvalidParams, new { message = "'assetName' must not contain path separators." });
            }

            if (!TryNormalizeFolderPath(folderPath, out var normalizedFolder, out var folderNormalizeError))
            {
                return new ErrorResponse(CodeInvalidFolderPath, new { message = folderNormalizeError, folderPath });
            }

            string fileName = assetName.EndsWith(".asset", StringComparison.OrdinalIgnoreCase) ? assetName : assetName + ".asset";
            string desiredPath;
            try
            {
                normalizedFolder = AssetPathUtility.GetContainedAssetPath(normalizedFolder);
                desiredPath = AssetPathUtility.GetContainedAssetPath($"{normalizedFolder}/{fileName}");
            }
            catch (Exception ex)
            {
                return new ErrorResponse(CodeInvalidFolderPath, new { message = ex.Message, folderPath });
            }

            var resolvedType = ResolveType(typeName);
            if (
                resolvedType == null
                || !typeof(ScriptableObject).IsAssignableFrom(resolvedType)
                || resolvedType.IsAbstract
                || resolvedType.ContainsGenericParameters
            )
            {
                return new ErrorResponse(CodeTypeNotFound, new { message = $"ScriptableObject type not found: '{typeName}'", typeName });
            }

            string finalPath = overwrite ? desiredPath : AssetDatabase.GenerateUniqueAssetPath(desiredPath);

            try
            {
                finalPath = AssetPathUtility.GetContainedAssetPath(finalPath);
            }
            catch (Exception ex)
            {
                return new ErrorResponse(CodeAssetCreateFailed, new { message = ex.Message, path = finalPath });
            }

            if (patchesToken is JArray sizePatches)
            {
                foreach (JObject patch in sizePatches)
                {
                    string path = patch["propertyPath"]?.ToString() ?? patch["property_path"]?.ToString() ?? patch["path"]?.ToString();
                    if (string.IsNullOrWhiteSpace(path))
                        continue;
                    string op = patch["op"]?.ToString()?.Trim().ToLowerInvariant();
                    if (string.IsNullOrEmpty(op))
                        op = "set";
                    if (
                        (op == "array_resize" || (op == "set" && NormalizePropertyPath(path).EndsWith(".Array.size", StringComparison.Ordinal)))
                        && !TryReadArraySize(patch["value"], out _)
                    )
                        return new ErrorResponse(
                            CodeInvalidParams,
                            new { message = "Serialized array growth rejected: Array size must be a non-negative Int32 value." }
                        );
                }
            }

            ScriptableObject instance;
            try
            {
                instance = ScriptableObject.CreateInstance(resolvedType);
                if (instance == null)
                {
                    return new ErrorResponse(CodeAssetCreateFailed, new { message = "CreateInstance returned null.", typeName = resolvedType.FullName });
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse(CodeAssetCreateFailed, new { message = ex.Message, typeName = resolvedType.FullName });
            }

            if (patchesToken is JArray budgetPatches && !TryValidateArrayGrowth(instance, budgetPatches, out var growthError))
            {
                UnityEngine.Object.DestroyImmediate(instance);
                return new ErrorResponse(CodeInvalidParams, new { message = growthError });
            }
            using var folders = new AssetFolderScope();
            try
            {
                folders.EnsureFolder(normalizedFolder);
            }
            catch (Exception ex)
            {
                UnityEngine.Object.DestroyImmediate(instance);
                return new ErrorResponse(CodeInvalidFolderPath, new { message = ex.Message, folderPath = normalizedFolder });
            }

            // GUID-preserving overwrite logic
            bool isNewAsset = true;
            try
            {
                if (overwrite)
                {
                    var existingAsset = AssetDatabase.LoadAssetAtPath<ScriptableObject>(finalPath);
                    if (existingAsset != null && existingAsset.GetType() == resolvedType)
                    {
                        // Preserve GUID by overwriting existing asset data in-place
                        AssetPathUtility.GetFullAssetPath(finalPath);
                        EditorUtility.CopySerialized(instance, existingAsset);

                        // Fix for "Main Object Name does not match filename" warning:
                        // CopySerialized overwrites the name with the (empty) name of the new instance.
                        // We must restore the correct name to match the filename.
                        existingAsset.name = Path.GetFileNameWithoutExtension(finalPath);

                        UnityEngine.Object.DestroyImmediate(instance); // Destroy temporary instance
                        instance = existingAsset; // Proceed with patching the existing asset
                        isNewAsset = false;

                        // Mark dirty to ensure changes are picked up
                        EditorUtility.SetDirty(instance);
                    }
                    else if (existingAsset != null)
                    {
                        // Type mismatch or not a ScriptableObject - must delete and recreate to change type, losing GUID
                        // (Or we could warn, but overwrite usually implies replacing)
                        AssetPathUtility.GetFullAssetPath(finalPath);
                        AssetDatabase.DeleteAsset(finalPath);
                    }
                }

                if (isNewAsset)
                {
                    // Ensure the new instance has the correct name before creating asset to avoid warnings
                    instance.name = Path.GetFileNameWithoutExtension(finalPath);
                    AssetPathUtility.GetFullAssetPath(finalPath);
                    AssetDatabase.CreateAsset(instance, finalPath);
                    if (
                        !EditorUtility.IsPersistent(instance)
                        || !string.Equals(AssetDatabase.GetAssetPath(instance), finalPath, StringComparison.OrdinalIgnoreCase)
                    )
                        throw new IOException($"Unity could not create the asset at '{finalPath}'.");
                }
            }
            catch (Exception ex)
            {
                if (instance != null && !EditorUtility.IsPersistent(instance))
                    UnityEngine.Object.DestroyImmediate(instance);
                return new ErrorResponse(CodeAssetCreateFailed, new { message = ex.Message, path = finalPath });
            }

            string guid = AssetDatabase.AssetPathToGUID(finalPath);
            object patchResults = null;
            var warnings = new List<string>();

            if (patchesToken is JArray patches && patches.Count > 0)
            {
                AssetPathUtility.GetFullAssetPath(finalPath);
                var patchApply = ApplyPatchesCore(instance, patches, saveAssets: false);
                patchResults = patchApply.results;
                warnings.AddRange(patchApply.warnings);
            }

            AssetPathUtility.GetFullAssetPath(finalPath);
            EditorUtility.SetDirty(instance);
            AssetDatabase.SaveAssets();

            var response = new SuccessResponse(
                "ScriptableObject created.",
                new
                {
                    guid,
                    path = finalPath,
                    typeNameResolved = resolvedType.FullName,
                    patchResults,
                    warnings = warnings.Count > 0 ? warnings : null,
                }
            );
            folders.Complete();
            return response;
        }

        private static object HandleModify(JObject @params)
        {
            bool dryRun = @params["dryRun"]?.ReadScalar<bool?>() ?? @params["dry_run"]?.ReadScalar<bool?>() ?? false;
            if (!TryResolveTarget(@params["target"], !dryRun, out var target, out var targetPath, out var targetGuid, out var err))
            {
                return err;
            }

            var patchesToken = @params["patches"];
            if (patchesToken == null || patchesToken.Type == JTokenType.Null)
            {
                return new ErrorResponse(
                    CodeInvalidParams,
                    new
                    {
                        message = "'patches' is required.",
                        targetPath,
                        targetGuid,
                    }
                );
            }

            if (patchesToken is not JArray patches)
            {
                return new ErrorResponse(
                    CodeInvalidParams,
                    new
                    {
                        message = "'patches' must be an array.",
                        targetPath,
                        targetGuid,
                    }
                );
            }

            if (!TryValidateArrayGrowth(target, patches, out var growthError))
                return new ErrorResponse(
                    CodeInvalidParams,
                    new
                    {
                        message = growthError,
                        targetPath,
                        targetGuid,
                    }
                );

            // Phase 5: Dry-run mode - validate patches without applying

            if (dryRun)
            {
                var validationResults = ValidatePatches(target, patches);
                return new SuccessResponse(
                    "Dry-run validation complete.",
                    new
                    {
                        targetGuid,
                        targetPath,
                        targetTypeName = target.GetType().FullName,
                        dryRun = true,
                        valid = validationResults.All(r => (bool)r.GetType().GetProperty("ok")?.GetValue(r)),
                        validationResults,
                    }
                );
            }

            try
            {
                AssetPathUtility.GetFullAssetPath(targetPath);
            }
            catch (Exception ex)
            {
                return new ErrorResponse(
                    CodeInvalidParams,
                    new
                    {
                        message = ex.Message,
                        targetPath,
                        targetGuid,
                    }
                );
            }
            var (results, warnings) = ApplyPatches(target, patches);

            return new SuccessResponse(
                "Serialized properties patched.",
                new
                {
                    targetGuid,
                    targetPath,
                    targetTypeName = target.GetType().FullName,
                    results,
                    warnings = warnings.Count > 0 ? warnings : null,
                }
            );
        }

        private static bool TryReadArraySize(JToken token, out int size)
        {
            size = -1;
            if (token == null || token.Type == JTokenType.Null || token.Type == JTokenType.Boolean)
                return false;
            try
            {
                long value = token.ReadScalar<long>();
                if (value < 0 || value > int.MaxValue)
                    return false;
                size = (int)value;
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static void CheckArraySizeChange(int oldSize, int newSize)
        {
            if (newSize < 0 || (newSize > oldSize && newSize > MaxArrayGrowthSize))
                throw new InvalidOperationException($"Array growth exceeds {MaxArrayGrowthSize} elements.");
        }

        private static bool TryValidateArrayGrowth(UnityEngine.Object target, JArray patches, out string error)
        {
            try
            {
                using var serialized = new SerializedObject(target);
                serialized.Update();
                var plan = new ArrayGrowthPlan(serialized);
                foreach (var token in patches)
                {
                    plan.Inspect();
                    if (token is not JObject patch)
                        continue;
                    string path = patch["propertyPath"]?.ToString() ?? patch["property_path"]?.ToString() ?? patch["path"]?.ToString();
                    if (string.IsNullOrWhiteSpace(path))
                        continue;
                    path = NormalizePropertyPath(path);
                    string op = patch["op"]?.ToString()?.Trim().ToLowerInvariant();
                    if (string.IsNullOrEmpty(op))
                        op = "set";
                    if (op == "array_resize" || (op == "set" && path.EndsWith(".Array.size", StringComparison.Ordinal)))
                    {
                        if (!TryReadArraySize(patch["value"], out int size))
                            throw new InvalidOperationException("Array size must be a non-negative Int32 value.");
                        plan.Resize(
                            path.EndsWith(".Array.size", StringComparison.Ordinal) ? path.Substring(0, path.Length - ".Array.size".Length) : path,
                            size
                        );
                    }
                    else if (op == "set" && (patch["value"] != null || patch["ref"] != null))
                    {
                        plan.EnsureIndices(path);
                        if (patch["value"] != null)
                            plan.Map(path, patch["value"], 0);
                    }
                }
                error = null;
                return true;
            }
            catch (Exception ex)
            {
                error = "Serialized array growth rejected: " + ex.Message;
                return false;
            }
        }

        // A read-only size model: no arraySize/intValue setters, Apply, clone or asset writes.
        // Resizes never refund work; aliases model elements appended from an existing prototype.
        private sealed class ArrayGrowthPlan
        {
            private readonly SerializedObject _serialized;
            private readonly Dictionary<string, int> _sizes = new(StringComparer.Ordinal);
            private readonly Dictionary<string, int> _peakSizes = new(StringComparer.Ordinal);
            private readonly Dictionary<string, int> _strings = new(StringComparer.Ordinal);
            private readonly Dictionary<string, int> _arrayPeaks = new(StringComparer.Ordinal);
            private readonly Dictionary<string, int> _stringPeaks = new(StringComparer.Ordinal);
            private readonly Dictionary<string, List<(int start, int end, string source)>> _copies = new(StringComparer.Ordinal);
            private long _growth;
            private long _work;
            private long _characters;

            private static string Shape(string path) => Regex.Replace(path, @"\.Array\.data\[\d+\]", ".Array.data[*]");

            public ArrayGrowthPlan(SerializedObject serialized)
            {
                _serialized = serialized;
            }

            public void Inspect()
            {
                if (++_work > MaxGrowthInspectionWork)
                    throw new InvalidOperationException($"Request inspection exceeds {MaxGrowthInspectionWork} serialized nodes.");
            }

            private SerializedProperty Resolve(string path, int depth = 0)
            {
                if (depth > 20)
                    throw new InvalidOperationException("Array nesting exceeds 20 levels.");
                foreach (Match match in Regex.Matches(path, @"\.Array\.data\[(\d+)\]"))
                {
                    Inspect();
                    if (!int.TryParse(match.Groups[1].Value, out int index) || index == int.MaxValue)
                        throw new InvalidOperationException("Array index is outside the supported Int32 range.");
                    string arrayPath = path.Substring(0, match.Index);
                    if (_copies.TryGetValue(arrayPath, out var copies))
                    {
                        var copy = copies.LastOrDefault(c => index >= c.start && index < c.end);
                        if (copy.source != null)
                        {
                            string suffix = path.Substring(match.Index + match.Length);
                            return Resolve(copy.source + suffix, depth + 1);
                        }
                    }
                }
                return _serialized.FindProperty(path);
            }

            private int Size(string path)
            {
                if (_sizes.TryGetValue(path, out int size))
                    return size;
                // Appended elements have unspecified content. Budget subsequent writes as if
                // their nested arrays were empty, rather than crediting guessed copied sizes.
                using var resolved = Resolve(path);
                return resolved == null || resolved.propertyPath != path ? 0
                    : resolved.isArray ? resolved.arraySize
                    : 0;
            }

            public void EnsureIndices(string path)
            {
                foreach (Match match in Regex.Matches(path, @"\.Array\.data\[(\d+)\]"))
                {
                    if (!int.TryParse(match.Groups[1].Value, out int index) || index == int.MaxValue)
                        throw new InvalidOperationException("Array index is outside the supported Int32 range.");
                    string arrayPath = path.Substring(0, match.Index);
                    if (index >= Size(arrayPath))
                        Resize(arrayPath, checked(index + 1));
                }
            }

            public void Resize(string path, int size)
            {
                Inspect();
                using var property = Resolve(path);
                if (property != null && (!property.isArray || property.propertyType == SerializedPropertyType.String))
                    return;
                int oldSize = Size(path);
                if (size > oldSize)
                {
                    if (size > MaxArrayGrowthSize)
                        throw new InvalidOperationException($"Growing '{path}' exceeds {MaxArrayGrowthSize} elements.");
                    long characters = 0;
                    long cost = TypeCost(ElementType(TypeAt(path)), 0);
                    if (oldSize > 0)
                    {
                        // Unity leaves appended content unspecified. Use the most expensive
                        // retained prototype, not an assumption that only the last is copied.
                        int retained = Math.Min(oldSize, property?.arraySize ?? 0);
                        using var sample = retained == 0 ? null : property.GetArrayElementAtIndex(0);
                        bool compound =
                            sample == null
                            || sample.isArray
                            || sample.propertyType == SerializedPropertyType.Generic
                            || sample.propertyType == SerializedPropertyType.ManagedReference
                            || sample.propertyType == SerializedPropertyType.String;
                        if (compound)
                        {
                            for (int i = 0; i < retained; i++)
                            {
                                long candidateCharacters = 0;
                                long candidateCost = ElementCost(path + $".Array.data[{i}]", 0, ref candidateCharacters);
                                cost = Math.Max(cost, candidateCost);
                                characters = Math.Max(characters, candidateCharacters);
                                if (cost > MaxRequestArrayGrowth || characters > MaxRequestCopiedCharacters)
                                    throw new InvalidOperationException("Copied element exceeds the request growth budget.");
                            }
                            long plannedCharacters = 0;
                            cost = Math.Max(cost, ElementCost(path + $".Array.data[{oldSize - 1}]", 0, ref plannedCharacters));
                            characters = Math.Max(characters, plannedCharacters);
                        }
                    }
                    long added = checked((long)(size - oldSize) * cost);
                    _growth = checked(_growth + added);
                    _characters = checked(_characters + checked((long)(size - oldSize) * characters));
                    if (_growth > MaxRequestArrayGrowth)
                        throw new InvalidOperationException($"Request exceeds {MaxRequestArrayGrowth} added serialized elements/fields.");
                    if (_characters > MaxRequestCopiedCharacters)
                        throw new InvalidOperationException($"Request exceeds {MaxRequestCopiedCharacters} copied string characters.");
                    if (oldSize > 0)
                    {
                        if (!_copies.TryGetValue(path, out var copies))
                            _copies[path] = copies = new();
                        string source = path + $".Array.data[{oldSize - 1}]";
                        using var sourceProperty = Resolve(source);
                        source = sourceProperty?.propertyPath ?? source;
                        copies.Add((oldSize, size, source));
                    }
                }
                if (size < oldSize)
                {
                    if (_copies.TryGetValue(path, out var copies))
                    {
                        copies.RemoveAll(c => c.start >= size);
                        for (int i = 0; i < copies.Count; i++)
                            if (copies[i].end > size)
                                copies[i] = (copies[i].start, size, copies[i].source);
                    }
                    string prefix = path + ".Array.data[";
                    foreach (string child in _sizes.Keys.Where(k => k.StartsWith(prefix, StringComparison.Ordinal)).ToArray())
                    {
                        int end = child.IndexOf(']', prefix.Length);
                        if (end >= 0 && int.TryParse(child.Substring(prefix.Length, end - prefix.Length), out int index) && index >= size)
                            _sizes.Remove(child);
                    }
                }
                _sizes[path] = size;
                _peakSizes[path] = Math.Max(size, _peakSizes.TryGetValue(path, out int peak) ? peak : 0);
                string shape = Shape(path);
                _arrayPeaks[shape] = Math.Max(Math.Max(size, property?.arraySize ?? 0), _arrayPeaks.TryGetValue(shape, out int shapePeak) ? shapePeak : 0);
            }

            private long ElementCost(string path, int depth, ref long characters)
            {
                Inspect();
                if (depth > 20)
                    throw new InvalidOperationException("Array nesting exceeds 20 levels.");
                using var property = Resolve(path);
                string shape = Shape(path);
                if (property == null)
                {
                    if (_stringPeaks.TryGetValue(shape, out int newLength))
                        characters = checked(characters + newLength);
                    Type type = TypeAt(path);
                    if (ElementType(type) != null)
                    {
                        int newSize = _arrayPeaks.TryGetValue(shape, out int plannedSize) ? plannedSize : 0;
                        if (newSize == 0)
                            return 1;
                        long sampleCharacters = 0;
                        long sampleCost = ElementCost(path + $".Array.data[{newSize - 1}]", depth + 1, ref sampleCharacters);
                        characters = checked(characters + checked((long)newSize * sampleCharacters));
                        return checked(1 + checked((long)newSize * sampleCost));
                    }
                    if (type == null || type.IsPrimitive || type.IsEnum || type == typeof(string) || typeof(UnityEngine.Object).IsAssignableFrom(type))
                        return 1;
                    long newCost = 1;
                    foreach (var field in SerializedFields(type))
                        newCost = checked(newCost + ElementCost(path + "." + field.Name, depth + 1, ref characters));
                    return newCost;
                }
                long cost = 1;
                if (property.propertyType == SerializedPropertyType.String)
                {
                    int length = property.stringValue?.Length ?? 0;
                    if (_strings.TryGetValue(path, out int mappedLength))
                        length = Math.Max(length, mappedLength);
                    if (_stringPeaks.TryGetValue(shape, out int peakLength))
                        length = Math.Max(length, peakLength);
                    _stringPeaks[shape] = length;
                    characters = checked(characters + length);
                }
                if (property.isArray && property.propertyType != SerializedPropertyType.String)
                {
                    int size = Math.Max(Size(path), property.arraySize);
                    if (_peakSizes.TryGetValue(path, out int peak))
                        size = Math.Max(size, peak);
                    if (_arrayPeaks.TryGetValue(shape, out int shapePeak))
                        size = Math.Max(size, shapePeak);
                    _arrayPeaks[shape] = size;
                    if (size == 0)
                        return cost;
                    using var sample = property.arraySize == 0 ? null : property.GetArrayElementAtIndex(0);
                    if (
                        sample != null
                        && !sample.isArray
                        && sample.propertyType != SerializedPropertyType.Generic
                        && sample.propertyType != SerializedPropertyType.ManagedReference
                        && sample.propertyType != SerializedPropertyType.String
                    )
                        return checked(cost + size);
                    for (int i = 0; i < size; i++)
                    {
                        string elementPath = path + $".Array.data[{i}]";
                        if (i >= property.arraySize && property.arraySize > 0)
                            elementPath = path + $".Array.data[{property.arraySize - 1}]";
                        cost = checked(cost + ElementCost(elementPath, depth + 1, ref characters));
                        if (cost > MaxRequestArrayGrowth)
                            return cost;
                    }
                }
                else if (property.propertyType == SerializedPropertyType.Generic || property.propertyType == SerializedPropertyType.ManagedReference)
                {
                    using var child = property.Copy();
                    using var end = property.GetEndProperty();
                    bool next = child.Next(true);
                    while (next && !SerializedProperty.EqualContents(child, end))
                    {
                        string suffix = child.propertyPath.Substring(property.propertyPath.Length);
                        cost = checked(cost + ElementCost(path + suffix, depth + 1, ref characters));
                        if (cost > MaxRequestArrayGrowth)
                            return cost;
                        next = child.Next(false);
                    }
                }
                return cost;
            }

            private static Type ElementType(Type type) =>
                type?.IsArray == true ? type.GetElementType()
                : type?.IsGenericType == true && type.GetGenericTypeDefinition() == typeof(List<>) ? type.GetGenericArguments()[0]
                : null;

            private Type TypeAt(string path)
            {
                Type type = _serialized.targetObject.GetType();
                foreach (string segment in Regex.Replace(path, @"\.Array\.data\[\d+\]", ".[]").Split('.'))
                {
                    if (segment == "[]")
                    {
                        type = ElementType(type);
                        continue;
                    }
                    System.Reflection.FieldInfo field = null;
                    for (Type declaring = type; declaring != null && field == null; declaring = declaring.BaseType)
                        field = declaring.GetField(
                            segment,
                            System.Reflection.BindingFlags.Instance
                                | System.Reflection.BindingFlags.Public
                                | System.Reflection.BindingFlags.NonPublic
                                | System.Reflection.BindingFlags.DeclaredOnly
                        );
                    type = field?.FieldType;
                    if (type == null)
                        break;
                }
                return type;
            }

            private long TypeCost(Type type, int depth)
            {
                Inspect();
                if (depth > 20)
                    throw new InvalidOperationException("Serialized type nesting exceeds 20 levels.");
                if (
                    type == null
                    || type.IsPrimitive
                    || type.IsEnum
                    || type == typeof(string)
                    || typeof(UnityEngine.Object).IsAssignableFrom(type)
                    || ElementType(type) != null
                )
                    return 1;
                long cost = 1;
                foreach (var field in SerializedFields(type))
                {
                    cost = checked(cost + TypeCost(field.FieldType, depth + 1));
                    if (cost > MaxRequestArrayGrowth)
                        return cost;
                }
                return cost;
            }

            private IEnumerable<System.Reflection.FieldInfo> SerializedFields(Type type)
            {
                for (Type declaring = type; declaring != null && declaring != typeof(object); declaring = declaring.BaseType)
                    foreach (
                        var field in declaring.GetFields(
                            System.Reflection.BindingFlags.Instance
                                | System.Reflection.BindingFlags.Public
                                | System.Reflection.BindingFlags.NonPublic
                                | System.Reflection.BindingFlags.DeclaredOnly
                        )
                    )
                    {
                        Inspect();
                        if (field.IsStatic || field.IsDefined(typeof(NonSerializedAttribute), false))
                            continue;
                        if (field.IsPublic || field.IsDefined(typeof(SerializeField), true) || field.IsDefined(typeof(SerializeReference), true))
                            yield return field;
                    }
            }

            public void Map(string path, JToken value, int depth)
            {
                Inspect();
                if (depth > 20)
                    throw new InvalidOperationException("Patch nesting exceeds 20 levels.");
                if (path.EndsWith(".Array.size", StringComparison.Ordinal))
                {
                    if (!TryReadArraySize(value, out int size))
                        throw new InvalidOperationException("Array size must be a non-negative Int32 value.");
                    Resize(path.Substring(0, path.Length - ".Array.size".Length), size);
                    return;
                }
                using var property = Resolve(path);
                if (property?.propertyType == SerializedPropertyType.String || (property == null && TypeAt(path) == typeof(string)))
                {
                    _strings[path] = Math.Max(
                        value.Type == JTokenType.Null ? 0 : value.ToString().Length,
                        _strings.TryGetValue(path, out int prior) ? prior : 0
                    );
                    string shape = Shape(path);
                    _stringPeaks[shape] = Math.Max(_strings[path], _stringPeaks.TryGetValue(shape, out int peak) ? peak : 0);
                }
                if (value is JArray array && (property == null || (property.isArray && property.propertyType != SerializedPropertyType.String)))
                {
                    Resize(path, array.Count);
                    for (int i = 0; i < array.Count; i++)
                        Map(path + $".Array.data[{i}]", array[i], depth + 1);
                }
                else if (value is JObject obj && (property == null || (property.propertyType == SerializedPropertyType.Generic && !property.isArray)))
                {
                    foreach (var child in obj)
                        Map(path + "." + child.Key, child.Value, depth + 1);
                }
            }
        }

        /// <summary>
        /// Validates patches without applying them (for dry-run mode).
        /// Checks that property paths exist and that value types are compatible.
        /// </summary>
        private static List<object> ValidatePatches(UnityEngine.Object target, JArray patches)
        {
            var results = new List<object>(patches.Count);
            using var so = new SerializedObject(target);
            so.Update();

            for (int i = 0; i < patches.Count; i++)
            {
                if (patches[i] is not JObject patchObj)
                {
                    results.Add(
                        new
                        {
                            index = i,
                            propertyPath = "",
                            op = "",
                            ok = false,
                            message = $"Patch at index {i} must be an object.",
                        }
                    );
                    continue;
                }

                string propertyPath = patchObj["propertyPath"]?.ToString() ?? patchObj["property_path"]?.ToString() ?? patchObj["path"]?.ToString();
                string op = (patchObj["op"]?.ToString() ?? "set").Trim();

                if (string.IsNullOrWhiteSpace(propertyPath))
                {
                    results.Add(
                        new
                        {
                            index = i,
                            propertyPath = propertyPath ?? "",
                            op,
                            ok = false,
                            message = "Missing required field: propertyPath",
                        }
                    );
                    continue;
                }

                // Normalize the path
                string normalizedPath = NormalizePropertyPath(propertyPath);
                string normalizedOp = string.IsNullOrWhiteSpace(op) ? "set" : op.ToLowerInvariant();
                if (normalizedOp != "set" && normalizedOp != "array_resize")
                {
                    results.Add(
                        new
                        {
                            index = i,
                            propertyPath = normalizedPath,
                            op,
                            ok = false,
                            message = $"Unknown patch operation: '{op}'.",
                        }
                    );
                    continue;
                }
                if (normalizedOp == "set" && patchObj["value"] == null && patchObj["ref"] == null)
                {
                    results.Add(
                        new
                        {
                            index = i,
                            propertyPath = normalizedPath,
                            op,
                            ok = false,
                            message = "Missing required field: value or ref",
                        }
                    );
                    continue;
                }

                // For array_resize, check if the array exists
                if (normalizedOp == "array_resize")
                {
                    var valueToken = patchObj["value"];
                    if (valueToken == null || valueToken.Type == JTokenType.Null)
                    {
                        results.Add(
                            new
                            {
                                index = i,
                                propertyPath = normalizedPath,
                                op,
                                ok = false,
                                message = "array_resize requires integer 'value'.",
                            }
                        );
                        continue;
                    }

                    if (!TryReadArraySize(valueToken, out int size))
                    {
                        results.Add(
                            new
                            {
                                index = i,
                                propertyPath = normalizedPath,
                                op,
                                ok = false,
                                message = "array_resize requires non-negative integer 'value'.",
                            }
                        );
                        continue;
                    }

                    // Check if the array path exists
                    string arrayPath = normalizedPath;
                    if (arrayPath.EndsWith(".Array.size", StringComparison.Ordinal))
                    {
                        arrayPath = arrayPath.Substring(0, arrayPath.Length - ".Array.size".Length);
                    }

                    using var arrayProp = so.FindProperty(arrayPath);
                    if (arrayProp == null)
                    {
                        results.Add(
                            new
                            {
                                index = i,
                                propertyPath = normalizedPath,
                                op,
                                ok = false,
                                message = $"Array not found: {arrayPath}",
                            }
                        );
                        continue;
                    }

                    if (!arrayProp.isArray)
                    {
                        results.Add(
                            new
                            {
                                index = i,
                                propertyPath = normalizedPath,
                                op,
                                ok = false,
                                message = $"Property is not an array: {arrayPath}",
                            }
                        );
                        continue;
                    }

                    results.Add(
                        new
                        {
                            index = i,
                            propertyPath = normalizedPath,
                            op,
                            ok = true,
                            message = $"Will resize to {size}.",
                            currentSize = arrayProp.arraySize,
                        }
                    );
                    continue;
                }

                // For set operations, check if the property exists (or can be auto-grown)
                using var prop = so.FindProperty(normalizedPath);

                // Check if it's an auto-growable array element path
                bool isAutoGrowable = false;
                if (prop == null)
                {
                    var match = Regex.Match(normalizedPath, @"^(.+?)\.Array\.data\[(\d+)\]");
                    if (match.Success)
                    {
                        string arrayPath = match.Groups[1].Value;
                        using var arrayProp = so.FindProperty(arrayPath);
                        if (arrayProp != null && arrayProp.isArray)
                        {
                            isAutoGrowable = true;
                            // Get the element type info from existing elements or report as growable
                            int targetIndex = int.Parse(match.Groups[2].Value);
                            if (arrayProp.arraySize > 0)
                            {
                                using var sampleElement = arrayProp.GetArrayElementAtIndex(0);
                                results.Add(
                                    new
                                    {
                                        index = i,
                                        propertyPath = normalizedPath,
                                        op,
                                        ok = true,
                                        message = $"Will auto-grow array from {arrayProp.arraySize} to {targetIndex + 1}.",
                                        elementType = sampleElement?.propertyType.ToString() ?? "unknown",
                                    }
                                );
                            }
                            else
                            {
                                results.Add(
                                    new
                                    {
                                        index = i,
                                        propertyPath = normalizedPath,
                                        op,
                                        ok = true,
                                        message = $"Will auto-grow empty array to size {targetIndex + 1}.",
                                    }
                                );
                            }
                            continue;
                        }
                    }
                }

                if (prop == null && !isAutoGrowable)
                {
                    results.Add(
                        new
                        {
                            index = i,
                            propertyPath = normalizedPath,
                            op,
                            ok = false,
                            message = $"Property not found: {normalizedPath}",
                        }
                    );
                    continue;
                }

                if (prop != null)
                {
                    // Property exists - validate value format for supported complex types
                    var valueToken = patchObj["value"];
                    string valueValidationMsg = null;
                    bool valueFormatOk = true;

                    // Enhanced dry-run: validate value format for AnimationCurve and Quaternion
                    // Uses shared validators from VectorParsing
                    if (prop.propertyType == SerializedPropertyType.Integer)
                    {
                        valueFormatOk = TryParseIntegerValue(prop, valueToken, out _, out valueValidationMsg);
                    }
                    else if (valueToken != null && valueToken.Type != JTokenType.Null)
                    {
                        switch (prop.propertyType)
                        {
                            case SerializedPropertyType.AnimationCurve:
                                valueFormatOk = VectorParsing.ValidateAnimationCurveFormat(valueToken, out valueValidationMsg);
                                break;
                            case SerializedPropertyType.Quaternion:
                                valueFormatOk = VectorParsing.ValidateQuaternionFormat(valueToken, out valueValidationMsg);
                                break;
                        }
                    }

                    if (valueFormatOk)
                    {
                        results.Add(
                            new
                            {
                                index = i,
                                propertyPath = normalizedPath,
                                op,
                                ok = true,
                                message = valueValidationMsg ?? "Property found.",
                                propertyType = prop.propertyType.ToString(),
                                isArray = prop.isArray,
                            }
                        );
                    }
                    else
                    {
                        results.Add(
                            new
                            {
                                index = i,
                                propertyPath = normalizedPath,
                                op,
                                ok = false,
                                message = valueValidationMsg,
                                propertyType = prop.propertyType.ToString(),
                                isArray = prop.isArray,
                            }
                        );
                    }
                }
            }

            return results;
        }

        private static (List<object> results, List<string> warnings) ApplyPatches(UnityEngine.Object target, JArray patches) =>
            ApplyPatchesCore(target, patches, saveAssets: true);

        private static (List<object> results, List<string> warnings) ApplyPatchesCore(UnityEngine.Object target, JArray patches, bool saveAssets)
        {
            var warnings = new List<string>();
            var results = new List<object>(patches.Count);
            bool anyChanged = false;

            using var so = new SerializedObject(target);
            so.Update();

            for (int i = 0; i < patches.Count; i++)
            {
                if (patches[i] is not JObject patchObj)
                {
                    results.Add(
                        new
                        {
                            propertyPath = "",
                            op = "",
                            ok = false,
                            message = $"Patch at index {i} must be an object.",
                        }
                    );
                    continue;
                }

                string propertyPath = patchObj["propertyPath"]?.ToString() ?? patchObj["property_path"]?.ToString() ?? patchObj["path"]?.ToString();
                string op = (patchObj["op"]?.ToString() ?? "set").Trim();
                if (string.IsNullOrWhiteSpace(propertyPath))
                {
                    results.Add(
                        new
                        {
                            propertyPath = propertyPath ?? "",
                            op,
                            ok = false,
                            message = "Missing required field: propertyPath",
                        }
                    );
                    continue;
                }

                if (string.IsNullOrWhiteSpace(op))
                {
                    op = "set";
                }

                var patchResult = ApplyPatch(so, propertyPath, op, patchObj, out bool changed);
                anyChanged |= changed;
                results.Add(patchResult);

                // Keep capacity and conversion changes staged until this patch succeeds.
                if (changed)
                {
                    AssetPathUtility.GetFullAssetPath(AssetDatabase.GetAssetPath(target));
                    so.ApplyModifiedProperties();
                }
                // Failed patches may have staged growth before resolving their leaf/value.
                so.Update();
            }

            if (anyChanged)
            {
                AssetPathUtility.GetFullAssetPath(AssetDatabase.GetAssetPath(target));
                EditorUtility.SetDirty(target);
                if (saveAssets)
                    AssetDatabase.SaveAssets();
            }

            return (results, warnings);
        }

        private static object ApplyPatch(SerializedObject so, string propertyPath, string op, JObject patchObj, out bool changed)
        {
            changed = false;
            try
            {
                // Phase 1.1: Normalize friendly path syntax (e.g., myList[5] → myList.Array.data[5])
                string normalizedPath = NormalizePropertyPath(propertyPath);
                string normalizedOp = op.Trim().ToLowerInvariant();

                switch (normalizedOp)
                {
                    case "array_resize":
                        return ApplyArrayResize(so, normalizedPath, patchObj, out changed);
                    case "set":
                        return ApplySet(so, normalizedPath, patchObj, out changed);
                    default:
                        return new
                        {
                            propertyPath,
                            op,
                            ok = false,
                            message = $"Unknown patch operation: '{op}'.",
                        };
                }
            }
            catch (Exception ex)
            {
                return new
                {
                    propertyPath,
                    op,
                    ok = false,
                    message = ex.Message,
                };
            }
        }

        /// <summary>
        /// Normalizes friendly property path syntax to Unity's internal format.
        /// Converts bracket notation (e.g., myList[5]) to Unity's Array.data format (myList.Array.data[5]).
        /// </summary>
        private static string NormalizePropertyPath(string path)
        {
            if (string.IsNullOrEmpty(path))
                return path;

            // Pattern: word[number] where it's not already in .Array.data[number] format
            // We need to handle cases like: myList[5], nested.list[0].field, etc.
            // But NOT: myList.Array.data[5] (already in Unity format)

            // Replace fieldName[index] with fieldName.Array.data[index]
            // But only if it's not already in Array.data format
            return Regex.Replace(
                path,
                @"(\w+)\[(\d+)\]",
                m =>
                {
                    string fieldName = m.Groups[1].Value;
                    string index = m.Groups[2].Value;

                    // Check if this match is already part of .Array.data[index] pattern
                    // by checking if the text immediately before the field name is ".Array."
                    // and the field name is "data"
                    int matchStart = m.Index;
                    if (fieldName == "data" && matchStart >= 7) // Length of ".Array."
                    {
                        string preceding = path.Substring(matchStart - 7, 7);
                        if (preceding == ".Array.")
                        {
                            // Already in Unity format (e.g., myList.Array.data[0]), return as-is
                            return m.Value;
                        }
                    }

                    return $"{fieldName}.Array.data[{index}]";
                }
            );
        }

        /// <summary>
        /// Ensures an array has sufficient capacity for the given index.
        /// Automatically resizes the array if the target index is beyond current bounds.
        /// </summary>
        /// <param name="so">The SerializedObject containing the array</param>
        /// <param name="path">The normalized property path (must be in Array.data format)</param>
        /// <param name="resized">True if the array was resized</param>
        /// <returns>True if the path is valid for setting, false if it cannot be resolved</returns>
        private static bool EnsureArrayCapacity(SerializedObject so, string path, out bool resized)
        {
            resized = false;

            // Match pattern: something.Array.data[N]
            foreach (Match match in Regex.Matches(path, @"\.Array\.data\[(\d+)\]"))
            {
                string arrayPath = path.Substring(0, match.Index);
                if (!int.TryParse(match.Groups[1].Value, out int targetIndex) || targetIndex == int.MaxValue)
                    return false;
                using var arrayProp = so.FindProperty(arrayPath);
                if (arrayProp == null || !arrayProp.isArray)
                    return false;
                if (arrayProp.arraySize <= targetIndex)
                {
                    int newSize = checked(targetIndex + 1);
                    CheckArraySizeChange(arrayProp.arraySize, newSize);
                    arrayProp.arraySize = newSize;
                    resized = true;
                }
            }

            return true;
        }

        private static object ApplyArrayResize(SerializedObject so, string propertyPath, JObject patchObj, out bool changed)
        {
            changed = false;

            // Use ParamCoercion for robust int parsing
            var valueToken = patchObj["value"];
            if (valueToken == null || valueToken.Type == JTokenType.Null)
            {
                return new
                {
                    propertyPath,
                    op = "array_resize",
                    ok = false,
                    message = "array_resize requires integer 'value'.",
                };
            }

            if (!TryReadArraySize(valueToken, out int newSize))
            {
                return new
                {
                    propertyPath,
                    op = "array_resize",
                    ok = false,
                    message = "array_resize requires integer 'value'.",
                };
            }

            newSize = Math.Max(0, newSize);

            // Unity supports resizing either:
            // - the array/list property itself (prop.isArray -> prop.arraySize)
            // - the synthetic leaf property "<array>.Array.size" (prop.intValue)
            //
            // Different Unity versions/serialization edge cases can fail to resolve the synthetic leaf via FindProperty
            // (or can return different property types), so we keep a "best-effort" fallback:
            // - Prefer acting on the requested path if it resolves.
            // - If the requested path doesn't resolve, try to resolve the *array property* and set arraySize directly.
            using SerializedProperty prop = so.FindProperty(propertyPath);
            SerializedProperty arrayProp = null;
            try
            {
                if (propertyPath.EndsWith(".Array.size", StringComparison.Ordinal))
                {
                    // Caller explicitly targeted the synthetic leaf. Resolve the parent array property as a fallback
                    // (Unity sometimes fails to resolve the synthetic leaf in certain serialization contexts).
                    var arrayPath = propertyPath.Substring(0, propertyPath.Length - ".Array.size".Length);
                    arrayProp = so.FindProperty(arrayPath);
                }
                else
                {
                    // Caller targeted either the array property itself (e.g., "items") or some other property.
                    // If it's already an array, we can resize it directly. Otherwise, we attempt to resolve
                    // a synthetic ".Array.size" leaf as a convenience, which some clients may pass.
                    arrayProp = prop != null && prop.isArray ? prop : so.FindProperty(propertyPath + ".Array.size");
                }

                if (prop == null)
                {
                    // If we failed to find the direct property but we *can* find the array property, use that.
                    if (arrayProp != null && arrayProp.isArray)
                    {
                        if (arrayProp.arraySize != newSize)
                        {
                            CheckArraySizeChange(arrayProp.arraySize, newSize);
                            arrayProp.arraySize = newSize;
                            changed = true;
                        }
                        return new
                        {
                            propertyPath,
                            op = "array_resize",
                            ok = true,
                            resolvedPropertyType = "Array",
                            message = $"Set array size to {newSize}.",
                        };
                    }

                    return new
                    {
                        propertyPath,
                        op = "array_resize",
                        ok = false,
                        message = $"Property not found: {propertyPath}",
                    };
                }

                // Unity may represent ".Array.size" as either Integer or ArraySize depending on version.
                if (
                    (prop.propertyType == SerializedPropertyType.Integer || prop.propertyType == SerializedPropertyType.ArraySize)
                    && propertyPath.EndsWith(".Array.size", StringComparison.Ordinal)
                )
                {
                    // We successfully resolved the synthetic leaf; write the size through its intValue.
                    if (prop.intValue != newSize)
                    {
                        CheckArraySizeChange(prop.intValue, newSize);
                        prop.intValue = newSize;
                        changed = true;
                    }
                    return new
                    {
                        propertyPath,
                        op = "array_resize",
                        ok = true,
                        resolvedPropertyType = prop.propertyType.ToString(),
                        message = $"Set array size to {newSize}.",
                    };
                }

                if (prop.isArray)
                {
                    // We resolved the array property itself; write through arraySize.
                    if (prop.arraySize != newSize)
                    {
                        CheckArraySizeChange(prop.arraySize, newSize);
                        prop.arraySize = newSize;
                        changed = true;
                    }
                    return new
                    {
                        propertyPath,
                        op = "array_resize",
                        ok = true,
                        resolvedPropertyType = "Array",
                        message = $"Set array size to {newSize}.",
                    };
                }

                return new
                {
                    propertyPath,
                    op = "array_resize",
                    ok = false,
                    resolvedPropertyType = prop.propertyType.ToString(),
                    message = $"Property is not an array or array-size field: {propertyPath}",
                };
            }
            finally
            {
                if (!ReferenceEquals(arrayProp, prop))
                    arrayProp?.Dispose();
            }
        }

        private static object ApplySet(SerializedObject so, string propertyPath, JObject patchObj, out bool changed)
        {
            changed = false;
            if (propertyPath.EndsWith(".Array.size", StringComparison.Ordinal) && patchObj["value"] != null)
                return ApplyArrayResize(so, propertyPath, patchObj, out changed);
            if (patchObj["value"] == null && patchObj["ref"] == null)
            {
                return new
                {
                    propertyPath,
                    op = "set",
                    ok = false,
                    message = "Missing required field: value or ref",
                };
            }

            // Phase 1.2: Auto-resize arrays if targeting an index beyond current bounds
            if (!EnsureArrayCapacity(so, propertyPath, out _))
            {
                // Could not resolve the array path - try to find the property anyway for a better error message
                using var checkProp = so.FindProperty(propertyPath);
                if (checkProp == null)
                {
                    // Try to provide helpful context about what went wrong
                    var arrayMatch = Regex.Match(propertyPath, @"^(.+?)\.Array\.data\[(\d+)\]");
                    if (arrayMatch.Success)
                    {
                        string arrayPath = arrayMatch.Groups[1].Value;
                        using var arrayProp = so.FindProperty(arrayPath);
                        if (arrayProp == null)
                        {
                            return new
                            {
                                propertyPath,
                                op = "set",
                                ok = false,
                                message = $"Array property not found: {arrayPath}",
                            };
                        }
                        if (!arrayProp.isArray)
                        {
                            return new
                            {
                                propertyPath,
                                op = "set",
                                ok = false,
                                message = $"Property is not an array: {arrayPath}",
                            };
                        }
                    }
                    return new
                    {
                        propertyPath,
                        op = "set",
                        ok = false,
                        message = $"Property not found: {propertyPath}",
                    };
                }
            }

            using var prop = so.FindProperty(propertyPath);
            if (prop == null)
            {
                return new
                {
                    propertyPath,
                    op = "set",
                    ok = false,
                    message = $"Property not found: {propertyPath}",
                };
            }

            if (prop.propertyType == SerializedPropertyType.ObjectReference)
            {
                // Legacy "ref" key takes precedence for backward compatibility.
                // Use TryGetValue to preserve non-JObject ref tokens (e.g. string GUID).
                patchObj.TryGetValue("ref", out JToken refToken);
                var objRefValue = patchObj["value"];
                JToken resolveToken = refToken ?? objRefValue;

                if (resolveToken == null)
                {
                    return new
                    {
                        propertyPath,
                        op = "set",
                        ok = false,
                        resolvedPropertyType = prop.propertyType.ToString(),
                        message = "ObjectReference patch requires a 'ref' or 'value' key.",
                    };
                }

                if (!ComponentOps.SetObjectReference(prop, resolveToken, out string refError))
                {
                    return new
                    {
                        propertyPath,
                        op = "set",
                        ok = false,
                        resolvedPropertyType = prop.propertyType.ToString(),
                        message = refError,
                    };
                }

                changed = true;
                string refMessage = prop.objectReferenceValue == null ? "Cleared reference." : $"Set reference to '{prop.objectReferenceValue.name}'.";
                return new
                {
                    propertyPath,
                    op = "set",
                    ok = true,
                    resolvedPropertyType = prop.propertyType.ToString(),
                    message = refMessage,
                };
            }

            var valueToken = patchObj["value"];
            if (valueToken == null)
            {
                return new
                {
                    propertyPath,
                    op = "set",
                    ok = false,
                    resolvedPropertyType = prop.propertyType.ToString(),
                    message = "Missing required field: value",
                };
            }

            bool ok = TrySetValue(prop, valueToken, out string message);
            changed = ok;
            return new
            {
                propertyPath,
                op = "set",
                ok,
                resolvedPropertyType = prop.propertyType.ToString(),
                message,
            };
        }

        private static bool TrySetValue(SerializedProperty prop, JToken valueToken, out string message)
        {
            return TrySetValueRecursive(prop, valueToken, out message, 0);
        }

        /// <summary>
        /// Recursively sets values on SerializedProperties, supporting bulk array and object mapping.
        /// </summary>
        /// <param name="prop">The property to set</param>
        /// <param name="valueToken">The JSON value</param>
        /// <param name="message">Output message describing the result</param>
        /// <param name="depth">Current recursion depth (for safety limits)</param>
        private static bool TrySetValueRecursive(SerializedProperty prop, JToken valueToken, out string message, int depth)
        {
            // Container mappings retain valid children, but an entirely rejected child must
            // restore its staged subtree, including array capacity, before its siblings run.
            bool isContainer =
                prop.isArray && prop.propertyType != SerializedPropertyType.String && valueToken is JArray
                || prop.propertyType == SerializedPropertyType.Generic && !prop.isArray && valueToken is JObject;
            if (!isContainer)
                return TrySetValueRecursiveCore(prop, valueToken, out message, depth);

            var so = prop.serializedObject;
            string path = prop.propertyPath;
            string rootPath = path.Split('.')[0];
            using var checkpoint = new SerializedObject(so.targetObjects);
            // Copy pending ancestors too, so newly grown elements exist in the checkpoint.
            using (var rootProperty = so.FindProperty(rootPath))
                checkpoint.CopyFromSerializedProperty(rootProperty);
            bool ok = TrySetValueRecursiveCore(prop, valueToken, out message, depth);
            if (!ok)
            {
                using var checkpointProperty = checkpoint.FindProperty(path);
                so.CopyFromSerializedProperty(checkpointProperty);
            }
            return ok;
        }

        private static bool TrySetValueRecursiveCore(SerializedProperty prop, JToken valueToken, out string message, int depth)
        {
            message = null;
            const int MaxRecursionDepth = 20;

            if (depth > MaxRecursionDepth)
            {
                message = $"Maximum recursion depth ({MaxRecursionDepth}) exceeded. Check for circular references.";
                return false;
            }

            try
            {
                if (prop.propertyPath.EndsWith(".Array.size", StringComparison.Ordinal))
                {
                    if (!TryReadArraySize(valueToken, out int size))
                    {
                        message = "Expected non-negative Int32 array size.";
                        return false;
                    }
                    CheckArraySizeChange(prop.intValue, size);
                    prop.intValue = size;
                    message = "Set array size.";
                    return true;
                }
                // Phase 3.1: Handle bulk array mapping - JArray value for array/list properties
                if (prop.isArray && prop.propertyType != SerializedPropertyType.String && valueToken is JArray jArray)
                {
                    // Resize the array to match the JSON array
                    CheckArraySizeChange(prop.arraySize, jArray.Count);
                    prop.arraySize = jArray.Count;

                    int successCount = 0;
                    var errors = new List<string>();

                    for (int i = 0; i < jArray.Count; i++)
                    {
                        using var elementProp = prop.GetArrayElementAtIndex(i);
                        if (elementProp == null)
                        {
                            errors.Add($"Could not get element at index {i}");
                            continue;
                        }

                        if (TrySetValueRecursive(elementProp, jArray[i], out string elemMessage, depth + 1))
                        {
                            successCount++;
                        }
                        else
                        {
                            errors.Add($"[{i}]: {elemMessage}");
                        }
                    }

                    if (errors.Count > 0)
                    {
                        message = $"Set {successCount}/{jArray.Count} elements. Errors: {string.Join("; ", errors)}";
                        return successCount > 0; // Partial success
                    }

                    message = $"Set array with {jArray.Count} elements.";
                    return true;
                }

                // Phase 3.2: Handle bulk object mapping - JObject value for Generic (struct/class) properties
                if (prop.propertyType == SerializedPropertyType.Generic && !prop.isArray && valueToken is JObject jObj)
                {
                    int successCount = 0;
                    var errors = new List<string>();
                    var so = prop.serializedObject;

                    foreach (var kvp in jObj)
                    {
                        string childPath = prop.propertyPath + "." + kvp.Key;
                        using var childProp = so.FindProperty(childPath);

                        if (childProp == null)
                        {
                            errors.Add($"Property not found: {kvp.Key}");
                            continue;
                        }

                        if (TrySetValueRecursive(childProp, kvp.Value, out string childMessage, depth + 1))
                        {
                            successCount++;
                        }
                        else
                        {
                            errors.Add($"{kvp.Key}: {childMessage}");
                        }
                    }

                    if (errors.Count > 0)
                    {
                        message = $"Set {successCount}/{jObj.Count} fields. Errors: {string.Join("; ", errors)}";
                        return successCount > 0; // Partial success
                    }

                    message = $"Set struct/class with {jObj.Count} fields.";
                    return true;
                }

                // ObjectReference - delegate to shared handler
                if (prop.propertyType == SerializedPropertyType.ObjectReference)
                {
                    if (!ComponentOps.SetObjectReference(prop, valueToken, out string refError))
                    {
                        message = refError;
                        return false;
                    }
                    message = prop.objectReferenceValue == null ? "Cleared reference." : $"Set reference to '{prop.objectReferenceValue.name}'.";
                    return true;
                }

                // Supported Types: Integer, Boolean, Float, String, Enum, Vector2, Vector3, Vector4, Color
                // Using shared helpers from ParamCoercion and VectorParsing
                switch (prop.propertyType)
                {
                    case SerializedPropertyType.Integer:
                        if (!TryParseIntegerValue(prop, valueToken, out long integerValue, out message))
                            return false;
                        if (prop.type == "long")
                            prop.longValue = integerValue;
                        else
                            prop.intValue = (int)integerValue;
                        message = prop.type == "long" ? "Set long." : "Set int.";
                        return true;

                    case SerializedPropertyType.Boolean:
                        // Use ParamCoercion for robust bool parsing (handles "true", "1", "yes", etc.)
                        if (valueToken == null || valueToken.Type == JTokenType.Null)
                        {
                            message = "Expected boolean value.";
                            return false;
                        }
                        bool boolVal = ParamCoercion.CoerceBool(valueToken, false);
                        prop.boolValue = boolVal;
                        message = "Set bool.";
                        return true;

                    case SerializedPropertyType.Float:
                        if (valueToken == null || valueToken.Type == JTokenType.Null)
                        {
                            message = "Expected floating-point value.";
                            return false;
                        }
                        if (prop.type == "double")
                        {
                            double doubleVal = valueToken.ReadScalar<double>();
                            prop.doubleValue = doubleVal;
                            message = "Set double.";
                            return true;
                        }
                        // Use ParamCoercion for robust float parsing
                        float floatVal = ParamCoercion.CoerceFloat(valueToken, float.NaN);
                        if (float.IsNaN(floatVal))
                        {
                            message = "Expected float value.";
                            return false;
                        }
                        prop.floatValue = floatVal;
                        message = "Set float.";
                        return true;

                    case SerializedPropertyType.String:
                        prop.stringValue = valueToken.Type == JTokenType.Null ? null : valueToken.ToString();
                        message = "Set string.";
                        return true;

                    case SerializedPropertyType.Enum:
                        return TrySetEnum(prop, valueToken, out message);

                    case SerializedPropertyType.Vector2:
                        // Use VectorParsing for Vector2
                        var v2 = VectorParsing.ParseVector2(valueToken);
                        if (v2 == null)
                        {
                            message = "Expected Vector2 (array or object).";
                            return false;
                        }
                        prop.vector2Value = v2.Value;
                        message = "Set Vector2.";
                        return true;

                    case SerializedPropertyType.Vector3:
                        // Use VectorParsing for Vector3
                        var v3 = VectorParsing.ParseVector3(valueToken);
                        if (v3 == null)
                        {
                            message = "Expected Vector3 (array or object).";
                            return false;
                        }
                        prop.vector3Value = v3.Value;
                        message = "Set Vector3.";
                        return true;

                    case SerializedPropertyType.Vector4:
                        // Use VectorParsing for Vector4
                        var v4 = VectorParsing.ParseVector4(valueToken);
                        if (v4 == null)
                        {
                            message = "Expected Vector4 (array or object).";
                            return false;
                        }
                        prop.vector4Value = v4.Value;
                        message = "Set Vector4.";
                        return true;

                    case SerializedPropertyType.Color:
                        // Use VectorParsing for Color
                        var col = VectorParsing.ParseColor(valueToken);
                        if (col == null)
                        {
                            message = "Expected Color (array or object).";
                            return false;
                        }
                        prop.colorValue = col.Value;
                        message = "Set Color.";
                        return true;

                    case SerializedPropertyType.AnimationCurve:
                        return TrySetAnimationCurve(prop, valueToken, out message);

                    case SerializedPropertyType.Quaternion:
                        return TrySetQuaternion(prop, valueToken, out message);

                    case SerializedPropertyType.Generic:
                        // Generic properties (structs/classes) should be handled above with JObject mapping
                        // If we get here, the value wasn't a JObject
                        if (prop.isArray)
                        {
                            message = $"Expected array (JArray) for array property, got {valueToken?.Type.ToString() ?? "null"}.";
                        }
                        else
                        {
                            message = $"Expected object (JObject) for struct/class property, got {valueToken?.Type.ToString() ?? "null"}.";
                        }
                        return false;

                    default:
                        message =
                            $"Unsupported SerializedPropertyType: {prop.propertyType}. "
                            + "This type cannot be set via MCP patches. Consider editing the .asset file directly "
                            + "or using Unity's Inspector. For complex types, check if there's a supported alternative format.";
                        return false;
                }
            }
            catch (Exception ex)
            {
                message = ex.Message;
                return false;
            }
        }

        private static bool TryParseIntegerValue(SerializedProperty prop, JToken token, out long value, out string message)
        {
            value = 0;
            message = "Expected integer value.";
            if (
                token == null
                || token.Type == JTokenType.Null
                || (token.Type != JTokenType.Integer && token.Type != JTokenType.String && !long.TryParse(token.ToString(), out _))
            )
                return false;

            try
            {
                value = token.ReadScalar<long>();
                if (prop.type != "long" && (value < int.MinValue || value > int.MaxValue))
                {
                    message = "Integer value is outside the Int32 range.";
                    return false;
                }
                message = null;
                return true;
            }
            catch (Exception ex)
            {
                message = ex.Message;
                return false;
            }
        }

        private static bool TrySetEnum(SerializedProperty prop, JToken valueToken, out string message)
        {
            message = null;
            var names = prop.enumNames;
            if (names == null || names.Length == 0)
            {
                message = "Enum has no names.";
                return false;
            }

            if (valueToken.Type == JTokenType.Integer)
            {
                int idx = valueToken.ReadScalar<int>();
                if (idx < 0 || idx >= names.Length)
                {
                    message = $"Enum index out of range: {idx}";
                    return false;
                }
                prop.enumValueIndex = idx;
                message = "Set enum.";
                return true;
            }

            string s = valueToken.ToString();
            for (int i = 0; i < names.Length; i++)
            {
                if (string.Equals(names[i], s, StringComparison.OrdinalIgnoreCase))
                {
                    prop.enumValueIndex = i;
                    message = "Set enum.";
                    return true;
                }
            }
            message = $"Unknown enum name '{s}'.";
            return false;
        }

        /// <summary>
        /// Sets an AnimationCurve property from a JSON structure.
        ///
        /// <para><b>Supported formats:</b></para>
        /// <list type="bullet">
        ///   <item>Wrapped: <c>{ "keys": [ { "time": 0, "value": 1.0 }, ... ] }</c></item>
        ///   <item>Direct array: <c>[ { "time": 0, "value": 1.0 }, ... ]</c></item>
        ///   <item>Null/empty: Sets an empty AnimationCurve</item>
        /// </list>
        ///
        /// <para><b>Keyframe fields:</b></para>
        /// <list type="bullet">
        ///   <item><c>time</c> (float): Keyframe time position. <b>Default: 0</b></item>
        ///   <item><c>value</c> (float): Keyframe value. <b>Default: 0</b></item>
        ///   <item><c>inSlope</c> or <c>inTangent</c> (float): Incoming tangent slope. <b>Default: 0</b></item>
        ///   <item><c>outSlope</c> or <c>outTangent</c> (float): Outgoing tangent slope. <b>Default: 0</b></item>
        ///   <item><c>weightedMode</c> (int): Weighted mode enum (0=None, 1=In, 2=Out, 3=Both). <b>Default: 0 (None)</b></item>
        ///   <item><c>inWeight</c> (float): Incoming tangent weight. <b>Default: 0</b></item>
        ///   <item><c>outWeight</c> (float): Outgoing tangent weight. <b>Default: 0</b></item>
        /// </list>
        ///
        /// <para><b>Note:</b> All keyframe fields are optional. Missing fields gracefully default to 0,
        /// which produces linear interpolation when both tangents are 0.</para>
        /// </summary>
        /// <param name="prop">The SerializedProperty of type AnimationCurve to set</param>
        /// <param name="valueToken">JSON token containing the curve data</param>
        /// <param name="message">Output message describing the result</param>
        /// <returns>True if successful, false if the format is invalid</returns>
        private static bool TrySetAnimationCurve(SerializedProperty prop, JToken valueToken, out string message)
        {
            message = null;

            if (valueToken == null || valueToken.Type == JTokenType.Null)
            {
                // Set to empty curve
                prop.animationCurveValue = new AnimationCurve();
                message = "Set AnimationCurve to empty.";
                return true;
            }

            JArray keysArray = null;

            // Accept either { "keys": [...] } or just [...]
            if (valueToken is JObject curveObj)
            {
                keysArray = curveObj["keys"] as JArray;
                if (keysArray == null)
                {
                    message = "AnimationCurve object requires 'keys' array. Expected: { \"keys\": [ { \"time\": 0, \"value\": 0 }, ... ] }";
                    return false;
                }
            }
            else if (valueToken is JArray directArray)
            {
                keysArray = directArray;
            }
            else
            {
                message =
                    "AnimationCurve requires object with 'keys' or array of keyframes. "
                    + "Expected: { \"keys\": [ { \"time\": 0, \"value\": 0, \"inSlope\": 0, \"outSlope\": 0 }, ... ] }";
                return false;
            }

            try
            {
                AnimationCurve curve = null;
                foreach (var keyToken in keysArray)
                {
                    if (keyToken is not JObject keyObj)
                    {
                        message = "Each keyframe must be an object with 'time' and 'value'.";
                        return false;
                    }

                    float time = keyObj["time"]?.ReadScalar<float?>() ?? 0f;
                    float value = keyObj["value"]?.ReadScalar<float?>() ?? 0f;
                    float inSlope = keyObj["inSlope"]?.ReadCurveTangent() ?? keyObj["inTangent"]?.ReadCurveTangent() ?? 0f;
                    float outSlope = keyObj["outSlope"]?.ReadCurveTangent() ?? keyObj["outTangent"]?.ReadCurveTangent() ?? 0f;

                    var keyframe = new Keyframe(time, value, inSlope, outSlope);

                    // Optional: weighted tangent mode (Unity 2018.1+)
                    if (keyObj["weightedMode"] != null)
                    {
                        int weightedMode = keyObj["weightedMode"].ReadScalar<int>();
                        keyframe.weightedMode = (WeightedMode)weightedMode;
                    }
                    if (keyObj["inWeight"] != null)
                    {
                        keyframe.inWeight = keyObj["inWeight"].ReadScalar<float>();
                    }
                    if (keyObj["outWeight"] != null)
                    {
                        keyframe.outWeight = keyObj["outWeight"].ReadScalar<float>();
                    }

                    curve ??= new AnimationCurve();
                    curve.AddKey(keyframe);
                }

                prop.animationCurveValue = curve ?? new AnimationCurve();
                message = $"Set AnimationCurve with {keysArray.Count} keyframes.";
                return true;
            }
            catch (Exception ex)
            {
                message = $"Failed to parse AnimationCurve: {ex.Message}";
                return false;
            }
        }

        /// <summary>
        /// Sets a Quaternion property from JSON.
        ///
        /// <para><b>Supported formats:</b></para>
        /// <list type="bullet">
        ///   <item>Euler array: <c>[x, y, z]</c> - Euler angles in degrees</item>
        ///   <item>Raw quaternion array: <c>[x, y, z, w]</c> - Direct quaternion components</item>
        ///   <item>Object format: <c>{ "x": 0, "y": 0, "z": 0, "w": 1 }</c> - Direct components</item>
        ///   <item>Explicit euler: <c>{ "euler": [x, y, z] }</c> - Euler angles in degrees</item>
        ///   <item>Null/empty: Sets Quaternion.identity (no rotation)</item>
        /// </list>
        ///
        /// <para><b>Format detection:</b></para>
        /// <list type="bullet">
        ///   <item>3-element array → Interpreted as Euler angles (degrees)</item>
        ///   <item>4-element array → Interpreted as raw quaternion [x, y, z, w]</item>
        ///   <item>Object with euler → Uses euler array for rotation</item>
        ///   <item>Object with x, y, z, w → Uses raw quaternion components</item>
        /// </list>
        /// </summary>
        /// <param name="prop">The SerializedProperty of type Quaternion to set</param>
        /// <param name="valueToken">JSON token containing the quaternion data</param>
        /// <param name="message">Output message describing the result</param>
        /// <returns>True if successful, false if the format is invalid</returns>
        private static bool TrySetQuaternion(SerializedProperty prop, JToken valueToken, out string message)
        {
            message = null;

            if (valueToken == null || valueToken.Type == JTokenType.Null)
            {
                prop.quaternionValue = Quaternion.identity;
                message = "Set Quaternion to identity.";
                return true;
            }

            try
            {
                if (valueToken is JArray arr)
                {
                    if (arr.Count == 3)
                    {
                        // Euler angles [x, y, z]
                        var euler = new Vector3(arr[0].ReadScalar<float>(), arr[1].ReadScalar<float>(), arr[2].ReadScalar<float>());
                        prop.quaternionValue = Quaternion.Euler(euler);
                        message = $"Set Quaternion from Euler({euler.x}, {euler.y}, {euler.z}).";
                        return true;
                    }
                    else if (arr.Count == 4)
                    {
                        // Raw quaternion [x, y, z, w]
                        prop.quaternionValue = new Quaternion(
                            arr[0].ReadScalar<float>(),
                            arr[1].ReadScalar<float>(),
                            arr[2].ReadScalar<float>(),
                            arr[3].ReadScalar<float>()
                        );
                        message = "Set Quaternion from [x, y, z, w].";
                        return true;
                    }
                    else
                    {
                        message = "Quaternion array must have 3 elements (Euler) or 4 elements (x, y, z, w).";
                        return false;
                    }
                }
                else if (valueToken is JObject obj)
                {
                    // Check for explicit euler property
                    if (obj["euler"] is JArray eulerArr && eulerArr.Count == 3)
                    {
                        var euler = new Vector3(eulerArr[0].ReadScalar<float>(), eulerArr[1].ReadScalar<float>(), eulerArr[2].ReadScalar<float>());
                        prop.quaternionValue = Quaternion.Euler(euler);
                        message = $"Set Quaternion from euler: ({euler.x}, {euler.y}, {euler.z}).";
                        return true;
                    }

                    // Object format { x, y, z, w }
                    if (obj["x"] != null && obj["y"] != null && obj["z"] != null && obj["w"] != null)
                    {
                        prop.quaternionValue = new Quaternion(
                            obj["x"].ReadScalar<float>(),
                            obj["y"].ReadScalar<float>(),
                            obj["z"].ReadScalar<float>(),
                            obj["w"].ReadScalar<float>()
                        );
                        message = "Set Quaternion from { x, y, z, w }.";
                        return true;
                    }

                    message = "Quaternion object must have { x, y, z, w } or { euler: [x, y, z] }.";
                    return false;
                }
                else
                {
                    message = "Quaternion requires array [x,y,z] (Euler), [x,y,z,w] (raw), or object { x, y, z, w }.";
                    return false;
                }
            }
            catch (Exception ex)
            {
                message = $"Failed to parse Quaternion: {ex.Message}";
                return false;
            }
        }

        private static bool TryResolveTarget(
            JToken targetToken,
            bool writable,
            out UnityEngine.Object target,
            out string targetPath,
            out string targetGuid,
            out object error
        )
        {
            target = null;
            targetPath = null;
            targetGuid = null;
            error = null;

            if (targetToken is not JObject targetObj)
            {
                error = new ErrorResponse(CodeInvalidParams, new { message = "'target' must be an object with {guid|path}." });
                return false;
            }

            string guid = targetObj["guid"]?.ToString();
            string path = targetObj["path"]?.ToString();

            if (string.IsNullOrWhiteSpace(guid) && string.IsNullOrWhiteSpace(path))
            {
                error = new ErrorResponse(CodeInvalidParams, new { message = "'target' must include 'guid' or 'path'." });
                return false;
            }

            string resolvedPath;
            try
            {
                resolvedPath = !string.IsNullOrWhiteSpace(guid)
                    ? AssetPathUtility.GetAssetPathFromGuid(guid, allowPackages: !writable)
                    : AssetPathUtility.GetAssetReferencePath(path, allowPackages: !writable);
                if (writable && !string.IsNullOrWhiteSpace(resolvedPath))
                    resolvedPath = AssetPathUtility.GetContainedAssetPath(resolvedPath);
            }
            catch (Exception ex)
            {
                error = new ErrorResponse(
                    CodeInvalidParams,
                    new
                    {
                        message = ex.Message,
                        guid,
                        path,
                    }
                );
                return false;
            }

            if (string.IsNullOrWhiteSpace(resolvedPath))
            {
                error = new ErrorResponse(
                    CodeTargetNotFound,
                    new
                    {
                        message = "Could not resolve target path.",
                        guid,
                        path,
                    }
                );
                return false;
            }

            var obj = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(resolvedPath);
            if (obj == null)
            {
                error = new ErrorResponse(
                    CodeTargetNotFound,
                    new
                    {
                        message = "Target asset not found.",
                        targetPath = resolvedPath,
                        targetGuid = guid,
                    }
                );
                return false;
            }

            target = obj;
            targetPath = resolvedPath;
            targetGuid = string.IsNullOrWhiteSpace(guid) ? AssetDatabase.AssetPathToGUID(resolvedPath) : guid;
            return true;
        }

        private static void CoerceJsonStringArrayParameter(JObject @params, string paramName)
        {
            var token = @params?[paramName];
            if (token != null && token.Type == JTokenType.String)
            {
                try
                {
                    var parsed = JToken.Parse(token.ToString());
                    if (parsed is JArray arr)
                    {
                        @params[paramName] = arr;
                    }
                }
                catch (Exception e)
                {
                    McpLog.Warn($"[MCP] Could not parse '{paramName}' JSON string: {e.Message}");
                }
            }
        }

        private static string SanitizeSlashes(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return path;
            }

            var s = AssetPathUtility.NormalizeSeparators(path);
            while (s.IndexOf("//", StringComparison.Ordinal) >= 0)
            {
                s = s.Replace("//", "/", StringComparison.Ordinal);
            }
            return s;
        }

        private static bool TryNormalizeFolderPath(string folderPath, out string normalized, out string error)
        {
            normalized = null;
            error = null;

            if (string.IsNullOrWhiteSpace(folderPath))
            {
                error = "Folder path is empty.";
                return false;
            }

            var s = SanitizeSlashes(folderPath.Trim());

            // Reject obvious non-project/invalid roots. We only support Assets/ (and relative paths that will be rooted under Assets/).
            if (s.StartsWith("/", StringComparison.Ordinal) || s.StartsWith("file:", StringComparison.OrdinalIgnoreCase) || Regex.IsMatch(s, @"^[a-zA-Z]:"))
            {
                error = "Folder path must be a project-relative path under Assets/.";
                return false;
            }

            if (
                s.StartsWith("Packages/", StringComparison.OrdinalIgnoreCase)
                || s.StartsWith("ProjectSettings/", StringComparison.OrdinalIgnoreCase)
                || s.StartsWith("Library/", StringComparison.OrdinalIgnoreCase)
            )
            {
                error = "Folder path must be under Assets/.";
                return false;
            }

            if (string.Equals(s, "Assets", StringComparison.OrdinalIgnoreCase))
            {
                normalized = "Assets";
                return true;
            }

            if (s.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                normalized = s.TrimEnd('/');
                return true;
            }

            // Allow relative paths like "Temp/MyFolder" and root them under Assets/.
            normalized = ("Assets/" + s.TrimStart('/')).TrimEnd('/');
            return true;
        }

        // NOTE: Local TryGet* helpers have been removed.
        // Using shared helpers instead: ParamCoercion (for int/float/bool) and VectorParsing (for Vector2/3/4, Color)

        private static string NormalizeAction(string raw)
        {
            var s = raw.Trim();
            s = s.Replace("-", "").Replace("_", "");
            return s.ToLowerInvariant();
        }

        private static bool IsCreateAction(string normalized)
        {
            return normalized == "create" || normalized == "createso";
        }

        /// <summary>
        /// Resolves a type by name. Delegates to UnityTypeResolver.ResolveAny().
        /// </summary>
        private static Type ResolveType(string typeName)
        {
            return Helpers.UnityTypeResolver.ResolveAny(typeName);
        }
    }
}
