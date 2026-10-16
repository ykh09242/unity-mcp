using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("unity_reflect", AutoRegister = false, Group = "docs")]
    public static class UnityReflect
    {
        private static Dictionary<string, Type[]> _assemblyTypeCache;
        private static readonly object CacheLock = new();
        private const int MaxExtensionReceiverCacheEntries = 256;
        private static readonly ConcurrentDictionary<Type, string[]> ExtensionMethodCache = new();
        private static readonly Dictionary<Type, MethodInfo[]> ExtensionMethodMetadataCache = new();
        private static Dictionary<string, Type[]> _extensionCacheGeneration;
        private static bool _hasDynamicExtensionProviders;
        private static Assembly[] _catalogAssemblies;
        private static string _catalogGeneration = Guid.NewGuid().ToString("N");
        private static readonly byte[] CursorKey = NewCursorKey();
        private static readonly List<JObject> EnumerationWarnings = new();
        private static int _enumerationWarningCount;
        private static Func<Assembly[]> _assemblyEnumerator = UnityAssembliesCompat.GetLoadedAssemblies;
        private static Func<DateTimeOffset> _searchClock = () => DateTimeOffset.UtcNow;
        private static int _pageByteLimit = 256 * 1024;
        private static int _publicTypeLimit = 100000;
        private static int _searchMatchLimit = 50000;
        private static double _workBudgetSeconds = 2;

        private sealed class SearchFailure : Exception
        {
            public SearchFailure(string code)
                : base(code) { }
        }

        private static byte[] NewCursorKey()
        {
            var key = new byte[32];
            using var random = RandomNumberGenerator.Create();
            random.GetBytes(key);
            return key;
        }

        private static void CheckSearchBudget(Stopwatch budget)
        {
            if (budget != null && budget.Elapsed.TotalSeconds > _workBudgetSeconds)
                throw new SearchFailure("search_budget_exceeded");
        }

        private static readonly string[] NamespacePrefixes =
        {
            "UnityEngine.",
            "UnityEditor.",
            "UnityEngine.UI.",
            "Unity.Cinemachine.",
            "UnityEngine.AI.",
            "UnityEngine.Rendering.Universal.",
            "UnityEngine.Rendering.HighDefinition.",
            "UnityEngine.InputSystem.",
            "UnityEngine.ProBuilder.",
            "UnityEngine.Tilemaps.",
            "UnityEngine.EventSystems.",
            "UnityEngine.Rendering.",
            "UnityEngine.SceneManagement.",
            "UnityEngine.Animations.",
            "UnityEngine.Playables.",
            "UnityEngine.UIElements.",
        };

        private static readonly Dictionary<Type, string> FriendlyTypeNames = new()
        {
            { typeof(void), "void" },
            { typeof(int), "int" },
            { typeof(float), "float" },
            { typeof(bool), "bool" },
            { typeof(string), "string" },
            { typeof(double), "double" },
            { typeof(long), "long" },
            { typeof(object), "object" },
            { typeof(byte), "byte" },
            { typeof(short), "short" },
            { typeof(char), "char" },
            { typeof(decimal), "decimal" },
            { typeof(uint), "uint" },
            { typeof(ulong), "ulong" },
            { typeof(ushort), "ushort" },
            { typeof(sbyte), "sbyte" },
        };

        [InitializeOnLoadMethod]
        private static void OnLoad()
        {
            AssemblyReloadEvents.afterAssemblyReload += InvalidateCache;
        }

        private static void InvalidateCache()
        {
            lock (CacheLock)
            {
                _assemblyTypeCache = null;
                _catalogAssemblies = null;
                _catalogGeneration = Guid.NewGuid().ToString("N");
                EnumerationWarnings.Clear();
                _enumerationWarningCount = 0;
                _extensionCacheGeneration = null;
                _hasDynamicExtensionProviders = false;
                ExtensionMethodCache.Clear();
                ExtensionMethodMetadataCache.Clear();
            }
        }

        private static bool SameAssemblies(Assembly[] left, Assembly[] right) =>
            left != null && left.Length == right.Length && left.All(a => right.Any(b => ReferenceEquals(a, b)));

        private static void WarnEnumeration(Assembly assembly, string reason)
        {
            _enumerationWarningCount++;
            if (EnumerationWarnings.Count < 16)
                EnumerationWarnings.Add(new JObject { ["assembly"] = assembly.FullName, ["reason"] = reason });
        }

        private static Dictionary<string, Type[]> GetAssemblyTypeCache(bool detectChanges = false, Stopwatch budget = null)
        {
            lock (CacheLock)
            {
                if (_assemblyTypeCache != null && !detectChanges)
                    return _assemblyTypeCache;
                var loaded = _assemblyEnumerator();
                if (_assemblyTypeCache != null && SameAssemblies(_catalogAssemblies, loaded))
                    return _assemblyTypeCache;
                InvalidateCache();
                var cache = new Dictionary<string, Type[]>();
                int publicCount = 0;
                foreach (var asm in loaded)
                {
                    CheckSearchBudget(budget);
                    if (asm.IsDynamic)
                    {
                        WarnEnumeration(asm, "dynamic_builder_unsupported");
                        continue;
                    }
                    Type[] types;
                    try
                    {
                        types = asm.GetExportedTypes();
                    }
                    catch (ReflectionTypeLoadException error)
                    {
                        types = error.Types.Where(t => t != null && t.IsVisible).ToArray();
                        WarnEnumeration(asm, "partial_type_load");
                    }
                    catch (Exception)
                    {
                        WarnEnumeration(asm, "exported_types_unavailable");
                        continue;
                    }
                    publicCount += types.Length;
                    if (publicCount > _publicTypeLimit)
                        throw new SearchFailure("search_limit_exceeded");
                    string key = asm.FullName + "|" + asm.ManifestModule.ModuleVersionId.ToString("N");
                    if (cache.ContainsKey(key))
                    {
                        WarnEnumeration(asm, "duplicate_assembly_identity");
                        continue;
                    }
                    cache[key] = types;
                }
                CheckSearchBudget(budget);
                if (!SameAssemblies(loaded, _assemblyEnumerator()))
                {
                    InvalidateCache();
                    throw new SearchFailure("catalog_changed");
                }
                _catalogAssemblies = loaded;
                _assemblyTypeCache = cache;
                return _assemblyTypeCache;
            }
        }

        public static object HandleCommand(JObject @params)
        {
            if (EditorStateCache.GetActualIsCompiling())
                return new ErrorResponse("Cannot reflect while Unity is compiling. Wait for domain reload to complete.");

            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");

            var p = new ToolParams(@params);

            var actionResult = p.GetRequired("action");
            if (!actionResult.IsSuccess)
                return new ErrorResponse(actionResult.ErrorMessage);

            string action = actionResult.Value.ToLowerInvariant();

            try
            {
                if (action != "search" && new[] { "page_size", "cursor", "assembly", "type_kind" }.Any(p.Has))
                    return new ErrorResponse("search_parameters_require_search");
                switch (action)
                {
                    case "get_type":
                        return GetTypeInfo(p);
                    case "get_member":
                        return GetMemberInfo(p);
                    case "search":
                        return SearchTypes(p);
                    default:
                        return new ErrorResponse($"Unknown action: '{action}'. Supported actions: get_type, get_member, search.");
                }
            }
            catch (SearchFailure ex)
            {
                return new ErrorResponse(ex.Message, new { hint = "Restart search without a cursor; narrow query or filters for work limits." });
            }
            catch (Exception ex)
            {
                return new ErrorResponse(ex.Message, new { stackTrace = ex.StackTrace });
            }
        }

        // === get_type ===
        private static object GetTypeInfo(ToolParams p)
        {
            var classResult = p.GetRequired("class_name", "'class_name' parameter is required for get_type.");
            if (!classResult.IsSuccess)
                return new ErrorResponse(classResult.ErrorMessage);

            string className = classResult.Value;
            string normalizedName = NormalizeGenericName(className);

            // Check for ambiguity first (only for short names without namespace)
            if (!normalizedName.Contains('.') && !normalizedName.Contains('`'))
            {
                var matches = FindAllTypesByShortName(normalizedName);
                if (matches.Count > 1)
                {
                    return new SuccessResponse(
                        $"Ambiguous type name '{className}'.",
                        new
                        {
                            found = true,
                            ambiguous = true,
                            query = className,
                            matches = matches.Select(t => t.FullName).OrderBy(n => n).ToArray(),
                            hint = "Use the fully qualified name (e.g., 'UnityEngine.UI.Button') to disambiguate.",
                        }
                    );
                }
            }

            var type = ResolveType(normalizedName);
            if (type == null)
            {
                return new SuccessResponse($"Type '{className}' not found.", new { found = false, query = className });
            }

            // Open generic type definitions (e.g. List<T>) segfault Mono on Unity 2021.3
            // in mono_metadata_generic_param_equal_internal when any member reflection is
            // performed. Return minimal info using only safe property accesses.
            if (type.IsGenericTypeDefinition)
            {
                return new SuccessResponse(
                    $"Type info for '{type.Name}'.",
                    new
                    {
                        found = true,
                        name = type.Name,
                        full_name = type.FullName,
                        @namespace = type.Namespace,
                        assembly = type.Assembly.GetName().Name,
                        is_generic_type_definition = true,
                        hint = "Open generic type — consult docs for member details.",
                    }
                );
            }

            var flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;

            var methods = type.GetMethods(flags).Where(m => !m.IsSpecialName).Select(m => m.Name).Distinct().OrderBy(n => n).ToArray();

            var properties = type.GetProperties(flags).Select(pr => pr.Name).Distinct().OrderBy(n => n).ToArray();

            var fields = type.GetFields(flags).Select(f => f.Name).Distinct().OrderBy(n => n).ToArray();

            var events = type.GetEvents(flags).Select(e => e.Name).Distinct().OrderBy(n => n).ToArray();

            var obsoleteMembers = GetObsoleteMembers(type, flags);
            var extensionMethods = FindExtensionMethods(type);

            var interfaces = type.GetInterfaces().Select(i => FormatTypeName(i)).OrderBy(n => n).ToArray();

            return new SuccessResponse(
                $"Type info for '{FormatTypeName(type)}'.",
                new
                {
                    found = true,
                    name = FormatTypeName(type),
                    full_name = type.FullName,
                    @namespace = type.Namespace,
                    assembly = type.Assembly.GetName().Name,
                    base_class = type.BaseType != null ? FormatTypeName(type.BaseType) : null,
                    interfaces,
                    is_abstract = type.IsAbstract,
                    is_sealed = type.IsSealed,
                    is_static = type.IsAbstract && type.IsSealed,
                    is_enum = type.IsEnum,
                    is_interface = type.IsInterface,
                    members = new
                    {
                        methods,
                        properties,
                        fields,
                        events,
                    },
                    extension_methods = extensionMethods,
                    obsolete_members = obsoleteMembers,
                }
            );
        }

        // === get_member ===
        private static object GetMemberInfo(ToolParams p)
        {
            var classResult = p.GetRequired("class_name", "'class_name' parameter is required for get_member.");
            if (!classResult.IsSuccess)
                return new ErrorResponse(classResult.ErrorMessage);

            var memberResult = p.GetRequired("member_name", "'member_name' parameter is required for get_member.");
            if (!memberResult.IsSuccess)
                return new ErrorResponse(memberResult.ErrorMessage);

            string className = classResult.Value;
            string memberName = memberResult.Value;
            string normalizedName = NormalizeGenericName(className);

            if (!normalizedName.Contains('.') && !normalizedName.Contains('`'))
            {
                var matches = FindAllTypesByShortName(normalizedName);
                if (matches.Count > 1)
                {
                    return new SuccessResponse(
                        $"Ambiguous type name '{className}'.",
                        new
                        {
                            found = true,
                            ambiguous = true,
                            query = className,
                            matches = matches.Select(t => t.FullName).OrderBy(n => n).ToArray(),
                            hint = "Use the fully qualified name to disambiguate before requesting member details.",
                        }
                    );
                }
            }

            var type = ResolveType(normalizedName);
            if (type == null)
            {
                return new SuccessResponse($"Type '{className}' not found.", new { found = false, query = className });
            }

            if (type.IsGenericTypeDefinition)
            {
                return new SuccessResponse(
                    $"Open generic type '{type.Name}' — consult docs for member details.",
                    new
                    {
                        found = false,
                        type_name = type.FullName,
                        member_name = memberName,
                        is_generic_type_definition = true,
                        hint = "Open generic type — consult docs for member details.",
                    }
                );
            }

            // Use flags without DeclaredOnly to find inherited members
            var flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static;

            // Try methods first
            var methods = type.GetMethods(flags).Where(m => !m.IsSpecialName && m.Name == memberName).ToArray();

            if (methods.Length > 0)
            {
                var overloads = methods.Select(m => FormatMethodDetail(m)).ToArray();
                return new SuccessResponse(
                    $"Member '{memberName}' on '{FormatTypeName(type)}'.",
                    new
                    {
                        found = true,
                        type_name = FormatTypeName(type),
                        member_name = memberName,
                        member_type = "method",
                        overload_count = overloads.Length,
                        overloads,
                    }
                );
            }

            // Try properties
            var properties = type.GetProperties(flags).Where(pr => pr.Name == memberName).ToArray();
            if (properties.Length > 1)
            {
                return new SuccessResponse(
                    $"Member '{memberName}' on '{FormatTypeName(type)}'.",
                    new
                    {
                        found = true,
                        type_name = FormatTypeName(type),
                        member_name = memberName,
                        member_type = "property",
                        overload_count = properties.Length,
                        overloads = properties.Select(FormatPropertyDetail).ToArray(),
                    }
                );
            }
            if (properties.Length == 1)
            {
                var prop = properties[0];
                return new SuccessResponse(
                    $"Member '{memberName}' on '{FormatTypeName(type)}'.",
                    new
                    {
                        found = true,
                        type_name = FormatTypeName(type),
                        member_name = memberName,
                        member_type = "property",
                        property_type = FormatTypeName(prop.PropertyType),
                        can_read = prop.CanRead,
                        can_write = prop.CanWrite,
                        is_static = (prop.GetMethod ?? prop.SetMethod)?.IsStatic ?? false,
                        is_obsolete = prop.GetCustomAttribute<ObsoleteAttribute>() != null,
                        declaring_type = prop.DeclaringType != type ? FormatTypeName(prop.DeclaringType) : null,
                        index_parameters = FormatIndexParameters(prop),
                    }
                );
            }

            // Try fields
            var field = type.GetField(memberName, flags);
            if (field != null)
            {
                return new SuccessResponse(
                    $"Member '{memberName}' on '{FormatTypeName(type)}'.",
                    new
                    {
                        found = true,
                        type_name = FormatTypeName(type),
                        member_name = memberName,
                        member_type = "field",
                        field_type = FormatTypeName(field.FieldType),
                        is_static = field.IsStatic,
                        is_readonly = field.IsInitOnly,
                        is_constant = field.IsLiteral,
                        constant_value = field.IsLiteral ? field.GetRawConstantValue() : null,
                        is_obsolete = field.GetCustomAttribute<ObsoleteAttribute>() != null,
                        declaring_type = field.DeclaringType != type ? FormatTypeName(field.DeclaringType) : null,
                    }
                );
            }

            // Try events
            var evt = type.GetEvent(memberName, flags);
            if (evt != null)
            {
                return new SuccessResponse(
                    $"Member '{memberName}' on '{FormatTypeName(type)}'.",
                    new
                    {
                        found = true,
                        type_name = FormatTypeName(type),
                        member_name = memberName,
                        member_type = "event",
                        event_handler_type = FormatTypeName(evt.EventHandlerType),
                        is_obsolete = evt.GetCustomAttribute<ObsoleteAttribute>() != null,
                        declaring_type = evt.DeclaringType != type ? FormatTypeName(evt.DeclaringType) : null,
                    }
                );
            }

            // Try extension methods as a last resort
            var extMethods = FindExtensionMethodInfos(type, memberName);
            if (extMethods.Length > 0)
            {
                var overloads = extMethods.Select(m => FormatMethodDetail(m)).ToArray();
                return new SuccessResponse(
                    $"Extension method '{memberName}' on '{FormatTypeName(type)}'.",
                    new
                    {
                        found = true,
                        type_name = FormatTypeName(type),
                        member_name = memberName,
                        member_type = "extension_method",
                        overload_count = overloads.Length,
                        overloads,
                        declaring_type = FormatTypeName(extMethods[0].DeclaringType),
                    }
                );
            }

            return new SuccessResponse(
                $"Member '{memberName}' not found on '{FormatTypeName(type)}'.",
                new
                {
                    found = false,
                    type_name = FormatTypeName(type),
                    member_name = memberName,
                }
            );
        }

        // === search ===
        private static string SearchString(ToolParams p, string name, bool required = false)
        {
            var token = p.GetRaw(name);
            if (token == null && !required)
                return null;
            if (token == null || token.Type != JTokenType.String || string.IsNullOrWhiteSpace((string)token))
                throw new SearchFailure("invalid_" + name);
            return (string)token;
        }

        private static string TypeKind(Type type)
        {
            if (type.IsEnum)
                return "enum";
            if (type.IsInterface)
                return "interface";
            if (type.IsValueType)
                return "struct";
            if (typeof(Delegate).IsAssignableFrom(type))
                return "delegate";
            return "class";
        }

        private static string Encode(byte[] bytes) => Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');

        private static byte[] Decode(string value) =>
            Convert.FromBase64String(value.Replace('-', '+').Replace('_', '/') + new string('=', (4 - value.Length % 4) % 4));

        private static byte[] Sign(byte[] bytes)
        {
            using var hmac = new HMACSHA256(CursorKey);
            return hmac.ComputeHash(bytes);
        }

        private static string SearchFingerprint(string query, string scope, string assembly, string kind, int size)
        {
            var canonical = new JArray(query.ToLowerInvariant(), scope, assembly, kind, size).ToString(Formatting.None);
            using var hash = SHA256.Create();
            return Encode(hash.ComputeHash(Encoding.UTF8.GetBytes(canonical)));
        }

        private static string MakeCursor(string generation, string fingerprint, int offset, long issued)
        {
            var payload = new JObject
            {
                ["v"] = 1,
                ["g"] = generation,
                ["q"] = fingerprint,
                ["o"] = offset,
                ["i"] = issued,
            };
            byte[] bytes = Encoding.UTF8.GetBytes(payload.ToString(Formatting.None));
            return Encode(bytes) + "." + Encode(Sign(bytes));
        }

        private static (int offset, long issued) ReadCursor(string cursor, string generation, string fingerprint, long now)
        {
            try
            {
                var pieces = cursor.Split('.');
                if (pieces.Length != 2)
                    throw new SearchFailure("invalid_cursor");
                byte[] bytes = Decode(pieces[0]);
                var payload = JObject.Parse(
                    new UTF8Encoding(false, true).GetString(bytes),
                    new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error }
                );
                if (
                    payload.Count != 5
                    || payload["v"]?.Type != JTokenType.Integer
                    || (int)payload["v"] != 1
                    || payload["g"]?.Type != JTokenType.String
                    || payload["q"]?.Type != JTokenType.String
                    || payload["o"]?.Type != JTokenType.Integer
                    || payload["i"]?.Type != JTokenType.Integer
                )
                    throw new SearchFailure("invalid_cursor");
                if ((string)payload["g"] != generation)
                    throw new SearchFailure("stale_cursor");
                byte[] supplied = Decode(pieces[1]);
                byte[] expected = Sign(bytes);
                int difference = supplied.Length ^ expected.Length;
                for (int i = 0; i < expected.Length; i++)
                    difference |= expected[i] ^ (i < supplied.Length ? supplied[i] : 0);
                if (difference != 0)
                    throw new SearchFailure("invalid_cursor");
                if ((string)payload["q"] != fingerprint)
                    throw new SearchFailure("cursor_mismatch");
                long issued = (long)payload["i"];
                if (issued > now + 60)
                    throw new SearchFailure("invalid_cursor");
                if (now - issued >= 300)
                    throw new SearchFailure("cursor_expired");
                int offset = checked((int)(long)payload["o"]);
                if (offset <= 0)
                    throw new SearchFailure("invalid_cursor");
                return (offset, issued);
            }
            catch (Exception error) when (error is FormatException || error is JsonException || error is OverflowException || error is ArgumentException)
            {
                throw new SearchFailure("invalid_cursor");
            }
        }

        private static object SearchTypes(ToolParams p)
        {
            string query = SearchString(p, "query", true);
            string scope = (SearchString(p, "scope") ?? "unity").ToLowerInvariant();
            string assembly = SearchString(p, "assembly");
            string kind = SearchString(p, "type_kind");
            string cursor = SearchString(p, "cursor");
            if (query.Length > 256 || assembly?.Length > 256)
                throw new SearchFailure("search_input_limit_exceeded");
            if (cursor != null && (cursor.Length > 1024 || cursor.Any(c => c > 127)))
                throw new SearchFailure("invalid_cursor");
            if (!new[] { "unity", "packages", "project", "all" }.Contains(scope))
                throw new SearchFailure("invalid_scope");
            if (kind != null && !new[] { "class", "interface", "struct", "enum", "delegate" }.Contains(kind))
                throw new SearchFailure("invalid_type_kind");
            int size = 25;
            var sizeToken = p.GetRaw("page_size");
            if (sizeToken != null)
            {
                if (sizeToken.Type != JTokenType.Integer || !int.TryParse(sizeToken.ToString(), out size) || size < 1 || size > 100)
                    throw new SearchFailure("invalid_page_size");
            }
            var budget = Stopwatch.StartNew();
            var cache = GetAssemblyTypeCache(true, budget);
            string generation = _catalogGeneration;
            string fingerprint = SearchFingerprint(query, scope, assembly, kind, size);
            long now = _searchClock().ToUnixTimeSeconds();
            var position = cursor == null ? (offset: 0, issued: now) : ReadCursor(cursor, generation, fingerprint, now);
            string queryLower = query.ToLowerInvariant();
            var candidates = new List<(Type type, int rank, string identity, string module)>();
            int scanned = 0;
            foreach (var entry in cache)
            {
                CheckSearchBudget(budget);
                foreach (var type in entry.Value)
                {
                    if (++scanned % 128 == 0)
                        CheckSearchBudget(budget);
                    var asm = type.Assembly;
                    string shortAssembly = asm.GetName().Name;
                    if (
                        !MatchesScope(shortAssembly, scope)
                        || (
                            assembly != null
                            && !string.Equals(assembly, shortAssembly, StringComparison.OrdinalIgnoreCase)
                            && !string.Equals(assembly, asm.FullName, StringComparison.OrdinalIgnoreCase)
                        )
                        || (kind != null && TypeKind(type) != kind)
                    )
                        continue;
                    string name = type.Name.ToLowerInvariant();
                    string fullName = type.FullName?.ToLowerInvariant() ?? name;
                    int rank =
                        name == queryLower || fullName == queryLower ? 0
                        : name.StartsWith(queryLower, StringComparison.Ordinal) ? 1
                        : name.Contains(queryLower) || fullName.Contains(queryLower) ? 2
                        : -1;
                    if (rank < 0)
                        continue;
                    candidates.Add((type, rank, asm.FullName, type.Module.ModuleVersionId.ToString("N")));
                    if (candidates.Count > _searchMatchLimit)
                        throw new SearchFailure("search_limit_exceeded");
                }
            }
            CheckSearchBudget(budget);
            var ordered = candidates
                .OrderBy(c => c.rank)
                .ThenBy(c => c.type.FullName, StringComparer.Ordinal)
                .ThenBy(c => c.identity, StringComparer.Ordinal)
                .ThenBy(c => c.module, StringComparer.Ordinal)
                .ToArray();
            CheckSearchBudget(budget);
            if (position.offset > 0 && position.offset >= ordered.Length)
                throw new SearchFailure("invalid_cursor");
            var rows = new JArray();
            var data = new JObject
            {
                ["query"] = query,
                ["scope"] = scope,
                ["count"] = 0,
                ["results"] = rows,
                ["total_count"] = ordered.Length,
                ["page_size"] = size,
                ["catalog_generation"] = generation,
                ["complete"] = _enumerationWarningCount == 0,
                ["enumeration_warnings"] = new JArray(EnumerationWarnings.Select(w => w.DeepClone())),
                ["enumeration_warning_count"] = _enumerationWarningCount,
                ["enumeration_warnings_truncated"] = _enumerationWarningCount > EnumerationWarnings.Count,
            };
            var response = new SuccessResponse($"Found type(s) matching '{query}' (scope: {scope}).", data);
            int maximum = Math.Min(size, ordered.Length - position.offset);
            for (int index = 0; index < maximum; index++)
            {
                CheckSearchBudget(budget);
                var candidate = ordered[position.offset + index];
                var type = candidate.type;
                rows.Add(
                    new JObject
                    {
                        ["name"] = type.Name,
                        ["full_name"] = type.FullName,
                        ["namespace"] = type.Namespace,
                        ["assembly"] = type.Assembly.GetName().Name,
                        ["assembly_full_name"] = candidate.identity,
                        ["module_version_id"] = candidate.module,
                        ["rank"] = candidate.rank,
                        ["type_kind"] = TypeKind(type),
                        ["is_class"] = type.IsClass,
                        ["is_enum"] = type.IsEnum,
                        ["is_interface"] = type.IsInterface,
                        ["is_struct"] = type.IsValueType && !type.IsEnum,
                    }
                );
                UpdatePagePosition(data, rows.Count, ordered.Length, position.offset, size, generation, fingerprint, position.issued);
                if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(response)) <= _pageByteLimit)
                    continue;
                rows.RemoveAt(rows.Count - 1);
                if (rows.Count == 0)
                    throw new SearchFailure("result_too_large");
                break;
            }
            UpdatePagePosition(data, rows.Count, ordered.Length, position.offset, size, generation, fingerprint, position.issued);
            if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(response)) > _pageByteLimit)
                throw new SearchFailure("result_too_large");
            CheckSearchBudget(budget);
            if (generation != _catalogGeneration || !SameAssemblies(_catalogAssemblies, _assemblyEnumerator()))
            {
                InvalidateCache();
                throw new SearchFailure("catalog_changed");
            }
            return response;
        }

        private static void UpdatePagePosition(JObject data, int count, int total, int offset, int size, string generation, string fingerprint, long issued)
        {
            data["count"] = count;
            data["truncated"] = total > count;
            bool more = offset + count < total;
            data["has_more"] = more;
            data["next_cursor"] = more ? MakeCursor(generation, fingerprint, offset + count, issued) : null;
        }

        // --- Type Resolution ---

        private static Type ResolveType(string className)
        {
            // Use the shared UnityTypeResolver which handles caching,
            // namespace prefixes, player-over-editor priority, and TypeCache fallback.
            var type = UnityTypeResolver.ResolveAny(className);
            if (type != null)
                return type;

            // UnityTypeResolver doesn't try our extended namespace prefixes,
            // so fall back to assembly cache scan for edge cases.
            var cache = GetAssemblyTypeCache();
            foreach (var prefix in NamespacePrefixes)
            {
                string fullName = prefix + className;
                foreach (var kvp in cache)
                {
                    type = Array.Find(kvp.Value, t => t.FullName == fullName);
                    if (type != null)
                        return type;
                }
            }

            return null;
        }

        private static List<Type> FindAllTypesByShortName(string shortName)
        {
            var matches = new List<Type>();
            foreach (var kvp in GetAssemblyTypeCache())
            {
                foreach (var t in kvp.Value)
                {
                    if (t.Name == shortName)
                        matches.Add(t);
                }
            }
            return matches;
        }

        // --- Generic Name Normalization ---

        private static string NormalizeGenericName(string name)
        {
            // Parse List<T> -> List`1, Dictionary<K,V> -> Dictionary`2
            var match = Regex.Match(name, @"^([^<>]+)<(.+)>$");
            if (!match.Success)
                return name;

            string baseName = match.Groups[1].Value;
            string typeArgs = match.Groups[2].Value;

            // Count generic args by tracking nesting depth
            int argCount = 1;
            int depth = 0;
            foreach (char c in typeArgs)
            {
                if (c == '<')
                    depth++;
                else if (c == '>')
                {
                    if (depth == 0)
                        return name;
                    depth--;
                }
                else if (c == ',' && depth == 0)
                    argCount++;
            }

            return depth == 0 ? $"{baseName}`{argCount}" : name;
        }

        // --- Type Name Formatting ---

        private static string FormatTypeName(Type type)
        {
            if (type == null)
                return "null";

            if (FriendlyTypeNames.TryGetValue(type, out var friendly))
                return friendly;

            if (type.IsArray)
                return FormatTypeName(type.GetElementType()) + "[" + new string(',', type.GetArrayRank() - 1) + "]";

            if (type.IsByRef)
                return FormatTypeName(type.GetElementType());

            if (type.IsGenericParameter)
                return type.Name;

            if (type.IsNested && type.DeclaringType != null)
                return FormatNestedTypeName(type, type.GetGenericArguments());

            if (type.IsGenericType)
            {
                string baseName = type.Name;
                int backtickIndex = baseName.IndexOf('`');
                if (backtickIndex > 0)
                    baseName = baseName.Substring(0, backtickIndex);

                var args = type.GetGenericArguments();
                string argsStr = string.Join(", ", args.Select(FormatTypeName));
                return $"{baseName}<{argsStr}>";
            }

            return type.Name;
        }

        private static string FormatNestedTypeName(Type type, Type[] arguments)
        {
            var declaringType = type.DeclaringType;
            int parentArgumentCount = declaringType?.GetGenericArguments().Length ?? 0;
            int ownArgumentCount = type.GetGenericArguments().Length - parentArgumentCount;
            string name = type.Name;
            if (ownArgumentCount > 0)
            {
                int backtickIndex = name.IndexOf('`');
                if (backtickIndex > 0)
                    name = name.Substring(0, backtickIndex);
                name += "<" + string.Join(", ", arguments.Skip(parentArgumentCount).Take(ownArgumentCount).Select(FormatTypeName)) + ">";
            }

            // DeclaringType can be open even for a closed nested type. The leaf's
            // flattened arguments retain the concrete arguments for every level.
            return declaringType == null ? name : FormatNestedTypeName(declaringType, arguments) + "." + name;
        }

        // --- Method Formatting ---

        private static object[] FormatIndexParameters(PropertyInfo property)
        {
            return property
                .GetIndexParameters()
                .Select(parameter => (object)new { name = parameter.Name, type = FormatTypeName(parameter.ParameterType) })
                .ToArray();
        }

        private static object FormatPropertyDetail(PropertyInfo property)
        {
            return new
            {
                property_type = FormatTypeName(property.PropertyType),
                can_read = property.CanRead,
                can_write = property.CanWrite,
                is_static = (property.GetMethod ?? property.SetMethod)?.IsStatic ?? false,
                is_obsolete = property.GetCustomAttribute<ObsoleteAttribute>() != null,
                declaring_type = property.DeclaringType != property.ReflectedType ? FormatTypeName(property.DeclaringType) : null,
                index_parameters = FormatIndexParameters(property),
            };
        }

        private static object FormatMethodDetail(MethodInfo m)
        {
            var parameters = m.GetParameters()
                .Select(param =>
                {
                    string prefix = "";
                    if (param.IsOut)
                        prefix = "out ";
                    else if (param.ParameterType.IsByRef)
                        prefix = "ref ";

                    return new
                    {
                        name = param.Name,
                        type = prefix + FormatTypeName(param.ParameterType),
                        has_default = param.HasDefaultValue,
                        default_value = param.HasDefaultValue ? FormatDefaultValue(param.DefaultValue) : null,
                        is_params = param.IsDefined(typeof(ParamArrayAttribute)),
                    };
                })
                .ToArray();

            string signature = FormatMethodSignature(m);
            var obsoleteAttr = m.GetCustomAttribute<ObsoleteAttribute>();

            return new
            {
                signature,
                return_type = FormatTypeName(m.ReturnType),
                parameters,
                is_static = m.IsStatic,
                is_virtual = m.IsVirtual && !m.IsFinal,
                is_abstract = m.IsAbstract,
                is_generic = m.IsGenericMethod,
                generic_arguments = m.IsGenericMethod ? m.GetGenericArguments().Select(a => a.Name).ToArray() : null,
                is_obsolete = obsoleteAttr != null,
                obsolete_message = obsoleteAttr?.Message,
                declaring_type = m.DeclaringType != m.ReflectedType ? FormatTypeName(m.DeclaringType) : null,
            };
        }

        private static string FormatMethodSignature(MethodInfo m)
        {
            string staticPrefix = m.IsStatic ? "static " : "";
            string returnType = FormatTypeName(m.ReturnType);
            string name = m.Name;

            if (m.IsGenericMethod)
            {
                var genArgs = m.GetGenericArguments();
                name += "<" + string.Join(", ", genArgs.Select(a => a.Name)) + ">";
            }

            var paramStrings = m.GetParameters()
                .Select(param =>
                {
                    string prefix = "";
                    if (param.IsOut)
                        prefix = "out ";
                    else if (param.ParameterType.IsByRef)
                        prefix = "ref ";

                    if (param.IsDefined(typeof(ParamArrayAttribute)))
                        prefix = "params ";

                    return prefix + FormatTypeName(param.ParameterType) + " " + param.Name;
                });

            return $"{staticPrefix}{returnType} {name}({string.Join(", ", paramStrings)})";
        }

        private static object FormatDefaultValue(object value)
        {
            if (value == null)
                return "null";
            if (value is string s)
                return $"\"{s}\"";
            if (value is bool b)
                return b ? "true" : "false";
            return value;
        }

        // --- Obsolete Member Detection ---

        private static string[] GetObsoleteMembers(Type type, BindingFlags flags)
        {
            var obsolete = new HashSet<string>();

            foreach (var m in type.GetMethods(flags).Where(m => !m.IsSpecialName))
            {
                if (m.GetCustomAttribute<ObsoleteAttribute>() != null)
                    obsolete.Add(m.Name);
            }
            foreach (var pr in type.GetProperties(flags))
            {
                if (pr.GetCustomAttribute<ObsoleteAttribute>() != null)
                    obsolete.Add(pr.Name);
            }
            foreach (var f in type.GetFields(flags))
            {
                if (f.GetCustomAttribute<ObsoleteAttribute>() != null)
                    obsolete.Add(f.Name);
            }
            foreach (var e in type.GetEvents(flags))
            {
                if (e.GetCustomAttribute<ObsoleteAttribute>() != null)
                    obsolete.Add(e.Name);
            }

            return obsolete.Count > 0 ? obsolete.OrderBy(n => n).ToArray() : Array.Empty<string>();
        }

        // --- Extension Method Discovery ---

        private static bool PrepareExtensionCache(Dictionary<string, Type[]> assemblyTypes)
        {
            lock (CacheLock)
            {
                if (!ReferenceEquals(_extensionCacheGeneration, assemblyTypes))
                {
                    ExtensionMethodCache.Clear();
                    ExtensionMethodMetadataCache.Clear();
                    _extensionCacheGeneration = assemblyTypes;
                    _hasDynamicExtensionProviders = assemblyTypes.Any(kvp => IsExtensionAssembly(kvp.Key) && kvp.Value.Any(type => type.Assembly.IsDynamic));
                }
                return !_hasDynamicExtensionProviders;
            }
        }

        private static bool IsExtensionAssembly(string fullName)
        {
            string name = fullName.Split(',')[0];
            return name.StartsWith("UnityEngine") || name.StartsWith("UnityEditor") || name.StartsWith("Unity.");
        }

        private static MethodInfo[] GetExtensionMethodMetadata(Type provider, Dictionary<string, Type[]> generation)
        {
            lock (CacheLock)
            {
                bool canCache = !provider.Assembly.IsDynamic && ReferenceEquals(_extensionCacheGeneration, generation);
                if (canCache && ExtensionMethodMetadataCache.TryGetValue(provider, out var cached))
                    return cached;

                var methods = provider
                    .GetMethods(BindingFlags.Public | BindingFlags.Static)
                    .Where(method => method.IsDefined(typeof(ExtensionAttribute), false))
                    .ToArray();
                // Dynamic providers can acquire metadata without another assembly-load event.
                if (canCache)
                    ExtensionMethodMetadataCache[provider] = methods;
                return methods;
            }
        }

        private static string[] FindExtensionMethods(Type targetType)
        {
            var cache = GetAssemblyTypeCache();
            bool canCache = PrepareExtensionCache(cache);
            if (canCache && ExtensionMethodCache.TryGetValue(targetType, out var cached))
                return cached;

            var extensionNames = new HashSet<string>();

            foreach (var kvp in cache)
            {
                if (!IsExtensionAssembly(kvp.Key))
                    continue;

                foreach (var t in kvp.Value)
                {
                    if (!t.IsAbstract || !t.IsSealed)
                        continue; // Static classes only
                    if (!t.IsDefined(typeof(ExtensionAttribute), false))
                        continue;

                    foreach (var method in GetExtensionMethodMetadata(t, cache))
                    {
                        var firstParam = method.GetParameters().FirstOrDefault();
                        if (firstParam == null)
                            continue;

                        var paramType = firstParam.ParameterType;
                        if (
                            paramType.IsAssignableFrom(targetType)
                            || targetType.IsSubclassOf(paramType)
                            || paramType == targetType
                            || (paramType.IsGenericType && IsGenericMatch(paramType, targetType))
                        )
                        {
                            extensionNames.Add(method.Name);
                        }
                    }
                }
            }

            var result = extensionNames.Count > 0 ? extensionNames.OrderBy(n => n).ToArray() : Array.Empty<string>();

            lock (CacheLock)
            {
                if (canCache && ReferenceEquals(_extensionCacheGeneration, cache))
                {
                    if (ExtensionMethodCache.Count >= MaxExtensionReceiverCacheEntries)
                        ExtensionMethodCache.Clear();
                    ExtensionMethodCache.TryAdd(targetType, result);
                }
            }
            return result;
        }

        private static MethodInfo[] FindExtensionMethodInfos(Type targetType, string methodName)
        {
            var results = new List<MethodInfo>();
            var cache = GetAssemblyTypeCache();
            PrepareExtensionCache(cache);

            foreach (var kvp in cache)
            {
                if (!IsExtensionAssembly(kvp.Key))
                    continue;

                foreach (var t in kvp.Value)
                {
                    if (!t.IsAbstract || !t.IsSealed)
                        continue;
                    if (!t.IsDefined(typeof(ExtensionAttribute), false))
                        continue;

                    foreach (var method in GetExtensionMethodMetadata(t, cache))
                    {
                        if (method.Name != methodName)
                            continue;
                        var firstParam = method.GetParameters().FirstOrDefault();
                        if (firstParam == null)
                            continue;

                        var paramType = firstParam.ParameterType;
                        if (
                            paramType.IsAssignableFrom(targetType)
                            || targetType.IsSubclassOf(paramType)
                            || paramType == targetType
                            || (paramType.IsGenericType && IsGenericMatch(paramType, targetType))
                        )
                        {
                            results.Add(method);
                        }
                    }
                }
            }

            return results.ToArray();
        }

        private static bool IsGenericMatch(Type genericParamType, Type targetType)
        {
            // Closed receivers are already checked by IsAssignableFrom. Comparing only
            // their definitions would match e.g. ICollection<int> to ICollection<string>.
            if (!genericParamType.IsGenericType || !genericParamType.ContainsGenericParameters)
                return false;

            var genDef = genericParamType.GetGenericTypeDefinition();

            // Check if targetType implements or inherits from the generic definition
            for (var current = targetType; current != null; current = current.BaseType)
            {
                if (current.IsGenericType && current.GetGenericTypeDefinition() == genDef)
                    return true;
            }

            foreach (var iface in targetType.GetInterfaces())
            {
                if (iface.IsGenericType && iface.GetGenericTypeDefinition() == genDef)
                    return true;
            }

            return false;
        }

        // --- Scope Matching ---

        private static bool MatchesScope(string assemblyName, string scope)
        {
            switch (scope)
            {
                case "unity":
                    return assemblyName.StartsWith("UnityEngine") || assemblyName.StartsWith("UnityEditor") || assemblyName.StartsWith("Unity.");

                case "packages":
                    return !assemblyName.StartsWith("System") && !assemblyName.StartsWith("mscorlib") && !assemblyName.StartsWith("netstandard");

                case "project":
                    return assemblyName == "Assembly-CSharp"
                        || assemblyName == "Assembly-CSharp-Editor"
                        || assemblyName.StartsWith("Assembly-CSharp-firstpass")
                        || assemblyName.StartsWith("Assembly-CSharp-Editor-firstpass");

                case "all":
                    return true;

                default:
                    return false;
            }
        }
    }
}
