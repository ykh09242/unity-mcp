using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Linq.Expressions;
using System.Reflection;
using System.Threading;
using Newtonsoft.Json.Linq;
using UnityEditor;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Tools.ProjectAuditor
{
    internal interface IProjectAuditorBackend
    {
        JObject Availability();
        int Initialize();
        bool ObservationTruncated { get; }
        string ObservationError { get; }
        void Start(Action<JObject> issue, Action<string> moduleCompleted, Action completed, Action beforeInvoke);
    }

    internal sealed class ProjectAuditorApi : IProjectAuditorBackend
    {
        internal const string PackageName = "com.unity.project-auditor";
        private const string RulesPackage = "com.unity.project-auditor-rules";
        private const string Namespace = "Unity.ProjectAuditor.Editor.";
        private const int MaximumInspected = 250000;
        private Type auditorType;
        private Type parametersType;
        private MethodInfo auditAsync;
        private MethodInfo descriptorIds;
        private int moduleResultArgument;
        private object auditor;
        private int inspected;
        private volatile bool observationTruncated;
        private volatile string observationError;
        public bool ObservationTruncated => observationTruncated;
        public string ObservationError => observationError;

        public JObject Availability()
        {
            var registered = PackageInfo.GetAllRegisteredPackages();
            var package = registered.FirstOrDefault(info => info.name == PackageName);
            var providers = AppDomain
                .CurrentDomain.GetAssemblies()
                .Where(assembly => !assembly.IsDynamic)
                .Select(assembly => assembly.GetType(Namespace + "ProjectAuditor", false))
                .Where(type => type != null && (IsEditorProvider(type.Assembly) || PackageInfo.FindForAssembly(type.Assembly)?.name == PackageName))
                .Distinct()
                .ToArray();
            auditorType = providers.Length == 1 ? providers[0] : null;
            bool integrated = auditorType != null && IsEditorProvider(auditorType.Assembly);
            var data = new JObject
            {
                ["state"] = "unavailable",
                ["package"] = PackageName,
                ["supported_version"] = JValue.CreateNull(),
                ["package_installed"] = package != null,
                ["version"] = package?.version,
                ["integration"] =
                    integrated ? "editor"
                    : auditorType == null ? "none"
                    : "package",
                ["api_supported"] = false,
                ["rules_available"] = false,
                ["registered_rules_observed"] = false,
                ["cancel_supported"] = false,
                ["external_audits_observable"] = false,
            };
            if (auditorType == null)
            {
                data["reason"] =
                    providers.Length > 1 ? "ambiguous_auditor_provider"
                    : package == null ? "package_not_installed"
                    : "package_assembly_not_loaded";
                return data;
            }
            bool supported;
            try
            {
                supported = BindApi(auditorType);
            }
            catch (Exception error) when (error is AmbiguousMatchException || error is TypeLoadException || error is NotSupportedException)
            {
                supported = false;
            }
            data["api_supported"] = supported;
            if (!supported)
            {
                data["reason"] = "unsupported_api_shape";
                return data;
            }
            bool separateRules = integrated || (package?.dependencies?.Any(dependency => dependency.name == RulesPackage) ?? false);
            var rules = separateRules ? registered.FirstOrDefault(info => info.name == RulesPackage) : package;
            data["rules_package"] = separateRules ? RulesPackage : PackageName;
            data["rules_version"] = rules?.version;
            if (rules == null)
            {
                data["reason"] = "rules_package_not_installed";
                return data;
            }
            try
            {
                string directory = Path.Combine(rules.resolvedPath, separateRules ? "Rules" : "Data");
                int count = ReadRules(Path.Combine(directory, "ApiDatabase.json")) + ReadRules(Path.Combine(directory, "ProjectSettings.json"));
                data["bundled_rule_count"] = count;
                data["rules_available"] = true;
                data["state"] = "available";
                data["reason"] = separateRules ? "supported_api_and_rules_package" : "supported_api_and_bundled_rules";
            }
            catch (Exception error)
                when (error is IOException || error is UnauthorizedAccessException || error is Newtonsoft.Json.JsonException || error is InvalidDataException)
            {
                data["reason"] = "rules_missing_or_invalid";
            }
            return data;
        }

        private static bool IsEditorProvider(Assembly assembly)
        {
            if (assembly == typeof(EditorApplication).Assembly)
                return true;
            if (!assembly.GetName().Name.StartsWith("UnityEditor.", StringComparison.Ordinal) || string.IsNullOrEmpty(assembly.Location))
                return false;
            var managed = Path.GetFullPath(Path.Combine(EditorApplication.applicationContentsPath, "Managed")) + Path.DirectorySeparatorChar;
            var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            return Path.GetFullPath(assembly.Location).StartsWith(managed, comparison);
        }

        private bool BindApi(Type type)
        {
            Type Lookup(string name) => type.Assembly.GetType(Namespace + name, false);
            parametersType = Lookup("AnalysisParams");
            var report = Lookup("Report");
            var reportItem = Lookup("ReportItem");
            var result = Lookup("AnalysisResult");
            var progress = Lookup("IProgress");
            if (parametersType == null || report == null || reportItem == null || result == null || !result.IsEnum || !Enum.IsDefined(result, "Success"))
                return false;
            const BindingFlags publicInstance = BindingFlags.Instance | BindingFlags.Public;
            auditAsync = progress == null ? null : type.GetMethod("AuditAsync", publicInstance, null, new[] { parametersType, progress }, null);
            auditAsync ??= type.GetMethod("AuditAsync", publicInstance, null, new[] { parametersType }, null);
            descriptorIds = type.GetMethod("GetDescriptorIDs", publicInstance | BindingFlags.NonPublic, null, Type.EmptyTypes, null);
            Type id = MemberType(reportItem, "Id");
            Type descriptor = id?.GetMethod("GetDescriptor", publicInstance, null, Type.EmptyTypes, null)?.ReturnType;
            var module = parametersType.GetField("OnModuleCompleted", publicInstance);
            moduleResultArgument = module == null || module.IsInitOnly ? -1 : ModuleResultArgument(module.FieldType, result);
            return type.GetConstructor(Type.EmptyTypes) != null
                && (parametersType.GetConstructor(new[] { typeof(bool) }) != null || parametersType.GetConstructor(Type.EmptyTypes) != null)
                && auditAsync?.ReturnType == typeof(void)
                && id != null
                && descriptorIds?.ReturnType == id.MakeArrayType()
                && Callback(parametersType, "OnIncomingIssues", typeof(IEnumerable<>).MakeGenericType(reportItem))
                && moduleResultArgument >= 0
                && Callback(parametersType, "OnCompleted", report)
                && reportItem.GetMethod("IsIssue", publicInstance, null, Type.EmptyTypes, null)?.ReturnType == typeof(bool)
                && MemberType(reportItem, "Category") != null
                && MemberType(reportItem, "Severity") != null
                && MemberType(reportItem, "Description") == typeof(string)
                && MemberType(reportItem, "RelativePath") == typeof(string)
                && MemberType(reportItem, "Line") == typeof(int)
                && MemberType(descriptor, "Areas") != null
                && MemberType(descriptor, "Recommendation") == typeof(string);
        }

        private static Type MemberType(Type type, string name)
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.Instance;
            var property = type?.GetProperty(name, flags);
            return property?.GetGetMethod() != null && property.GetIndexParameters().Length == 0
                ? property.PropertyType
                : type?.GetField(name, flags)?.FieldType;
        }

        private static bool Callback(Type parameters, string name, Type argument)
        {
            var field = parameters.GetField(name, BindingFlags.Instance | BindingFlags.Public);
            return field != null && !field.IsInitOnly && field.FieldType == typeof(Action<>).MakeGenericType(argument);
        }

        internal static int ModuleResultArgument(Type callback, Type result)
        {
            if (callback == typeof(Action<>).MakeGenericType(result))
                return 0;
            return callback == typeof(Action<,,>).MakeGenericType(typeof(string), result, typeof(long)) ? 1 : -1;
        }

        private static int ReadRules(string path)
        {
            var file = new FileInfo(path);
            if (!file.Exists || file.Length == 0 || file.Length > 4 * 1024 * 1024)
                throw new InvalidDataException("Rule data is absent or exceeds the supported size.");
            var rules = JArray.Parse(File.ReadAllText(path));
            if (
                rules.Count == 0
                || rules.Count > 16384
                || rules.Any(rule =>
                    !(rule is JObject descriptor) || descriptor["id"]?.Type != JTokenType.String || string.IsNullOrEmpty((string)descriptor["id"])
                )
            )
                throw new InvalidDataException("Rule data has no valid descriptors.");
            return rules.Count;
        }

        public int Initialize()
        {
            auditor = Activator.CreateInstance(auditorType);
            var descriptors = (Array)descriptorIds.Invoke(auditor, null);
            return descriptors?.Length ?? 0;
        }

        public void Start(Action<JObject> issue, Action<string> moduleCompleted, Action completed, Action beforeInvoke)
        {
            // The official default constructor registers thresholds and saves Auditor settings. Only explicit start reaches here.
            var constructor = parametersType.GetConstructor(new[] { typeof(bool) });
            object parameters = constructor != null ? constructor.Invoke(new object[] { true }) : Activator.CreateInstance(parametersType);
            Bind(parameters, "OnIncomingIssues", value =>
            {
                foreach (object item in (IEnumerable)value)
                {
                    if (Interlocked.Increment(ref inspected) > MaximumInspected)
                    {
                        observationTruncated = true;
                        break;
                    }
                    try
                    {
                        if ((bool)item.GetType().GetMethod("IsIssue").Invoke(item, null))
                            issue(Diagnostic(item));
                    }
                    catch (Exception error)
                        when (error is TargetInvocationException
                            || error is InvalidCastException
                            || error is InvalidOperationException
                            || error is ArgumentException
                        )
                    {
                        observationError = "diagnostic_conversion_failed";
                        observationTruncated = true;
                        break;
                    }
                }
            });
            Bind(parameters, "OnModuleCompleted", value => moduleCompleted(value.ToString()), moduleResultArgument);
            Bind(parameters, "OnCompleted", _ => completed());
            var arguments = auditAsync.GetParameters().Length == 1 ? new[] { parameters } : new[] { parameters, null };
            beforeInvoke();
            auditAsync.Invoke(auditor, arguments);
        }

        internal static void Bind(object parameters, string name, Action<object> callback, int valueIndex = 0)
        {
            var field = parameters.GetType().GetField(name);
            var arguments = field.FieldType.GetMethod("Invoke").GetParameters().Select(parameter => Expression.Parameter(parameter.ParameterType)).ToArray();
            var body = Expression.Invoke(Expression.Constant(callback), Expression.Convert(arguments[valueIndex], typeof(object)));
            field.SetValue(parameters, Expression.Lambda(field.FieldType, body, arguments).Compile());
        }

        internal static JObject Diagnostic(object item)
        {
            object id = Member(item, "Id");
            object descriptor = id?.GetType().GetMethod("GetDescriptor")?.Invoke(id, null);
            return new JObject
            {
                ["Category"] = Member(item, "Category")?.ToString(),
                ["Severity"] = Member(item, "Severity")?.ToString(),
                ["Areas"] = Member(descriptor, "Areas")?.ToString(),
                ["Description"] = Member(item, "Description")?.ToString(),
                ["RelativePath"] = Member(item, "RelativePath")?.ToString(),
                ["Line"] = Member(item, "Line") == null ? JValue.CreateNull() : JToken.FromObject(Member(item, "Line")),
                ["DescriptorId"] = id?.ToString(),
                ["Recommendation"] = Member(descriptor, "Recommendation")?.ToString(),
            };
        }

        private static object Member(object instance, string name) =>
            instance?.GetType().GetProperty(name)?.GetValue(instance) ?? instance?.GetType().GetField(name)?.GetValue(instance);
    }
}
