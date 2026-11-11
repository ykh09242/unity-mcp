using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Linq.Expressions;
using System.Reflection;
using System.Threading;
using Newtonsoft.Json.Linq;
using UnityEditor.PackageManager;

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

    // This adapter is deliberately pinned: optional package internals must be reviewed before widening support.
    internal sealed class ProjectAuditorApi : IProjectAuditorBackend
    {
        internal const string PackageName = "com.unity.project-auditor";
        internal const string SupportedVersion = "1.0.1";
        private const string Namespace = "Unity.ProjectAuditor.Editor.";
        private const int MaximumInspected = 250000;
        private readonly Type auditorType = AppDomain
            .CurrentDomain.GetAssemblies()
            .Select(assembly => assembly.GetType(Namespace + "ProjectAuditor", false))
            .FirstOrDefault(type => type != null);
        private object auditor;
        private int inspected;
        private volatile bool observationTruncated;
        private volatile string observationError;
        public bool ObservationTruncated => observationTruncated;
        public string ObservationError => observationError;

        public JObject Availability()
        {
            var package =
                auditorType == null
                    ? PackageInfo.GetAllRegisteredPackages().FirstOrDefault(info => info.name == PackageName)
                    : PackageInfo.FindForAssembly(auditorType.Assembly);
            var data = new JObject
            {
                ["state"] = "unavailable",
                ["package"] = PackageName,
                ["supported_version"] = SupportedVersion,
                ["package_installed"] = package != null,
                ["version"] = package?.version,
                ["api_supported"] = false,
                ["rules_available"] = false,
                ["registered_rules_observed"] = false,
                ["cancel_supported"] = false,
                ["external_audits_observable"] = false,
            };
            if (auditorType == null)
            {
                data["reason"] = package == null ? "package_not_installed" : "package_assembly_not_loaded";
                return data;
            }
            if (package?.name != PackageName || package.version != SupportedVersion)
            {
                data["reason"] = "unsupported_package_version";
                return data;
            }
            var parameters = auditorType.Assembly.GetType(Namespace + "AnalysisParams");
            var report = auditorType.Assembly.GetType(Namespace + "Report");
            bool supported =
                parameters != null
                && report != null
                && parameters.GetConstructor(new[] { typeof(bool) }) != null
                && auditorType
                    .GetMethods()
                    .Any(method => method.Name == "AuditAsync" && method.GetParameters().Length == 2 && method.GetParameters()[0].ParameterType == parameters)
                && parameters.GetField("OnIncomingIssues") != null
                && parameters.GetField("OnCompleted") != null
                && parameters.GetField("OnModuleCompleted") != null
                && auditorType.GetMethod("GetDescriptorIDs", BindingFlags.Instance | BindingFlags.NonPublic) != null;
            data["api_supported"] = supported;
            if (!supported)
            {
                data["reason"] = "unsupported_api_shape";
                return data;
            }
            try
            {
                int count =
                    ReadRules(Path.Combine(package.resolvedPath, "Data", "ApiDatabase.json"))
                    + ReadRules(Path.Combine(package.resolvedPath, "Data", "ProjectSettings.json"));
                data["bundled_rule_count"] = count;
                data["rules_available"] = true;
                data["state"] = "available";
                data["reason"] = "supported_api_and_bundled_rules";
            }
            catch (Exception error)
                when (error is IOException || error is UnauthorizedAccessException || error is Newtonsoft.Json.JsonException || error is InvalidDataException)
            {
                data["reason"] = "rules_missing_or_invalid";
            }
            return data;
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
            var descriptors = (Array)auditorType.GetMethod("GetDescriptorIDs", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(auditor, null);
            return descriptors?.Length ?? 0;
        }

        public void Start(Action<JObject> issue, Action<string> moduleCompleted, Action completed, Action beforeInvoke)
        {
            var parameterType = auditorType.Assembly.GetType(Namespace + "AnalysisParams");
            // The official default constructor registers thresholds and saves Auditor settings. Only explicit start reaches here.
            object parameters = Activator.CreateInstance(parameterType, new object[] { true });
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
            Bind(parameters, "OnModuleCompleted", value => moduleCompleted(value.ToString()));
            Bind(parameters, "OnCompleted", _ => completed());
            var method = auditorType
                .GetMethods()
                .Single(candidate =>
                    candidate.Name == "AuditAsync" && candidate.GetParameters().Length == 2 && candidate.GetParameters()[0].ParameterType == parameterType
                );
            beforeInvoke();
            method.Invoke(auditor, new[] { parameters, null });
        }

        private static void Bind(object parameters, string name, Action<object> callback)
        {
            var field = parameters.GetType().GetField(name);
            var argument = Expression.Parameter(field.FieldType.GetGenericArguments()[0]);
            var body = Expression.Invoke(Expression.Constant(callback), Expression.Convert(argument, typeof(object)));
            field.SetValue(parameters, Expression.Lambda(field.FieldType, body, argument).Compile());
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
