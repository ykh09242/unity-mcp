using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using System.Threading;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
#if USE_ROSLYN
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
#endif

#if UNITY_EDITOR
using UnityEditor.Compilation;
#endif

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Handles CRUD operations for C# scripts within the Unity project.
    /// </summary>
    /// <remarks>
    /// ROSLYN INSTALLATION GUIDE:
    /// To enable advanced syntax validation with Roslyn compiler services:
    ///
    /// 1. Install Microsoft.CodeAnalysis.CSharp NuGet package:
    ///    - Open Package Manager in Unity
    ///    - Follow the instruction on https://github.com/GlitchEnzo/NuGetForUnity
    ///
    /// 2. Open NuGet Package Manager and Install Microsoft.CodeAnalysis.CSharp:
    ///
    /// 3. Alternative: Manual DLL installation:
    ///    - Download Microsoft.CodeAnalysis.CSharp.dll and dependencies
    ///    - Place in Assets/Plugins/ folder
    ///    - Ensure .NET compatibility settings are correct
    ///
    /// 4. Define USE_ROSLYN symbol:
    ///    - Go to Player Settings > Scripting Define Symbols
    ///    - Add "USE_ROSLYN" to enable Roslyn-based validation
    ///
    /// 5. Restart Unity after installation
    ///
    /// Note: Without Roslyn, the system falls back to basic structural validation.
    /// Roslyn provides full C# compiler diagnostics with line numbers and detailed error messages.
    /// </remarks>
    [McpForUnityTool("manage_script", AutoRegister = false, RequiresExplicitConsent = true)]
    public static class ManageScript
    {
        // Private, default-preserving seams; no command payload can set these.
        private static Func<bool> _explicitConsentForTests = null;
        private static Func<bool> _parentEnabledForTests = null;
        private static Action<string, bool> _preparedRefreshForTests = null;
        private static Action<string> _preparedCommitStageForTests = null;

        internal static ErrorResponse RequireExplicitConsent()
        {
            if (
                !(_explicitConsentForTests?.Invoke() ?? MCPServiceLocator.ToolConsent.GetStatus("manage_script").HasGrant)
                || !(_parentEnabledForTests?.Invoke() ?? MCPServiceLocator.ToolDiscovery.IsToolEnabled("manage_script"))
            )
                return new ErrorResponse(
                    "script_consent_required",
                    "Enable manage_script explicitly in the Unity Editor before using script or compilation-affecting asset operations."
                );
            return null;
        }

        private static readonly System.Text.UTF8Encoding StrictFileUtf8 = new System.Text.UTF8Encoding(false, true);

        /// <summary>
        /// Resolves a directory under Assets/, preventing traversal and escaping.
        /// Returns fullPathDir on disk and canonical 'Assets/...' relative path.
        /// </summary>
        private static bool TryResolveUnderAssets(string relDir, out string fullPathDir, out string relPathSafe)
        {
            fullPathDir = null;
            relPathSafe = null;
            try
            {
                string rel = string.IsNullOrWhiteSpace(relDir) ? "Scripts" : relDir.Trim().TrimEnd('/', '\\');
                relPathSafe = AssetPathUtility.GetContainedAssetPath(rel);
                fullPathDir = AssetPathUtility.GetFullAssetPath(relPathSafe);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>
        /// Main handler for script management actions.
        /// </summary>
        public static object HandleCommand(JObject @params)
        {
            // Handle null parameters
            if (@params == null)
            {
                return new ErrorResponse("invalid_params", "Parameters cannot be null.");
            }

            var consentError = RequireExplicitConsent();
            if (consentError != null)
            {
                string requestedAction = @params["action"]?.Type == JTokenType.String ? @params.Value<string>("action") : null;
                if (
                    string.Equals(requestedAction, "preview_script_buffer", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(requestedAction, "apply_prepared_script", StringComparison.OrdinalIgnoreCase)
                )
                    return OrderedRefusal("script_consent_required", "Enable manage_script and its explicit consent before ordered editing.");
                return consentError;
            }

            var p = new ToolParams(@params);

            // Extract and validate required parameters
            var actionResult = p.GetRequired("action");
            if (!actionResult.IsSuccess)
            {
                return new ErrorResponse(actionResult.ErrorMessage);
            }
            string action = actionResult.Value.ToLowerInvariant();

            if (action == "prepare_change_set" || action == "apply_change_set" || action == "get_change_set" || action == "recover_change_set")
                return ScriptChangeSet.HandleCommand(@params);

            var nameResult = p.GetRequired("name");
            if (!nameResult.IsSuccess)
            {
                return new ErrorResponse(nameResult.ErrorMessage);
            }
            string name = nameResult.Value;

            // Optional parameters
            string path = p.Get("path"); // Relative to Assets/
            // If the caller passed a full file path (e.g. "Assets/Scripts/Foo.cs"),
            // strip the filename so path is treated as a directory.
            if (path != null && path.EndsWith(".cs", StringComparison.OrdinalIgnoreCase))
            {
                path = Path.GetDirectoryName(path)?.Replace('\\', '/');
            }
            string contents = null;

            // Check if we have base64 encoded contents
            bool contentsEncoded = p.GetBool("contentsEncoded", false);
            if (contentsEncoded)
            {
                try
                {
                    var encodedContents = p.GetRaw("encodedContents");
                    if (encodedContents == null || encodedContents.Type != JTokenType.String)
                        return new ErrorResponse("invalid_contents", "contentsEncoded requires a string encodedContents payload.");
                    contents = DecodeBase64(encodedContents.Value<string>());
                }
                catch (Exception e)
                {
                    return new ErrorResponse($"Failed to decode script contents: {e.Message}");
                }
            }
            else
            {
                contents = p.Get("contents");
            }

            string scriptType = p.Get("scriptType"); // For templates/validation
            string namespaceName = p.Get("namespace"); // For organizing code
            // Basic name validation (alphanumeric, underscores, cannot start with number)
            if (!Regex.IsMatch(name, @"^[a-zA-Z_][a-zA-Z0-9_]*$", RegexOptions.CultureInvariant, TimeSpan.FromSeconds(2)))
            {
                return new ErrorResponse($"Invalid script name: '{name}'. Use only letters, numbers, underscores, and don't start with a number.");
            }

            // Resolve and harden target directory under Assets/
            if (!TryResolveUnderAssets(path, out string fullPathDir, out string relPathSafeDir))
            {
                return new ErrorResponse($"Invalid path. Target directory must be within 'Assets/'. Provided: '{(path ?? "(null)")}'");
            }

            // Construct file paths
            string scriptFileName = $"{name}.cs";
            string relativePath = AssetPathUtility.NormalizeSeparators(Path.Combine(relPathSafeDir, scriptFileName));
            string fullPath;
            try
            {
                fullPath = AssetPathUtility.GetFullAssetPath(relativePath);
            }
            catch (Exception)
            {
                return new ErrorResponse("Unsafe script path: linked or unreadable paths are not permitted.");
            }

            // Route to specific action handlers
            switch (action)
            {
                case "preview_script_buffer":
                    return PreviewScriptBuffer(fullPath, relativePath, name, @params);
                case "apply_prepared_script":
                    return ApplyPreparedScript(fullPath, relativePath, @params);
                case "create":
                    return CreateScript(fullPath, relativePath, name, contents, scriptType, namespaceName);
                case "read":
                    McpLog.Warn("manage_script.read is deprecated; prefer resources/read. Serving read for backward compatibility.");
                    return ReadScript(fullPath, relativePath);
                case "update":
                    McpLog.Warn("manage_script.update is deprecated; prefer apply_text_edits. Serving update for backward compatibility.");
                    return UpdateScript(fullPath, relativePath, name, contents);
                case "delete":
                    return DeleteScript(fullPath, relativePath);
                case "apply_text_edits":
                case "preview_text_edits":
                {
                    var textEdits = p.GetRaw("edits") as JArray;
                    string precondition = p.Get("precondition_sha256");
                    var optionsError = ValidateScriptOptions(p.GetRaw("options"), out var optionsObj);
                    if (optionsError != null)
                        return optionsError;
                    string refreshOpt = optionsObj?["refresh"]?.ToString()?.ToLowerInvariant();
                    string validateOpt = optionsObj?["validate"]?.ToString()?.ToLowerInvariant();
                    bool preview = action == "preview_text_edits" || optionsObj?.ReadScalar<bool?>("preview") == true;
                    return ApplyTextEdits(fullPath, relativePath, name, textEdits, precondition, refreshOpt, validateOpt, preview);
                }
                case "validate":
                {
                    string level = p.Get("level", "standard").ToLowerInvariant();
                    var chosen = level switch
                    {
                        "basic" => ValidationLevel.Basic,
                        "standard" => ValidationLevel.Standard,
                        "strict" => ValidationLevel.Strict,
                        "comprehensive" => ValidationLevel.Comprehensive,
                        _ => ValidationLevel.Standard,
                    };
                    string fileText;
                    try
                    {
                        fileText = ReadScriptContents(fullPath);
                    }
                    catch (Exception ex)
                    {
                        return new ErrorResponse($"Failed to read script: {ex.Message}");
                    }

                    var context = CreateValidationContext(relativePath, chosen);
                    bool ok = ValidateScriptSyntax(fileText, chosen, out string[] diagsRaw, context);
                    var diags = (diagsRaw ?? Array.Empty<string>()).Select(s => ValidationDiagnostic(s, context)).ToArray();

                    var result = new { diagnostics = diags, validation = ValidationMetadata(level, chosen, context) };
                    return ok ? new SuccessResponse("Validation completed.", result) : new ErrorResponse("Validation failed.", result);
                }
                case "edit":
                case "preview_edit":
                {
                    McpLog.Warn("manage_script.edit is deprecated; prefer apply_text_edits. Serving structured edit for backward compatibility.");
                    var structEdits = @params["edits"] as JArray;
                    var optionsError = ValidateScriptOptions(p.GetRaw("options"), out var options);
                    if (optionsError != null)
                        return optionsError;
                    return EditScript(fullPath, relativePath, name, structEdits, options, action == "preview_edit");
                }
                // Internal mixed-edit preflight: inspect input without reading or matching the file.
                case "validate_edit":
                    var editError = ValidateStructuredEdits(@params["edits"] as JArray);
                    return (object)editError ?? new SuccessResponse("Structured edit input is valid.");
                case "get_sha":
                {
                    try
                    {
                        if (!File.Exists(fullPath))
                            return new ErrorResponse($"Script not found at '{relativePath}'.");

                        string text = ReadScriptContents(fullPath);
                        string sha = ComputeSha256(text);
                        var fi = new FileInfo(fullPath);
                        long lengthBytes;
                        try
                        {
                            lengthBytes = new System.Text.UTF8Encoding(encoderShouldEmitUTF8Identifier: false).GetByteCount(text);
                        }
                        catch
                        {
                            lengthBytes = fi.Exists ? fi.Length : 0;
                        }
                        var data = new
                        {
                            uri = $"mcpforunity://path/{relativePath}",
                            path = relativePath,
                            sha256 = sha,
                            lengthBytes,
                            lastModifiedUtc = fi.Exists ? fi.LastWriteTimeUtc.ToString("o") : string.Empty,
                        };
                        return new SuccessResponse($"SHA computed for '{relativePath}'.", data);
                    }
                    catch (Exception ex)
                    {
                        return new ErrorResponse($"Failed to compute SHA: {ex.Message}");
                    }
                }
                default:
                    return new ErrorResponse(
                        $"Unknown action: '{action}'. Valid actions are: create, delete, apply_text_edits, validate, read (deprecated), update (deprecated), edit (deprecated)."
                    );
            }
        }

        private static ErrorResponse ValidateScriptOptions(JToken token, out JObject options)
        {
            options = token as JObject;
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (options == null)
                return new ErrorResponse("invalid_options", "Script options must be an object or null.");
            foreach (string selector in new[] { "refresh", "validate", "applyMode", "apply_mode" })
            {
                var value = options[selector];
                if (value != null && value.Type != JTokenType.Null && value.Type != JTokenType.String)
                    return new ErrorResponse("invalid_options", $"Script option '{selector}' must be a string or null.");
            }
            try
            {
                _ = options.ReadScalar<bool?>("preview");
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse("invalid_options", ex.Message);
            }
            return null;
        }

        /// <summary>
        /// Decode base64 string to normal text
        /// </summary>
        private static string DecodeBase64(string encoded)
        {
            byte[] data = Convert.FromBase64String(encoded);
            return StrictFileUtf8.GetString(data);
        }

        /// <summary>
        /// Encode text to base64 string
        /// </summary>
        private static string EncodeBase64(string text)
        {
            byte[] data = StrictFileUtf8.GetBytes(text);
            return Convert.ToBase64String(data);
        }

        private static object CreateScript(string fullPath, string relativePath, string name, string contents, string scriptType, string namespaceName)
        {
            // Check if script already exists
            if (File.Exists(fullPath))
            {
                return new ErrorResponse($"Script already exists at '{relativePath}'. Use 'update' action to modify.");
            }

            // Generate default content if none provided
            if (string.IsNullOrEmpty(contents))
            {
                contents = GenerateDefaultScriptContent(name, scriptType, namespaceName);
            }

            try
            {
                _ = StrictFileUtf8.GetByteCount(contents);
            }
            catch (System.Text.EncoderFallbackException ex)
            {
                return new ErrorResponse("invalid_contents", $"Script contents must contain valid Unicode: {ex.Message}");
            }

            // Validate syntax with detailed error reporting using GUI setting
            ValidationLevel validationLevel = GetValidationLevelFromGUI();
            bool isValid = ValidateScriptSyntax(contents, validationLevel, out string[] validationErrors);
            if (!isValid)
            {
                return new ErrorResponse("validation_failed", new { status = "validation_failed", diagnostics = validationErrors ?? Array.Empty<string>() });
            }
            else if (validationErrors != null && validationErrors.Length > 0)
            {
                // Log warnings but don't block creation
                McpLog.Warn($"Script validation warnings for {name}:\n" + string.Join("\n", validationErrors));
            }

            try
            {
                using var folders = new AssetFolderScope();
                folders.EnsureParentDirectory(relativePath);
                fullPath = AssetPathUtility.GetFullAssetPath(relativePath);
                WriteScriptFile(fullPath, contents, overwrite: false);

                var uri = $"mcpforunity://path/{relativePath}";
                var ok = new SuccessResponse($"Script '{name}.cs' created successfully at '{relativePath}'.", new { uri, scheduledRefresh = false });

                ManageScriptRefreshHelpers.ImportAndRequestCompile(relativePath);
                folders.Complete();
                return ok;
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to create script '{relativePath}': {e.Message}");
            }
        }

        private static void WriteScriptFile(string fullPath, string contents, bool overwrite)
        {
            byte[] bytes = StrictFileUtf8.GetBytes(contents ?? string.Empty);
            string attempt = Guid.NewGuid().ToString("N");
            string tempPath = fullPath + "." + attempt + ".tmp";
            string backupPath = fullPath + "." + attempt + ".bak";
            bool ownsTemp = false;
            bool writeCompleted = false;
            try
            {
                using (var stream = File.Open(tempPath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    ownsTemp = true;
                    stream.Write(bytes, 0, bytes.Length);
                }

                if (!overwrite)
                {
                    // A destination created since validation belongs to another writer.
                    File.Move(tempPath, fullPath);
                }
                else
                {
                    try
                    {
                        File.Replace(tempPath, fullPath, backupPath);
                    }
                    catch (PlatformNotSupportedException)
                    {
                        File.Copy(tempPath, fullPath, true);
                    }
                    catch (IOException)
                    {
                        File.Copy(tempPath, fullPath, true);
                    }
                }
                writeCompleted = true;
            }
            finally
            {
                if (ownsTemp)
                {
                    try
                    {
                        if (File.Exists(tempPath))
                            File.Delete(tempPath);
                    }
                    catch { }
                }
                // Keep this attempt's recovery backup if replacement/fallback failed.
                if (writeCompleted)
                {
                    try
                    {
                        if (File.Exists(backupPath))
                            File.Delete(backupPath);
                    }
                    catch { }
                }
            }
        }

        private static object ReadScript(string fullPath, string relativePath)
        {
            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"Script not found at '{relativePath}'.");
            }

            try
            {
                string contents = ReadScriptContents(fullPath);

                // Return both normal and encoded contents for larger files
                bool isLarge = contents.Length > 10000; // If content is large, include encoded version
                var uri = $"mcpforunity://path/{relativePath}";
                var responseData = new
                {
                    uri,
                    path = relativePath,
                    contents = contents,
                    // For large files, also include base64-encoded version
                    encodedContents = isLarge ? EncodeBase64(contents) : null,
                    contentsEncoded = isLarge,
                };

                return new SuccessResponse($"Script '{Path.GetFileName(relativePath)}' read successfully.", responseData);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to read script '{relativePath}': {e.Message}");
            }
        }

        private static string ReadScriptContents(string fullPath)
        {
            byte[] bytes = File.ReadAllBytes(fullPath);
            System.Text.Encoding encoding = StrictFileUtf8;
            int offset = 0;
            // Keep ReadAllText's Unicode BOM detection while rejecting malformed bytes.
            // UTF32 LE precedes UTF16 LE because their BOM prefixes overlap.
            if (bytes.Length >= 4 && bytes[0] == 0xff && bytes[1] == 0xfe && bytes[2] == 0 && bytes[3] == 0)
            {
                encoding = new System.Text.UTF32Encoding(false, false, true);
                offset = 4;
            }
            else if (bytes.Length >= 4 && bytes[0] == 0 && bytes[1] == 0 && bytes[2] == 0xfe && bytes[3] == 0xff)
            {
                encoding = new System.Text.UTF32Encoding(true, false, true);
                offset = 4;
            }
            else if (bytes.Length >= 3 && bytes[0] == 0xef && bytes[1] == 0xbb && bytes[2] == 0xbf)
                offset = 3;
            else if (bytes.Length >= 2 && bytes[0] == 0xff && bytes[1] == 0xfe)
            {
                encoding = new System.Text.UnicodeEncoding(false, false, true);
                offset = 2;
            }
            else if (bytes.Length >= 2 && bytes[0] == 0xfe && bytes[1] == 0xff)
            {
                encoding = new System.Text.UnicodeEncoding(true, false, true);
                offset = 2;
            }
            return encoding.GetString(bytes, offset, bytes.Length - offset);
        }

        private static object UpdateScript(string fullPath, string relativePath, string name, string contents)
        {
            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"Script not found at '{relativePath}'. Use 'create' action to add a new script.");
            }
            if (string.IsNullOrEmpty(contents))
            {
                return new ErrorResponse("Content is required for the 'update' action.");
            }

            // Validate syntax with detailed error reporting using GUI setting
            ValidationLevel validationLevel = GetValidationLevelFromGUI();
            bool isValid = ValidateScriptSyntax(contents, validationLevel, out string[] validationErrors);
            if (!isValid)
            {
                return new ErrorResponse("validation_failed", new { status = "validation_failed", diagnostics = validationErrors ?? Array.Empty<string>() });
            }
            else if (validationErrors != null && validationErrors.Length > 0)
            {
                // Log warnings but don't block update
                McpLog.Warn($"Script validation warnings for {name}:\n" + string.Join("\n", validationErrors));
            }

            try
            {
                WriteScriptFile(fullPath, contents, overwrite: true);

                // Prepare success response BEFORE any operation that can trigger a domain reload
                var uri = $"mcpforunity://path/{relativePath}";
                var ok = new SuccessResponse(
                    $"Script '{name}.cs' updated successfully at '{relativePath}'.",
                    new
                    {
                        uri,
                        path = relativePath,
                        scheduledRefresh = true,
                    }
                );

                // Schedule a debounced import/compile on next editor tick to avoid stalling the reply
                ManageScriptRefreshHelpers.ScheduleScriptRefresh(relativePath);

                return ok;
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to update script '{relativePath}': {e.Message}");
            }
        }

        /// <summary>
        /// Apply simple text edits specified by line/column ranges. Applies transactionally and validates result.
        /// </summary>
        private const int MaxEditPayloadBytes = 64 * 1024;
        private const int MaxPreviewTextBytes = 1024 * 1024;

        private static object ApplyTextEdits(
            string fullPath,
            string relativePath,
            string name,
            JArray edits,
            string preconditionSha256,
            string refreshModeFromCaller = null,
            string validateMode = null,
            bool preview = false,
            string sourceBuffer = null,
            bool computeOnly = false
        )
        {
            if (!File.Exists(fullPath))
                return new ErrorResponse($"Script not found at '{relativePath}'.");
            try
            {
                fullPath = AssetPathUtility.GetFullAssetPath(relativePath);
            }
            catch (Exception)
            {
                return new ErrorResponse("Unsafe script path: linked or unreadable paths are not permitted.");
            }
            if (edits == null || edits.Count == 0)
                return new ErrorResponse("No edits provided.");

            string original;
            try
            {
                original = computeOnly ? sourceBuffer : ReadScriptContents(fullPath);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Failed to read script: {ex.Message}");
            }

            // Require precondition to avoid drift on large files
            string currentSha = ComputeSha256(original);
            if (string.IsNullOrEmpty(preconditionSha256))
                return new ErrorResponse("precondition_required", new { status = "precondition_required", current_sha256 = currentSha });
            if (!preconditionSha256.Equals(currentSha, StringComparison.OrdinalIgnoreCase))
                return new ErrorResponse(
                    "stale_file",
                    new
                    {
                        status = "stale_file",
                        expected_sha256 = preconditionSha256,
                        current_sha256 = currentSha,
                    }
                );

            // Convert edits to absolute index ranges
            var spans = new List<(int start, int end, string text)>();
            long totalBytes = 0;
            foreach (var e in edits)
            {
                try
                {
                    if (
                        !(e is JObject edit)
                        || new[] { "startLine", "startCol", "endLine", "endCol", "newText" }.Any(field =>
                            edit[field] == null || edit[field].Type == JTokenType.Null
                        )
                        || edit["newText"].Type != JTokenType.String
                    )
                        return new ErrorResponse("Invalid edit payload: requires startLine/startCol/endLine/endCol and a string newText (empty for deletion).");

                    int sl = Math.Max(1, e.ReadScalar<int>("startLine"));
                    int sc = Math.Max(1, e.ReadScalar<int>("startCol"));
                    int el = Math.Max(1, e.ReadScalar<int>("endLine"));
                    int ec = Math.Max(1, e.ReadScalar<int>("endCol"));
                    string newText = e.Value<string>("newText") ?? string.Empty;

                    if (!TryIndexFromLineCol(original, sl, sc, out int sidx))
                        return new ErrorResponse($"apply_text_edits: start out of range (line {sl}, col {sc})");
                    if (!TryIndexFromLineCol(original, el, ec, out int eidx))
                        return new ErrorResponse($"apply_text_edits: end out of range (line {el}, col {ec})");
                    if (eidx < sidx)
                        (sidx, eidx) = (eidx, sidx);

                    spans.Add((sidx, eidx, newText));
                    checked
                    {
                        totalBytes += StrictFileUtf8.GetByteCount(newText);
                    }
                }
                catch (Exception ex)
                {
                    return new ErrorResponse($"Invalid edit payload: {ex.Message}");
                }
            }

            // Header guard: refuse edits that touch before the first 'using ' directive (after optional BOM) to prevent file corruption
            int headerBoundary = (original.Length > 0 && original[0] == '\uFEFF') ? 1 : 0; // skip BOM once if present
            // Find first top-level using (supports alias, static, and dotted namespaces)
            var mUsing = System.Text.RegularExpressions.Regex.Match(
                original,
                @"(?m)^\s*using\s+(?:static\s+)?(?:[A-Za-z_]\w*\s*=\s*)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*;",
                System.Text.RegularExpressions.RegexOptions.CultureInvariant,
                TimeSpan.FromSeconds(2)
            );
            if (mUsing.Success)
            {
                headerBoundary = Math.Min(Math.Max(headerBoundary, mUsing.Index), original.Length);
            }
            foreach (var sp in spans)
            {
                if (sp.start < headerBoundary)
                {
                    return new ErrorResponse(
                        "using_guard",
                        new
                        {
                            status = "using_guard",
                            hint = "Refusing to edit before the first 'using'. Use anchor_insert near a method or a structured edit.",
                        }
                    );
                }
            }

            if (totalBytes > MaxEditPayloadBytes)
            {
                return new ErrorResponse(
                    "too_large",
                    new
                    {
                        status = "too_large",
                        limitBytes = MaxEditPayloadBytes,
                        hint = "split into smaller edits",
                    }
                );
            }

            // Ensure non-overlap and apply from back to front
            spans = spans.OrderByDescending(t => t.start).ToList();
            for (int i = 1; i < spans.Count; i++)
            {
                if (spans[i].end > spans[i - 1].start)
                {
                    var conflict = new[]
                    {
                        new
                        {
                            startA = spans[i].start,
                            endA = spans[i].end,
                            startB = spans[i - 1].start,
                            endB = spans[i - 1].end,
                        },
                    };
                    return new ErrorResponse(
                        "overlap",
                        new
                        {
                            status = "overlap",
                            conflicts = conflict,
                            hint = "Sort ranges descending by start and compute from the same snapshot.",
                        }
                    );
                }
            }

            string working = original;
            bool syntaxOnly = string.Equals(validateMode, "syntax", StringComparison.OrdinalIgnoreCase);
            foreach (var sp in spans)
            {
                working = working.Remove(sp.start, sp.end - sp.start).Insert(sp.start, sp.text ?? string.Empty);
            }
            if (computeOnly)
                return new SuccessResponse("Computed literal buffer without validation or writing.", new { new_contents = working });

            // No-op guard: if resulting text is identical, avoid writes and return explicit no-op
            if (string.Equals(working, original, StringComparison.Ordinal))
            {
                if (preview)
                    return ScriptPreviewResponse(fullPath, relativePath, original, working, 0);
                string noChangeSha = ComputeSha256(original);
                return new SuccessResponse(
                    $"No-op: contents unchanged for '{relativePath}'.",
                    new
                    {
                        uri = $"mcpforunity://path/{relativePath}",
                        path = relativePath,
                        editsApplied = 0,
                        no_op = true,
                        sha256 = noChangeSha,
                        evidence = new { reason = "identical_content" },
                    }
                );
            }

            // Always check final structural balance
            if (!CheckBalancedDelimiters(working, out int line, out char expected))
            {
                int startLine = Math.Max(1, line - 5);
                int endLine = line + 5;
                string hint =
                    $"unbalanced_braces at line {line}. Call resources/read for lines {startLine}-{endLine} and resend a smaller apply_text_edits that restores balance.";
                return new ErrorResponse(
                    hint,
                    new
                    {
                        status = "unbalanced_braces",
                        line,
                        expected = expected.ToString(),
                        evidenceWindow = new { startLine, endLine },
                    }
                );
            }

#if USE_ROSLYN
            if (!syntaxOnly)
            {
                var tree = CSharpSyntaxTree.ParseText(working);
                var diagnostics = tree.GetDiagnostics()
                    .Where(d => d.Severity == DiagnosticSeverity.Error)
                    .Take(3)
                    .Select(d => new
                    {
                        line = d.Location.GetLineSpan().StartLinePosition.Line + 1,
                        col = d.Location.GetLineSpan().StartLinePosition.Character + 1,
                        code = d.Id,
                        message = d.GetMessage(),
                    })
                    .ToArray();
                if (diagnostics.Length > 0)
                {
                    int firstLine = diagnostics[0].line;
                    int startLineRos = Math.Max(1, firstLine - 5);
                    int endLineRos = firstLine + 5;
                    return new ErrorResponse(
                        "syntax_error",
                        new
                        {
                            status = "syntax_error",
                            diagnostics,
                            evidenceWindow = new { startLine = startLineRos, endLine = endLineRos },
                        }
                    );
                }
            }
#endif

            string newSha = ComputeSha256(working);

            // Atomic write and schedule refresh
            if (preview)
                return ScriptPreviewResponse(fullPath, relativePath, original, working, spans.Count);
            try
            {
                WriteScriptFile(fullPath, working, overwrite: true);

                // Respect refresh mode: immediate vs debounced
                bool immediate =
                    string.Equals(refreshModeFromCaller, "immediate", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(refreshModeFromCaller, "sync", StringComparison.OrdinalIgnoreCase);
                if (immediate)
                {
                    McpLog.Info($"[ManageScript] ApplyTextEdits: immediate refresh for '{relativePath}'");
                    AssetDatabase.ImportAsset(relativePath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
#if UNITY_EDITOR
                    UnityEditor.Compilation.CompilationPipeline.RequestScriptCompilation();
#endif
                }
                else
                {
                    McpLog.Info($"[ManageScript] ApplyTextEdits: debounced refresh scheduled for '{relativePath}'");
                    ManageScriptRefreshHelpers.ScheduleScriptRefresh(relativePath);
                }

                return new SuccessResponse(
                    $"Applied {spans.Count} text edit(s) to '{relativePath}'.",
                    new
                    {
                        uri = $"mcpforunity://path/{relativePath}",
                        path = relativePath,
                        editsApplied = spans.Count,
                        sha256 = newSha,
                        scheduledRefresh = !immediate,
                    }
                );
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Failed to write edits: {ex.Message}");
            }
        }

        private static object ScriptPreviewResponse(string fullPath, string relativePath, string original, string candidate, int preparedCount)
        {
            long textBytes = (long)StrictFileUtf8.GetByteCount(original) + StrictFileUtf8.GetByteCount(candidate);
            if (textBytes > MaxPreviewTextBytes)
                return new ErrorResponse(
                    "too_large",
                    new
                    {
                        status = "too_large",
                        preview = true,
                        limitBytes = MaxPreviewTextBytes,
                        hint = "Inline preparation is limited to 1 MiB of combined original and candidate UTF-8 text.",
                    }
                );

            bool noOp = string.Equals(original, candidate, StringComparison.Ordinal);
            string originalSha = ComputeSha256(original);
            string candidateSha = ComputeSha256(candidate);
            return new SuccessResponse(
                noOp ? $"Preview no-op for '{relativePath}'." : $"Prepared {preparedCount} edit(s) for '{relativePath}' without writing.",
                new
                {
                    path = relativePath,
                    uri = $"mcpforunity://path/{relativePath}",
                    absolute_path = Path.GetFullPath(fullPath),
                    project_root = Path.GetDirectoryName(Path.GetFullPath(Application.dataPath)),
                    preview = true,
                    no_op = noOp,
                    editsApplied = 0,
                    editsPrepared = noOp ? 0 : preparedCount,
                    scheduledRefresh = false,
                    complete = true,
                    truncated = false,
                    original_contents = original,
                    new_contents = candidate,
                    original_sha256 = originalSha,
                    sha256 = originalSha,
                    candidate_sha256 = candidateSha,
                    // Direct writes use UTF-8 without BOM, so these declared bytes share
                    // the logical candidate hash. Original hashes exclude any input BOM.
                    candidate_bytes_sha256 = candidateSha,
                    encoding = "utf-8",
                    bom = false,
                }
            );
        }

        private sealed class RawScriptSnapshot
        {
            public readonly string Text;
            public readonly bool Bom;
            public readonly string RawSha;
            public readonly int ByteCount;

            public RawScriptSnapshot(byte[] bytes)
            {
                Bom = bytes.Length >= 3 && bytes[0] == 0xef && bytes[1] == 0xbb && bytes[2] == 0xbf;
                Text = StrictFileUtf8.GetString(bytes, Bom ? 3 : 0, bytes.Length - (Bom ? 3 : 0));
                if (Text.IndexOf('\0') >= 0)
                    throw new ArgumentException("UTF-16/32 and NUL-containing sources are not supported by ordered editing.");
                RawSha = RawSha256(bytes);
                ByteCount = bytes.Length;
            }
        }

        private static string RawSha256(byte[] bytes)
        {
            using var algorithm = SHA256.Create();
            return BitConverter.ToString(algorithm.ComputeHash(bytes)).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static byte[] OrderedBytes(string text, bool bom)
        {
            int count = StrictFileUtf8.GetByteCount(text);
            if (count > MaxPreviewTextBytes - (bom ? 3 : 0))
                throw new ArgumentException("Ordered script buffer exceeds the byte limit.");
            var bytes = new byte[count + (bom ? 3 : 0)];
            if (bom)
            {
                bytes[0] = 0xef;
                bytes[1] = 0xbb;
                bytes[2] = 0xbf;
            }
            StrictFileUtf8.GetBytes(text, 0, text.Length, bytes, bom ? 3 : 0);
            return bytes;
        }

        private static RawScriptSnapshot ReadOrderedSnapshot(string relativePath)
        {
            string fullPath = AssetPathUtility.GetFullAssetPath(relativePath);
            using var stream = File.Open(fullPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (stream.Length > MaxPreviewTextBytes)
                throw new ArgumentException("Ordered script file exceeds the byte limit.");
            var bytes = new byte[(int)stream.Length];
            int read = 0;
            while (read < bytes.Length)
            {
                int amount = stream.Read(bytes, read, bytes.Length - read);
                if (amount == 0)
                    throw new IOException("The script changed during its bounded read.");
                read += amount;
            }
            if (stream.ReadByte() != -1)
                throw new IOException("The script grew during its bounded read.");
            return new RawScriptSnapshot(bytes);
        }

        private static string OrderedString(JObject parameters, string field)
        {
            if (parameters[field]?.Type != JTokenType.String)
                throw new ArgumentException($"Ordered '{field}' must be a string.");
            return parameters.Value<string>(field);
        }

        private static bool OrderedBoolean(JObject parameters, string field)
        {
            if (parameters[field]?.Type != JTokenType.Boolean)
                throw new ArgumentException($"Ordered '{field}' must be a boolean.");
            return parameters.Value<bool>(field);
        }

        private static string OrderedDigest(JObject parameters, string field)
        {
            string digest = OrderedString(parameters, field);
            if (digest.Length != 64 || digest.Any(c => !Uri.IsHexDigit(c)))
                throw new ArgumentException($"Ordered '{field}' must be a 64-hex SHA256 digest.");
            return digest.ToLowerInvariant();
        }

        private static JObject OrderedOptions(JObject parameters)
        {
            _ = OrderedString(parameters, "action");
            _ = OrderedString(parameters, "name");
            _ = OrderedString(parameters, "path");
            var token = parameters["options"];
            if (token != null && token.Type != JTokenType.Object)
                throw new ArgumentException("Ordered options must be an object when supplied.");
            var options = token as JObject ?? new JObject();
            foreach (string field in new[] { "validate", "refresh", "applyMode", "apply_mode", "mixed_order" })
                if (options[field] != null && options[field].Type != JTokenType.String)
                    throw new ArgumentException($"Ordered option '{field}' must be a string.");
            if (options["preview"] != null && options["preview"].Type != JTokenType.Boolean)
                throw new ArgumentException("Ordered preview must be a boolean.");
            foreach (string field in new[] { "applyMode", "apply_mode" })
                if (options[field] != null && options.Value<string>(field) != "sequential")
                    throw new ArgumentException("Ordered interpretation requires sequential apply mode.");
            return options;
        }

        private static object OrderedRefusal(string code, string message)
        {
            return new
            {
                success = false,
                code,
                error = message,
                message,
                data = new { applied = false, outcome = "not_applied" },
            };
        }

        private static ErrorResponse ValidateOrderedFinal(string contents, JObject options)
        {
            string requested = (options.Value<string>("validate") ?? "standard").ToLowerInvariant();
            var level = requested switch
            {
                "basic" => ValidationLevel.Basic,
                "syntax" => ValidationLevel.Basic,
                "standard" => ValidationLevel.Standard,
                "comprehensive" => ValidationLevel.Comprehensive,
                "strict" => ValidationLevel.Strict,
                _ => throw new ArgumentException("Unknown ordered validation level."),
            };
            if (!CheckBalancedDelimiters(contents, out int line, out char expected))
                return new ErrorResponse("validation_failed", new { line, expected = expected.ToString() });
            if (!ValidateScriptSyntax(contents, level, out var errors))
                return new ErrorResponse("validation_failed", new { diagnostics = errors ?? Array.Empty<string>() });
            return null;
        }

        private static object OrderedPreviewResponse(string fullPath, string relativePath, RawScriptSnapshot original, string candidate, bool final)
        {
            byte[] bytes = OrderedBytes(candidate, original.Bom);
            if ((long)original.ByteCount + bytes.Length > MaxPreviewTextBytes)
                return OrderedRefusal("payload_too_large", "Original/final buffers exceed 1 MiB.");
            bool noOp = original.Text == candidate;
            return new SuccessResponse(
                "Prepared ordered buffer without writing or refreshing.",
                new
                {
                    path = relativePath,
                    uri = $"mcpforunity://path/{relativePath}",
                    absolute_path = Path.GetFullPath(fullPath),
                    project_root = Path.GetDirectoryName(Path.GetFullPath(Application.dataPath)),
                    preview = true,
                    complete = true,
                    truncated = false,
                    original_contents = original.Text,
                    new_contents = candidate,
                    original_sha256 = ComputeSha256(original.Text),
                    sha256 = ComputeSha256(original.Text),
                    candidate_sha256 = ComputeSha256(candidate),
                    original_bytes_sha256 = original.RawSha,
                    candidate_bytes_sha256 = RawSha256(bytes),
                    encoding = "utf-8",
                    original_bom = original.Bom,
                    bom = original.Bom,
                    editsApplied = 0,
                    editsPrepared = noOp ? 0 : 1,
                    scheduledRefresh = false,
                    no_op = noOp,
                    validation_status = final ? "passed" : "pending",
                }
            );
        }

        private static object PreviewScriptBuffer(string fullPath, string relativePath, string name, JObject parameters)
        {
            try
            {
                var options = OrderedOptions(parameters);
                var original = ReadOrderedSnapshot(relativePath);
                if (parameters["source_contents"] == null)
                {
                    if (parameters["edit"] != null || parameters["validate_final"] != null)
                        return OrderedRefusal("invalid_buffer", "A snapshot cannot contain an edit or final-validation request.");
                    return OrderedPreviewResponse(fullPath, relativePath, original, original.Text, false);
                }
                string source = OrderedString(parameters, "source_contents");
                bool bom = OrderedBoolean(parameters, "bom");
                bool final = OrderedBoolean(parameters, "validate_final");
                if (
                    OrderedBoolean(parameters, "original_bom") != original.Bom
                    || bom != original.Bom
                    || OrderedDigest(parameters, "original_bytes_sha256") != original.RawSha
                    || OrderedDigest(parameters, "original_sha256") != ComputeSha256(original.Text)
                )
                    return OrderedRefusal("stale_file", "Original raw bytes or preamble changed during preparation.");
                byte[] sourceBytes = OrderedBytes(source, bom);
                if (
                    OrderedDigest(parameters, "source_sha256") != ComputeSha256(source)
                    || OrderedDigest(parameters, "source_bytes_sha256") != RawSha256(sourceBytes)
                )
                    return OrderedRefusal("invalid_buffer", "Source logical/raw digests do not match the supplied buffer.");
                if ((long)original.ByteCount + sourceBytes.Length > MaxPreviewTextBytes)
                    return OrderedRefusal("payload_too_large", "Original/current buffers exceed the stage limit.");
                string candidate = source;
                if (parameters["edit"] != null)
                {
                    if (parameters["edit"] is not JObject edit)
                        return OrderedRefusal("invalid_edit", "The single buffer edit must be an object.");
                    object computed;
                    if (edit["startLine"] != null)
                    {
                        foreach (string field in new[] { "startLine", "startCol", "endLine", "endCol" })
                            if (edit[field]?.Type != JTokenType.Integer || edit.Value<long>(field) < 1 || edit.Value<long>(field) > int.MaxValue)
                                return OrderedRefusal("invalid_range", "Literal coordinates must be positive integers.");
                        _ = OrderedString(edit, "newText");
                        if (
                            !TryIndexFromLineCol(source, edit.Value<int>("startLine"), edit.Value<int>("startCol"), out int start)
                            || !TryIndexFromLineCol(source, edit.Value<int>("endLine"), edit.Value<int>("endCol"), out int end)
                            || end < start
                        )
                            return OrderedRefusal("invalid_range", "Literal range is outside the current source or reversed.");
                        computed = ApplyTextEdits(
                            fullPath,
                            relativePath,
                            name,
                            new JArray(edit),
                            ComputeSha256(source),
                            sourceBuffer: source,
                            computeOnly: true
                        );
                    }
                    else
                    {
                        computed = EditScript(
                            fullPath,
                            relativePath,
                            name,
                            new JArray(edit),
                            new JObject { ["applyMode"] = "sequential" },
                            sourceBuffer: source,
                            computeOnly: true
                        );
                    }
                    var result = JObject.FromObject(computed);
                    if (result.Value<bool?>("success") != true)
                        return computed;
                    candidate = result["data"].Value<string>("new_contents");
                }
                else if (!final)
                    return OrderedRefusal("invalid_edit", "An empty operation is permitted only for final validation.");
                if ((long)original.ByteCount + sourceBytes.Length + OrderedBytes(candidate, bom).Length > MaxPreviewTextBytes)
                    return OrderedRefusal("payload_too_large", "Original/current/next buffers exceed 1 MiB.");
                if (final)
                {
                    var validationError = ValidateOrderedFinal(candidate, options);
                    if (validationError != null)
                        return validationError;
                }
                return OrderedPreviewResponse(fullPath, relativePath, original, candidate, final);
            }
            catch (Exception ex)
                when (ex is IOException
                    || ex is UnauthorizedAccessException
                    || ex is ArgumentException
                    || ex is System.Text.DecoderFallbackException
                    || ex is System.Text.EncoderFallbackException
                    || ex is OverflowException
                )
            {
                return OrderedRefusal("invalid_buffer", ex.Message);
            }
        }

        private static object ApplyPreparedScript(string fullPath, string relativePath, JObject parameters)
        {
            string tempPath = null;
            string backupPath = null;
            bool ownsTemp = false;
            bool attempted = false;
            bool committed = false;
            string candidateSha = null;
            string expectedRaw = null;
            bool bom = false;
            try
            {
                var options = OrderedOptions(parameters);
                string originalSha = OrderedDigest(parameters, "original_bytes_sha256");
                expectedRaw = OrderedDigest(parameters, "candidate_bytes_sha256");
                string candidate = OrderedString(parameters, "candidate_contents");
                bom = OrderedBoolean(parameters, "bom");
                byte[] bytes = OrderedBytes(candidate, bom);
                candidateSha = ComputeSha256(candidate);
                if (RawSha256(bytes) != expectedRaw)
                    return OrderedRefusal("candidate_mismatch", "Candidate bytes do not match their digest.");
                var original = ReadOrderedSnapshot(relativePath);
                if (original.RawSha != originalSha || original.Bom != bom)
                    return OrderedRefusal("stale_file", "Original bytes or BOM changed before commit.");
                if ((long)original.ByteCount + bytes.Length > MaxPreviewTextBytes)
                    return OrderedRefusal("payload_too_large", "Original/final buffers exceed 1 MiB.");
                var validationError = ValidateOrderedFinal(candidate, options);
                if (validationError != null)
                    return OrderedRefusal("validation_failed", JObject.FromObject(validationError).ToString());
                if (original.RawSha == expectedRaw)
                    return new SuccessResponse(
                        "Ordered no-op; no write or refresh.",
                        new
                        {
                            applied = false,
                            no_op = true,
                            editsApplied = 0,
                            scheduledRefresh = false,
                            sha256 = candidateSha,
                            bytes_sha256 = expectedRaw,
                            bom,
                        }
                    );
                fullPath = AssetPathUtility.GetFullAssetPath(relativePath);
                string attempt = Guid.NewGuid().ToString("N");
                tempPath = fullPath + "." + attempt + ".prepared.tmp";
                backupPath = fullPath + "." + attempt + ".prepared.bak";
                using (var stream = File.Open(tempPath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    ownsTemp = true;
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                _preparedCommitStageForTests?.Invoke("before_compare");
                if (ReadOrderedSnapshot(relativePath).RawSha != originalSha)
                    return OrderedRefusal("stale_file", "Original raw bytes changed at final comparison.");
                fullPath = AssetPathUtility.GetFullAssetPath(relativePath);
                _preparedCommitStageForTests?.Invoke("before_replace");
                attempted = true;
                File.Replace(tempPath, fullPath, backupPath);
                committed = true;
                _preparedCommitStageForTests?.Invoke("after_replace");
                if (ReadOrderedSnapshot(relativePath).RawSha != expectedRaw)
                    return new SuccessResponse(
                        "Commit completed but later bytes differ; refresh was not requested.",
                        new
                        {
                            applied = true,
                            outcome = "conflict",
                            confirmation = "postcommit_bytes_changed",
                            sha256 = candidateSha,
                            bytes_sha256 = expectedRaw,
                            bom,
                            scheduledRefresh = false,
                            refresh_status = "not_requested",
                        }
                    );
                bool immediate = options.Value<string>("refresh") == "immediate" || options.Value<string>("refresh") == "sync";
                try
                {
                    if (_preparedRefreshForTests != null)
                        _preparedRefreshForTests(relativePath, immediate);
                    else if (immediate)
                        ManageScriptRefreshHelpers.ImportAndRequestCompile(relativePath);
                    else
                        ManageScriptRefreshHelpers.ScheduleScriptRefresh(relativePath);
                    return new SuccessResponse(
                        "Prepared bytes committed once.",
                        new
                        {
                            applied = true,
                            sha256 = candidateSha,
                            bytes_sha256 = expectedRaw,
                            bom,
                            editsApplied = 1,
                            no_op = false,
                            scheduledRefresh = !immediate,
                            refresh_status = immediate ? "requested" : "scheduled",
                            confirmation = "candidate_bytes_observed",
                        }
                    );
                }
                catch (Exception ex)
                {
                    return new SuccessResponse(
                        "Prepared bytes committed; refresh failed.",
                        new
                        {
                            applied = true,
                            sha256 = candidateSha,
                            bytes_sha256 = expectedRaw,
                            bom,
                            editsApplied = 1,
                            scheduledRefresh = (bool?)null,
                            refresh_attempted = true,
                            refresh_confirmation = "unknown",
                            refresh_status = "failed",
                            refresh_error = ex.Message.Substring(0, Math.Min(512, ex.Message.Length)),
                        }
                    );
                }
            }
            catch (PlatformNotSupportedException ex)
            {
                if (committed)
                    return new SuccessResponse(
                        "File replacement completed; postcommit observation failed.",
                        new
                        {
                            applied = true,
                            sha256 = candidateSha,
                            bytes_sha256 = expectedRaw,
                            bom,
                            scheduledRefresh = false,
                            refresh_status = "not_requested",
                            confirmation = "postcommit_observation_failed",
                        }
                    );
                return OrderedRefusal("atomic_replace_unsupported", ex.Message);
            }
            // This commit boundary must retain returned-replace evidence even for unexpected postcommit failures.
            catch (Exception ex)
            {
                if (committed)
                    return new SuccessResponse(
                        "File replacement completed; postcommit observation failed.",
                        new
                        {
                            applied = true,
                            sha256 = candidateSha,
                            bytes_sha256 = expectedRaw,
                            bom,
                            scheduledRefresh = false,
                            refresh_status = "not_requested",
                            confirmation = "postcommit_observation_failed",
                        }
                    );
                if (attempted)
                    return new
                    {
                        success = false,
                        code = "outcome_unknown",
                        data = new
                        {
                            outcome = "unknown",
                            expected_bytes_sha256 = expectedRaw,
                            confirmation = "pending_verification",
                            refresh_status = "not_requested",
                            recovery_backup = backupPath,
                        },
                    };
                return OrderedRefusal("commit_refused", ex.Message);
            }
            finally
            {
                // These unique paths belong to this attempt only. Never roll a target back.
                if (ownsTemp && tempPath != null)
                    try
                    {
                        if (File.Exists(tempPath))
                            File.Delete(tempPath);
                    }
                    catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is System.Security.SecurityException) { }
                if (committed && backupPath != null)
                    try
                    {
                        if (File.Exists(backupPath))
                            File.Delete(backupPath);
                    }
                    catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is System.Security.SecurityException) { }
            }
        }

        private static bool TryIndexFromLineCol(string text, int line1, int col1, out int index)
        {
            // 1-based line/col to absolute index (0-based), col positions are counted in code points
            int line = 1,
                col = 1;
            for (int i = 0; i <= text.Length; i++)
            {
                if (line == line1 && col == col1)
                {
                    index = i;
                    return true;
                }
                if (i == text.Length)
                    break;
                char c = text[i];
                if (c == '\r')
                {
                    // Treat CRLF as a single newline; skip the LF if present
                    if (i + 1 < text.Length && text[i + 1] == '\n')
                        i++;
                    line++;
                    col = 1;
                }
                else if (c == '\n')
                {
                    line++;
                    col = 1;
                }
                else
                {
                    if (char.IsHighSurrogate(c) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
                        i++;
                    col++;
                }
            }
            index = -1;
            return false;
        }

        private static string ComputeSha256(string contents)
        {
            using (var sha = SHA256.Create())
            {
                var bytes = StrictFileUtf8.GetBytes(contents);
                var hash = sha.ComputeHash(bytes);
                return BitConverter.ToString(hash).Replace("-", string.Empty).ToLowerInvariant();
            }
        }

        /// <summary>
        /// Lightweight lexer that tracks whether the current position is inside a
        /// string literal (regular, verbatim, interpolated, raw) or a comment.
        /// Callers advance character-by-character and check <see cref="InNonCode"/>.
        /// </summary>
        private struct CSharpLexer
        {
            private readonly string _text;
            private int _pos;
            private readonly int _end;
            private readonly int _literalDepth;
            private int _line;

            // String/comment state
            private bool _inSingleComment;
            private bool _inMultiComment;

            public CSharpLexer(string text, int start = 0, int end = -1, int literalDepth = 0)
            {
                _text = text;
                _pos = start;
                _end = end < 0 ? text.Length : end;
                _literalDepth = literalDepth;
                _line = 1;
                // count newlines before start
                for (int i = 0; i < start && i < text.Length; i++)
                    if (text[i] == '\n')
                        _line++;
                _inSingleComment = false;
                _inMultiComment = false;
                InNonCode = false;
            }

            public bool InNonCode { get; private set; }
            public int Position => _pos;
            public int Line => _line;

            // Validation directive messages are arbitrary text, not C# literals.
            // Only the validation view calls this after observing a code '#' at line start.
            public void SkipDirectiveLine()
            {
                while (_pos < _end && _text[_pos] != '\n')
                    _pos++;
            }

            /// <summary>
            /// Advance to the next character, updating all state.
            /// Returns false at end of range.
            /// </summary>
            public bool Advance(out char c)
            {
                if (_pos >= _end)
                {
                    c = '\0';
                    return false;
                }

                c = _text[_pos];
                char next = _pos + 1 < _end ? _text[_pos + 1] : '\0';

                if (c == '\n')
                {
                    _line++;
                    if (_inSingleComment)
                        _inSingleComment = false;
                }

                // Inside single-line comment
                if (_inSingleComment)
                {
                    InNonCode = true;
                    _pos++;
                    return true;
                }

                // Inside multi-line comment
                if (_inMultiComment)
                {
                    if (c == '*' && next == '/')
                    {
                        _inMultiComment = false;
                        InNonCode = true;
                        _pos += 2;
                        c = '/';
                        return true;
                    }
                    InNonCode = true;
                    _pos++;
                    return true;
                }

                // Start of comment
                if (c == '/' && next == '/')
                {
                    _inSingleComment = true;
                    InNonCode = true;
                    _pos += 2;
                    return true;
                }
                if (c == '/' && next == '*')
                {
                    _inMultiComment = true;
                    InNonCode = true;
                    _pos += 2;
                    return true;
                }

                // Interpolated raw string: $"""...""" or $$"""...""" etc. (C# 11)
                // Must check BEFORE regular $" and BEFORE plain """
                if (c == '$')
                {
                    int dollarCount = 1;
                    while (_pos + dollarCount < _end && _text[_pos + dollarCount] == '$')
                        dollarCount++;
                    int afterDollars = _pos + dollarCount;
                    if (afterDollars + 2 < _end && _text[afterDollars] == '"' && _text[afterDollars + 1] == '"' && _text[afterDollars + 2] == '"')
                    {
                        int q = 3;
                        while (afterDollars + q < _end && _text[afterDollars + q] == '"')
                            q++;
                        _pos = afterDollars + q; // past all opening quotes
                        SkipInterpolatedRawStringBody(dollarCount, q);
                        InNonCode = true;
                        return true;
                    }
                }

                // Raw string literal: """...""" (C# 11, non-interpolated)
                if (c == '"' && next == '"' && _pos + 2 < _end && _text[_pos + 2] == '"')
                {
                    int q = 3;
                    while (_pos + q < _end && _text[_pos + q] == '"')
                        q++;
                    _pos += q; // past opening quotes
                    int closeCount = 0;
                    while (_pos < _end)
                    {
                        if (_text[_pos] == '\n')
                            _line++;
                        if (_text[_pos] == '"')
                        {
                            closeCount++;
                            if (closeCount >= q)
                            {
                                _pos++;
                                break;
                            }
                        }
                        else
                            closeCount = 0;
                        _pos++;
                    }
                    InNonCode = true;
                    return true;
                }

                // Interpolated string: $"..." or $@"..." or @$"..."
                if (
                    (c == '$' && next == '"')
                    || (c == '$' && next == '@' && _pos + 2 < _end && _text[_pos + 2] == '"')
                    || (c == '@' && next == '$' && _pos + 2 < _end && _text[_pos + 2] == '"')
                )
                {
                    bool isVerbatim = (next == '@') || (c == '@');
                    _pos += (c == '$' && next == '"') ? 2 : 3;
                    SkipInterpolatedStringBody(isVerbatim);
                    InNonCode = true;
                    return true;
                }

                // Verbatim string: @"..."
                if (c == '@' && next == '"')
                {
                    _pos += 2;
                    while (_pos < _end)
                    {
                        if (_text[_pos] == '\n')
                            _line++;
                        if (_text[_pos] == '"')
                        {
                            if (_pos + 1 < _end && _text[_pos + 1] == '"')
                            {
                                _pos += 2;
                                continue;
                            }
                            _pos++;
                            break;
                        }
                        _pos++;
                    }
                    InNonCode = true;
                    return true;
                }

                // Regular string: "..."
                if (c == '"')
                {
                    _pos++;
                    while (_pos < _end)
                    {
                        if (_text[_pos] == '\\')
                        {
                            _pos += 2;
                            continue;
                        }
                        if (_text[_pos] == '"')
                        {
                            _pos++;
                            break;
                        }
                        if (_text[_pos] == '\n')
                            _line++;
                        _pos++;
                    }
                    InNonCode = true;
                    return true;
                }

                // Char literal: '...'
                if (c == '\'')
                {
                    _pos++;
                    while (_pos < _end)
                    {
                        if (_text[_pos] == '\\')
                        {
                            _pos += 2;
                            continue;
                        }
                        if (_text[_pos] == '\'')
                        {
                            _pos++;
                            break;
                        }
                        _pos++;
                    }
                    InNonCode = true;
                    return true;
                }

                InNonCode = false;
                _pos++;
                return true;
            }

            /// <summary>
            /// Skip the body of an interpolated string, handling nested interpolation holes.
            /// _pos should be right after the opening quote.
            /// </summary>
            private void SkipInterpolatedStringBody(bool isVerbatim)
            {
                int interpDepth = 0;
                while (_pos < _end)
                {
                    char ch = _text[_pos];
                    if (ch == '\n')
                        _line++;

                    if (interpDepth > 0)
                    {
                        // Inside interpolation hole — this is code, scan for nested strings/braces
                        if (ch == '{')
                        {
                            interpDepth++;
                            _pos++;
                            continue;
                        }
                        if (ch == '}')
                        {
                            interpDepth--;
                            _pos++;
                            continue;
                        }
                        if (SkipNestedInterpolationLiteral())
                            continue;
                        if (ch == '/' && _pos + 1 < _end)
                        {
                            if (_text[_pos + 1] == '/')
                            {
                                _pos += 2;
                                while (_pos < _end && _text[_pos] != '\n')
                                    _pos++;
                                continue;
                            }
                            if (_text[_pos + 1] == '*')
                            {
                                _pos += 2;
                                while (_pos + 1 < _end && !(_text[_pos] == '*' && _text[_pos + 1] == '/'))
                                {
                                    if (_text[_pos] == '\n')
                                        _line++;
                                    _pos++;
                                }
                                if (_pos + 1 < _end)
                                    _pos += 2;
                                continue;
                            }
                        }
                        if (ch == '\'')
                        {
                            _pos++;
                            while (_pos < _end)
                            {
                                if (_text[_pos] == '\\')
                                {
                                    _pos += 2;
                                    continue;
                                }
                                if (_text[_pos] == '\'')
                                {
                                    _pos++;
                                    break;
                                }
                                _pos++;
                            }
                            continue;
                        }
                        _pos++;
                        continue;
                    }

                    // interpDepth == 0: inside string content
                    if (ch == '{')
                    {
                        if (_pos + 1 < _end && _text[_pos + 1] == '{')
                        {
                            _pos += 2;
                            continue;
                        } // escaped {{
                        interpDepth = 1;
                        _pos++;
                        continue;
                    }
                    if (ch == '}')
                    {
                        if (_pos + 1 < _end && _text[_pos + 1] == '}')
                        {
                            _pos += 2;
                            continue;
                        } // escaped }}
                        // Stray } at depth 0 — shouldn't happen in valid code, just advance
                        _pos++;
                        continue;
                    }
                    if (ch == '"')
                    {
                        if (isVerbatim && _pos + 1 < _end && _text[_pos + 1] == '"')
                        {
                            _pos += 2;
                            continue;
                        } // doubled quote
                        _pos++;
                        return; // closing quote
                    }
                    if (!isVerbatim && ch == '\\')
                    {
                        _pos += 2;
                        continue;
                    } // escape in regular interpolated
                    _pos++;
                }
            }

            /// <summary>
            /// Skip the body of an interpolated raw string ($"""...""", $$"""...""", etc.).
            /// dollarCount determines how many consecutive { start an interpolation hole.
            /// quoteCount is the number of " that close the string.
            /// _pos should be right after the opening quotes.
            /// </summary>
            private void SkipInterpolatedRawStringBody(int dollarCount, int quoteCount)
            {
                int interpDepth = 0;
                while (_pos < _end)
                {
                    char ch = _text[_pos];
                    if (ch == '\n')
                        _line++;

                    if (interpDepth > 0)
                    {
                        // Inside interpolation hole — code context
                        if (ch == '{')
                        {
                            interpDepth++;
                            _pos++;
                            continue;
                        }
                        if (ch == '}')
                        {
                            interpDepth--;
                            _pos++;
                            continue;
                        }
                        if (SkipNestedInterpolationLiteral())
                            continue;
                        if (ch == '/' && _pos + 1 < _end)
                        {
                            if (_text[_pos + 1] == '/')
                            {
                                _pos += 2;
                                while (_pos < _end && _text[_pos] != '\n')
                                    _pos++;
                                continue;
                            }
                            if (_text[_pos + 1] == '*')
                            {
                                _pos += 2;
                                while (_pos + 1 < _end && !(_text[_pos] == '*' && _text[_pos + 1] == '/'))
                                {
                                    if (_text[_pos] == '\n')
                                        _line++;
                                    _pos++;
                                }
                                if (_pos + 1 < _end)
                                    _pos += 2;
                                continue;
                            }
                        }
                        _pos++;
                        continue;
                    }

                    // String content (interpDepth == 0)
                    // Check for closing quote sequence
                    if (ch == '"')
                    {
                        int qc = 1;
                        while (_pos + qc < _end && _text[_pos + qc] == '"')
                            qc++;
                        if (qc >= quoteCount)
                        {
                            _pos += quoteCount;
                            return;
                        }
                        // Fewer quotes than needed — literal content
                        _pos += qc;
                        continue;
                    }

                    // Check for interpolation hole: dollarCount consecutive {'s
                    if (ch == '{')
                    {
                        int bc = 1;
                        while (_pos + bc < _end && _text[_pos + bc] == '{')
                            bc++;
                        if (bc >= dollarCount)
                        {
                            // Exactly dollarCount opens an interpolation hole; extras are literal
                            _pos += dollarCount;
                            interpDepth = 1;
                        }
                        else
                        {
                            // Fewer than dollarCount — literal braces
                            _pos += bc;
                        }
                        continue;
                    }

                    // Closing braces with dollarCount threshold — literal if fewer
                    if (ch == '}')
                    {
                        int bc = 1;
                        while (_pos + bc < _end && _text[_pos + bc] == '}')
                            bc++;
                        _pos += bc; // all literal at depth 0
                        continue;
                    }

                    _pos++;
                }
            }

            private bool SkipNestedInterpolationLiteral()
            {
                char c = _text[_pos];
                if (c != '"' && c != '\'' && c != '@' && c != '$')
                    return false;
                // Unusually deep nested literals are left opaque and rejected by the
                // existing delimiter guard, rather than risking unbounded recursion.
                if (_literalDepth >= 64)
                {
                    _pos = _end;
                    return true;
                }
                var nested = new CSharpLexer(_text, _pos, _end, _literalDepth + 1);
                if (!nested.Advance(out _) || !nested.InNonCode || nested.Position <= _pos + 1)
                    return false;
                int end = Math.Min(_end, nested.Position);
                while (_pos < end)
                {
                    if (_text[_pos] == '\n')
                        _line++;
                    _pos++;
                }
                return true;
            }
        }

        private static bool CheckBalancedDelimiters(string text, out int line, out char expected)
        {
            var delimiters = new Stack<(char closing, int line)>();
            line = 1;
            expected = '\0';

            var lexer = new CSharpLexer(text);
            while (lexer.Advance(out char c))
            {
                if (lexer.InNonCode)
                    continue;

                switch (c)
                {
                    case '{':
                        delimiters.Push(('}', lexer.Line));
                        break;
                    case '(':
                        delimiters.Push((')', lexer.Line));
                        break;
                    case '[':
                        delimiters.Push((']', lexer.Line));
                        break;
                    case '}':
                    case ')':
                    case ']':
                        if (delimiters.Count == 0)
                        {
                            line = lexer.Line;
                            expected =
                                c == '}' ? '{'
                                : c == ')' ? '('
                                : '[';
                            return false;
                        }
                        var opening = delimiters.Pop();
                        if (c != opening.closing)
                        {
                            line = lexer.Line;
                            expected = opening.closing;
                            return false;
                        }
                        break;
                }
            }

            if (delimiters.Count > 0)
            {
                var opening = delimiters.Peek();
                line = opening.line;
                expected = opening.closing;
                return false;
            }

            return true;
        }

        private static object DeleteScript(string fullPath, string relativePath)
        {
            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"Script not found at '{relativePath}'. Cannot delete.");
            }

            try
            {
                // Use AssetDatabase.MoveAssetToTrash for safer deletion (allows undo)
                bool deleted = AssetDatabase.MoveAssetToTrash(relativePath);
                if (deleted)
                {
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    return new SuccessResponse($"Script '{Path.GetFileName(relativePath)}' moved to trash successfully.", new { deleted = true });
                }
                else
                {
                    // Fallback or error if MoveAssetToTrash fails
                    return new ErrorResponse($"Failed to move script '{relativePath}' to trash. It might be locked or in use.");
                }
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error deleting script '{relativePath}': {e.Message}");
            }
        }

        private static ErrorResponse ValidateStructuredEdits(JArray edits)
        {
            if (edits == null || edits.Count == 0)
                return new ErrorResponse("invalid_edit", "No structured edits provided.");
            foreach (var token in edits)
            {
                if (token is not JObject edit)
                    return new ErrorResponse("invalid_edit", "Each structured edit must be an object.");
                foreach (
                    string field in new[]
                    {
                        "mode",
                        "op",
                        "className",
                        "namespace",
                        "methodName",
                        "replacement",
                        "replacementBase64",
                        "text",
                        "anchor",
                        "position",
                        "returnType",
                        "parametersSignature",
                        "attributesContains",
                        "afterMethodName",
                        "beforeMethodName",
                        "afterReturnType",
                        "afterParametersSignature",
                        "afterAttributesContains",
                    }
                )
                {
                    var value = edit[field];
                    if (value != null && value.Type != JTokenType.Null && value.Type != JTokenType.String)
                        return new ErrorResponse("invalid_edit", $"Structured edit '{field}' must be a string.");
                    if (value?.Type == JTokenType.String)
                    {
                        try
                        {
                            string text = value.Value<string>();
                            _ = StrictFileUtf8.GetByteCount(text);
                            if (field == "replacementBase64" && !string.IsNullOrEmpty(text))
                                _ = DecodeBase64(text);
                        }
                        catch (Exception ex)
                            when (ex is FormatException || ex is System.Text.DecoderFallbackException || ex is System.Text.EncoderFallbackException)
                        {
                            return new ErrorResponse(
                                "invalid_edit",
                                $"Structured edit '{field}' must contain valid Unicode and base64 where applicable: {ex.Message}"
                            );
                        }
                    }
                }
                string mode = (edit.Value<string>("mode") ?? edit.Value<string>("op") ?? string.Empty).ToLowerInvariant();
                switch (mode)
                {
                    case "replace_class":
                    case "delete_class":
                    case "replace_method":
                    case "delete_method":
                    case "insert_method":
                        if (string.IsNullOrWhiteSpace(edit.Value<string>("className")))
                            return new ErrorResponse("invalid_edit", $"{mode} requires 'className'.");
                        if ((mode == "replace_method" || mode == "delete_method") && string.IsNullOrWhiteSpace(edit.Value<string>("methodName")))
                            return new ErrorResponse("invalid_edit", $"{mode} requires 'methodName'.");
                        if (mode == "replace_class" || mode == "replace_method" || mode == "insert_method")
                        {
                            string replacement = ExtractReplacement(edit);
                            if (replacement == null || (mode == "insert_method" && string.IsNullOrWhiteSpace(replacement)))
                                return new ErrorResponse("invalid_edit", $"{mode} requires valid 'replacement' text (inline or base64).");
                            var snippetError = ValidateReplacementSnippet(replacement, mode == "replace_class", edit.Value<string>("className"));
                            if (snippetError != null)
                                return snippetError;
                        }
                        if (mode == "insert_method")
                        {
                            string position = (edit.Value<string>("position") ?? "end").ToLowerInvariant();
                            if ((position == "before" || position == "after") && string.IsNullOrEmpty(edit.Value<string>(position + "MethodName")))
                                return new ErrorResponse("invalid_edit", $"insert_method with position='{position}' requires '{position}MethodName'.");
                        }
                        break;
                    case "anchor_insert":
                    case "anchor_replace":
                    case "anchor_delete":
                        string anchor = edit.Value<string>("anchor");
                        if (string.IsNullOrWhiteSpace(anchor))
                            return new ErrorResponse("invalid_edit", $"{mode} requires 'anchor' (regex).");
                        try
                        {
                            _ = new Regex(anchor, RegexOptions.Multiline, TimeSpan.FromSeconds(2));
                        }
                        catch (ArgumentException ex)
                        {
                            return new ErrorResponse("invalid_edit", $"{mode} has an invalid anchor regex: {ex.Message}");
                        }
                        if (mode == "anchor_insert" && (edit.Value<string>("text") ?? ExtractReplacement(edit)) == null)
                            return new ErrorResponse("invalid_edit", "anchor_insert requires 'text' or valid replacement text.");
                        break;
                    default:
                        return new ErrorResponse("invalid_edit", $"Unknown edit mode: '{mode}'.");
                }
            }
            return null;
        }

        /// <summary>
        /// Structured edits (AST-backed where available) on existing scripts.
        /// Supports class-level replace/delete with Roslyn span computation if USE_ROSLYN is defined,
        /// otherwise falls back to a conservative balanced-brace scan.
        /// </summary>
        private static object EditScript(
            string fullPath,
            string relativePath,
            string name,
            JArray edits,
            JObject options,
            bool preview = false,
            string sourceBuffer = null,
            bool computeOnly = false
        )
        {
            var editError = ValidateStructuredEdits(edits);
            if (editError != null)
                return editError;
            if (!File.Exists(fullPath))
                return new ErrorResponse($"Script not found at '{relativePath}'.");
            try
            {
                fullPath = AssetPathUtility.GetFullAssetPath(relativePath);
            }
            catch (Exception)
            {
                return new ErrorResponse("Unsafe script path: linked or unreadable paths are not permitted.");
            }
            if (edits == null || edits.Count == 0)
                return new ErrorResponse("No edits provided.");

            string original;
            try
            {
                original = computeOnly ? sourceBuffer : ReadScriptContents(fullPath);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Failed to read script: {ex.Message}");
            }

            string working = original;

            try
            {
                var replacements = new List<(int start, int length, string text)>();
                preview = preview || options?.ReadScalar<bool?>("preview") == true;
                var structuralInsertions = new HashSet<int>();
                int appliedCount = 0;

                // Apply mode: atomic (default) computes all spans against original and applies together.
                // Sequential applies each edit immediately to the current working text (useful for dependent edits).
                string applyMode = options?["applyMode"]?.ToString()?.ToLowerInvariant();
                bool applySequentially = applyMode == "sequential";

                foreach (var e in edits)
                {
                    var op = (JObject)e;
                    var mode = (op.Value<string>("mode") ?? op.Value<string>("op") ?? string.Empty).ToLowerInvariant();

                    switch (mode)
                    {
                        case "replace_class":
                        {
                            string className = op.Value<string>("className");
                            string ns = op.Value<string>("namespace");
                            string replacement = ExtractReplacement(op);

                            if (string.IsNullOrWhiteSpace(className))
                                return new ErrorResponse("replace_class requires 'className'.");
                            if (replacement == null)
                                return new ErrorResponse("replace_class requires 'replacement' (inline or base64).");

                            if (!TryComputeClassSpan(working, className, ns, out var spanStart, out var spanLength, out var why))
                                return new ErrorResponse($"replace_class failed: {why}");

                            if (!ValidateClassSnippet(replacement, className, out var vErr))
                                return new ErrorResponse($"Replacement snippet invalid: {vErr}");

                            replacement = FormatStructuralReplacement(working, spanStart, spanLength, replacement);

                            if (applySequentially)
                            {
                                working = working.Remove(spanStart, spanLength).Insert(spanStart, replacement);
                                appliedCount++;
                            }
                            else
                            {
                                replacements.Add((spanStart, spanLength, replacement));
                            }
                            break;
                        }

                        case "delete_class":
                        {
                            string className = op.Value<string>("className");
                            string ns = op.Value<string>("namespace");
                            if (string.IsNullOrWhiteSpace(className))
                                return new ErrorResponse("delete_class requires 'className'.");

                            if (!TryComputeClassSpan(working, className, ns, out var s, out var l, out var why))
                                return new ErrorResponse($"delete_class failed: {why}");

                            if (applySequentially)
                            {
                                working = working.Remove(s, l);
                                appliedCount++;
                            }
                            else
                            {
                                replacements.Add((s, l, string.Empty));
                            }
                            break;
                        }

                        case "replace_method":
                        {
                            string className = op.Value<string>("className");
                            string ns = op.Value<string>("namespace");
                            string methodName = op.Value<string>("methodName");
                            string replacement = ExtractReplacement(op);
                            string returnType = op.Value<string>("returnType");
                            string parametersSignature = op.Value<string>("parametersSignature");
                            string attributesContains = op.Value<string>("attributesContains");

                            if (string.IsNullOrWhiteSpace(className))
                                return new ErrorResponse("replace_method requires 'className'.");
                            if (string.IsNullOrWhiteSpace(methodName))
                                return new ErrorResponse("replace_method requires 'methodName'.");
                            if (replacement == null)
                                return new ErrorResponse("replace_method requires 'replacement' (inline or base64).");

                            if (!TryComputeClassSpan(working, className, ns, out var clsStart, out var clsLen, out var whyClass))
                                return new ErrorResponse($"replace_method failed to locate class: {whyClass}");

                            if (
                                !TryComputeMethodSpan(
                                    working,
                                    clsStart,
                                    clsLen,
                                    methodName,
                                    returnType,
                                    parametersSignature,
                                    attributesContains,
                                    out var mStart,
                                    out var mLen,
                                    out var whyMethod
                                )
                            )
                            {
                                bool hasDependentInsert = edits.Any(j =>
                                    j is JObject jo
                                    && string.Equals(jo.Value<string>("className"), className, StringComparison.Ordinal)
                                    && string.Equals(jo.Value<string>("methodName"), methodName, StringComparison.Ordinal)
                                    && ((jo.Value<string>("mode") ?? jo.Value<string>("op") ?? string.Empty).ToLowerInvariant() == "insert_method")
                                );
                                string hint =
                                    hasDependentInsert && !applySequentially
                                        ? " Hint: This batch inserts this method. Use options.applyMode='sequential' or split into separate calls."
                                        : string.Empty;
                                return new ErrorResponse($"replace_method failed: {whyMethod}.{hint}");
                            }

                            replacement = FormatStructuralReplacement(working, mStart, mLen, replacement, clsStart, clsLen);
                            if (applySequentially)
                            {
                                working = working.Remove(mStart, mLen).Insert(mStart, replacement);
                                appliedCount++;
                            }
                            else
                            {
                                replacements.Add((mStart, mLen, replacement));
                            }
                            break;
                        }

                        case "delete_method":
                        {
                            string className = op.Value<string>("className");
                            string ns = op.Value<string>("namespace");
                            string methodName = op.Value<string>("methodName");
                            string returnType = op.Value<string>("returnType");
                            string parametersSignature = op.Value<string>("parametersSignature");
                            string attributesContains = op.Value<string>("attributesContains");

                            if (string.IsNullOrWhiteSpace(className))
                                return new ErrorResponse("delete_method requires 'className'.");
                            if (string.IsNullOrWhiteSpace(methodName))
                                return new ErrorResponse("delete_method requires 'methodName'.");

                            if (!TryComputeClassSpan(working, className, ns, out var clsStart, out var clsLen, out var whyClass))
                                return new ErrorResponse($"delete_method failed to locate class: {whyClass}");

                            if (
                                !TryComputeMethodSpan(
                                    working,
                                    clsStart,
                                    clsLen,
                                    methodName,
                                    returnType,
                                    parametersSignature,
                                    attributesContains,
                                    out var mStart,
                                    out var mLen,
                                    out var whyMethod
                                )
                            )
                            {
                                bool hasDependentInsert = edits.Any(j =>
                                    j is JObject jo
                                    && string.Equals(jo.Value<string>("className"), className, StringComparison.Ordinal)
                                    && string.Equals(jo.Value<string>("methodName"), methodName, StringComparison.Ordinal)
                                    && ((jo.Value<string>("mode") ?? jo.Value<string>("op") ?? string.Empty).ToLowerInvariant() == "insert_method")
                                );
                                string hint =
                                    hasDependentInsert && !applySequentially
                                        ? " Hint: This batch inserts this method. Use options.applyMode='sequential' or split into separate calls."
                                        : string.Empty;
                                return new ErrorResponse($"delete_method failed: {whyMethod}.{hint}");
                            }

                            if (applySequentially)
                            {
                                working = working.Remove(mStart, mLen);
                                appliedCount++;
                            }
                            else
                            {
                                replacements.Add((mStart, mLen, string.Empty));
                            }
                            break;
                        }

                        case "insert_method":
                        {
                            string className = op.Value<string>("className");
                            string ns = op.Value<string>("namespace");
                            string position = (op.Value<string>("position") ?? "end").ToLowerInvariant();
                            string afterMethodName = op.Value<string>("afterMethodName");
                            string afterReturnType = op.Value<string>("afterReturnType");
                            string afterParameters = op.Value<string>("afterParametersSignature");
                            string afterAttributesContains = op.Value<string>("afterAttributesContains");
                            string snippet = ExtractReplacement(op);
                            // Harden: refuse empty replacement for inserts
                            if (snippet == null || snippet.Trim().Length == 0)
                                return new ErrorResponse("insert_method requires a non-empty 'replacement' text.");

                            if (string.IsNullOrWhiteSpace(className))
                                return new ErrorResponse("insert_method requires 'className'.");
                            if (snippet == null)
                                return new ErrorResponse("insert_method requires 'replacement' (inline or base64) containing a full method declaration.");

                            if (!TryComputeClassSpan(working, className, ns, out var clsStart, out var clsLen, out var whyClass))
                                return new ErrorResponse($"insert_method failed to locate class: {whyClass}");

                            if (position == "after" || position == "before")
                            {
                                if (position == "before")
                                {
                                    afterMethodName = op.Value<string>("beforeMethodName");
                                    afterReturnType = afterParameters = afterAttributesContains = null;
                                }
                                if (string.IsNullOrEmpty(afterMethodName))
                                    return new ErrorResponse($"insert_method with position='{position}' requires '{position}MethodName'.");
                                if (
                                    !TryComputeMethodSpan(
                                        working,
                                        clsStart,
                                        clsLen,
                                        afterMethodName,
                                        afterReturnType,
                                        afterParameters,
                                        afterAttributesContains,
                                        out var aStart,
                                        out var aLen,
                                        out var whyAfter
                                    )
                                )
                                    return new ErrorResponse($"insert_method(after) failed to locate anchor method: {whyAfter}");
                                int insAt = position == "after" ? aStart + aLen : aStart;
                                if (position == "before")
                                    insAt = BeforeMethodTrivia(working, clsStart, insAt);
                                string text = FormatMethodInsertion(working, clsStart, clsLen, ref insAt, snippet);
                                if (applySequentially)
                                {
                                    working = working.Insert(insAt, text);
                                    appliedCount++;
                                }
                                else
                                {
                                    QueueStructuralInsertion(replacements, structuralInsertions, insAt, text);
                                }
                            }
                            else if (!TryFindClassInsertionPoint(working, clsStart, clsLen, position, out var insAt, out var whyIns))
                                return new ErrorResponse($"insert_method failed: {whyIns}");
                            else
                            {
                                string text = FormatMethodInsertion(working, clsStart, clsLen, ref insAt, snippet);
                                if (applySequentially)
                                {
                                    working = working.Insert(insAt, text);
                                    appliedCount++;
                                }
                                else
                                {
                                    QueueStructuralInsertion(replacements, structuralInsertions, insAt, text);
                                }
                            }
                            break;
                        }

                        case "anchor_insert":
                        {
                            string anchor = op.Value<string>("anchor");
                            string position = (op.Value<string>("position") ?? "before").ToLowerInvariant();
                            string text = op.Value<string>("text") ?? ExtractReplacement(op);
                            if (string.IsNullOrWhiteSpace(anchor))
                                return new ErrorResponse("anchor_insert requires 'anchor' (regex).");
                            if (string.IsNullOrEmpty(text))
                                return new ErrorResponse("anchor_insert requires non-empty 'text'.");

                            try
                            {
                                var rx = new Regex(anchor, RegexOptions.Multiline, TimeSpan.FromSeconds(2));
                                var allMatches = rx.Matches(working);
                                if (allMatches.Count == 0)
                                    return new ErrorResponse($"anchor_insert: anchor not found: {anchor}");
                                var m = FindBestAnchorMatch(allMatches, working, anchor);
                                if (m == null)
                                    return new ErrorResponse($"anchor_insert: anchor not found (filtered): {anchor}");
                                int insAt = position == "after" ? m.Index + m.Length : m.Index;
                                string norm = text;

                                // Duplicate guard: if identical snippet already exists within this class, skip insert
                                if (TryComputeClassSpan(working, name, null, out var clsStartDG, out var clsLenDG, out _))
                                {
                                    string classSlice = working.Substring(clsStartDG, Math.Min(clsLenDG, working.Length - clsStartDG));
                                    if (classSlice.IndexOf(norm, StringComparison.Ordinal) >= 0)
                                    {
                                        // Do not insert duplicate; treat as no-op
                                        break;
                                    }
                                }
                                if (applySequentially)
                                {
                                    working = working.Insert(insAt, norm);
                                    appliedCount++;
                                }
                                else
                                {
                                    replacements.Add((insAt, 0, norm));
                                }
                            }
                            catch (Exception ex)
                            {
                                return new ErrorResponse($"anchor_insert failed: {ex.Message}");
                            }
                            break;
                        }

                        case "anchor_delete":
                        {
                            string anchor = op.Value<string>("anchor");
                            if (string.IsNullOrWhiteSpace(anchor))
                                return new ErrorResponse("anchor_delete requires 'anchor' (regex).");
                            try
                            {
                                var rx = new Regex(anchor, RegexOptions.Multiline, TimeSpan.FromSeconds(2));
                                var allDelMatches = rx.Matches(working);
                                if (allDelMatches.Count == 0)
                                    return new ErrorResponse($"anchor_delete: anchor not found: {anchor}");
                                var m = FindBestAnchorMatch(allDelMatches, working, anchor);
                                if (m == null)
                                    return new ErrorResponse($"anchor_delete: anchor not found (filtered): {anchor}");
                                int delAt = m.Index;
                                int delLen = m.Length;
                                if (applySequentially)
                                {
                                    working = working.Remove(delAt, delLen);
                                    appliedCount++;
                                }
                                else
                                {
                                    replacements.Add((delAt, delLen, string.Empty));
                                }
                            }
                            catch (Exception ex)
                            {
                                return new ErrorResponse($"anchor_delete failed: {ex.Message}");
                            }
                            break;
                        }

                        case "anchor_replace":
                        {
                            string anchor = op.Value<string>("anchor");
                            string replacement = op.Value<string>("text") ?? op.Value<string>("replacement") ?? ExtractReplacement(op) ?? string.Empty;
                            if (string.IsNullOrWhiteSpace(anchor))
                                return new ErrorResponse("anchor_replace requires 'anchor' (regex).");
                            try
                            {
                                var rx = new Regex(anchor, RegexOptions.Multiline, TimeSpan.FromSeconds(2));
                                var allReplMatches = rx.Matches(working);
                                if (allReplMatches.Count == 0)
                                    return new ErrorResponse($"anchor_replace: anchor not found: {anchor}");
                                var m = FindBestAnchorMatch(allReplMatches, working, anchor);
                                if (m == null)
                                    return new ErrorResponse($"anchor_replace: anchor not found (filtered): {anchor}");
                                int at = m.Index;
                                int len = m.Length;
                                string norm = replacement;
                                if (applySequentially)
                                {
                                    working = working.Remove(at, len).Insert(at, norm);
                                    appliedCount++;
                                }
                                else
                                {
                                    replacements.Add((at, len, norm));
                                }
                            }
                            catch (Exception ex)
                            {
                                return new ErrorResponse($"anchor_replace failed: {ex.Message}");
                            }
                            break;
                        }

                        default:
                            return new ErrorResponse(
                                $"Unknown edit mode: '{mode}'. Allowed: replace_class, delete_class, replace_method, delete_method, insert_method, anchor_insert, anchor_delete, anchor_replace."
                            );
                    }
                }

                if (!applySequentially)
                {
                    if (HasOverlaps(replacements))
                    {
                        var ordered = replacements.OrderByDescending(r => r.start).ToList();
                        for (int i = 1; i < ordered.Count; i++)
                        {
                            if (ordered[i].start + ordered[i].length > ordered[i - 1].start)
                            {
                                var conflict = new[]
                                {
                                    new
                                    {
                                        startA = ordered[i].start,
                                        endA = ordered[i].start + ordered[i].length,
                                        startB = ordered[i - 1].start,
                                        endB = ordered[i - 1].start + ordered[i - 1].length,
                                    },
                                };
                                return new ErrorResponse(
                                    "overlap",
                                    new
                                    {
                                        status = "overlap",
                                        conflicts = conflict,
                                        hint = "Sort ranges descending by start and compute from the same snapshot.",
                                    }
                                );
                            }
                        }
                        return new ErrorResponse("overlap", new { status = "overlap" });
                    }

                    foreach (var r in replacements.OrderByDescending(r => r.start))
                        working = working.Remove(r.start, r.length).Insert(r.start, r.text);
                    appliedCount = replacements.Count;
                }

                if (computeOnly)
                    return new SuccessResponse("Computed structured buffer without final validation or writing.", new { new_contents = working });

                // Guard against structural imbalance before validation
                if (!CheckBalancedDelimiters(working, out int lineBal, out char expectedBal))
                    return new ErrorResponse(
                        "unbalanced_braces",
                        new
                        {
                            status = "unbalanced_braces",
                            line = lineBal,
                            expected = expectedBal.ToString(),
                        }
                    );

                // No-op guard for structured edits: if text unchanged, return explicit no-op
                if (string.Equals(working, original, StringComparison.Ordinal))
                {
                    if (preview)
                        return ScriptPreviewResponse(fullPath, relativePath, original, working, 0);
                    var sameSha = ComputeSha256(original);
                    return new SuccessResponse(
                        $"No-op: contents unchanged for '{relativePath}'.",
                        new
                        {
                            path = relativePath,
                            uri = $"mcpforunity://path/{relativePath}",
                            editsApplied = 0,
                            no_op = true,
                            sha256 = sameSha,
                            evidence = new { reason = "identical_content" },
                        }
                    );
                }

                // Validate result using override from options if provided; otherwise GUI strictness
                var level = GetValidationLevelFromGUI();
                try
                {
                    var validateOpt = options?["validate"]?.ToString()?.ToLowerInvariant();
                    if (!string.IsNullOrEmpty(validateOpt))
                    {
                        level = validateOpt switch
                        {
                            "basic" => ValidationLevel.Basic,
                            "standard" => ValidationLevel.Standard,
                            "comprehensive" => ValidationLevel.Comprehensive,
                            "strict" => ValidationLevel.Strict,
                            _ => level,
                        };
                    }
                }
                catch { /* ignore option parsing issues */ }
                if (!ValidateScriptSyntax(working, level, out var errors))
                    return new ErrorResponse("validation_failed", new { status = "validation_failed", diagnostics = errors ?? Array.Empty<string>() });
                else if (errors != null && errors.Length > 0)
                    McpLog.Warn($"Script validation warnings for {name}:\n" + string.Join("\n", errors));

                // Atomic write with backup; schedule refresh
                if (preview)
                    return ScriptPreviewResponse(fullPath, relativePath, original, working, appliedCount);
                // Decide refresh behavior
                string refreshMode = options?["refresh"]?.ToString()?.ToLowerInvariant();
                bool immediate = refreshMode == "immediate" || refreshMode == "sync";

                // Persist changes atomically (no BOM), then compute/return new file SHA
                WriteScriptFile(fullPath, working, overwrite: true);

                var newSha = ComputeSha256(working);
                var ok = new SuccessResponse(
                    $"Applied {appliedCount} structured edit(s) to '{relativePath}'.",
                    new
                    {
                        path = relativePath,
                        uri = $"mcpforunity://path/{relativePath}",
                        editsApplied = appliedCount,
                        scheduledRefresh = !immediate,
                        sha256 = newSha,
                    }
                );

                if (immediate)
                {
                    McpLog.Info($"[ManageScript] EditScript: immediate refresh for '{relativePath}'", always: false);
                    ManageScriptRefreshHelpers.ImportAndRequestCompile(relativePath);
                }
                else
                {
                    ManageScriptRefreshHelpers.ScheduleScriptRefresh(relativePath);
                }
                return ok;
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Edit failed: {ex.Message}");
            }
        }

        private static bool HasOverlaps(IEnumerable<(int start, int length, string text)> list)
        {
            var arr = list.OrderBy(x => x.start).ToArray();
            for (int i = 1; i < arr.Length; i++)
            {
                if (arr[i - 1].start + arr[i - 1].length > arr[i].start)
                    return true;
            }
            return false;
        }

        private static string ExtractReplacement(JObject op)
        {
            var inline = op.Value<string>("replacement");
            if (!string.IsNullOrEmpty(inline))
                return inline;

            var b64 = op.Value<string>("replacementBase64");
            if (!string.IsNullOrEmpty(b64))
            {
                try
                {
                    return DecodeBase64(b64);
                }
                catch
                {
                    return null;
                }
            }
            return null;
        }

        // Formatting is limited to structural snippets. Literal operations never use these helpers.
        private static bool[] ProtectedLiteralBytes(string text, bool literalsOnly = true)
        {
            var protectedBytes = new bool[text.Length];
            var lexer = new CSharpLexer(text);
            while (lexer.Position < text.Length)
            {
                int start = lexer.Position;
                if (!lexer.Advance(out _))
                    break;
                char c = text[start];
                if (lexer.InNonCode && (!literalsOnly || (lexer.Position > start + 1 && (c == '"' || c == '\'' || c == '@' || c == '$'))))
                    for (int i = start; i < Math.Min(text.Length, lexer.Position); i++)
                        protectedBytes[i] = true;
            }
            return protectedBytes;
        }

        private static IEnumerable<(int start, int end, int next)> FormattingLines(string text)
        {
            for (int start = 0; start < text.Length; )
            {
                int end = start;
                while (end < text.Length && text[end] != '\r' && text[end] != '\n')
                    end++;
                int next = end;
                if (next < text.Length && text[next++] == '\r' && next < text.Length && text[next] == '\n')
                    next++;
                yield return (start, end, next);
                start = next;
            }
        }

        private static string LineIndent(string text, int start)
        {
            int end = start;
            while (end < text.Length && (text[end] == ' ' || text[end] == '\t'))
                end++;
            return text.Substring(start, end - start);
        }

        private static string FormattingBaseIndent(string text, bool[] protectedBytes)
        {
            foreach (var line in FormattingLines(text))
            {
                string indent = LineIndent(text, line.start);
                int token = line.start + indent.Length;
                if (token < line.end && !protectedBytes[line.start] && text[token] != '#')
                    return indent;
            }
            return string.Empty;
        }

        private static string FormattingIndentUnit(string text, bool[] protectedBytes, bool includeZero = true)
        {
            var nonCode = ProtectedLiteralBytes(text, literalsOnly: false);
            var indents = new HashSet<string>();
            if (includeZero)
                indents.Add(string.Empty);
            foreach (var line in FormattingLines(text))
            {
                string indent = LineIndent(text, line.start);
                int token = line.start + indent.Length;
                if (token < line.end && !protectedBytes[line.start] && !nonCode[token] && text[token] != '#')
                    indents.Add(indent);
            }
            string unit = null;
            string outer = includeZero ? string.Empty : FormattingBaseIndent(text, protectedBytes);
            foreach (string inner in indents)
                if (inner.Length > outer.Length && inner.StartsWith(outer, StringComparison.Ordinal))
                {
                    // Compare to the declaration base, not differences between deeper
                    // aligned expressions (e.g. a six-space initializer under a four-space body).
                    string delta = inner.Substring(outer.Length);
                    if (unit == null || delta.Length < unit.Length)
                        unit = delta;
                }
            return unit;
        }

        private static string LocalFormattingNewline(string source, int position)
        {
            // The closest source boundary wins, including mixed-EOL files.
            var protectedBytes = ProtectedLiteralBytes(source);
            string newline = "\n";
            int closest = int.MaxValue;
            foreach (var line in FormattingLines(source))
            {
                int distance = Math.Abs(line.end - position);
                if (line.end < source.Length && !protectedBytes[line.end] && distance < closest)
                {
                    closest = distance;
                    newline = source.Substring(line.end, line.next - line.end);
                }
            }
            return newline;
        }

        private static string FormatStructuralSnippet(string snippet, string indent, string unit, string newline)
        {
            var protectedBytes = ProtectedLiteralBytes(snippet);
            string baseIndent = FormattingBaseIndent(snippet, protectedBytes);
            string snippetUnit = FormattingIndentUnit(snippet, protectedBytes, includeZero: false);
            // Only completely flat snippets need brace-based indentation. Existing relative
            // alignment is otherwise retained, rather than reformatted.
            var nonCode = snippetUnit == null ? ProtectedLiteralBytes(snippet, literalsOnly: false) : null;
            int braceDepth = 0;
            var result = new System.Text.StringBuilder(snippet.Length);
            var lines = FormattingLines(snippet).ToList();
            int first = 0;
            while (
                first < lines.Count
                && string.IsNullOrWhiteSpace(snippet.Substring(lines[first].start, lines[first].end - lines[first].start))
                && !protectedBytes[lines[first].start]
            )
                first++;
            int last = snippet.Length;
            while (last > 0 && char.IsWhiteSpace(snippet[last - 1]) && !protectedBytes[last - 1])
                last--;
            foreach (var line in lines.Skip(first))
            {
                if (line.start >= last)
                    break;
                int end = Math.Min(line.end, last);
                if (protectedBytes[line.start])
                    result.Append(snippet, line.start, end - line.start);
                else
                {
                    string leading = LineIndent(snippet, line.start);
                    int token = line.start + leading.Length;
                    if (token < end && snippet[token] != '#' && leading.StartsWith(baseIndent, StringComparison.Ordinal))
                    {
                        string relative = leading.Substring(baseIndent.Length);
                        result.Append(indent);
                        if (snippetUnit == null)
                        {
                            int closing = 0;
                            while (token + closing < end && snippet[token + closing] == '}' && !nonCode[token + closing])
                                closing++;
                            for (int depth = 0; depth < Math.Max(0, braceDepth - closing); depth++)
                                result.Append(unit);
                        }
                        else
                            while (relative.StartsWith(snippetUnit, StringComparison.Ordinal))
                            {
                                result.Append(unit);
                                relative = relative.Substring(snippetUnit.Length);
                            }
                        result.Append(relative).Append(snippet, token, end - token);
                    }
                    else
                        result.Append(snippet, line.start, end - line.start);
                }
                if (nonCode != null)
                {
                    int token = line.start + LineIndent(snippet, line.start).Length;
                    if (token < end && snippet[token] != '#')
                        for (int i = line.start; i < end; i++)
                            if (!nonCode[i])
                            {
                                if (snippet[i] == '{')
                                    braceDepth++;
                                else if (snippet[i] == '}')
                                    braceDepth = Math.Max(0, braceDepth - 1);
                            }
                }
                if (line.end < last)
                {
                    if (protectedBytes[line.end])
                        result.Append(snippet, line.end, line.next - line.end);
                    else
                        result.Append(newline);
                }
            }
            return result.ToString();
        }

        private static string FormatStructuralReplacement(string source, int start, int length, string snippet, int contextStart = -1, int contextLength = 0)
        {
            string old = source.Substring(start, length);
            var protectedBytes = ProtectedLiteralBytes(old);
            string indent = FormattingBaseIndent(old, protectedBytes);
            string context = contextStart >= 0 ? source.Substring(contextStart, contextLength) : old;
            string unit =
                FormattingIndentUnit(context, ProtectedLiteralBytes(context), includeZero: false)
                ?? FormattingIndentUnit(source, ProtectedLiteralBytes(source))
                ?? "    ";
            int first = 0;
            foreach (var line in FormattingLines(old))
            {
                if (!string.IsNullOrWhiteSpace(old.Substring(line.start, line.end - line.start)))
                    break;
                first = line.next;
            }
            int last = old.Length;
            while (last > first && char.IsWhiteSpace(old[last - 1]) && !protectedBytes[last - 1])
                last--;
            return old.Substring(0, first) + FormatStructuralSnippet(snippet, indent, unit, LocalFormattingNewline(source, start)) + old.Substring(last);
        }

        private static int BeforeMethodTrivia(string source, int classStart, int position)
        {
            int originalPosition = position;
            bool block = false;
            while (position > classStart)
            {
                int end = position;
                if (end > 0 && source[end - 1] == '\n')
                    end--;
                if (end > 0 && source[end - 1] == '\r')
                    end--;
                int start = end;
                while (start > classStart && source[start - 1] != '\r' && source[start - 1] != '\n')
                    start--;
                string line = source.Substring(start, end - start).Trim();
                if (line.EndsWith("*/", StringComparison.Ordinal))
                    block = true;
                if (!block && !line.StartsWith("//", StringComparison.Ordinal))
                    break;
                int opening = line.IndexOf("/*", StringComparison.Ordinal);
                if (block && opening >= 0)
                {
                    if (!string.IsNullOrWhiteSpace(line.Substring(0, opening)))
                        return originalPosition;
                    block = false;
                }
                position = start;
            }
            return block ? originalPosition : position;
        }

        private static void QueueStructuralInsertion(
            List<(int start, int length, string text)> replacements,
            HashSet<int> structuralInsertions,
            int position,
            string text
        )
        {
            // Equal-position inserts keep the existing stable order (last edit appears first).
            // A line break needed against the original prefix belongs only to that first final snippet.
            for (int i = 0; i < replacements.Count; i++)
                if (structuralInsertions.Contains(i) && replacements[i].start == position)
                {
                    var previous = replacements[i];
                    int prefix =
                        previous.text.StartsWith("\r\n", StringComparison.Ordinal) ? 2
                        : previous.text.StartsWith("\r", StringComparison.Ordinal) || previous.text.StartsWith("\n", StringComparison.Ordinal) ? 1
                        : 0;
                    if (prefix > 0)
                        replacements[i] = (previous.start, previous.length, previous.text.Substring(prefix));
                }
            structuralInsertions.Add(replacements.Count);
            replacements.Add((position, 0, text));
        }

        private static string FormatMethodInsertion(string source, int classStart, int classLength, ref int insertAt, string snippet)
        {
            string classText = source.Substring(classStart, classLength);
            var protectedBytes = ProtectedLiteralBytes(classText);
            string classIndent = FormattingBaseIndent(classText, protectedBytes);
            string unit =
                FormattingIndentUnit(classText, protectedBytes, includeZero: false) ?? FormattingIndentUnit(source, ProtectedLiteralBytes(source)) ?? "    ";
            int lineStart = insertAt;
            while (lineStart > 0 && source[lineStart - 1] != '\r' && source[lineStart - 1] != '\n')
                lineStart--;
            if (source.Substring(lineStart, insertAt - lineStart).All(c => c == ' ' || c == '\t'))
                insertAt = lineStart;
            else
            {
                int end = insertAt;
                while (end < source.Length && (source[end] == ' ' || source[end] == '\t'))
                    end++;
                if (end + 1 < source.Length && source[end] == '/' && source[end + 1] == '/')
                    while (end < source.Length && source[end] != '\r' && source[end] != '\n')
                        end++;
                if (end < source.Length && (source[end] == '\r' || source[end] == '\n'))
                {
                    insertAt = end + 1;
                    if (source[end] == '\r' && insertAt < source.Length && source[insertAt] == '\n')
                        insertAt++;
                }
            }
            string newline = LocalFormattingNewline(source, insertAt);
            string text = FormatStructuralSnippet(snippet, classIndent + unit, unit, newline);
            string before = insertAt > 0 && source[insertAt - 1] != '\n' && source[insertAt - 1] != '\r' ? newline : string.Empty;
            return before + text + newline;
        }

        private static ErrorResponse ValidateReplacementSnippet(string snippet, bool replacesClass, string className)
        {
            if (!CheckBalancedDelimiters(snippet, out int line, out char expected))
                return new ErrorResponse(
                    "unbalanced_braces",
                    new
                    {
                        status = "unbalanced_braces",
                        line,
                        expected = expected.ToString(),
                    }
                );
            if (replacesClass && !ValidateClassSnippet(snippet, className, out string classError))
                return new ErrorResponse("invalid_edit", $"Replacement snippet invalid: {classError}");
            return null;
        }

        private static bool ValidateClassSnippet(string snippet, string expectedName, out string err)
        {
#if USE_ROSLYN
            try
            {
                var tree = CSharpSyntaxTree.ParseText(snippet);
                var root = tree.GetRoot();
                var classes = root.DescendantNodes().OfType<Microsoft.CodeAnalysis.CSharp.Syntax.ClassDeclarationSyntax>().ToList();
                if (classes.Count != 1)
                {
                    err = "snippet must contain exactly one class declaration";
                    return false;
                }
                // Optional: enforce expected name
                // if (classes[0].Identifier.ValueText != expectedName) { err = $"snippet declares '{classes[0].Identifier.ValueText}', expected '{expectedName}'"; return false; }
                err = null;
                return true;
            }
            catch (Exception ex)
            {
                err = ex.Message;
                return false;
            }
#else
            if (string.IsNullOrWhiteSpace(snippet) || !snippet.Contains("class "))
            {
                err = "no 'class' keyword found in snippet";
                return false;
            }
            err = null;
            return true;
#endif
        }

        private static bool TryComputeClassSpan(string source, string className, string ns, out int start, out int length, out string why)
        {
#if USE_ROSLYN
            try
            {
                var tree = CSharpSyntaxTree.ParseText(source);
                var root = tree.GetRoot();
                var classes = root.DescendantNodes()
                    .OfType<Microsoft.CodeAnalysis.CSharp.Syntax.ClassDeclarationSyntax>()
                    .Where(c => c.Identifier.ValueText == className);

                if (!string.IsNullOrEmpty(ns))
                {
                    classes = classes.Where(c =>
                        (c.FirstAncestorOrSelf<Microsoft.CodeAnalysis.CSharp.Syntax.NamespaceDeclarationSyntax>()?.Name?.ToString() ?? "") == ns
                        || (c.FirstAncestorOrSelf<Microsoft.CodeAnalysis.CSharp.Syntax.FileScopedNamespaceDeclarationSyntax>()?.Name?.ToString() ?? "") == ns
                    );
                }

                var list = classes.ToList();
                if (list.Count == 0)
                {
                    start = length = 0;
                    why = $"class '{className}' not found" + (ns != null ? $" in namespace '{ns}'" : "");
                    return false;
                }
                if (list.Count > 1)
                {
                    start = length = 0;
                    why = $"class '{className}' matched {list.Count} declarations (partial/nested?). Disambiguate.";
                    return false;
                }

                var cls = list[0];
                var span = cls.FullSpan; // includes attributes & leading trivia
                start = span.Start;
                length = span.Length;
                why = null;
                return true;
            }
            catch
            {
                // fall back below
            }
#endif
            return TryComputeClassSpanBalanced(source, className, ns, out start, out length, out why);
        }

        private static bool TryComputeClassSpanBalanced(string source, string className, string ns, out int start, out int length, out string why)
        {
            start = length = 0;
            why = null;
            var idx = IndexOfClassToken(source, className);
            if (idx < 0)
            {
                why = $"class '{className}' not found (balanced scan)";
                return false;
            }

            if (!string.IsNullOrEmpty(ns) && !AppearsWithinNamespaceHeader(source, idx, ns))
            {
                why = $"class '{className}' not under namespace '{ns}' (balanced scan)";
                return false;
            }

            // Include modifiers/attributes on the same line: back up to the start of line
            int lineStart = idx;
            while (lineStart > 0 && source[lineStart - 1] != '\n' && source[lineStart - 1] != '\r')
                lineStart--;

            int i = idx;
            while (i < source.Length && source[i] != '{')
                i++;
            if (i >= source.Length)
            {
                why = "no opening brace after class header";
                return false;
            }

            int depth = 0;
            int startSpan = lineStart;
            var lexer = new CSharpLexer(source, i);
            while (lexer.Advance(out char c))
            {
                if (lexer.InNonCode)
                    continue;
                if (c == '{')
                {
                    depth++;
                }
                else if (c == '}')
                {
                    depth--;
                    if (depth == 0)
                    {
                        start = startSpan;
                        length = (lexer.Position - 1 - startSpan) + 1;
                        return true;
                    }
                    if (depth < 0)
                    {
                        why = "brace underflow";
                        return false;
                    }
                }
            }
            why = "unterminated class block";
            return false;
        }

        private static bool TryComputeMethodSpan(
            string source,
            int classStart,
            int classLength,
            string methodName,
            string returnType,
            string parametersSignature,
            string attributesContains,
            out int start,
            out int length,
            out string why
        )
        {
            start = length = 0;
            why = null;
            int searchStart = classStart;
            int searchEnd = Math.Min(source.Length, classStart + classLength);

            // 1) Find the method header using a stricter regex (allows optional attributes above)
            string rtPattern = string.IsNullOrEmpty(returnType) ? @"[^\s]+" : Regex.Escape(returnType).Replace("\\ ", "\\s+");
            string namePattern = Regex.Escape(methodName);
            // If a parametersSignature is provided, it may include surrounding parentheses. Strip them so
            // we can safely embed the signature inside our own parenthesis group without duplicating.
            string paramsPattern;
            if (string.IsNullOrEmpty(parametersSignature))
            {
                paramsPattern = @"[\s\S]*?"; // permissive when not specified
            }
            else
            {
                string ps = parametersSignature.Trim();
                if (ps.StartsWith("(") && ps.EndsWith(")") && ps.Length >= 2)
                {
                    ps = ps.Substring(1, ps.Length - 2);
                }
                // Escape literal text of the signature
                paramsPattern = Regex.Escape(ps);
            }
            string pattern =
                @"(?m)^[\t ]*(?:\[[^\]]+\][\t ]*)*[\t ]*"
                + @"(?:(?:public|private|protected|internal|static|virtual|override|sealed|async|extern|unsafe|new|partial|readonly|volatile|event|abstract|ref|in|out)\s+)*"
                + rtPattern
                + @"[\t ]+"
                + namePattern
                + @"\s*(?:<[^>]+>)?\s*\("
                + paramsPattern
                + @"\)";

            string slice = source.Substring(searchStart, searchEnd - searchStart);
            var headerMatch = Regex.Match(slice, pattern, RegexOptions.Multiline, TimeSpan.FromSeconds(2));
            if (!headerMatch.Success)
            {
                why = $"method '{methodName}' header not found in class";
                return false;
            }
            int headerIndex = searchStart + headerMatch.Index;

            // Optional attributes filter: look upward from headerIndex for contiguous attribute lines
            if (!string.IsNullOrEmpty(attributesContains))
            {
                int attrScanStart = headerIndex;
                while (attrScanStart > searchStart)
                {
                    int prevNl = source.LastIndexOf('\n', attrScanStart - 1);
                    if (prevNl < 0 || prevNl < searchStart)
                        break;
                    string prevLine = source.Substring(prevNl + 1, attrScanStart - (prevNl + 1));
                    if (prevLine.TrimStart().StartsWith("["))
                    {
                        attrScanStart = prevNl;
                        continue;
                    }
                    break;
                }
                string attrBlock = source.Substring(attrScanStart, headerIndex - attrScanStart);
                if (attrBlock.IndexOf(attributesContains, StringComparison.Ordinal) < 0)
                {
                    why = $"method '{methodName}' found but attributes filter did not match";
                    return false;
                }
            }

            // backtrack to the very start of header/attributes to include in span
            int lineStart = headerIndex;
            while (lineStart > searchStart && source[lineStart - 1] != '\n' && source[lineStart - 1] != '\r')
                lineStart--;
            // If previous lines are attributes, include them
            int attrStart = lineStart;
            int probe = lineStart - 1;
            while (probe > searchStart)
            {
                // Skip past line-ending chars so LastIndexOf finds the *previous* newline
                while (probe > searchStart && (source[probe] == '\n' || source[probe] == '\r'))
                    probe--;
                if (probe <= searchStart)
                    break;
                int prevNl = source.LastIndexOf('\n', probe);
                if (prevNl < 0 || prevNl < searchStart)
                    break;
                string prev = source.Substring(prevNl + 1, attrStart - (prevNl + 1));
                if (prev.TrimStart().StartsWith("["))
                {
                    attrStart = prevNl + 1;
                    probe = prevNl - 1;
                }
                else
                    break;
            }

            // 2) Walk from the end of signature to detect body style ('{' or '=> ...;') and compute end
            // Find the '(' that belongs to the method signature, not attributes
            int nameTokenIdx = IndexOfTokenWithin(source, methodName, headerIndex, searchEnd);
            if (nameTokenIdx < 0)
            {
                why = $"method '{methodName}' token not found after header";
                return false;
            }
            int sigOpenParen = IndexOfTokenWithin(source, "(", nameTokenIdx, searchEnd);
            if (sigOpenParen < 0)
            {
                why = "method parameter list '(' not found";
                return false;
            }

            int i = sigOpenParen;
            int parenDepth = 0;
            var parenLexer = new CSharpLexer(source, i, searchEnd);
            while (parenLexer.Advance(out char pc))
            {
                if (parenLexer.InNonCode)
                    continue;
                if (pc == '(')
                    parenDepth++;
                if (pc == ')')
                {
                    parenDepth--;
                    if (parenDepth == 0)
                        break;
                }
            }
            i = parenLexer.Position;

            // After params: detect expression-bodied or block-bodied
            // Skip whitespace/comments
            for (; i < searchEnd; i++)
            {
                char c = source[i];
                char n = i + 1 < searchEnd ? source[i + 1] : '\0';
                if (char.IsWhiteSpace(c))
                    continue;
                if (c == '/' && n == '/')
                {
                    while (i < searchEnd && source[i] != '\n')
                        i++;
                    continue;
                }
                if (c == '/' && n == '*')
                {
                    i += 2;
                    while (i + 1 < searchEnd && !(source[i] == '*' && source[i + 1] == '/'))
                        i++;
                    i++;
                    continue;
                }
                break;
            }

            // Tolerate generic constraints between params and body: multiple 'where T : ...'
            for (; ; )
            {
                // Skip whitespace/comments before checking for 'where'
                for (; i < searchEnd; i++)
                {
                    char c = source[i];
                    char n = i + 1 < searchEnd ? source[i + 1] : '\0';
                    if (char.IsWhiteSpace(c))
                        continue;
                    if (c == '/' && n == '/')
                    {
                        while (i < searchEnd && source[i] != '\n')
                            i++;
                        continue;
                    }
                    if (c == '/' && n == '*')
                    {
                        i += 2;
                        while (i + 1 < searchEnd && !(source[i] == '*' && source[i + 1] == '/'))
                            i++;
                        i++;
                        continue;
                    }
                    break;
                }

                // Check word-boundary 'where'
                bool hasWhere = false;
                if (i + 5 <= searchEnd)
                {
                    hasWhere = source[i] == 'w' && source[i + 1] == 'h' && source[i + 2] == 'e' && source[i + 3] == 'r' && source[i + 4] == 'e';
                    if (hasWhere)
                    {
                        // Left boundary
                        if (i - 1 >= 0)
                        {
                            char lb = source[i - 1];
                            if (char.IsLetterOrDigit(lb) || lb == '_')
                                hasWhere = false;
                        }
                        // Right boundary
                        if (hasWhere && i + 5 < searchEnd)
                        {
                            char rb = source[i + 5];
                            if (char.IsLetterOrDigit(rb) || rb == '_')
                                hasWhere = false;
                        }
                    }
                }
                if (!hasWhere)
                    break;

                // Advance past the entire where-constraint clause until we hit '{' or '=>' or ';'
                i += 5; // past 'where'
                while (i < searchEnd)
                {
                    char c = source[i];
                    char n = i + 1 < searchEnd ? source[i + 1] : '\0';
                    if (c == '{' || c == ';' || (c == '=' && n == '>'))
                        break;
                    // Skip comments inline
                    if (c == '/' && n == '/')
                    {
                        while (i < searchEnd && source[i] != '\n')
                            i++;
                        continue;
                    }
                    if (c == '/' && n == '*')
                    {
                        i += 2;
                        while (i + 1 < searchEnd && !(source[i] == '*' && source[i + 1] == '/'))
                            i++;
                        i++;
                        continue;
                    }
                    i++;
                }
            }

            // Re-check for expression-bodied after constraints
            if (i < searchEnd - 1 && source[i] == '=' && source[i + 1] == '>')
            {
                // expression-bodied method: seek to terminating semicolon
                int j = i;
                bool done = false;
                while (j < searchEnd)
                {
                    char c = source[j];
                    if (c == ';')
                    {
                        done = true;
                        break;
                    }
                    j++;
                }
                if (!done)
                {
                    why = "unterminated expression-bodied method";
                    return false;
                }
                start = attrStart;
                length = (j - attrStart) + 1;
                return true;
            }

            if (i >= searchEnd || source[i] != '{')
            {
                why = "no opening brace after method signature";
                return false;
            }

            int depth = 0;
            int startSpan = attrStart;
            var bodyLexer = new CSharpLexer(source, i, searchEnd);
            while (bodyLexer.Advance(out char bc))
            {
                if (bodyLexer.InNonCode)
                    continue;
                if (bc == '{')
                    depth++;
                else if (bc == '}')
                {
                    depth--;
                    if (depth == 0)
                    {
                        start = startSpan;
                        length = (bodyLexer.Position - 1 - startSpan) + 1;
                        return true;
                    }
                    if (depth < 0)
                    {
                        why = "brace underflow in method";
                        return false;
                    }
                }
            }
            why = "unterminated method block";
            return false;
        }

        private static int IndexOfTokenWithin(string s, string token, int start, int end)
        {
            int idx = s.IndexOf(token, start, StringComparison.Ordinal);
            return (idx >= 0 && idx < end) ? idx : -1;
        }

        private static bool TryFindClassInsertionPoint(string source, int classStart, int classLength, string position, out int insertAt, out string why)
        {
            insertAt = 0;
            why = null;
            int searchStart = classStart;
            int searchEnd = Math.Min(source.Length, classStart + classLength);

            if (position == "start")
            {
                // find first '{' after class header, insert just after with a newline
                int i = IndexOfTokenWithin(source, "{", searchStart, searchEnd);
                if (i < 0)
                {
                    why = "could not find class opening brace";
                    return false;
                }
                insertAt = i + 1;
                return true;
            }
            else // end
            {
                // walk to matching closing brace of class and insert just before it
                int i = IndexOfTokenWithin(source, "{", searchStart, searchEnd);
                if (i < 0)
                {
                    why = "could not find class opening brace";
                    return false;
                }
                int depth = 0;
                var lexer = new CSharpLexer(source, i, searchEnd);
                while (lexer.Advance(out char c))
                {
                    if (lexer.InNonCode)
                        continue;
                    if (c == '{')
                        depth++;
                    else if (c == '}')
                    {
                        depth--;
                        if (depth == 0)
                        {
                            insertAt = lexer.Position - 1;
                            return true;
                        }
                        if (depth < 0)
                        {
                            why = "brace underflow while scanning class";
                            return false;
                        }
                    }
                }
                why = "could not find class closing brace";
                return false;
            }
        }

        /// <summary>
        /// Given multiple regex matches in C# source, pick the best one for
        /// anchor-based insertions.  For closing-brace patterns, uses actual
        /// brace-depth analysis to prefer the outermost (class-level) brace.
        /// Otherwise, returns the last match.
        /// </summary>
        private static Match FindBestAnchorMatch(MatchCollection matches, string text, string pattern)
        {
            if (matches.Count == 0)
                return null;
            if (matches.Count == 1)
                return matches[0];

            bool isClosingBracePattern = pattern.Contains("}") && (pattern.Contains("$") || pattern.EndsWith(@"\s*"));

            if (isClosingBracePattern)
            {
                // Compute brace depth at each match's '}' position using CSharpLexer
                Match best = null;
                int bestDepth = int.MaxValue;
                int bestPos = -1;

                // Single-pass: scan text and record depth at every '}' in real code
                var depthMap = new Dictionary<int, int>();
                int depth = 0;
                var lexer = new CSharpLexer(text);
                while (lexer.Advance(out char c))
                {
                    if (lexer.InNonCode)
                        continue;
                    if (c == '{')
                        depth++;
                    else if (c == '}')
                    {
                        depthMap[lexer.Position - 1] = depth;
                        depth = Math.Max(0, depth - 1);
                    }
                }

                foreach (Match m in matches)
                {
                    // Find the '}' within the match span
                    int bracePos = -1;
                    for (int k = m.Index; k < m.Index + m.Length && k < text.Length; k++)
                    {
                        if (text[k] == '}')
                        {
                            bracePos = k;
                            break;
                        }
                    }
                    if (bracePos < 0)
                        continue;
                    if (!depthMap.TryGetValue(bracePos, out int d))
                        continue; // in string/comment

                    // Prefer shallowest depth, then latest position
                    if (d < bestDepth || (d == bestDepth && bracePos > bestPos))
                    {
                        bestDepth = d;
                        bestPos = bracePos;
                        best = m;
                    }
                }
                return best ?? matches[matches.Count - 1];
            }

            // Default: prefer last match
            return matches[matches.Count - 1];
        }

        private static int IndexOfClassToken(string s, string className)
        {
            var pattern = "class " + className;
            int searchFrom = 0;
            while (searchFrom < s.Length)
            {
                int idx = s.IndexOf(pattern, searchFrom, StringComparison.Ordinal);
                if (idx < 0)
                    return -1;

                // Word boundary on left: char before "class" must not be letter/digit/_
                if (idx > 0)
                {
                    char left = s[idx - 1];
                    if (char.IsLetterOrDigit(left) || left == '_')
                    {
                        searchFrom = idx + 1;
                        continue;
                    }
                }

                // Word boundary on right: char after className must not be letter/digit/_
                int afterEnd = idx + pattern.Length;
                if (afterEnd < s.Length)
                {
                    char right = s[afterEnd];
                    if (char.IsLetterOrDigit(right) || right == '_')
                    {
                        searchFrom = idx + 1;
                        continue;
                    }
                }

                // Check that this position is not inside a string or comment
                var lexer = new CSharpLexer(s, 0, idx + 1);
                bool inNonCode = false;
                while (lexer.Advance(out _))
                {
                    inNonCode = lexer.InNonCode;
                }
                // After advancing to idx+1, check if the last character processed was in non-code
                if (inNonCode)
                {
                    searchFrom = idx + 1;
                    continue;
                }

                return idx;
            }
            return -1;
        }

        private static bool AppearsWithinNamespaceHeader(string s, int pos, string ns)
        {
            int from = Math.Max(0, pos - 2000);
            var slice = s.Substring(from, pos - from);
            return slice.Contains("namespace " + ns);
        }

        /// <summary>
        /// Generates basic C# script content based on name and type.
        /// </summary>
        private static string GenerateDefaultScriptContent(string name, string scriptType, string namespaceName)
        {
            string usingStatements = "using UnityEngine;\nusing System.Collections;\n";
            string classDeclaration;
            string body =
                "\n    // Use this for initialization\n    void Start() {\n\n    }\n\n    // Update is called once per frame\n    void Update() {\n\n    }\n";

            string baseClass = "";
            if (!string.IsNullOrEmpty(scriptType))
            {
                if (scriptType.Equals("MonoBehaviour", StringComparison.OrdinalIgnoreCase))
                    baseClass = " : MonoBehaviour";
                else if (scriptType.Equals("ScriptableObject", StringComparison.OrdinalIgnoreCase))
                {
                    baseClass = " : ScriptableObject";
                    body = ""; // ScriptableObjects don't usually need Start/Update
                }
                else if (
                    scriptType.Equals("Editor", StringComparison.OrdinalIgnoreCase) || scriptType.Equals("EditorWindow", StringComparison.OrdinalIgnoreCase)
                )
                {
                    usingStatements += "using UnityEditor;\n";
                    if (scriptType.Equals("Editor", StringComparison.OrdinalIgnoreCase))
                        baseClass = " : Editor";
                    else
                        baseClass = " : EditorWindow";
                    body = ""; // Editor scripts have different structures
                }
                // Add more types as needed
            }

            classDeclaration = $"public class {name}{baseClass}";

            string fullContent = $"{usingStatements}\n";
            bool useNamespace = !string.IsNullOrEmpty(namespaceName);

            if (useNamespace)
            {
                fullContent += $"namespace {namespaceName}\n{{\n";
                // Indent class and body if using namespace
                classDeclaration = "    " + classDeclaration;
                body = string.Join("\n", body.Split('\n').Select(line => "    " + line));
            }

            fullContent += $"{classDeclaration}\n{{\n{body}\n}}";

            if (useNamespace)
            {
                fullContent += "\n}"; // Close namespace
            }

            return fullContent.Trim() + "\n"; // Ensure a trailing newline
        }

        /// <summary>
        /// Gets the validation level from the GUI settings
        /// </summary>
        private static ValidationLevel GetValidationLevelFromGUI()
        {
            int savedLevel = EditorPrefs.GetInt(EditorPrefKeys.ValidationLevel, (int)ValidationLevel.Standard);
            return (ValidationLevel)Mathf.Clamp(savedLevel, 0, 3);
        }

        /// <summary>
        /// Validates C# script syntax using multiple validation layers.
        /// </summary>
        private static bool ValidateScriptSyntax(string contents)
        {
            return ValidateScriptSyntax(contents, ValidationLevel.Standard, out _);
        }

        /// <summary>
        /// Advanced syntax validation with detailed diagnostics and configurable strictness.
        /// </summary>
        private static bool ValidateScriptSyntax(string contents, ValidationLevel level, out string[] errors)
        {
            return ValidateScriptSyntax(contents, level, out errors, null);
        }

        private static bool ValidateScriptSyntax(string contents, ValidationLevel level, out string[] errors, ScriptValidationContext context)
        {
            var errorList = new System.Collections.Generic.List<string>();
            errors = null;
            if (context != null)
                context.Structural = true;

            if (string.IsNullOrEmpty(contents))
            {
                return true; // Empty content is valid
            }

            // Directive message text is not C# code; retain offsets while masking it.
            string structuralContents = context == null ? contents : ValidationDirectiveText(contents);
            // Basic structural validation: check balanced delimiters
            if (!CheckBalancedDelimiters(structuralContents, out int errLine, out char errExpected))
            {
                errorList.Add($"ERROR: Unbalanced delimiter at line {errLine} (expected '{errExpected}')");
                errors = errorList.ToArray();
                return false;
            }

            // Basic structural validation: check for duplicate method signatures
            CheckDuplicateMethodSignatures(structuralContents, errorList);

#if USE_ROSLYN
            // Advanced Roslyn-based validation: only run for Standard+; fail on Roslyn errors
            if (level >= ValidationLevel.Standard)
            {
                if (!ValidateScriptSyntaxRoslyn(contents, level, errorList, context))
                {
                    errors = errorList.ToArray();
                    return false;
                }
            }
#endif

            // Unity-specific validation
            if (level >= ValidationLevel.Standard)
            {
                if (context != null)
                    context.UnityRules = true;
                ValidateScriptSyntaxUnity(contents, errorList);
            }

            // Semantic analysis for common issues
            if (level >= ValidationLevel.Comprehensive)
            {
                ValidateSemanticRules(contents, errorList);
            }

#if USE_ROSLYN
            // Full semantic compilation validation for Strict level
            if (level == ValidationLevel.Strict)
            {
                if (!ValidateScriptSemantics(contents, errorList, context))
                {
                    errors = errorList.ToArray();
                    return false; // Strict level fails on any semantic errors
                }
            }
#endif

            errors = errorList.ToArray();
            return errorList.Count == 0 || (level != ValidationLevel.Strict && !errorList.Any(e => e.StartsWith("ERROR:")));
        }

        /// <summary>
        /// Validation strictness levels
        /// </summary>
        private enum ValidationLevel
        {
            Basic, // Only syntax errors
            Standard, // Syntax + Unity best practices
            Comprehensive, // All checks + semantic analysis
            Strict, // Treat all issues as errors
        }

        private sealed class ScriptValidationContext
        {
            internal string Path;
            internal bool Structural = false,
                Syntax = false,
                UnityRules = false,
                Semantics = false,
                Defines = false,
                References = false,
                PartialTypes = false;
            internal int SourceFileCount = 0;
            internal readonly List<string> Limitations = new();
            internal readonly Dictionary<string, JObject> CompilerDiagnostics = new();
#if USE_ROSLYN
            internal UnityEditor.Compilation.Assembly Assembly;
            internal CSharpParseOptions ParseOptions;
#endif
        }

        private static ScriptValidationContext CreateValidationContext(string path, ValidationLevel level)
        {
            var context = new ScriptValidationContext { Path = path };
            context.Limitations.Add(
                "This request does not run Unity project compilation. Use refresh_unity and its existing compilation job for native compiler results."
            );
            context.Limitations.Add(
                "Structural and Unity rules are lexical heuristics, not type or control-flow analysis; inactive preprocessor branches and interpolation expressions are not fully analyzed."
            );
#if USE_ROSLYN
            if (level >= ValidationLevel.Standard)
            {
                try
                {
                    string fullPath = ValidationFullPath(path);
                    context.Assembly = CompilationPipeline
                        .GetAssemblies()
                        .FirstOrDefault(a =>
                            (a.sourceFiles ?? Array.Empty<string>()).Any(source =>
                                string.Equals(ValidationFullPath(source), fullPath, StringComparison.Ordinal)
                            )
                        );
                    if (context.Assembly != null)
                        context.ParseOptions = CSharpParseOptions.Default.WithPreprocessorSymbols(context.Assembly.defines ?? Array.Empty<string>());
                    else
                        context.Limitations.Add("No owning imported assembly was found; project defines and partial source context are unavailable.");
                }
                catch (Exception ex)
                {
                    context.Limitations.Add($"Owning assembly context is unavailable: {ex.Message}");
                }
            }
#else
            context.Limitations.Add("USE_ROSLYN is not enabled; compiler syntax and semantic diagnostics are unavailable.");
#endif
            return context;
        }

        private static object ValidationMetadata(string requested, ValidationLevel level, ScriptValidationContext context)
        {
            string effective =
                !context.Syntax && !context.UnityRules ? "basic_structural"
                : context.Semantics ? (context.PartialTypes ? "strict_assembly_semantic" : "strict_single_source_semantic")
                : context.Syntax ? level.ToString().ToLowerInvariant() + "_roslyn_syntax"
                : level.ToString().ToLowerInvariant() + "_heuristic";
            if (!context.Semantics)
                context.Limitations.Add("References and cross-file partial types were not semantically validated.");
            return new
            {
                requestedLevel = requested,
                effectiveLevel = effective,
                engine = context.Syntax || context.Semantics ? "roslyn" : "heuristic",
                capabilities = new
                {
#if USE_ROSLYN
                    roslynAvailable = true,
#else
                    roslynAvailable = false,
#endif
                    structural = context.Structural,
                    syntax = context.Syntax,
                    unityHeuristics = context.UnityRules,
                    semanticCompilation = context.Semantics,
                    projectCompilation = false,
                },
                context = new
                {
                    scope = context.PartialTypes ? "project_assembly" : "single_source",
#if USE_ROSLYN
                    assembly = context.Assembly?.name,
#else
                    assembly = (string)null,
#endif
                    defines = context.Defines,
                    references = context.References,
                    partialTypes = context.PartialTypes,
                    sourceFileCount = context.SourceFileCount > 0 ? context.SourceFileCount : 1,
                },
                limitations = context.Limitations.ToArray(),
            };
        }

        private static JObject ValidationDiagnostic(string raw, ScriptValidationContext context)
        {
            if (context.CompilerDiagnostics.TryGetValue(raw, out var compilerDiagnostic))
                return compilerDiagnostic;
            var match = Regex.Match(
                raw,
                @"^(ERROR|WARNING|INFO): (.*?)(?: \[([A-Z]+\d+)\])?(?: \(Line (\d+)(?:, Column (\d+))?\))?$",
                RegexOptions.CultureInvariant | RegexOptions.Singleline,
                TimeSpan.FromMilliseconds(250)
            );
            int line = match.Success && int.TryParse(match.Groups[4].Value, out int l) ? l : 0;
            int column = match.Success && int.TryParse(match.Groups[5].Value, out int c) ? c : 0;
            if (line == 0)
            {
                var structuralLocation = Regex.Match(raw, @"\bat line (\d+)\b", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(250));
                if (structuralLocation.Success)
                    int.TryParse(structuralLocation.Groups[1].Value, out line);
            }
            return new JObject
            {
                ["line"] = line,
                ["col"] = column,
                ["severity"] = match.Success ? match.Groups[1].Value.ToLowerInvariant() : "info",
                ["message"] = match.Success ? match.Groups[2].Value : raw,
                ["id"] = match.Success && match.Groups[3].Success ? match.Groups[3].Value : null,
                ["file"] = context.Path,
            };
        }

#if USE_ROSLYN
        /// <summary>
        /// Cached compilation references for performance
        /// </summary>
        private static System.Collections.Generic.List<MetadataReference> _cachedReferences = null;
        private static DateTime _cacheTime = DateTime.MinValue;
        private static readonly TimeSpan CacheExpiry = TimeSpan.FromMinutes(5);

        /// <summary>
        /// Validates syntax using Roslyn compiler services
        /// </summary>
        private static bool ValidateScriptSyntaxRoslyn(
            string contents,
            ValidationLevel level,
            System.Collections.Generic.List<string> errors,
            ScriptValidationContext context = null
        )
        {
            try
            {
                var syntaxTree = CSharpSyntaxTree.ParseText(contents, context?.ParseOptions, context?.Path ?? "");
                var diagnostics = syntaxTree.GetDiagnostics();
                if (context != null)
                {
                    context.Syntax = true;
                    context.Defines = context.ParseOptions != null;
                    context.SourceFileCount = 1;
                }

                bool hasErrors = false;
                foreach (var diagnostic in diagnostics)
                {
                    string severity = diagnostic.Severity.ToString().ToUpperInvariant();
                    string message = $"{severity}: {diagnostic.GetMessage()}";

                    if (diagnostic.Severity == DiagnosticSeverity.Error)
                    {
                        hasErrors = true;
                    }

                    // Include warnings in comprehensive mode
                    if (level >= ValidationLevel.Standard || diagnostic.Severity == DiagnosticSeverity.Error) //Also use Standard for now
                    {
                        var location = diagnostic.Location.GetLineSpan();
                        if (location.IsValid)
                        {
                            message += $" (Line {location.StartLinePosition.Line + 1})";
                        }
                        if (context != null)
                            message = RecordCompilerDiagnostic(diagnostic, context);
                        errors.Add(message);
                    }
                }

                return !hasErrors;
            }
            catch (Exception ex)
            {
                errors.Add($"ERROR: Roslyn validation failed: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Validates semantics in a bounded owning-assembly context when available, otherwise a single-source approximation.
        /// </summary>
        private static bool ValidateScriptSemantics(string contents, System.Collections.Generic.List<string> errors, ScriptValidationContext context = null)
        {
            try
            {
                const int maxSources = 256;
                const int maxReferences = 512;
                const long maxSourceBytes = 8 * 1024 * 1024;
                if (context != null && System.Text.Encoding.UTF8.GetByteCount(contents) > maxSourceBytes)
                {
                    context?.Limitations.Add("Semantic validation skipped: the requested source exceeds 8 MiB.");
                    return true;
                }
                var trees = new List<SyntaxTree>();
                var references = new List<MetadataReference>();
                bool assemblyContext = context?.Assembly != null;
                if (assemblyContext)
                {
                    var assembly = context.Assembly;
                    string[] sources = assembly.sourceFiles ?? Array.Empty<string>();
                    string[] paths = assembly.allReferences ?? Array.Empty<string>();
                    if (sources.Length == 0 || sources.Length > maxSources || paths.Length > maxReferences)
                    {
                        context.Limitations.Add($"Assembly semantic validation skipped: input limit is {maxSources} sources and {maxReferences} references.");
                        return true;
                    }
                    long totalBytes = 0;
                    string requestPath = ValidationFullPath(context.Path);
                    foreach (string source in sources)
                    {
                        string sourcePath = ValidationFullPath(source);
                        bool requestedSource = string.Equals(sourcePath, requestPath, StringComparison.Ordinal);
                        long bytes = requestedSource ? System.Text.Encoding.UTF8.GetByteCount(contents) : new FileInfo(sourcePath).Length;
                        totalBytes += bytes;
                        if (totalBytes > maxSourceBytes)
                        {
                            context.Limitations.Add("Assembly semantic validation skipped: source context exceeds 8 MiB.");
                            return true;
                        }
                        string text = requestedSource ? contents : ReadScriptContents(sourcePath);
                        trees.Add(CSharpSyntaxTree.ParseText(text, context.ParseOptions, ValidationSourcePath(sourcePath)));
                    }
                    // No cache: assembly references may change after a define/asmdef edit.
                    foreach (string reference in paths.Distinct(StringComparer.Ordinal))
                    {
                        if (!File.Exists(reference))
                        {
                            context.Limitations.Add($"Assembly semantic validation skipped: a compiler reference is missing ({Path.GetFileName(reference)}).");
                            return true;
                        }
                        references.Add(MetadataReference.CreateFromFile(reference));
                    }
                    context.Limitations.Add(
                        "Owning assembly sources, defines and references are used, but Unity compiler options, response files, analyzers and source generators are not reproduced."
                    );
                }
                else
                {
                    // Preserve the existing single-source strict path for unimported scripts and edit validation.
                    references = GetCompilationReferences();
                    if (context != null && references.Count > maxReferences)
                    {
                        context?.Limitations.Add($"Single-source semantic validation skipped: reference context exceeds {maxReferences} references.");
                        return true;
                    }
                    trees.Add(CSharpSyntaxTree.ParseText(contents, context?.ParseOptions, context?.Path ?? ""));
                    context?.Limitations.Add(
                        "Single-source semantic validation uses cached loaded references; asmdef reference isolation, project defines and cross-file partial types are not established."
                    );
                }
                if (references == null || references.Count == 0)
                {
                    errors.Add("WARNING: Could not load compilation references for semantic validation");
                    context?.Limitations.Add("Compilation references are unavailable; semantic validation was skipped.");
                    return true; // Don't fail if we can't get references
                }

                // This extends the existing in-memory strict path; native compilation stays with CompilationJobTracker.
                var compilation = CSharpCompilation.Create(
                    context?.Assembly?.name ?? "TempValidation",
                    trees,
                    references,
                    new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary)
                );

                using var cancellation = context == null ? new CancellationTokenSource() : new CancellationTokenSource(TimeSpan.FromSeconds(5));
                var diagnostics = compilation.GetDiagnostics(cancellation.Token);
                if (context != null)
                {
                    context.Semantics = true;
                    context.References = true;
                    context.PartialTypes = assemblyContext;
                    context.SourceFileCount = trees.Count;
                }

                bool hasErrors = false;
                foreach (var diagnostic in diagnostics)
                {
                    if (diagnostic.Severity == DiagnosticSeverity.Error)
                    {
                        hasErrors = true;
                        var location = diagnostic.Location.GetLineSpan();
                        string locationInfo = location.IsValid
                            ? $" (Line {location.StartLinePosition.Line + 1}, Column {location.StartLinePosition.Character + 1})"
                            : "";

                        // Include diagnostic ID for better error identification
                        string diagnosticId = !string.IsNullOrEmpty(diagnostic.Id) ? $" [{diagnostic.Id}]" : "";
                        errors.Add(
                            context == null ? $"ERROR: {diagnostic.GetMessage()}{diagnosticId}{locationInfo}" : RecordCompilerDiagnostic(diagnostic, context)
                        );
                    }
                    else if (diagnostic.Severity == DiagnosticSeverity.Warning)
                    {
                        var location = diagnostic.Location.GetLineSpan();
                        string locationInfo = location.IsValid
                            ? $" (Line {location.StartLinePosition.Line + 1}, Column {location.StartLinePosition.Character + 1})"
                            : "";

                        string diagnosticId = !string.IsNullOrEmpty(diagnostic.Id) ? $" [{diagnostic.Id}]" : "";
                        errors.Add(
                            context == null ? $"WARNING: {diagnostic.GetMessage()}{diagnosticId}{locationInfo}" : RecordCompilerDiagnostic(diagnostic, context)
                        );
                    }
                }

                return !hasErrors;
            }
            catch (Exception ex)
            {
                context?.Limitations.Add($"Semantic validation could not complete: {ex.Message}");
                errors.Add($"ERROR: Semantic validation failed: {ex.Message}");
                return false;
            }
        }

        private static string ValidationSourcePath(string path)
        {
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).Replace('\\', '/') + "/";
            string full = Path.GetFullPath(path).Replace('\\', '/');
            return full.StartsWith(root, StringComparison.Ordinal) ? full.Substring(root.Length) : full;
        }

        private static string ValidationFullPath(string path) =>
            Path.GetFullPath(Path.IsPathRooted(path) ? path : Path.Combine(Application.dataPath, "..", path));

        private static string RecordCompilerDiagnostic(Diagnostic diagnostic, ScriptValidationContext context)
        {
            var span = diagnostic.Location.GetLineSpan();
            bool located = diagnostic.Location.IsInSource && span.IsValid;
            string file = located && !string.IsNullOrEmpty(span.Path) ? span.Path.Replace('\\', '/') : context.Path;
            int line = located ? span.StartLinePosition.Line + 1 : 0;
            int column = located ? span.StartLinePosition.Character + 1 : 0;
            string severity = diagnostic.Severity.ToString().ToLowerInvariant();
            string raw = $"{severity.ToUpperInvariant()}: {diagnostic.GetMessage()} [{diagnostic.Id}] (Line {line}, Column {column}) in {file}";
            context.CompilerDiagnostics[raw] = new JObject
            {
                ["line"] = line,
                ["col"] = column,
                ["severity"] = severity,
                ["message"] = diagnostic.GetMessage(),
                ["id"] = diagnostic.Id,
                ["file"] = file,
            };
            return raw;
        }

        /// <summary>
        /// Gets compilation references with caching for performance
        /// </summary>
        private static System.Collections.Generic.List<MetadataReference> GetCompilationReferences()
        {
            // Check cache validity
            if (_cachedReferences != null && DateTime.Now - _cacheTime < CacheExpiry)
            {
                return _cachedReferences;
            }

            try
            {
                var references = new System.Collections.Generic.List<MetadataReference>();

                // Core .NET assemblies
                references.Add(MetadataReference.CreateFromFile(typeof(object).Assembly.Location)); // mscorlib/System.Private.CoreLib
                references.Add(MetadataReference.CreateFromFile(typeof(System.Linq.Enumerable).Assembly.Location)); // System.Linq
                references.Add(MetadataReference.CreateFromFile(typeof(System.Collections.Generic.List<>).Assembly.Location)); // System.Collections

                // Unity assemblies
                try
                {
                    references.Add(MetadataReference.CreateFromFile(typeof(UnityEngine.Debug).Assembly.Location)); // UnityEngine
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Could not load UnityEngine assembly: {ex.Message}");
                }

#if UNITY_EDITOR
                try
                {
                    references.Add(MetadataReference.CreateFromFile(typeof(UnityEditor.Editor).Assembly.Location)); // UnityEditor
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Could not load UnityEditor assembly: {ex.Message}");
                }

                // Get Unity project assemblies
                try
                {
                    var assemblies = CompilationPipeline.GetAssemblies();
                    foreach (var assembly in assemblies)
                    {
                        if (File.Exists(assembly.outputPath))
                        {
                            references.Add(MetadataReference.CreateFromFile(assembly.outputPath));
                        }
                    }
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Could not load Unity project assemblies: {ex.Message}");
                }
#endif

                // Cache the results
                _cachedReferences = references;
                _cacheTime = DateTime.Now;

                return references;
            }
            catch (Exception ex)
            {
                McpLog.Error($"Failed to get compilation references: {ex.Message}");
                return new System.Collections.Generic.List<MetadataReference>();
            }
        }
#else
        private static bool ValidateScriptSyntaxRoslyn(string contents, ValidationLevel level, System.Collections.Generic.List<string> errors)
        {
            // Fallback when Roslyn is not available
            return true;
        }
#endif

        private static int CountTopLevelParams(string paramStr)
        {
            if (string.IsNullOrWhiteSpace(paramStr))
                return 0;
            int depth = 0,
                count = 1;
            foreach (char c in paramStr)
            {
                if (c == '<' || c == '(' || c == '[')
                    depth++;
                else if (c == '>' || c == ')' || c == ']')
                    depth--;
                else if (c == ',' && depth == 0)
                    count++;
            }
            return count;
        }

        /// <summary>
        /// Extracts only the type portions from a parameter list, dropping parameter names.
        /// e.g. "Dictionary&lt;string, int&gt; data, List&lt;Vector3&gt; points" → "Dictionary&lt;string, int&gt;, List&lt;Vector3&gt;"
        /// </summary>
        private static string ExtractParamTypes(string paramStr)
        {
            if (string.IsNullOrWhiteSpace(paramStr))
                return "";
            var types = new System.Text.StringBuilder();
            // Split at top-level commas (respecting <> depth)
            int depth = 0,
                start = 0;
            for (int i = 0; i <= paramStr.Length; i++)
            {
                char c = i < paramStr.Length ? paramStr[i] : ','; // sentinel
                if (c == '<' || c == '(' || c == '[')
                    depth++;
                else if (c == '>' || c == ')' || c == ']')
                    depth--;
                else if (c == ',' && depth == 0)
                {
                    string param = paramStr.Substring(start, i - start).Trim();
                    if (types.Length > 0)
                        types.Append(", ");
                    // The type is everything except the last token (the name).
                    // But if the last token ends with '>' or ']', it's all type (e.g. "List<int>").
                    // Find the last whitespace that is NOT inside <> brackets.
                    int lastSplit = -1;
                    int d2 = 0;
                    for (int j = 0; j < param.Length; j++)
                    {
                        char pc = param[j];
                        if (pc == '<' || pc == '(' || pc == '[')
                            d2++;
                        else if (pc == '>' || pc == ')' || pc == ']')
                            d2--;
                        else if (d2 == 0 && char.IsWhiteSpace(pc))
                            lastSplit = j;
                    }
                    types.Append(lastSplit > 0 ? param.Substring(0, lastSplit).Trim() : param);
                    start = i + 1;
                }
            }
            return Regex.Replace(types.ToString(), @"\s+", " ");
        }

        /// <summary>
        /// Validates Unity-specific coding rules and best practices
        /// </summary>
        private static void ValidateScriptSyntaxUnity(string contents, System.Collections.Generic.List<string> errors)
        {
            string code = ValidationCodeOnly(contents);
            string update = ValidationUpdateCode(code);
            foreach (Match call in Regex.Matches(update, @"\bGameObject\s*\.\s*Find\s*\(", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(250)))
                AddValidationWarning(contents, call.Index, "MCPUNITY001", "GameObject.Find in Update() can cause performance issues", errors);
            foreach (
                Match call in Regex.Matches(
                    update,
                    @"\bFindObjectOfType\s*(?:<[^;{}()]+>)?\s*\(",
                    RegexOptions.CultureInvariant,
                    TimeSpan.FromMilliseconds(250)
                )
            )
                AddValidationWarning(contents, call.Index, "MCPUNITY002", "FindObjectOfType in Update() can cause performance issues", errors);

            contents = code;

            // Check for proper MonoBehaviour usage
            if (contents.Contains(": MonoBehaviour") && !contents.Contains("using UnityEngine"))
            {
                errors.Add("WARNING: MonoBehaviour requires 'using UnityEngine;'");
            }

            // Check for SerializeField usage
            if (contents.Contains("[SerializeField]") && !contents.Contains("using UnityEngine"))
            {
                errors.Add("WARNING: SerializeField requires 'using UnityEngine;'");
            }

            // Check for proper coroutine usage
            if (contents.Contains("StartCoroutine") && !contents.Contains("IEnumerator"))
            {
                errors.Add("WARNING: StartCoroutine typically requires IEnumerator methods");
            }

            // Check for Update without FixedUpdate for physics
            if (
                contents.Contains("Rigidbody")
                && Regex.IsMatch(update, @"\b(?:AddForce|MovePosition|MoveRotation)\s*\(", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(250))
                && !Regex.IsMatch(contents, @"\bvoid\s+FixedUpdate\s*\(\s*\)", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(250))
            )
            {
                errors.Add("WARNING: Consider using FixedUpdate() for Rigidbody operations");
            }

            // Check for missing null checks on Unity objects
            if (contents.Contains("GetComponent<") && !contents.Contains("!= null"))
            {
                errors.Add("WARNING: Consider null checking GetComponent results");
            }

            // Check for proper event function signatures
            if (contents.Contains("void Start(") && !contents.Contains("void Start()"))
            {
                errors.Add("WARNING: Start() should not have parameters");
            }

            if (contents.Contains("void Update(") && !contents.Contains("void Update()"))
            {
                errors.Add("WARNING: Update() should not have parameters");
            }

            // A '+' and a literal anywhere in a script do not establish string concatenation in Update.
        }

        private static string ValidationCodeOnly(string contents)
        {
            contents = ValidationDirectiveText(contents);
            var chars = contents.ToCharArray();
            var lexer = new CSharpLexer(contents);
            while (true)
            {
                int start = lexer.Position;
                if (!lexer.Advance(out _))
                    break;
                if (lexer.InNonCode)
                    for (int i = start; i < lexer.Position && i < chars.Length; i++)
                        if (chars[i] != '\n' && chars[i] != '\r')
                            chars[i] = ' ';
            }
            return new string(chars);
        }

        private static string ValidationDirectiveText(string contents)
        {
            var chars = contents.ToCharArray();
            var lexer = new CSharpLexer(contents);
            while (lexer.Advance(out char c))
            {
                if (lexer.InNonCode || c != '#')
                    continue;
                int start = lexer.Position - 1;
                int before = start - 1;
                while (before >= 0 && contents[before] != '\n' && (contents[before] == ' ' || contents[before] == '\t' || contents[before] == '\r'))
                    before--;
                if (before >= 0 && contents[before] != '\n')
                    continue;
                lexer.SkipDirectiveLine();
                for (int i = start; i < lexer.Position; i++)
                    if (chars[i] != '\r')
                        chars[i] = ' ';
            }
            return new string(chars);
        }

        private static string ValidationUpdateCode(string code)
        {
            var chars = new string(' ', code.Length).ToCharArray();
            foreach (Match method in Regex.Matches(code, @"\bvoid\s+Update\s*\(\s*\)\s*(\{|=>)", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(250)))
            {
                int start = method.Index + method.Length;
                int end = start;
                if (method.Groups[1].Value == "=>")
                {
                    int semicolon = code.IndexOf(';', start);
                    end = semicolon < 0 ? code.Length : semicolon;
                }
                else
                {
                    int depth = 1;
                    while (end < code.Length && depth > 0)
                    {
                        if (code[end] == '{')
                            depth++;
                        else if (code[end] == '}')
                            depth--;
                        if (depth > 0)
                            end++;
                    }
                }
                for (int i = start; i < end; i++)
                    chars[i] = code[i];
            }
            return new string(chars);
        }

        private static void AddValidationWarning(string source, int index, string id, string message, List<string> errors)
        {
            int line = 1,
                lineStart = 0;
            for (int i = 0; i < index; i++)
                if (source[i] == '\n')
                {
                    line++;
                    lineStart = i + 1;
                }
            errors.Add($"WARNING: {message} [{id}] (Line {line}, Column {index - lineStart + 1})");
        }

        /// <summary>
        /// Checks for duplicate method signatures (same name + param types + scope).
        /// Catches corruption from anchor_replace overshoot and similar edit mistakes.
        /// Runs at Basic level since duplicates are structural errors, not style warnings.
        /// </summary>
        private static void CheckDuplicateMethodSignatures(string contents, System.Collections.Generic.List<string> errors)
        {
            // Step 1: Build a code-only view (comments/strings replaced with spaces)
            var codeChars = contents.ToCharArray();
            {
                var lexer = new CSharpLexer(contents);
                while (true)
                {
                    int startPos = lexer.Position;
                    if (!lexer.Advance(out _))
                        break;
                    if (lexer.InNonCode)
                    {
                        for (int i = startPos; i < lexer.Position && i < codeChars.Length; i++)
                            if (codeChars[i] != '\n')
                                codeChars[i] = ' ';
                    }
                }
            }
            var codeOnly = new string(codeChars);

            // Step 2: Build containing type name at each position via single pass
            var typePattern = new Regex(@"\b(?:class|struct|interface|record)\s+(\w+)", RegexOptions.CultureInvariant, TimeSpan.FromSeconds(2));
            // Map opening-brace position -> fully qualified type name
            var typeBraceMap = new System.Collections.Generic.Dictionary<int, string>();
            {
                var typeMatches = typePattern.Matches(codeOnly);
                // Pre-scan: for each type declaration, find its opening brace and record full name
                // We need to process in order and track nesting, so do a single forward pass
                var preStack = new System.Collections.Generic.List<(string name, int openDepth)>();
                int preBd = 0;
                int nextTm = 0;
                for (int i = 0; i < codeOnly.Length; i++)
                {
                    while (nextTm < typeMatches.Count && typeMatches[nextTm].Index == i)
                    {
                        int bp = codeOnly.IndexOf('{', typeMatches[nextTm].Index + typeMatches[nextTm].Length);
                        if (bp >= 0)
                        {
                            string tn = typeMatches[nextTm].Groups[1].Value;
                            string fn = preStack.Count > 0 ? preStack[preStack.Count - 1].name + "." + tn : tn;
                            typeBraceMap[bp] = fn;
                        }
                        nextTm++;
                    }
                    if (codeOnly[i] == '{')
                    {
                        if (typeBraceMap.TryGetValue(i, out string mappedType))
                            preStack.Add((mappedType, preBd));
                        preBd++;
                    }
                    else if (codeOnly[i] == '}')
                    {
                        preBd = Math.Max(0, preBd - 1);
                        if (preStack.Count > 0 && preStack[preStack.Count - 1].openDepth == preBd)
                            preStack.RemoveAt(preStack.Count - 1);
                    }
                }
            }
            // Second pass: build per-position containing type array
            var containingTypeArr = new string[codeOnly.Length];
            var braceDepthArr = new int[codeOnly.Length];
            var typeMemberDepthArr = new int[codeOnly.Length];
            {
                var stack = new System.Collections.Generic.List<(string name, int openDepth)>();
                int bd2 = 0;
                string current = "";
                for (int i = 0; i < codeOnly.Length; i++)
                {
                    containingTypeArr[i] = current;
                    braceDepthArr[i] = bd2;
                    typeMemberDepthArr[i] = stack.Count > 0 ? stack[stack.Count - 1].openDepth + 1 : -1;

                    if (codeOnly[i] == '{')
                    {
                        if (typeBraceMap.TryGetValue(i, out string tn))
                        {
                            stack.Add((tn, bd2));
                            current = tn;
                        }
                        bd2++;
                    }
                    else if (codeOnly[i] == '}')
                    {
                        bd2 = Math.Max(0, bd2 - 1);
                        if (stack.Count > 0 && stack[stack.Count - 1].openDepth == bd2)
                        {
                            stack.RemoveAt(stack.Count - 1);
                            current = stack.Count > 0 ? stack[stack.Count - 1].name : "";
                        }
                    }
                }
            }

            // Step 3: Match method signatures on code-only text (includes => for expression-bodied)
            var methodSigPattern = new Regex(
                @"(?:(?:public|private|protected|internal)\s+)?(?:(?:static|virtual|override|abstract|sealed|async|new)\s+)*(\S+)\s+(\w+)\s*\(([^)]*)\)\s*(?:where\s+\S+\s*:\s*\S+\s*)?(?:[{;]|=>)",
                RegexOptions.Multiline | RegexOptions.CultureInvariant,
                TimeSpan.FromSeconds(2)
            );
            var sigMatches = methodSigPattern.Matches(codeOnly);
            var seen = new System.Collections.Generic.Dictionary<string, int>(System.StringComparer.Ordinal);
            foreach (Match sm in sigMatches)
            {
                string returnType = sm.Groups[1].Value;
                string methodName = sm.Groups[2].Value;
                if (string.Equals(returnType, "new", StringComparison.Ordinal))
                    continue; // constructor invocation, not a method declaration
                // A punctuation "return type" means this match is a CALL, not a declaration:
                // the opening brace of a method body ("{ Foo();") or an expression-bodied
                // member ("=> Foo();"). Both sit at class-member depth, so the brace-depth
                // guard below cannot reject them.
                if (returnType.Length == 0)
                    continue;
                char returnTypeStart = returnType[0];
                if (!char.IsLetter(returnTypeStart) && returnTypeStart != '_' && returnTypeStart != '@' && returnTypeStart != '(')
                    continue;
                if (IsCSharpKeyword(methodName))
                    continue;
                int paramCount = CountTopLevelParams(sm.Groups[3].Value);
                string paramTypes = ExtractParamTypes(sm.Groups[3].Value);
                string containingType = containingTypeArr[sm.Index];
                if (string.IsNullOrEmpty(containingType))
                    continue;
                if (braceDepthArr[sm.Index] != typeMemberDepthArr[sm.Index])
                    continue;
                string key = $"{containingType}/{methodName}/{paramCount}/{paramTypes}";
                if (seen.TryGetValue(key, out _))
                    errors.Add(
                        $"ERROR: Duplicate method signature detected: '{methodName}' with {paramCount} parameter(s). This may indicate a corrupted edit."
                    );
                else
                    seen[key] = 1;
            }
        }

        private static readonly System.Collections.Generic.HashSet<string> CSharpKeywords = new System.Collections.Generic.HashSet<string>(
            System.StringComparer.Ordinal
        )
        {
            "if",
            "else",
            "for",
            "foreach",
            "while",
            "do",
            "switch",
            "case",
            "try",
            "catch",
            "finally",
            "throw",
            "return",
            "yield",
            "await",
            "lock",
            "using",
            "fixed",
            "checked",
            "unchecked",
            "typeof",
            "sizeof",
            "nameof",
            "default",
            "new",
            "stackalloc",
            "when",
            "in",
            "is",
            "as",
            "ref",
            "out",
            "params",
            "this",
            "base",
            "null",
            "true",
            "false",
            "get",
            "set",
            "var",
            "dynamic",
            "where",
            "from",
            "select",
            "group",
            "into",
            "orderby",
            "join",
            "let",
            "on",
            "equals",
            "by",
            "ascending",
            "descending",
        };

        private static bool IsCSharpKeyword(string name) => CSharpKeywords.Contains(name);

        /// <summary>
        /// Validates semantic rules and common coding issues
        /// </summary>
        private static void ValidateSemanticRules(string contents, System.Collections.Generic.List<string> errors)
        {
            // Check for potential memory leaks
            contents = ValidationCodeOnly(contents);
            string update = ValidationUpdateCode(contents);
            if (Regex.IsMatch(update, @"\bnew\s+", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(250)))
            {
                errors.Add("WARNING: Creating objects in Update() may cause memory issues");
            }

            // Check for magic numbers
            var magicNumberPattern = new Regex(@"\b\d+\.?\d*f?\b(?!\s*[;})\]])", RegexOptions.CultureInvariant, TimeSpan.FromSeconds(2));
            var matches = magicNumberPattern.Matches(contents);
            if (matches.Count > 5)
            {
                errors.Add("WARNING: Consider using named constants instead of magic numbers");
            }

            // Check for long methods (simple line count check)
            var methodPattern = new Regex(
                @"(public|private|protected|internal)?\s*(static)?\s*\w+\s+\w+\s*\([^)]*\)\s*{",
                RegexOptions.CultureInvariant,
                TimeSpan.FromSeconds(2)
            );
            var methodMatches = methodPattern.Matches(contents);
            foreach (Match match in methodMatches)
            {
                int startIndex = match.Index;
                int braceCount = 0;
                int lineCount = 0;
                bool inMethod = false;

                for (int i = startIndex; i < contents.Length; i++)
                {
                    if (contents[i] == '{')
                    {
                        braceCount++;
                        inMethod = true;
                    }
                    else if (contents[i] == '}')
                    {
                        braceCount--;
                        if (braceCount == 0 && inMethod)
                            break;
                    }
                    else if (contents[i] == '\n' && inMethod)
                    {
                        lineCount++;
                    }
                }

                if (lineCount > 50)
                {
                    errors.Add("WARNING: Method is very long, consider breaking it into smaller methods");
                    break; // Only report once
                }
            }

            // Check for proper exception handling
            if (contents.Contains("catch") && contents.Contains("catch()"))
            {
                errors.Add("WARNING: Empty catch blocks should be avoided");
            }

            // Check for proper async/await usage
            if (contents.Contains("async ") && !contents.Contains("await"))
            {
                errors.Add("WARNING: Async method should contain await or return Task");
            }
        }

        //TODO: A easier way for users to update incorrect scripts (now duplicated with the updateScript method and need to also update server side, put aside for now)
        /// <summary>
        /// Public method to validate script syntax with configurable validation level
        /// Returns detailed validation results including errors and warnings
        /// </summary>
        // public static object ValidateScript(JObject @params)
        // {
        //     string contents = @params["contents"]?.ToString();
        //     string validationLevel = @params["validationLevel"]?.ToString() ?? "standard";

        //     if (string.IsNullOrEmpty(contents))
        //     {
        //         return new ErrorResponse("Contents parameter is required for validation.");
        //     }

        //     // Parse validation level
        //     ValidationLevel level = ValidationLevel.Standard;
        //     switch (validationLevel.ToLower())
        //     {
        //         case "basic": level = ValidationLevel.Basic; break;
        //         case "standard": level = ValidationLevel.Standard; break;
        //         case "comprehensive": level = ValidationLevel.Comprehensive; break;
        //         case "strict": level = ValidationLevel.Strict; break;
        //         default:
        //             return new ErrorResponse($"Invalid validation level: '{validationLevel}'. Valid levels are: basic, standard, comprehensive, strict.");
        //     }

        //     // Perform validation
        //     bool isValid = ValidateScriptSyntax(contents, level, out string[] validationErrors);

        //     var errors = validationErrors?.Where(e => e.StartsWith("ERROR:")).ToArray() ?? new string[0];
        //     var warnings = validationErrors?.Where(e => e.StartsWith("WARNING:")).ToArray() ?? new string[0];

        //     var result = new
        //     {
        //         isValid = isValid,
        //         validationLevel = validationLevel,
        //         errorCount = errors.Length,
        //         warningCount = warnings.Length,
        //         errors = errors,
        //         warnings = warnings,
        //         summary = isValid
        //             ? (warnings.Length > 0 ? $"Validation passed with {warnings.Length} warnings" : "Validation passed with no issues")
        //             : $"Validation failed with {errors.Length} errors and {warnings.Length} warnings"
        //     };

        //     if (isValid)
        //     {
        //         return new SuccessResponse("Script validation completed successfully.", result);
        //     }
        //     else
        //     {
        //         return new ErrorResponse("Script validation failed.", result);
        //     }
        // }
    }

    // Debounced refresh/compile scheduler to coalesce bursts of edits
    static class RefreshDebounce
    {
        private static int _pending;
        private static readonly object _lock = new object();
        private static readonly HashSet<string> _paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        // The timestamp of the most recent schedule request.
        private static DateTime _lastRequest;

        // Guard to ensure we only have a single ticking callback running.
        private static bool _scheduled;

        public static void Schedule(string relPath, TimeSpan window)
        {
            // Record that work is pending and track the path in a threadsafe way.
            Interlocked.Exchange(ref _pending, 1);
            lock (_lock)
            {
                _paths.Add(relPath);
                _lastRequest = DateTime.UtcNow;

                // If a debounce timer is already scheduled it will pick up the new request.
                if (_scheduled)
                    return;

                _scheduled = true;
            }

            // Kick off a ticking callback that waits until the window has elapsed
            // from the last request before performing the refresh.
            EditorApplication.delayCall += () => Tick(window);
            // Nudge the editor loop so ticks run even if the window is unfocused
            EditorApplication.QueuePlayerLoopUpdate();
        }

        private static void Tick(TimeSpan window)
        {
            bool ready;
            lock (_lock)
            {
                // Only proceed once the debounce window has fully elapsed.
                ready = (DateTime.UtcNow - _lastRequest) >= window;
                if (ready)
                {
                    _scheduled = false;
                }
            }

            if (!ready)
            {
                // Window has not yet elapsed; check again on the next editor tick.
                EditorApplication.delayCall += () => Tick(window);
                return;
            }

            if (Interlocked.Exchange(ref _pending, 0) == 1)
            {
                string[] toImport;
                lock (_lock)
                {
                    toImport = _paths.ToArray();
                    _paths.Clear();
                }
                foreach (var p in toImport)
                {
                    var sp = ManageScriptRefreshHelpers.SanitizeAssetsPath(p);
                    AssetDatabase.ImportAsset(sp, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
                }
#if UNITY_EDITOR
                UnityEditor.Compilation.CompilationPipeline.RequestScriptCompilation();
#endif
                // Fallback if needed:
                // AssetDatabase.Refresh();
            }
        }
    }

    static class ManageScriptRefreshHelpers
    {
        public static string SanitizeAssetsPath(string p)
        {
            if (string.IsNullOrEmpty(p))
                return p;
            p = AssetPathUtility.NormalizeSeparators(p).Trim();
            if (p.StartsWith("mcpforunity://path/", StringComparison.OrdinalIgnoreCase))
                p = p.Substring("mcpforunity://path/".Length);
            while (p.StartsWith("Assets/Assets/", StringComparison.OrdinalIgnoreCase))
                p = p.Substring("Assets/".Length);
            if (!p.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                p = "Assets/" + p.TrimStart('/');
            return p;
        }

        public static void ScheduleScriptRefresh(string relPath)
        {
            var sp = SanitizeAssetsPath(relPath);
            RefreshDebounce.Schedule(sp, TimeSpan.FromMilliseconds(200));
        }

        public static void ImportAndRequestCompile(string relPath, bool synchronous = true)
        {
            var sp = SanitizeAssetsPath(relPath);
            var opts = ImportAssetOptions.ForceUpdate;
            if (synchronous)
                opts |= ImportAssetOptions.ForceSynchronousImport;
            AssetDatabase.ImportAsset(sp, opts);
#if UNITY_EDITOR
            UnityEditor.Compilation.CompilationPipeline.RequestScriptCompilation();
#endif
        }
    }
}
