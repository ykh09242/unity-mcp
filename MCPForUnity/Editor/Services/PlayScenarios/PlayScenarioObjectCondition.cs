using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Exact current-scene conditions. Native references exist only during one observation.</summary>
    internal static class PlayScenarioObjectCondition
    {
        internal const int MaximumInspectedObjects = 100000;

        internal struct Matches
        {
            public GameObject Target;
            public int Count;
            public int ActiveCount;
        }

        internal static string[] ParseTarget(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || path.Length > 4096 || path.Any(char.IsControl) || path.IndexOf('\\') >= 0)
                throw new ArgumentException("Target must be a bounded exact relative hierarchy path.");
            string[] segments = path.Split('/');
            if (segments.Length > 128 || segments.Any(part => part.Length == 0 || part == "." || part == ".."))
                throw new ArgumentException("Target must be an exact relative hierarchy path with at most 128 levels.");
            return segments;
        }

        internal static Type ValidateCondition(PlayScenarioStep step)
        {
            int expectedCount = step.Count ?? 1;
            if (step.TargetId != null && expectedCount > 1)
                throw new ArgumentException("ID target conditions require count 0 or 1.");
            if (expectedCount < 0 || expectedCount > 10000)
                throw new ArgumentException("count must be between 0 and 10000.");
            if (expectedCount == 0 && (step.Active.HasValue || step.Component != null || step.Property != null))
                throw new ArgumentException("An absence condition cannot specify active, component or property.");
            if ((step.Component != null || step.Property != null) && expectedCount != 1)
                throw new ArgumentException("Component and property conditions require count 1.");
            if (step.Property != null && step.Component == null)
                throw new ArgumentException("A serialized property condition requires component.");
            Type componentType = null;
            if (step.Component != null)
            {
                string name = step.Component;
                if (string.IsNullOrWhiteSpace(name) || name.Length > 256 || name.Any(char.IsControl))
                    throw new ArgumentException("component must be a bounded exact Component type full name.");
                if (!UnityTypeResolver.TryResolve(name, out componentType, out string error, typeof(Component)))
                    throw new ArgumentException("Invalid component type: " + error);
                if (!string.Equals(componentType.FullName, name, StringComparison.Ordinal) || componentType.ContainsGenericParameters)
                    throw new ArgumentException("component must be an exact Component type full name.");
            }
            if (step.Property != null)
            {
                string path = step.Property.Path;
                JToken expected = step.Property.Equals;
                if (string.IsNullOrWhiteSpace(path) || path.Length > 256 || path.Any(char.IsControl))
                    throw new ArgumentException("property.path must be a bounded exact serialized property path.");
                if (
                    expected == null
                    || (
                        expected.Type != JTokenType.Boolean
                        && expected.Type != JTokenType.Integer
                        && expected.Type != JTokenType.Float
                        && expected.Type != JTokenType.String
                    )
                )
                    throw new ArgumentException("property.equals must be a boolean, signed integer, finite number or string.");
                if (expected.Type == JTokenType.String && expected.Value<string>().Length > 1024)
                    throw new ArgumentException("property.equals string exceeds 1024 characters.");
                if (expected.Type == JTokenType.Integer && !long.TryParse(expected.ToString(), out _))
                    throw new ArgumentException("property.equals integer must fit a signed 64-bit value.");
                if (expected.Type == JTokenType.Float && (double.IsNaN(expected.Value<double>()) || double.IsInfinity(expected.Value<double>())))
                    throw new ArgumentException("property.equals number must be finite.");
            }
            return componentType;
        }

        internal static Matches Resolve(Scene scene, string[] segments)
        {
            var matches = new Matches();
            if (!scene.IsValid() || !scene.isLoaded)
                return matches;
            if (scene.rootCount > MaximumInspectedObjects)
                throw new InvalidOperationException("Target inspection exceeds the bounded hierarchy budget.");
            int inspected = 0;
            foreach (GameObject root in scene.GetRootGameObjects())
            {
                Inspect(ref inspected);
                if (root.name == segments[0])
                    Match(root.transform, segments, 1, ref matches, ref inspected);
            }
            return matches;
        }

        internal static void ValidateSelector(PlayScenarioStep step)
        {
            if ((step.Target != null) == (step.TargetId != null))
                throw new ArgumentException("Specify exactly one target or target_id.");
            if (step.TargetId != null && !PlayScenarioTarget.IsValidTargetId(step.TargetId))
                throw new ArgumentException("target_id must match [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}.");
        }

        internal static Matches ResolveId(Scene scene, string identifier)
        {
            var matches = new Matches();
            if (!scene.IsValid() || !scene.isLoaded)
                return matches;
            if (!PlayScenarioTarget.IsValidTargetId(identifier))
                throw new ArgumentException("target_id must match [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}.");
            int inspected = 0;
            var pending = new Stack<Transform>();
            if (scene.rootCount > MaximumInspectedObjects)
                throw new InvalidOperationException("Target inspection exceeds the bounded hierarchy budget.");
            foreach (GameObject root in scene.GetRootGameObjects())
            {
                Inspect(ref inspected);
                pending.Push(root.transform);
            }
            while (pending.Count > 0)
            {
                Transform current = pending.Pop();
                PlayScenarioTarget marker = current.GetComponent<PlayScenarioTarget>();
                if (marker != null && string.Equals(marker.TargetId, identifier, StringComparison.Ordinal))
                {
                    matches.Count++;
                    if (current.gameObject.activeInHierarchy)
                        matches.ActiveCount++;
                    if (matches.Target == null)
                        matches.Target = current.gameObject;
                    if (matches.Count > 1)
                        throw new PlayScenarioException(
                            new PlayScenarioFailure
                            {
                                Code = "target_ambiguous",
                                Target = identifier,
                                Expected = "one unique ID in the active scene",
                                Actual = "at least two matching markers, including inactive objects",
                                Message = "Target ID is ambiguous in the active scene, including inactive objects.",
                            }
                        );
                }
                for (int index = 0; index < current.childCount; index++)
                {
                    Inspect(ref inspected);
                    pending.Push(current.GetChild(index));
                }
            }
            return matches;
        }

        private static void Match(Transform parent, string[] segments, int index, ref Matches matches, ref int inspected)
        {
            if (index == segments.Length)
            {
                matches.Count++;
                if (parent.gameObject.activeInHierarchy)
                    matches.ActiveCount++;
                if (matches.Target == null)
                    matches.Target = parent.gameObject;
                return;
            }
            for (int i = 0; i < parent.childCount; i++)
            {
                Inspect(ref inspected);
                Transform child = parent.GetChild(i);
                if (child.name == segments[index])
                    Match(child, segments, index + 1, ref matches, ref inspected);
            }
        }

        private static void Inspect(ref int inspected)
        {
            if (++inspected > MaximumInspectedObjects)
                throw new InvalidOperationException("Target inspection exceeds the bounded hierarchy budget.");
        }

        internal static PlayScenarioObservation Observe(PlayScenarioStep step, Matches matches, Type componentType)
        {
            if (!step.Count.HasValue && matches.Count > 1)
                throw new PlayScenarioException(
                    new PlayScenarioFailure
                    {
                        Code = "target_ambiguous",
                        Target = step.Target,
                        Expected = "1",
                        Actual = matches.Count.ToString(System.Globalization.CultureInfo.InvariantCulture),
                        Message = "Target path is ambiguous in the active scene, including inactive objects.",
                    }
                );
            int expectedCount = step.Count ?? 1;
            if (step.TargetId != null && expectedCount > 1)
                throw new ArgumentException("ID target conditions require count 0 or 1.");
            string counts =
                (step.TargetId == null ? "Exact path matches: " : "Exact ID matches: ")
                + matches.Count
                + "; expected: "
                + expectedCount
                + "; active matches: "
                + matches.ActiveCount
                + ".";
            if (matches.Count != expectedCount)
                return new PlayScenarioObservation(
                    false,
                    counts,
                    new PlayScenarioFailure
                    {
                        Code = matches.Count == 0 ? "target_missing" : "condition_unmet",
                        Target = step.TargetId ?? step.Target,
                        Expected = expectedCount.ToString(System.Globalization.CultureInfo.InvariantCulture),
                        Actual = matches.Count.ToString(System.Globalization.CultureInfo.InvariantCulture),
                        Message = counts,
                    }
                );
            if (expectedCount == 0)
                return new PlayScenarioObservation(true, counts + " Target is absent.");
            bool expectedActive = step.Active ?? true;
            if ((expectedActive && matches.ActiveCount != matches.Count) || (!expectedActive && matches.ActiveCount != 0))
                return Pending(
                    step,
                    counts + " Waiting for every match to be " + (expectedActive ? "active." : "inactive."),
                    "condition_unmet",
                    expectedActive ? "all active" : "all inactive",
                    matches.ActiveCount + " active of " + matches.Count
                );
            if (componentType == null)
                return new PlayScenarioObservation(true, counts + " Every match has the required active state.");
            Component component = matches.Target.GetComponent(componentType);
            if (component == null)
                return Pending(step, counts + " Waiting for component " + step.Component + ".", "condition_unmet", step.Component, "absent");
            if (step.Property == null)
                return new PlayScenarioObservation(true, counts + " Required component exists.");
            bool equal = ReadScalar(component, step.Property, out JToken actual);
            string propertyDetail =
                " Serialized property "
                + step.Property.Path
                + ": "
                + actual.ToString(Formatting.None)
                + "; expected: "
                + step.Property.Equals.ToString(Formatting.None)
                + ".";
            string detail = PlayScenarioEngine.Bounded(counts + propertyDetail, 2048);
            return equal
                ? new PlayScenarioObservation(true, detail)
                : Pending(step, detail, "property_mismatch", step.Property.Equals.ToString(Formatting.None), actual.ToString(Formatting.None));
        }

        private static PlayScenarioObservation Pending(PlayScenarioStep step, string detail, string code, string expected, string actual) =>
            new PlayScenarioObservation(
                false,
                detail,
                new PlayScenarioFailure
                {
                    Code = code,
                    Target = step.TargetId ?? step.Target,
                    Component = step.Component,
                    PropertyPath = step.Property?.Path,
                    Expected = expected,
                    Actual = actual,
                    Message = detail,
                }
            );

        internal static bool ReadScalar(Component component, PlayScenarioPropertyCondition condition, out JToken actual)
        {
            using var serialized = new SerializedObject(component);
            using var property = serialized.FindProperty(condition.Path);
            if (property == null)
                throw new ArgumentException("Serialized property was not found: " + condition.Path + ". Reflection getters are unsupported.");
            JToken expected = condition.Equals;
            switch (property.propertyType)
            {
                case SerializedPropertyType.Boolean:
                    if (expected.Type != JTokenType.Boolean)
                        throw new ArgumentException("A boolean serialized property requires a boolean equals value.");
                    actual = new JValue(property.boolValue);
                    return property.boolValue == expected.Value<bool>();
                case SerializedPropertyType.Integer:
                    if (expected.Type != JTokenType.Integer)
                        throw new ArgumentException("An integer serialized property requires an integer equals value.");
                    actual = new JValue(property.longValue);
                    return property.longValue == expected.Value<long>();
                case SerializedPropertyType.Float:
                    if (expected.Type != JTokenType.Integer && expected.Type != JTokenType.Float)
                        throw new ArgumentException("A numeric serialized property requires a numeric equals value.");
                    if (property.type == "float")
                    {
                        float value = expected.Value<float>();
                        if (float.IsNaN(value) || float.IsInfinity(value))
                            throw new ArgumentException("equals is outside the serialized float range.");
                        actual = new JValue(property.floatValue);
                        return property.floatValue.Equals(value);
                    }
                    actual = new JValue(property.doubleValue);
                    return property.doubleValue.Equals(expected.Value<double>());
                case SerializedPropertyType.String:
                    if (expected.Type != JTokenType.String)
                        throw new ArgumentException("A string serialized property requires a string equals value.");
                    actual = new JValue(PlayScenarioEngine.Bounded(property.stringValue, 1024));
                    return string.Equals(property.stringValue, expected.Value<string>(), StringComparison.Ordinal);
                default:
                    throw new ArgumentException(
                        "Unsupported serialized property type: " + property.propertyType + ". Only boolean, integer, float and string values are supported."
                    );
            }
        }
    }
}
