using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    /// <summary>Builds step fields. Selection is copied to a path and never retained.</summary>
    internal static class PlayScenarioStepEditor
    {
        private static readonly List<string> Actions = new List<string> { "load_scene", "click_ui", "wait_scene", "wait_object" };

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
                    edit(() =>
                    {
                        step.Action = evt.newValue;
                        step.Scene = IsSceneAction(step.Action) ? "" : null;
                        step.Target = IsSceneAction(step.Action) ? null : "";
                        step.Count = null;
                        step.Active = null;
                        step.Component = null;
                        step.Property = null;
                        step.StableForMs = null;
                    });
                    Render(stepList, steps, prefix, edit, setMessage, limit);
                });
                card.Add(action);
                if (IsSceneAction(step.Action))
                    BuildSceneFields(card, step, suffix, edit);
                else
                    BuildTargetFields(card, step, suffix, edit, setMessage);
                var timeout = new IntegerField("Timeout (s)") { name = "stepTimeout" + suffix, value = step.TimeoutSeconds };
                timeout.RegisterValueChangedCallback(evt => edit(() => step.TimeoutSeconds = evt.newValue));
                card.Add(timeout);
                if (step.Action == "wait_scene" || step.Action == "wait_object")
                    OptionalInteger(card, "Stable duration (ms)", "stepStable" + suffix, step.StableForMs, value => edit(() => step.StableForMs = value));
                if (step.Action == "wait_object")
                    BuildConditionFields(card, step, suffix, edit);
                stepList.Add(card);
            }
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
            var target = new TextField("Exact hierarchy path") { name = "stepTarget" + index, value = step.Target ?? "" };
            target.tooltip =
                "Active scene hierarchy path, for example Canvas/StartButton or World/Player. Template targets are placeholders; replace them with your objects.";
            target.RegisterValueChangedCallback(evt => edit(() => step.Target = evt.newValue));
            card.Add(target);
            card.Add(
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
                        var names = new List<string>();
                        for (Transform current = selected.transform; current != null; current = current.parent)
                            names.Add(current.name);
                        names.Reverse();
                        string value = string.Join("/", names);
                        edit(() => step.Target = value);
                        target.SetValueWithoutNotify(value);
                    }
                )
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
