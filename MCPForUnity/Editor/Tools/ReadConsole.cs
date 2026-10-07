using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers; // For Response class
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Handles reading and clearing Unity Editor console log entries.
    /// Uses reflection to access internal LogEntry methods/properties.
    /// </summary>
    [McpForUnityTool("read_console", AutoRegister = false)]
    public static class ReadConsole
    {
        // (Calibration removed)

        // Reflection members for accessing internal LogEntry data
        // private static MethodInfo _getEntriesMethod; // Removed as it's unused and fails reflection
        private static MethodInfo _startGettingEntriesMethod;
        private static MethodInfo _endGettingEntriesMethod; // Renamed from _stopGettingEntriesMethod, trying End...
        private static MethodInfo _clearMethod;
        private static MethodInfo _getCountMethod;
        private static MethodInfo _getEntryMethod;
        private static FieldInfo _modeField;
        private static FieldInfo _messageField;
        private static FieldInfo _fileField;
        private static FieldInfo _lineField;
        private static FieldInfo _callstackTextStartField;

        // Optional reflection members: used to neutralize the Console window's own filters
        // while reading. Absent members degrade to the previous (filter-inheriting) behavior.
        private static PropertyInfo _consoleFlagsProperty;
        private static MethodInfo _setFilteringTextMethod;
        private static MethodInfo _getFilteringTextMethod;

        // Severity bits from the internal UnityEditor.ConsoleWindow.ConsoleFlags enum.
        private const int ConsoleFlagLogLevelLog = 1 << 7;
        private const int ConsoleFlagLogLevelWarning = 1 << 8;
        private const int ConsoleFlagLogLevelError = 1 << 9;
        private const int ConsoleFlagLogLevelMask = ConsoleFlagLogLevelLog | ConsoleFlagLogLevelWarning | ConsoleFlagLogLevelError;

        // Static constructor for reflection setup
        static ReadConsole()
        {
            try
            {
                Type logEntriesType = typeof(EditorApplication).Assembly.GetType("UnityEditor.LogEntries");
                if (logEntriesType == null)
                    throw new Exception("Could not find internal type UnityEditor.LogEntries");

                // Include NonPublic binding flags as internal APIs might change accessibility
                BindingFlags staticFlags = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
                BindingFlags instanceFlags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

                _startGettingEntriesMethod = logEntriesType.GetMethod("StartGettingEntries", staticFlags);
                if (_startGettingEntriesMethod == null)
                    throw new Exception("Failed to reflect LogEntries.StartGettingEntries");

                // Try reflecting EndGettingEntries based on warning message
                _endGettingEntriesMethod = logEntriesType.GetMethod("EndGettingEntries", staticFlags);
                if (_endGettingEntriesMethod == null)
                    throw new Exception("Failed to reflect LogEntries.EndGettingEntries");

                _clearMethod = logEntriesType.GetMethod("Clear", staticFlags);
                if (_clearMethod == null)
                    throw new Exception("Failed to reflect LogEntries.Clear");

                _getCountMethod = logEntriesType.GetMethod("GetCount", staticFlags);
                if (_getCountMethod == null)
                    throw new Exception("Failed to reflect LogEntries.GetCount");

                _getEntryMethod = logEntriesType.GetMethod("GetEntryInternal", staticFlags);
                if (_getEntryMethod == null)
                    throw new Exception("Failed to reflect LogEntries.GetEntryInternal");

                Type logEntryType = typeof(EditorApplication).Assembly.GetType("UnityEditor.LogEntry");
                if (logEntryType == null)
                    throw new Exception("Could not find internal type UnityEditor.LogEntry");

                _modeField = logEntryType.GetField("mode", instanceFlags);
                if (_modeField == null)
                    throw new Exception("Failed to reflect LogEntry.mode");

                _messageField = logEntryType.GetField("message", instanceFlags);
                if (_messageField == null)
                    throw new Exception("Failed to reflect LogEntry.message");

                _fileField = logEntryType.GetField("file", instanceFlags);
                if (_fileField == null)
                    throw new Exception("Failed to reflect LogEntry.file");

                _lineField = logEntryType.GetField("line", instanceFlags);
                if (_lineField == null)
                    throw new Exception("Failed to reflect LogEntry.line");

                // Unity supplies the boundary in UTF-16 string indices. Keep this optional
                // so older or renamed layouts can still use the stack-frame fallback.
                _callstackTextStartField = logEntryType.GetField("callstackTextStartUTF16", instanceFlags);

                // Console window UI state. Present on every Unity version this package
                // supports, but reflected optionally so a future rename degrades to the old
                // behavior rather than disabling console reads outright.
                _consoleFlagsProperty = logEntriesType.GetProperty("consoleFlags", staticFlags);
                _setFilteringTextMethod = logEntriesType.GetMethod("SetFilteringText", staticFlags);
                _getFilteringTextMethod = logEntriesType.GetMethod("GetFilteringText", staticFlags);

                // (Calibration removed)
            }
            catch (Exception e)
            {
                McpLog.Error(
                    $"[ReadConsole] Static Initialization Failed: Could not setup reflection for LogEntries/LogEntry. Console reading/clearing will likely fail. Specific Error: {e.Message}"
                );
                // Set members to null to prevent NullReferenceExceptions later, HandleCommand should check this.
                _startGettingEntriesMethod = _endGettingEntriesMethod = _clearMethod = _getCountMethod = _getEntryMethod = null;
                _modeField = _messageField = _fileField = _lineField = _callstackTextStartField = null;
                _consoleFlagsProperty = null;
                _setFilteringTextMethod = _getFilteringTextMethod = null;
            }
        }

        // --- Main Handler ---

        public static object HandleCommand(JObject @params)
        {
            // Check if ALL required reflection members were successfully initialized.
            if (
                _startGettingEntriesMethod == null
                || _endGettingEntriesMethod == null
                || _clearMethod == null
                || _getCountMethod == null
                || _getEntryMethod == null
                || _modeField == null
                || _messageField == null
                || _fileField == null
                || _lineField == null
            )
            {
                // Log the error here as well for easier debugging in Unity Console
                McpLog.Error(
                    "[ReadConsole] HandleCommand called but reflection members are not initialized. Static constructor might have failed silently or there's an issue."
                );
                return new ErrorResponse("ReadConsole handler failed to initialize due to reflection errors. Cannot access console logs.");
            }

            if (@params == null)
            {
                return new ErrorResponse("Parameters cannot be null.");
            }

            var p = new ToolParams(@params);
            string action = p.Get("action", "get").ToLower();

            try
            {
                if (action == "clear")
                {
                    if (p.GetRaw("fields") != null && p.GetRaw("fields").Type != JTokenType.Null)
                        return new ErrorResponse("'fields' is supported only for get with json or detailed format.");
                    return ClearConsole();
                }
                else if (action == "get")
                {
                    // Extract parameters for 'get'
                    var types = (p.GetRaw("types") as JArray)?.Select(t => t.ToString().ToLower()).ToList() ?? new List<string> { "error", "warning" };
                    int? count = p.GetInt("count");
                    int? pageSize = p.GetInt("pageSize");
                    int? cursor = p.GetInt("cursor");
                    string filterText = p.Get("filterText");
                    string format = p.Get("format", "plain").ToLower();
                    bool includeStacktrace = p.GetBool("includeStacktrace", false);
                    JToken includeMcpLogsToken = p.GetRaw("includeMcpLogs");
                    if (includeMcpLogsToken != null && includeMcpLogsToken.Type != JTokenType.Null && includeMcpLogsToken.Type != JTokenType.Boolean)
                        return new ErrorResponse("'includeMcpLogs' must be a boolean.");
                    bool includeMcpLogs = p.GetBool("includeMcpLogs", false);
                    HashSet<string> fields = null;
                    JToken fieldsToken = p.GetRaw("fields");
                    if (fieldsToken != null && fieldsToken.Type != JTokenType.Null)
                    {
                        if (format != "json" && format != "detailed")
                            return new ErrorResponse("'fields' is supported only for get with json or detailed format.");
                        if (fieldsToken.Type == JTokenType.String)
                        {
                            try
                            {
                                fieldsToken = JArray.Parse(fieldsToken.Value<string>());
                            }
                            catch (Newtonsoft.Json.JsonException)
                            {
                                return new ErrorResponse("'fields' must be a list of type, message, file, line, or stackTrace.");
                            }
                        }
                        if (!(fieldsToken is JArray fieldArray) || fieldArray.Count < 2 || fieldArray.Count > 5)
                            return new ErrorResponse("'fields' must be unique and include both type and message.");
                        fields = new HashSet<string>(StringComparer.Ordinal);
                        foreach (JToken field in fieldArray)
                        {
                            if (field.Type != JTokenType.String)
                                return new ErrorResponse("'fields' entries must be strings.");
                            string name = field.Value<string>();
                            if (name != "type" && name != "message" && name != "file" && name != "line" && name != "stackTrace")
                                return new ErrorResponse("'fields' contains an unsupported field.");
                            if (!fields.Add(name))
                                return new ErrorResponse("'fields' entries must be unique.");
                        }
                        if (!fields.Contains("type") || !fields.Contains("message"))
                            return new ErrorResponse("'fields' must include both type and message.");
                        if (fields.Contains("stackTrace") && !includeStacktrace)
                            return new ErrorResponse("The stackTrace field requires includeStacktrace=true.");
                    }

                    if (types.Contains("all"))
                    {
                        types = new List<string> { "error", "warning", "log" }; // Expand 'all'
                    }

                    return GetConsoleEntries(types, count, pageSize, cursor, filterText, format, includeStacktrace, fields, includeMcpLogs);
                }
                else
                {
                    return new ErrorResponse($"Unknown action: '{action}'. Valid actions are 'get' or 'clear'.");
                }
            }
            catch (Exception e)
            {
                McpLog.Error($"[ReadConsole] Action '{action}' failed: {e}");
                return new ErrorResponse($"Internal error processing action '{action}': {e.Message}");
            }
        }

        // --- Action Implementations ---

        /// <summary>
        /// Forces the Console window's Log/Warning/Error severity bits on so that
        /// StartGettingEntries reports every entry regardless of the toolbar toggles.
        /// </summary>
        /// <param name="savedConsoleFlags">The flags as they were, for restoration.</param>
        /// <returns>True when the flags were changed and must be restored.</returns>
        private static bool TryForceLogLevelFlags(out int savedConsoleFlags)
        {
            savedConsoleFlags = 0;
            if (_consoleFlagsProperty == null)
            {
                return false;
            }

            try
            {
                savedConsoleFlags = (int)_consoleFlagsProperty.GetValue(null);
                int forcedFlags = savedConsoleFlags | ConsoleFlagLogLevelMask;
                if (forcedFlags == savedConsoleFlags)
                {
                    return false; // Nothing is hidden; leave the property untouched.
                }

                _consoleFlagsProperty.SetValue(null, forcedFlags);
                return true;
            }
            catch (Exception e)
            {
                McpLog.Warn($"[ReadConsole] Could not override console severity flags; entries hidden by the Console window may be missing: {e.Message}");
                return false;
            }
        }

        private static void RestoreConsoleFlags(int savedConsoleFlags)
        {
            try
            {
                _consoleFlagsProperty.SetValue(null, savedConsoleFlags);
            }
            catch (Exception e)
            {
                McpLog.Error($"[ReadConsole] Failed to restore console severity flags: {e}");
            }
        }

        /// <summary>
        /// Clears the Console window's search query for the duration of a read. Only clears it
        /// when it can also be read back, so a user's search is never silently discarded.
        /// </summary>
        /// <param name="savedFilteringText">The query as it was, for restoration.</param>
        /// <returns>True when the query was cleared and must be restored.</returns>
        private static bool TryClearFilteringText(out string savedFilteringText)
        {
            savedFilteringText = null;
            if (_setFilteringTextMethod == null || _getFilteringTextMethod == null)
            {
                return false;
            }

            try
            {
                savedFilteringText = _getFilteringTextMethod.Invoke(null, null) as string;
                if (string.IsNullOrEmpty(savedFilteringText))
                {
                    return false; // No search query active; nothing to neutralize.
                }

                _setFilteringTextMethod.Invoke(null, new object[] { string.Empty });
                return true;
            }
            catch (Exception e)
            {
                McpLog.Warn($"[ReadConsole] Could not clear the console search filter; entries hidden by it may be missing: {e.Message}");
                return false;
            }
        }

        private static void RestoreFilteringText(string savedFilteringText)
        {
            try
            {
                _setFilteringTextMethod.Invoke(null, new object[] { savedFilteringText });
            }
            catch (Exception e)
            {
                McpLog.Error($"[ReadConsole] Failed to restore the console search filter: {e}");
            }
        }

        private static object ClearConsole()
        {
            try
            {
                _clearMethod.Invoke(null, null); // Static method, no instance, no parameters
                return new SuccessResponse("Console cleared successfully.");
            }
            catch (Exception e)
            {
                McpLog.Error($"[ReadConsole] Failed to clear console: {e}");
                return new ErrorResponse($"Failed to clear console: {e.Message}");
            }
        }

        /// <summary>
        /// Retrieves console log entries with optional filtering and paging.
        /// </summary>
        /// <param name="types">Log types to include (e.g., "error", "warning", "log").</param>
        /// <param name="count">Maximum entries to return in non-paging mode. Ignored when paging is active.</param>
        /// <param name="pageSize">Number of entries per page. Defaults to 50 when omitted.</param>
        /// <param name="cursor">Starting index for paging (0-based). Defaults to 0.</param>
        /// <param name="filterText">Optional text filter (case-insensitive substring match).</param>
        /// <param name="format">Output format: "plain", "detailed", or "json".</param>
        /// <param name="includeStacktrace">Whether to include stack traces in the output.</param>
        /// <returns>A success response with entries, or an error response.</returns>
        private static object GetConsoleEntries(
            List<string> types,
            int? count,
            int? pageSize,
            int? cursor,
            string filterText,
            string format,
            bool includeStacktrace,
            HashSet<string> fields = null,
            bool includeMcpLogs = false
        )
        {
            List<object> formattedEntries = new List<object>();
            int retrievedCount = 0;
            int totalMatches = 0;
            bool usePaging = pageSize.HasValue || cursor.HasValue;
            if (!usePaging && count.HasValue && count.Value <= 0)
                return new ErrorResponse("'count' must be greater than zero for non-paging console reads.");
            // pageSize defaults to 50 when omitted; count is the overall non-paging limit only
            int resolvedPageSize = Mathf.Clamp(pageSize ?? 50, 1, 500);
            int resolvedCursor = Mathf.Max(0, cursor ?? 0);
            long pageEndExclusive = (long)resolvedCursor + resolvedPageSize;

            // LogEntries filtering state is global and shared with the Console window, so a
            // severity toggle switched off or a leftover search query in the toolbar silently
            // starves this tool of entries. Neutralize both for the duration of the read; the
            // tool applies its own 'types' and 'filterText' arguments instead.
            int savedConsoleFlags = 0;
            bool consoleFlagsOverridden = false;
            string savedFilteringText = null;
            bool filteringTextOverridden = false;
            bool entriesStarted = false;

            try
            {
                consoleFlagsOverridden = TryForceLogLevelFlags(out savedConsoleFlags);
                filteringTextOverridden = TryClearFilteringText(out savedFilteringText);

                // LogEntries requires calling Start/Stop around GetEntries/GetEntryInternal.
                // StartGettingEntries() returns the entry count — use it instead of GetCount()
                // which may return stale values within an active iteration session.
                object startResult = _startGettingEntriesMethod.Invoke(null, null);
                entriesStarted = true;
                int totalEntries = startResult is int startCount ? startCount : (int)_getCountMethod.Invoke(null, null);
                // Create instance to pass to GetEntryInternal - Ensure the type is correct
                Type logEntryType = typeof(EditorApplication).Assembly.GetType("UnityEditor.LogEntry");
                if (logEntryType == null)
                    throw new Exception("Could not find internal type UnityEditor.LogEntry during GetConsoleEntries.");
                object logEntryInstance = Activator.CreateInstance(logEntryType);

                for (int i = 0; i < totalEntries; i++)
                {
                    // Get the entry data into our instance using reflection
                    object entryResult = _getEntryMethod.Invoke(null, new object[] { i, logEntryInstance });
                    if (entryResult is bool entryAvailable && !entryAvailable)
                        continue;

                    // Extract data using reflection
                    int mode = (int)_modeField.GetValue(logEntryInstance);
                    string message = (string)_messageField.GetValue(logEntryInstance);
                    if (string.IsNullOrEmpty(message))
                    {
                        continue; // Skip empty messages
                    }

                    // (Calibration removed)

                    // --- Filtering ---
                    // Severity comes from Unity, not words or API names in the user's message.
                    LogType unityType = GetLogTypeFromMode(mode);

                    bool want;
                    // Treat Exception/Assert as errors for filtering convenience
                    if (unityType == LogType.Exception)
                    {
                        want = types.Contains("error") || types.Contains("exception");
                    }
                    else if (unityType == LogType.Assert)
                    {
                        want = types.Contains("error") || types.Contains("assert");
                    }
                    else
                    {
                        want = types.Contains(unityType.ToString().ToLowerInvariant());
                    }

                    if (!want)
                        continue;

                    // Only an anchored logger prefix identifies package output. A user's
                    // message or stack trace mentioning the package remains visible.
                    if (!includeMcpLogs && IsMcpLogMessage(message))
                        continue;

                    // Filter by text (case-insensitive)
                    if (!string.IsNullOrEmpty(filterText) && message.IndexOf(filterText, StringComparison.OrdinalIgnoreCase) < 0)
                    {
                        continue;
                    }

                    totalMatches++;
                    // Filtering/counting does not need a formatted entry. Only
                    // materialize the requested page; the lookahead match still
                    // determines truncated/nextCursor exactly as before.
                    if (usePaging)
                    {
                        if (totalMatches <= resolvedCursor)
                            continue;
                        if (totalMatches > pageEndExclusive)
                            break;
                    }

                    int? callstackStart = _callstackTextStartField?.GetValue(logEntryInstance) as int?;
                    string messageOnly;
                    string stackTrace = null;
                    if (includeStacktrace && (fields == null || fields.Contains("stackTrace")) && format != "plain")
                    {
                        (messageOnly, stackTrace) = SplitMessageAndStackTrace(message, callstackStart);
                    }
                    else
                        messageOnly = GetMessageBody(message, callstackStart);

                    object formattedEntry = null;
                    switch (format)
                    {
                        case "plain":
                            formattedEntry = messageOnly;
                            break;
                        case "json":
                        case "detailed": // Treat detailed as json for structured return
                        default:
                            if (fields != null)
                            {
                                var projected = new Dictionary<string, object> { ["type"] = unityType.ToString(), ["message"] = messageOnly };
                                if (fields.Contains("file"))
                                    projected["file"] = (string)_fileField.GetValue(logEntryInstance);
                                if (fields.Contains("line"))
                                    projected["line"] = (int)_lineField.GetValue(logEntryInstance);
                                if (fields.Contains("stackTrace"))
                                    projected["stackTrace"] = stackTrace;
                                formattedEntry = projected;
                            }
                            else
                            {
                                formattedEntry = new
                                {
                                    type = unityType.ToString(),
                                    message = messageOnly,
                                    file = (string)_fileField.GetValue(logEntryInstance),
                                    line = (int)_lineField.GetValue(logEntryInstance),
                                    stackTrace = stackTrace, // null if not requested or unavailable
                                };
                            }
                            break;
                    }

                    if (usePaging)
                    {
                        formattedEntries.Add(formattedEntry);
                        retrievedCount++;
                    }
                    else
                    {
                        formattedEntries.Add(formattedEntry);
                        retrievedCount++;

                        // Apply count limit (after filtering)
                        if (count.HasValue && retrievedCount >= count.Value)
                        {
                            break;
                        }
                    }
                }
            }
            catch (Exception e)
            {
                McpLog.Error($"[ReadConsole] Error while retrieving log entries: {e}");
                // EndGettingEntries will be called in the finally block
                return new ErrorResponse($"Error retrieving log entries: {e.Message}");
            }
            finally
            {
                // Pair End only with a successfully opened iteration session.
                try
                {
                    if (entriesStarted)
                        _endGettingEntriesMethod.Invoke(null, null);
                }
                catch (Exception e)
                {
                    McpLog.Error($"[ReadConsole] Failed to call EndGettingEntries: {e}");
                    // Don't return error here as we might have valid data, but log it.
                }

                // Restore the Console window's filters once the iteration session is closed,
                // so the user's view is left exactly as they had it.
                if (filteringTextOverridden)
                {
                    RestoreFilteringText(savedFilteringText);
                }

                if (consoleFlagsOverridden)
                {
                    RestoreConsoleFlags(savedConsoleFlags);
                }
            }

            if (usePaging)
            {
                bool truncated = totalMatches > pageEndExclusive;
                string nextCursor = truncated ? pageEndExclusive.ToString() : null;
                var payload = new
                {
                    cursor = resolvedCursor,
                    pageSize = resolvedPageSize,
                    nextCursor = nextCursor,
                    truncated = truncated,
                    total = totalMatches,
                    items = formattedEntries,
                };

                return new SuccessResponse($"Retrieved {formattedEntries.Count} log entries.", payload);
            }

            // Return the filtered and formatted list (might be empty)
            return new SuccessResponse($"Retrieved {formattedEntries.Count} log entries.", formattedEntries);
        }

        // --- Internal Helpers ---

        internal static bool IsMcpLogMessage(string message)
        {
            if (string.IsNullOrEmpty(message))
                return false;
            // McpLog wraps the product label in <b><color=...>...</color></b>.
            // Strip only those leading label tags, never arbitrary body text.
            string label = message;
            int start = 0;
            while (start < label.Length && label[start] == '<')
            {
                int end = label.IndexOf('>', start);
                if (end < 0)
                    return false;
                string tag = label.Substring(start + 1, end - start - 1);
                if (tag != "b" && !tag.StartsWith("color=", StringComparison.OrdinalIgnoreCase))
                    return false;
                start = end + 1;
            }
            string[] names = { ProductInfo.ProductName, "MCP-FOR-UNITY", "MCPForUnity" };
            foreach (string name in names)
            {
                if (string.CompareOrdinal(label, start, name, 0, name.Length) != 0)
                    continue;
                int after = start + name.Length;
                while (after < label.Length && label[after] == '<')
                {
                    int end = label.IndexOf('>', after);
                    if (end < 0)
                        break;
                    string tag = label.Substring(after + 1, end - after - 1);
                    if (tag != "/b" && tag != "/color")
                        break;
                    after = end + 1;
                }
                if (after < label.Length && label[after] == ':')
                    return true;
            }
            return label.StartsWith("[MCP-FOR-UNITY]", StringComparison.Ordinal) || label.StartsWith("[MCPForUnity]", StringComparison.Ordinal);
        }

        // Mapping bits from LogEntry.mode, mirroring UnityEditor.ConsoleWindow.Mode.
        // These values are stable from 2021.3 through 6000.x.
        private const int ModeBitError = 1 << 0;
        private const int ModeBitAssert = 1 << 1;
        private const int ModeBitLog = 1 << 2;
        private const int ModeBitFatal = 1 << 4;
        private const int ModeBitAssetImportError = 1 << 6;
        private const int ModeBitAssetImportWarning = 1 << 7;
        private const int ModeBitScriptingError = 1 << 8;
        private const int ModeBitScriptingWarning = 1 << 9;
        private const int ModeBitScriptingLog = 1 << 10;
        private const int ModeBitScriptCompileError = 1 << 11;
        private const int ModeBitScriptCompileWarning = 1 << 12;
        private const int ModeBitStickyError = 1 << 13;
        private const int ModeBitScriptingException = 1 << 17;
        private const int ModeBitScriptingAssertion = 1 << 21;
        private const int ModeBitVisualScriptingError = 1 << 22;

        private const int ModeMaskError =
            ModeBitError
            | ModeBitFatal
            | ModeBitAssetImportError
            | ModeBitScriptingError
            | ModeBitScriptCompileError
            | ModeBitStickyError
            | ModeBitVisualScriptingError;

        private const int ModeMaskWarning = ModeBitAssetImportWarning | ModeBitScriptingWarning | ModeBitScriptCompileWarning;

        internal static LogType GetLogTypeFromMode(int mode)
        {
            // Preserve Unity's real type (no remapping). Order matters: an exception
            // also carries the Error bit, and an assertion also carries Assert.
            if ((mode & ModeBitScriptingException) != 0)
                return LogType.Exception;
            if ((mode & (ModeBitAssert | ModeBitScriptingAssertion)) != 0)
                return LogType.Assert;
            if ((mode & ModeMaskError) != 0)
                return LogType.Error;
            if ((mode & ModeMaskWarning) != 0)
                return LogType.Warning;
            return LogType.Log;
        }

        // (Calibration helpers removed)

        /// <summary>
        /// Splits a Unity log message into its body and appended stack trace.
        /// Unity concatenates both, separated by newlines, so the body may span
        /// several lines before the stack trace begins.
        /// </summary>
        /// <param name="fullMessage">The complete log message including any appended stack trace.</param>
        /// <returns>The message body (line endings normalized to "\n", internal blank lines preserved) and the stack trace, or null when none is found.</returns>
        internal static (string body, string stackTrace) SplitMessageAndStackTrace(string fullMessage, int? callstackStart = null)
        {
            if (string.IsNullOrEmpty(fullMessage))
                return (fullMessage, null);

            if (callstackStart.HasValue && callstackStart.Value >= 0 && callstackStart.Value <= fullMessage.Length)
            {
                int start = callstackStart.Value;
                if (start == 0 || start == fullMessage.Length)
                    return (fullMessage.Replace("\r\n", "\n").Replace('\r', '\n'), null);

                string body = fullMessage.Substring(0, start);
                // The newline separating the body and stack is not part of either value.
                if (body.EndsWith("\r\n", StringComparison.Ordinal))
                    body = body.Substring(0, body.Length - 2);
                else if (body.EndsWith("\n", StringComparison.Ordinal) || body.EndsWith("\r", StringComparison.Ordinal))
                    body = body.Substring(0, body.Length - 1);
                string stack = fullMessage.Substring(start);
                return (body.Replace("\r\n", "\n").Replace('\r', '\n'), stack.Replace("\r\n", "\n").Replace('\r', '\n'));
            }

            string[] lines = fullMessage.Replace("\r\n", "\n").Replace('\r', '\n').Split('\n');

            // If there's only one line or less, there's no separate stack trace.
            if (lines.Length <= 1)
                return (fullMessage, null);

            int stackStartIndex = FindStackStartIndex(lines);
            if (stackStartIndex <= 0)
                return (string.Join("\n", lines), null);

            return (string.Join("\n", lines.Take(stackStartIndex)), string.Join("\n", lines.Skip(stackStartIndex)));
        }

        // Native boundaries let summary/projection reads avoid allocating the
        // omitted stack substring. Older layouts preserve the existing fallback.
        private static string GetMessageBody(string fullMessage, int? callstackStart)
        {
            if (!callstackStart.HasValue || callstackStart.Value < 0 || callstackStart.Value > fullMessage.Length)
                return SplitMessageAndStackTrace(fullMessage, callstackStart).body;
            int start = callstackStart.Value;
            if (start == 0 || start == fullMessage.Length)
                return fullMessage.Replace("\r\n", "\n").Replace('\r', '\n');
            string body = fullMessage.Substring(0, start);
            if (body.EndsWith("\r\n", StringComparison.Ordinal))
                body = body.Substring(0, body.Length - 2);
            else if (body.EndsWith("\n", StringComparison.Ordinal) || body.EndsWith("\r", StringComparison.Ordinal))
                body = body.Substring(0, body.Length - 1);
            return body.Replace("\r\n", "\n").Replace('\r', '\n');
        }

        private static int FindStackStartIndex(string[] lines)
        {
            // Start checking from the second line onwards.
            for (int i = 1; i < lines.Length; ++i)
            {
                // Performance: TrimStart creates a new string. Consider using IsWhiteSpace check if performance critical.
                string trimmedLine = lines[i].TrimStart();

                // Check for common stack trace patterns.
                if (
                    trimmedLine.StartsWith("at ")
                    || IsNativeStackFrame(trimmedLine)
                    || trimmedLine.StartsWith("UnityEngine.")
                    || trimmedLine.StartsWith("UnityEditor.")
                    || trimmedLine.Contains("(at ")
                    || // Covers "(at Assets/..." pattern
                    // Heuristic: Check if line starts with likely namespace/class pattern (Uppercase.Something)
                    (trimmedLine.Length > 0 && char.IsUpper(trimmedLine[0]) && trimmedLine.Contains('.'))
                )
                {
                    return i; // Found the likely start of the stack trace
                }
            }

            return -1;
        }

        // Native frames Unity appends when Stack Trace Logging is set to Full, e.g.
        // "0x00007ffd387f224e (Unity) StackWalker::ShowCallstack".
        private static bool IsNativeStackFrame(string line)
        {
            if (!line.StartsWith("0x", StringComparison.Ordinal))
                return false;

            int i = 2;
            while (i < line.Length && Uri.IsHexDigit(line[i]))
                i++;

            // A pointer-width address, a "(Module)" label and a symbol after it.
            int digits = i - 2;
            if ((digits != 8 && digits != 16) || string.CompareOrdinal(line, i, " (", 0, 2) != 0)
                return false;

            int close = line.IndexOf(") ", i + 2, StringComparison.Ordinal);
            return close > i + 2 && close + 2 < line.Length;
        }

        /* LogEntry.mode bits exploration (based on Unity decompilation/observation):
           May change between versions.

           Basic Types:
           kError = 1 << 0 (1)
           kAssert = 1 << 1 (2)
           kWarning = 1 << 2 (4)
           kLog = 1 << 3 (8)
           kFatal = 1 << 4 (16) - Often treated as Exception/Error

           Modifiers/Context:
           kAssetImportError = 1 << 7 (128)
           kAssetImportWarning = 1 << 8 (256)
           kScriptingError = 1 << 9 (512)
           kScriptingWarning = 1 << 10 (1024)
           kScriptingLog = 1 << 11 (2048)
           kScriptCompileError = 1 << 12 (4096)
           kScriptCompileWarning = 1 << 13 (8192)
           kStickyError = 1 << 14 (16384) - Stays visible even after Clear On Play
           kMayIgnoreLineNumber = 1 << 15 (32768)
           kReportBug = 1 << 16 (65536) - Shows the "Report Bug" button
           kDisplayPreviousErrorInStatusBar = 1 << 17 (131072)
           kScriptingException = 1 << 18 (262144)
           kDontExtractStacktrace = 1 << 19 (524288) - Hint to the console UI
           kShouldClearOnPlay = 1 << 20 (1048576) - Default behavior
           kGraphCompileError = 1 << 21 (2097152)
           kScriptingAssertion = 1 << 22 (4194304)
           kVisualScriptingError = 1 << 23 (8388608)

           Example observed values:
           Log: 2048 (ScriptingLog) or 8 (Log)
           Warning: 1028 (ScriptingWarning | Warning) or 4 (Warning)
           Error: 513 (ScriptingError | Error) or 1 (Error)
           Exception: 262161 (ScriptingException | Error | kFatal?) - Complex combination
           Assertion: 4194306 (ScriptingAssertion | Assert) or 2 (Assert)
        */
    }
}
