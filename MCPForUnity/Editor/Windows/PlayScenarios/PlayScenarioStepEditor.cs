using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
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

        internal static void Render(ScrollView stepList, PlayScenarioDefinition draft, Action<Action> edit, Action<string, bool> setMessage)
        {
            stepList.Clear();
            for (int index = 0; index < draft.Steps.Count; index++)
            {
                int position = index;
                PlayScenarioStep step = draft.Steps[index];
                var card = new VisualElement { name = "step" + index };
                card.AddToClassList("scenario-step");
                var toolbar = Row();
                toolbar.Add(new Label("Step " + (index + 1)));
                var up = ActionButton("Up", "moveStepUp" + index, () => MoveStep(stepList, draft, position, -1, edit, setMessage));
                up.SetEnabled(index > 0);
                var down = ActionButton("Down", "moveStepDown" + index, () => MoveStep(stepList, draft, position, 1, edit, setMessage));
                down.SetEnabled(index < draft.Steps.Count - 1);
                toolbar.Add(up);
                toolbar.Add(down);
                toolbar.Add(ActionButton("Delete Step", "deleteStep" + index, () => DeleteStep(stepList, draft, position, edit, setMessage)));
                card.Add(toolbar);
                var stepName = new TextField("Step name") { name = "stepName" + index, value = step.Name };
                stepName.RegisterValueChangedCallback(evt => edit(() => step.Name = evt.newValue));
                card.Add(stepName);
                var action = new PopupField<string>("Action", Actions, Actions.IndexOf(step.Action)) { name = "stepAction" + index };
                action.RegisterValueChangedCallback(evt =>
                {
                    edit(() =>
                    {
                        step.Action = evt.newValue;
                        step.Scene = IsSceneAction(step.Action) ? "" : null;
                        step.Target = IsSceneAction(step.Action) ? null : "";
                    });
                    Render(stepList, draft, edit, setMessage);
                });
                card.Add(action);
                if (IsSceneAction(step.Action))
                    BuildSceneFields(card, step, index, edit);
                else
                    BuildTargetFields(card, step, index, edit, setMessage);
                var timeout = new IntegerField("Timeout (s)") { name = "stepTimeout" + index, value = step.TimeoutSeconds };
                timeout.RegisterValueChangedCallback(evt => edit(() => step.TimeoutSeconds = evt.newValue));
                card.Add(timeout);
                stepList.Add(card);
            }
        }

        private static bool IsSceneAction(string action) => action == "load_scene" || action == "wait_scene";

        private static void BuildSceneFields(VisualElement card, PlayScenarioStep step, int index, Action<Action> edit)
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

        private static void BuildTargetFields(VisualElement card, PlayScenarioStep step, int index, Action<Action> edit, Action<string, bool> setMessage)
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

        private static void DeleteStep(ScrollView stepList, PlayScenarioDefinition draft, int index, Action<Action> edit, Action<string, bool> setMessage)
        {
            edit(() => draft.Steps.RemoveAt(index));
            Render(stepList, draft, edit, setMessage);
        }

        private static void MoveStep(
            ScrollView stepList,
            PlayScenarioDefinition draft,
            int index,
            int offset,
            Action<Action> edit,
            Action<string, bool> setMessage
        )
        {
            int destination = index + offset;
            if (destination < 0 || destination >= draft.Steps.Count)
                return;
            edit(() =>
            {
                var step = draft.Steps[index];
                draft.Steps.RemoveAt(index);
                draft.Steps.Insert(destination, step);
            });
            Render(stepList, draft, edit, setMessage);
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
