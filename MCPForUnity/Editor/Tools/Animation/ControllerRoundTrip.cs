using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Animation
{
    internal static class ControllerRoundTrip
    {
        internal static object Handle(JObject request, string action)
        {
            ControllerGraph.Snapshot graph = ControllerGraph.Read(request);
            AnimationAssetAccess.Expected(request, graph.Revision);
            bool stateAction = action.EndsWith("state", StringComparison.Ordinal);
            ControllerGraph.Node node = graph.Select(request, stateAction ? "stateId" : "transitionId", stateAction ? "state" : null);
            bool delete = action.StartsWith("delete_", StringComparison.Ordinal);
            var changes = new List<Action>();
            if (delete && stateAction)
            {
                var state = (AnimatorState)node.Asset;
                if (
                    node.Machine.defaultState == state
                    || state.transitions.Length > 0
                    || state.behaviours.Length > 0
                    || graph.Nodes.Any(other => other.Asset is AnimatorTransitionBase transition && transition.destinationState == state)
                )
                    throw new ArgumentException(
                        "State deletion requires a non-default state without behaviours, outgoing transitions or incoming references. Edit explicit transitions first."
                    );
            }
            else if (!delete)
            {
                if (!(request["changes"] is JObject input) || input.Count == 0 || input.Count > 16)
                    throw new ArgumentException("changes must contain 1-16 supported fields.");
                foreach (JProperty change in input.Properties())
                {
                    if (stateAction)
                        StateChange((AnimatorState)node.Asset, change, changes);
                    else
                        TransitionChange(graph, (AnimatorTransitionBase)node.Asset, change, changes);
                }
            }
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            string name = delete ? "Delete Animator Graph Object" : "Modify Animator Graph Object";
            Undo.SetCurrentGroupName(name);
            try
            {
                Undo.RegisterCompleteObjectUndo(
                    new UnityEngine.Object[] { graph.Controller, node.Asset, node.State != null ? (UnityEngine.Object)node.State : node.Machine },
                    name
                );
                if (delete)
                {
                    if (stateAction)
                        node.Machine.states = node.Machine.states.Where(child => child.state != node.Asset).ToArray();
                    else
                        RemoveTransition(node);
                    Undo.DestroyObjectImmediate(node.Asset);
                }
                else
                    foreach (Action change in changes)
                        change();
                EditorUtility.SetDirty(graph.Controller);
                if (!delete)
                    EditorUtility.SetDirty(node.Asset);
                if (node.State != null)
                    EditorUtility.SetDirty(node.State);
                else
                    EditorUtility.SetDirty(node.Machine);
                AssetDatabase.SaveAssetIfDirty(graph.Controller);
                Undo.CollapseUndoOperations(group);
                return new SuccessResponse(
                    "Exact Animator graph object edited with Undo; unrelated graph objects retained.",
                    new
                    {
                        id = delete ? request[stateAction ? "stateId" : "transitionId"].ToString() : node.Id,
                        kind = node.Kind,
                        undo_group = group,
                    }
                );
            }
            catch
            {
                Undo.RevertAllDownToGroup(group);
                throw;
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
        }

        private static void StateChange(AnimatorState state, JProperty change, List<Action> changes)
        {
            switch (change.Name)
            {
                case "name":
                    string name = AnimationAssetAccess.Text((JObject)change.Parent, "name");
                    changes.Add(() => state.name = name);
                    break;
                case "speed":
                    float speed = AnimationAssetAccess.Number(change.Value);
                    changes.Add(() => state.speed = speed);
                    break;
                case "motion":
                    UnityEngine.Object reference = AnimationAssetAccess.ResolveReference(change.Value);
                    if (reference != null && !(reference is Motion))
                        throw new ArgumentException("motion requires a Motion asset reference or null.");
                    changes.Add(() => state.motion = (Motion)reference);
                    break;
                default:
                    throw new ArgumentException("State changes support name, speed and exact asset motion references.");
            }
        }

        private static void TransitionChange(ControllerGraph.Snapshot graph, AnimatorTransitionBase transition, JProperty change, List<Action> changes)
        {
            switch (change.Name)
            {
                case "mute":
                    bool mute = AnimationAssetAccess.Boolean(change.Value);
                    changes.Add(() => transition.mute = mute);
                    return;
                case "solo":
                    bool solo = AnimationAssetAccess.Boolean(change.Value);
                    changes.Add(() => transition.solo = solo);
                    return;
                case "conditions":
                    var conditions = Conditions(graph.Controller, change.Value);
                    changes.Add(() => transition.conditions = conditions);
                    return;
                case "destinationStateId":
                    var destination = (AnimatorState)graph.Select(new JObject { ["stateId"] = change.Value }, "stateId", "state").Asset;
                    changes.Add(() => transition.destinationState = destination);
                    return;
            }
            if (!(transition is AnimatorStateTransition timed))
                throw new ArgumentException("Entry/state-machine transitions support mute, solo, conditions and destination_state_id only.");
            switch (change.Name)
            {
                case "duration":
                    float duration = AnimationAssetAccess.Number(change.Value);
                    if (duration < 0)
                        throw new ArgumentException("duration must be nonnegative.");
                    changes.Add(() => timed.duration = duration);
                    break;
                case "offset":
                    float offset = AnimationAssetAccess.Number(change.Value);
                    changes.Add(() => timed.offset = offset);
                    break;
                case "exitTime":
                    float exit = AnimationAssetAccess.Number(change.Value);
                    if (exit < 0)
                        throw new ArgumentException("exit_time must be nonnegative.");
                    changes.Add(() => timed.exitTime = exit);
                    break;
                case "hasExitTime":
                    bool hasExit = AnimationAssetAccess.Boolean(change.Value);
                    changes.Add(() => timed.hasExitTime = hasExit);
                    break;
                case "hasFixedDuration":
                    bool fixedDuration = AnimationAssetAccess.Boolean(change.Value);
                    changes.Add(() => timed.hasFixedDuration = fixedDuration);
                    break;
                case "canTransitionToSelf":
                    bool self = AnimationAssetAccess.Boolean(change.Value);
                    changes.Add(() => timed.canTransitionToSelf = self);
                    break;
                default:
                    throw new ArgumentException("Unsupported transition change field.");
            }
        }

        private static AnimatorCondition[] Conditions(AnimatorController controller, JToken token)
        {
            if (!(token is JArray input) || input.Count > 32)
                throw new ArgumentException("conditions must contain at most 32 entries.");
            var conditions = new List<AnimatorCondition>();
            foreach (JToken item in input)
            {
                if (!(item is JObject condition))
                    throw new ArgumentException("Each condition must be an object.");
                AnimationAssetAccess.Keys(condition, "parameter", "mode", "threshold");
                string parameter = AnimationAssetAccess.Text(condition, "parameter");
                string mode = AnimationAssetAccess.Text(condition, "mode");
                if (
                    controller.parameters.Count(candidate => candidate.name == parameter) != 1
                    || !Enum.TryParse(mode, true, out AnimatorConditionMode parsed)
                    || !Enum.IsDefined(typeof(AnimatorConditionMode), parsed)
                )
                    throw new ArgumentException("Condition parameter or mode is missing/ambiguous.");
                conditions.Add(
                    new AnimatorCondition
                    {
                        parameter = parameter,
                        mode = parsed,
                        threshold = AnimationAssetAccess.Number(condition["threshold"]),
                    }
                );
            }
            return conditions.ToArray();
        }

        private static void RemoveTransition(ControllerGraph.Node node)
        {
            switch (node.Kind)
            {
                case "state_transition":
                    node.State.transitions = node.State.transitions.Where(transition => transition != node.Asset).ToArray();
                    break;
                case "any_state_transition":
                    node.Machine.anyStateTransitions = node.Machine.anyStateTransitions.Where(transition => transition != node.Asset).ToArray();
                    break;
                case "entry_transition":
                    node.Machine.entryTransitions = node.Machine.entryTransitions.Where(transition => transition != node.Asset).ToArray();
                    break;
                case "state_machine_transition":
                    node.Machine.SetStateMachineTransitions(
                        node.SourceMachine,
                        node.Machine.GetStateMachineTransitions(node.SourceMachine).Where(transition => transition != node.Asset).ToArray()
                    );
                    break;
                default:
                    throw new ArgumentException("Unsupported transition ownership.");
            }
        }
    }
}
