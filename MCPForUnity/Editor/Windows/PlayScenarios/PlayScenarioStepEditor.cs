using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.CompilerServices;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    /// <summary>Builds step fields. Selection is copied to a string selector and never retained.</summary>
    internal static class PlayScenarioStepEditor
    {
        private static readonly List<string> Actions = new List<string> { "load_scene", "click_ui", "wait_scene", "wait_object", "reset_state", "wait_state" };

        private sealed class StateValueDraft
        {
            internal string Type;
            internal string Text;
        }

        private static readonly ConditionalWeakTable<PlayScenarioStep, StateValueDraft> StateValueDrafts =
            new ConditionalWeakTable<PlayScenarioStep, StateValueDraft>();

        internal static void Render(ScrollView stepList, PlayScenarioDefinition draft, Action<Action> edit, Action<string, bool> setMessage) =>
            Render(stepList, draft.Steps, "", edit, setMessage);

        internal static void Render(
            ScrollView stepList,
            List<PlayScenarioStep> steps,
            string prefix,
            Action<Action> edit,
            Action<string, bool> setMessage,
            int limit = 32
        )
        {
            stepList.Clear();
            for (int index = 0; index < steps.Count; index++)
            {
                int position = index;
                PlayScenarioStep step = steps[index];
                string suffix = prefix + index;
                var card = new VisualElement { name = "step" + suffix };
                card.AddToClassList("scenario-step");
                var toolbar = Row();
                toolbar.Add(new Label("Step " + (index + 1)));
                var up = ActionButton("Up", "moveStepUp" + suffix, () => MoveStep(stepList, steps, prefix, position, -1, edit, setMessage, limit));
                up.SetEnabled(index > 0);
                var down = ActionButton("Down", "moveStepDown" + suffix, () => MoveStep(stepList, steps, prefix, position, 1, edit, setMessage, limit));
                down.SetEnabled(index < steps.Count - 1);
                toolbar.Add(up);
                toolbar.Add(down);
                var duplicate = ActionButton(
                    "Duplicate",
                    "duplicateStep" + suffix,
                    () => DuplicateStep(stepList, steps, prefix, position, edit, setMessage, limit)
                );
                duplicate.SetEnabled(steps.Count < limit);
                toolbar.Add(duplicate);
                toolbar.Add(ActionButton("Delete Step", "deleteStep" + suffix, () => DeleteStep(stepList, steps, prefix, position, edit, setMessage, limit)));
                card.Add(toolbar);
                var stepName = new TextField("Step name") { name = "stepName" + suffix, value = step.Name };
                stepName.RegisterValueChangedCallback(evt => edit(() => step.Name = evt.newValue));
                card.Add(stepName);
                var action = new PopupField<string>("Action", Actions, Actions.IndexOf(step.Action)) { name = "stepAction" + suffix };
                action.RegisterValueChangedCallback(evt =>
                {
                    StateValueDrafts.Remove(step);
                    edit(() =>
                    {
                        step.Action = evt.newValue;
                        step.Scene = IsSceneAction(step.Action) ? "" : null;
                        step.Target = IsSceneAction(step.Action) || step.Action == "reset_state" || step.Action == "wait_state" ? null : "";
                        step.ResetIds = step.Action == "reset_state" ? new List<string>() : null;
                        step.StateId = step.Action == "wait_state" ? "" : null;
                        step.StateEquals = step.Action == "wait_state" ? new JValue("") : null;
                        step.TargetId = null;
                        step.ClickMode = step.Action == "click_ui" ? "direct" : null;
                        step.Count = null;
                        step.Active = null;
                        step.Component = null;
                        step.Property = null;
                        step.StableForMs = null;
                    });
                    Render(stepList, steps, prefix, edit, setMessage, limit);
                });
                card.Add(action);
                if (step.Action == "reset_state")
                    BuildResetFields(card, step, suffix, edit);
                else if (step.Action == "wait_state")
                    BuildStateFields(card, step, suffix, edit);
                else if (IsSceneAction(step.Action))
                    BuildSceneFields(card, step, suffix, edit);
                else
                    BuildTargetFields(card, step, suffix, edit, setMessage);
                if (step.Action == "click_ui")
                    BuildClickFields(card, step, suffix, edit);
                var timeout = new IntegerField("Timeout (s)") { name = "stepTimeout" + suffix, value = step.TimeoutSeconds };
                timeout.RegisterValueChangedCallback(evt => edit(() => step.TimeoutSeconds = evt.newValue));
                card.Add(timeout);
                if (step.Action == "wait_scene" || step.Action == "wait_object" || step.Action == "wait_state")
                    OptionalInteger(card, "Stable duration (ms)", "stepStable" + suffix, step.StableForMs, value => edit(() => step.StableForMs = value));
                if (step.Action == "wait_object")
                    BuildConditionFields(card, step, suffix, edit);
                stepList.Add(card);
            }
        }

        private static void BuildStateFields(VisualElement card, PlayScenarioStep step, string index, Action<Action> edit)
        {
            var id = new TextField("State ID") { name = "stepStateId" + index, value = step.StateId ?? "" };
            id.RegisterValueChangedCallback(evt => edit(() => step.StateId = evt.newValue));
            card.Add(id);
            var raw = StateValueDrafts.GetValue(
                step,
                value =>
                {
                    JToken expected = value.StateEquals;
                    return new StateValueDraft
                    {
                        Type =
                            expected?.Type == JTokenType.Boolean ? "Boolean"
                            : expected?.Type == JTokenType.Integer ? "Integer"
                            : expected?.Type == JTokenType.Float ? "Number"
                            : "String",
                        Text = expected?.Type == JTokenType.String ? (string)expected : expected?.ToString(Formatting.None) ?? "",
                    };
                }
            );
            var types = new List<string> { "String", "Boolean", "Integer", "Number" };
            var type = new PopupField<string>("Value type", types, types.IndexOf(raw.Type)) { name = "stepStateType" + index };
            var valueField = new TextField("Expected value") { name = "stepStateValue" + index, value = raw.Text };
            var error = new Label { name = "stepStateError" + index };
            error.AddToClassList("scenario-error");
            error.text = TryStateScalar(raw.Type, raw.Text, out _) ? "" : StateScalarError(raw.Type);
            Action update = () =>
            {
                raw.Type = type.value;
                raw.Text = valueField.value;
                bool valid = TryStateScalar(raw.Type, raw.Text, out JToken token);
                error.text = valid ? "" : StateScalarError(raw.Type);
                edit(() => step.StateEquals = token);
            };
            type.RegisterValueChangedCallback(_ => update());
            valueField.RegisterValueChangedCallback(_ => update());
            card.Add(type);
            card.Add(valueField);
            card.Add(error);
            card.Add(
                new Label(
                    "Waits for an explicitly registered scalar state probe. IDs are case-sensitive stable IDs, as with reset_state. This action does not query scenes, hierarchy objects or reflected properties. Boolean: true/false; Integer: signed 64-bit; Number: finite invariant decimal; String: up to 1024 characters. Stable duration requires a continuous match."
                )
            );
        }

        private static string StateScalarError(string type) =>
            type == "String" ? "Enter a string of at most 1024 characters."
            : type == "Boolean" ? "Enter true or false."
            : type == "Integer" ? "Enter a signed 64-bit integer."
            : "Enter a finite number using invariant decimal or exponent notation.";

        private static bool TryStateScalar(string type, string text, out JToken token)
        {
            token = null;
            if (text == null)
                return false;
            if (type == "Boolean")
            {
                if (!bool.TryParse(text, out bool parsed))
                    return false;
                token = new JValue(parsed);
            }
            else if (type == "Integer")
            {
                if (!long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out long parsed))
                    return false;
                token = new JValue(parsed);
            }
            else if (type == "Number")
            {
                if (
                    !double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double parsed)
                    || double.IsNaN(parsed)
                    || double.IsInfinity(parsed)
                )
                    return false;
                token = new JValue(parsed);
            }
            else
            {
                if (text.Length > 1024)
                    return false;
                token = new JValue(text);
            }
            return true;
        }

        private static void BuildResetFields(VisualElement card, PlayScenarioStep step, string index, Action<Action> edit)
        {
            var ids = new TextField("Reset IDs (one per line)")
            {
                name = "stepResetIds" + index,
                multiline = true,
                value = string.Join("\n", step.ResetIds ?? new List<string>()),
            };
            ids.RegisterValueChangedCallback(evt =>
                edit(() => step.ResetIds = new List<string>(evt.newValue.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries)))
            );
            card.Add(ids);
            card.Add(
                new Label(
                    "Use 1–16 unique, case-sensitive stable IDs registered by reset participants. Place after load_scene in Setup to reset before each repeat. BeginReset runs once; completion is polled until this step's timeout."
                )
            );
        }

        private static bool IsSceneAction(string action) => action == "load_scene" || action == "wait_scene";

        private static void BuildSceneFields(VisualElement card, PlayScenarioStep step, string index, Action<Action> edit)
        {
            var scene = new ObjectField("Scene asset")
            {
                name = "stepSceneAsset" + index,
                objectType = typeof(SceneAsset),
                allowSceneObjects = false,
            };
            if (!string.IsNullOrEmpty(step.Scene) && step.Scene.IndexOf("/GameData/", StringComparison.OrdinalIgnoreCase) < 0)
                scene.SetValueWithoutNotify(AssetDatabase.LoadAssetAtPath<SceneAsset>(step.Scene));
            var path = new TextField("Scene path")
            {
                name = "stepScene" + index,
                value = step.Scene ?? "",
                isDelayed = true,
            };
            path.tooltip = "Choose a SceneAsset or enter its canonical Assets/.../*.unity path.";
            scene.RegisterValueChangedCallback(evt =>
            {
                string value = evt.newValue == null ? "" : AssetDatabase.GetAssetPath(evt.newValue);
                edit(() => step.Scene = value);
                path.SetValueWithoutNotify(value);
            });
            path.RegisterValueChangedCallback(evt =>
            {
                edit(() => step.Scene = evt.newValue);
                // Clear an old picker value when the manually entered path changes.
                scene.SetValueWithoutNotify(null);
            });
            card.Add(scene);
            card.Add(path);
        }

        private static void BuildTargetFields(VisualElement card, PlayScenarioStep step, string index, Action<Action> edit, Action<string, bool> setMessage)
        {
            var selector = new PopupField<string>("Target selector", new List<string> { "Hierarchy path", "Test ID" }, step.TargetId != null ? 1 : 0)
            {
                name = "stepTargetSelector" + index,
            };
            var fields = new VisualElement();
            Action renderTarget = () =>
            {
                fields.Clear();
                bool useId = step.TargetId != null;
                var target = new TextField(useId ? "Stable test ID" : "Exact hierarchy path")
                {
                    name = (useId ? "stepTargetId" : "stepTarget") + index,
                    value = (useId ? step.TargetId : step.Target) ?? "",
                };
                target.tooltip = useId
                    ? "Unique PlayScenarioTarget marker ID in the active scene, including inactive objects."
                    : "Exact active-scene hierarchy path, for example Canvas/StartButton or World/Player.";
                target.RegisterValueChangedCallback(evt =>
                    edit(() =>
                    {
                        if (useId)
                            step.TargetId = evt.newValue;
                        else
                            step.Target = evt.newValue;
                    })
                );
                fields.Add(target);
                fields.Add(
                    ActionButton(
                        "Use Current Selection",
                        "useSelection" + index,
                        () =>
                        {
                            var selected = Selection.activeGameObject;
                            if (selected == null || !selected.scene.IsValid())
                            {
                                setMessage("Select a scene GameObject first.", true);
                                return;
                            }
                            string value;
                            if (useId)
                            {
                                var marker = selected.GetComponent<MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget>();
                                if (marker == null || string.IsNullOrEmpty(marker.TargetId))
                                {
                                    setMessage(
                                        "Selection needs a PlayScenarioTarget marker with a nonempty ID. Author the marker explicitly in the Inspector.",
                                        true
                                    );
                                    return;
                                }
                                value = marker.TargetId;
                            }
                            else
                            {
                                var names = new List<string>();
                                for (Transform current = selected.transform; current != null; current = current.parent)
                                    names.Add(current.name);
                                names.Reverse();
                                value = string.Join("/", names);
                            }
                            edit(() =>
                            {
                                if (useId)
                                    step.TargetId = value;
                                else
                                    step.Target = value;
                            });
                            target.SetValueWithoutNotify(value);
                        }
                    )
                );
            };
            selector.RegisterValueChangedCallback(evt =>
            {
                edit(() =>
                {
                    step.TargetId = evt.newValue == "Test ID" ? "" : null;
                    step.Target = evt.newValue == "Hierarchy path" ? "" : null;
                });
                renderTarget();
            });
            card.Add(selector);
            card.Add(fields);
            renderTarget();
        }

        private static void BuildClickFields(VisualElement card, PlayScenarioStep step, string index, Action<Action> edit)
        {
            var click = new PopupField<string>(
                "uGUI click mode",
                new List<string> { "Direct handler", "Raycast + pointer events" },
                step.ClickMode == "raycast" ? 1 : 0
            )
            {
                name = "stepClickMode" + index,
            };
            click.RegisterValueChangedCallback(evt => edit(() => step.ClickMode = evt.newValue == "Raycast + pointer events" ? "raycast" : "direct"));
            card.Add(click);
            card.Add(
                new Label("Direct invokes the handler. Raycast checks the target at its center before dispatching pointer events. Neither mode sends OS input.")
            );
        }

        private static void OptionalInteger(VisualElement card, string label, string name, int? value, Action<int?> change)
        {
            var enabled = new Toggle(label) { name = name + "Enabled", value = value.HasValue };
            var field = new IntegerField { name = name, value = value ?? 0 };
            field.SetEnabled(value.HasValue);
            enabled.RegisterValueChangedCallback(evt =>
            {
                field.SetEnabled(evt.newValue);
                change(evt.newValue ? field.value : (int?)null);
            });
            field.RegisterValueChangedCallback(evt => change(evt.newValue));
            card.Add(enabled);
            card.Add(field);
        }

        private static void BuildConditionFields(VisualElement card, PlayScenarioStep step, string index, Action<Action> edit)
        {
            var conditions = new Foldout { text = "Object conditions", name = "stepConditions" + index };
            OptionalInteger(conditions, "Require exact count", "stepCount" + index, step.Count, value => edit(() => step.Count = value));
            var active = new PopupField<string>(
                "Active state",
                new List<string> { "Default (active)", "Active", "Inactive" },
                step.Active.HasValue ? (step.Active.Value ? 1 : 2) : 0
            )
            {
                name = "stepActive" + index,
            };
            active.RegisterValueChangedCallback(evt => edit(() => step.Active = evt.newValue == "Default (active)" ? (bool?)null : evt.newValue == "Active"));
            conditions.Add(active);
            var component = new TextField("Component full name")
            {
                name = "stepComponent" + index,
                value = step.Component ?? "",
                isDelayed = true,
            };
            component.RegisterValueChangedCallback(evt => edit(() => step.Component = string.IsNullOrEmpty(evt.newValue) ? null : evt.newValue));
            conditions.Add(component);
            var property = new Toggle("Require serialized property") { name = "stepPropertyEnabled" + index, value = step.Property != null };
            var fields = new VisualElement();
            var path = new TextField("Property path")
            {
                name = "stepPropertyPath" + index,
                value = step.Property?.Path ?? "",
                isDelayed = true,
            };
            JToken expected = step.Property?.Equals;
            var types = new List<string> { "String", "Boolean", "Integer", "Number" };
            int selected =
                expected?.Type == JTokenType.Boolean ? 1
                : expected?.Type == JTokenType.Integer ? 2
                : expected?.Type == JTokenType.Float ? 3
                : 0;
            var type = new PopupField<string>("Value type", types, selected) { name = "stepPropertyType" + index };
            var value = new TextField("Expected value")
            {
                name = "stepPropertyValue" + index,
                value = expected?.ToString() ?? "",
                isDelayed = true,
            };
            var error = new Label { name = "stepPropertyError" + index };
            error.AddToClassList("scenario-error");
            Action update = () =>
            {
                JToken token = null;
                bool valid = true;
                if (type.value == "Boolean")
                {
                    valid = bool.TryParse(value.value, out bool parsed);
                    if (valid)
                        token = new JValue(parsed);
                }
                else if (type.value == "Integer")
                {
                    valid = long.TryParse(
                        value.value,
                        System.Globalization.NumberStyles.Integer,
                        System.Globalization.CultureInfo.InvariantCulture,
                        out long parsed
                    );
                    if (valid)
                        token = new JValue(parsed);
                }
                else if (type.value == "Number")
                {
                    valid =
                        double.TryParse(
                            value.value,
                            System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture,
                            out double parsed
                        )
                        && !double.IsNaN(parsed)
                        && !double.IsInfinity(parsed);
                    if (valid)
                        token = new JValue(parsed);
                }
                else
                    token = new JValue(value.value);
                error.text = valid ? "" : "Enter a valid " + type.value.ToLowerInvariant() + " value.";
                edit(() => step.Property = property.value ? new PlayScenarioPropertyCondition { Path = path.value, Equals = token } : null);
            };
            property.RegisterValueChangedCallback(evt =>
            {
                fields.SetEnabled(evt.newValue);
                update();
            });
            path.RegisterValueChangedCallback(_ => update());
            type.RegisterValueChangedCallback(_ => update());
            value.RegisterValueChangedCallback(_ => update());
            fields.Add(path);
            fields.Add(type);
            fields.Add(value);
            fields.Add(error);
            fields.SetEnabled(property.value);
            conditions.Add(property);
            conditions.Add(fields);
            conditions.Add(new Label("Component/property checks require one match. Count 0 forbids active/component/property checks."));
            card.Add(conditions);
        }

        private static void DuplicateStep(
            ScrollView stepList,
            List<PlayScenarioStep> steps,
            string prefix,
            int index,
            Action<Action> edit,
            Action<string, bool> setMessage,
            int limit
        )
        {
            if (steps.Count >= limit)
                return;
            var copy = JObject.FromObject(steps[index], JsonSerializer.Create()).ToObject<PlayScenarioStep>(JsonSerializer.Create());
            edit(() => steps.Insert(index + 1, copy));
            Render(stepList, steps, prefix, edit, setMessage, limit);
        }

        private static void DeleteStep(
            ScrollView stepList,
            List<PlayScenarioStep> steps,
            string prefix,
            int index,
            Action<Action> edit,
            Action<string, bool> setMessage,
            int limit
        )
        {
            edit(() => steps.RemoveAt(index));
            Render(stepList, steps, prefix, edit, setMessage, limit);
        }

        private static void MoveStep(
            ScrollView stepList,
            List<PlayScenarioStep> steps,
            string prefix,
            int index,
            int offset,
            Action<Action> edit,
            Action<string, bool> setMessage,
            int limit
        )
        {
            int destination = index + offset;
            if (destination < 0 || destination >= steps.Count)
                return;
            edit(() =>
            {
                var step = steps[index];
                steps.RemoveAt(index);
                steps.Insert(destination, step);
            });
            Render(stepList, steps, prefix, edit, setMessage, limit);
        }

        private static VisualElement Row()
        {
            var row = new VisualElement();
            row.AddToClassList("scenario-row");
            return row;
        }

        private static Button ActionButton(string text, string name, Action action) => new Button(action) { text = text, name = name };
    }
}
