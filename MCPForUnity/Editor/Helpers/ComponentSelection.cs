using System;
using System.Globalization;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Live, explicit component selection shared by opt-in reads and writes. No persistent identity or mutation is retained.</summary>
    internal static class ComponentSelection
    {
        // Tests observe the real lookup boundary; production leaves this unset.
        internal static Action BeforeTargetLookupForTests = null;

        internal sealed class Failure : Exception
        {
            internal readonly string Code;
            internal readonly JObject Details;

            internal Failure(string code, string message, JObject details = null)
                : base(message)
            {
                Code = code;
                Details = details ?? new JObject();
            }

            internal ErrorResponse Response()
            {
                var data = (JObject)Details.DeepClone();
                data["reason"] = Code;
                data["message"] = Message;
                return new ErrorResponse(Code, data);
            }
        }

        internal sealed class Result
        {
            internal readonly GameObject Target;
            internal readonly Component Component;
            internal readonly Type RequestedType;
            internal readonly int Index;

            internal Result(GameObject target, Component component, Type type, int index)
            {
                Target = target;
                Component = component;
                RequestedType = type;
                Index = index;
            }

            internal JObject Describe() =>
                new JObject
                {
                    ["contractVersion"] = 1,
                    ["gameObjectInstanceID"] = Target.GetInstanceIDCompat(),
                    ["componentInstanceID"] = Component.GetInstanceIDCompat(),
                    ["requestedComponentType"] = RequestedType.FullName,
                    ["actualComponentType"] = Component.GetType().FullName,
                    ["componentIndex"] = Index,
                };
        }

        internal static JToken Alias(JObject parameters, string camel, string snake)
        {
            JToken first = parameters[camel],
                second = parameters[snake];
            if (first != null && second != null && !JToken.DeepEquals(first, second))
                throw new Failure("invalid_selection", "Conflicting selector aliases: " + camel + " and " + snake + ".");
            return first ?? second;
        }

        internal static bool HasValue(JToken token) => token != null && token.Type != JTokenType.Null;

        internal static bool IsRequested(JObject parameters, bool indexIsNew)
        {
            JToken strict = Alias(parameters, "strictSelection", "strict_selection");
            if (strict != null && strict.Type != JTokenType.Boolean)
                throw new Failure("invalid_selection", "strict_selection must be a boolean when supplied.");
            JToken id = Alias(parameters, "componentInstanceID", "component_instance_id");
            return (strict?.Value<bool>() ?? false) || HasValue(id) || (indexIsNew && HasValue(Alias(parameters, "componentIndex", "component_index")));
        }

        internal static int? Index(JToken token)
        {
            if (!HasValue(token))
                return null;
            if (
                (token.Type != JTokenType.Integer && token.Type != JTokenType.String)
                || !int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int value)
                || value < 0
            )
                throw new Failure("invalid_selection", "component_index must be a nonnegative Int32 integer.");
            return value;
        }

        internal static int? InstanceId(JToken token)
        {
            if (!HasValue(token))
                return null;
            if (
                token.Type != JTokenType.Integer
                || !int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int value)
                || value == 0
            )
                throw new Failure("invalid_selection", "component_instance_id must be a nonzero Int32 integer.");
            return value;
        }

        internal static void ValidateSelectors(JObject parameters, out int? index, out int? id)
        {
            IsRequested(parameters, true);
            index = Index(Alias(parameters, "componentIndex", "component_index"));
            id = InstanceId(Alias(parameters, "componentInstanceID", "component_instance_id"));
            if (index.HasValue && id.HasValue)
                throw new Failure("invalid_selection", "Use either component_index or component_instance_id, not both.");
        }

        internal static Type ResolveType(string name)
        {
            if (string.IsNullOrWhiteSpace(name) || name.Length > 1024)
                throw new Failure("invalid_component_type", "A canonical component type name of at most 1024 characters is required.");
            if (!UnityTypeResolver.TryResolve(name, out Type type, out string error, typeof(Component)))
                throw new Failure("invalid_component_type", error);
            return type;
        }

        internal static void EnsureTarget(GameObject target)
        {
            if (target == null)
                throw new Failure("target_not_found", "The selected GameObject no longer exists.");
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            bool currentStage = stage != null && target.scene == stage.scene;
            if (
                EditorUtility.IsPersistent(target)
                || !target.scene.IsValid()
                || !target.scene.isLoaded
                || (EditorSceneManager.IsPreviewScene(target.scene) && !currentStage)
            )
                throw new Failure("invalid_target", "Selection requires a loaded scene GameObject or the current prefab-stage contents.");
        }

        internal static Result Resolve(GameObject target, string requestedType, int? index, int? instanceId)
        {
            EnsureTarget(target);
            Type type = ResolveType(requestedType);
            if (index.HasValue && instanceId.HasValue)
                throw new Failure("invalid_selection", "Use either component_index or component_instance_id, not both.");
            Component[] matches = target.GetComponents(type);
            if (instanceId.HasValue)
            {
                var component = UnityObjectIdCompat.InstanceIDToObjectCompat(instanceId.Value) as Component;
                if (component == null)
                    throw new Failure("component_not_found", "The selected component instance ID does not resolve to a live component.");
                if (component.gameObject != target || !type.IsInstanceOfType(component))
                    throw new Failure("component_selector_mismatch", "The component instance ID does not match the selected GameObject and requested type.");
                int position = Array.IndexOf(matches, component);
                if (position < 0)
                    throw new Failure("component_not_found", "The selected component is no longer in the current component list.");
                return new Result(target, component, type, position);
            }
            if (index.HasValue)
            {
                if (index.Value < 0 || index.Value >= matches.Length)
                    throw new Failure(
                        "component_index_out_of_range",
                        "component_index is outside the current requested-type component list.",
                        new JObject { ["componentCount"] = matches.Length }
                    );
                if (matches[index.Value] == null)
                    throw new Failure("component_not_found", "The selected component slot is missing.");
                return new Result(target, matches[index.Value], type, index.Value);
            }
            if (matches.Length == 0 || (matches.Length == 1 && matches[0] == null))
                throw new Failure("component_not_found", "No component of the requested type exists on the selected GameObject.");
            if (matches.Length != 1)
                throw new Failure(
                    "ambiguous_component",
                    "More than one component matches; supply component_index or component_instance_id.",
                    new JObject { ["componentCount"] = matches.Length }
                );
            return new Result(target, matches[0], type, 0);
        }
    }
}
