using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Animation
{
    internal static class ControllerGraph
    {
        internal sealed class Node
        {
            internal Object Asset;
            internal AnimatorStateMachine Machine;
            internal AnimatorState State;
            internal AnimatorStateMachine SourceMachine;
            internal string Kind;
            internal string Path;
            internal int Layer;
            internal string Id => AnimationAssetAccess.Id(Asset);

            internal JObject Describe()
            {
                var row = new JObject
                {
                    ["id"] = Id,
                    ["kind"] = Kind,
                    ["name"] = Asset.name,
                    ["path"] = Path,
                    ["layer_index"] = Layer,
                    ["source_id"] =
                        State != null ? AnimationAssetAccess.Id(State)
                        : SourceMachine != null ? AnimationAssetAccess.Id(SourceMachine)
                        : Machine != null ? AnimationAssetAccess.Id(Machine)
                        : null,
                };
                if (Asset is AnimatorState state)
                {
                    row["speed"] = state.speed;
                    row["motion"] = AnimationAssetAccess.Reference(state.motion);
                    row["is_default"] = Machine.defaultState == state;
                }
                if (Asset is AnimatorTransitionBase transition)
                {
                    row["destination_state_id"] = transition.destinationState == null ? null : AnimationAssetAccess.Id(transition.destinationState);
                    row["destination_state_machine_id"] =
                        transition.destinationStateMachine == null ? null : AnimationAssetAccess.Id(transition.destinationStateMachine);
                    row["is_exit"] = transition.isExit;
                    row["mute"] = transition.mute;
                    row["solo"] = transition.solo;
                    row["conditions"] = new JArray(
                        transition.conditions.Select(condition => new JObject
                        {
                            ["parameter"] = condition.parameter,
                            ["mode"] = condition.mode.ToString(),
                            ["threshold"] = condition.threshold,
                        })
                    );
                    if (transition is AnimatorStateTransition timed)
                    {
                        row["duration"] = timed.duration;
                        row["exit_time"] = timed.exitTime;
                        row["offset"] = timed.offset;
                        row["has_exit_time"] = timed.hasExitTime;
                        row["has_fixed_duration"] = timed.hasFixedDuration;
                        row["can_transition_to_self"] = timed.canTransitionToSelf;
                    }
                }
                return row;
            }
        }

        internal sealed class Snapshot
        {
            internal AnimatorController Controller;
            internal string Path;
            internal string Revision;
            internal List<Node> Nodes = new List<Node>();

            internal Node Select(JObject request, string selector, string kind = null)
            {
                string id = AnimationAssetAccess.Text(request, selector);
                Node[] matches = Nodes.Where(node => node.Id == id && (kind == null ? node.Asset is AnimatorTransitionBase : node.Kind == kind)).ToArray();
                if (matches.Length != 1)
                    throw new ArgumentException(selector + " must identify exactly one saved object in this controller; names are not selectors.");
                return matches[0];
            }
        }

        internal static Snapshot Read(JObject request)
        {
            AnimationAssetAccess.Stable();
            if (request.ToString(Newtonsoft.Json.Formatting.None).Length > 1048576)
                throw new ArgumentException("Animation round-trip request exceeds 1 MiB.");
            string path = AnimationAssetAccess.Path(request, "controllerPath");
            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(path);
            if (controller == null || !path.EndsWith(".controller", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("An existing native AnimatorController is required.");
            var snapshot = new Snapshot { Controller = controller, Path = path };
            var seen = new HashSet<AnimatorStateMachine>();
            for (int i = 0; i < controller.layers.Length; i++)
                Visit(snapshot, controller.layers[i].stateMachine, null, controller.layers[i].name, i, 0, seen);
            snapshot.Revision = AnimationAssetAccess.Revision(path, snapshot.Nodes.Select(node => node.Asset).Append(controller).Distinct().ToArray());
            return snapshot;
        }

        private static void Visit(
            Snapshot graph,
            AnimatorStateMachine machine,
            AnimatorStateMachine parent,
            string path,
            int layer,
            int depth,
            HashSet<AnimatorStateMachine> seen
        )
        {
            if (depth > 64 || !seen.Add(machine))
                throw new ArgumentException("Graph exceeds depth 64 or shares a state machine between traversal paths.");
            Add(
                graph,
                new Node
                {
                    Asset = machine,
                    Machine = parent,
                    Kind = "state_machine",
                    Path = path,
                    Layer = layer,
                }
            );
            foreach (var child in machine.states)
            {
                AnimatorState state = child.state;
                string statePath = path + "/" + state.name;
                Add(
                    graph,
                    new Node
                    {
                        Asset = state,
                        Machine = machine,
                        Kind = "state",
                        Path = statePath,
                        Layer = layer,
                    }
                );
                foreach (var transition in state.transitions)
                    Add(
                        graph,
                        new Node
                        {
                            Asset = transition,
                            Machine = machine,
                            State = state,
                            Kind = "state_transition",
                            Path = statePath,
                            Layer = layer,
                        }
                    );
            }
            foreach (var transition in machine.anyStateTransitions)
                Add(
                    graph,
                    new Node
                    {
                        Asset = transition,
                        Machine = machine,
                        Kind = "any_state_transition",
                        Path = path,
                        Layer = layer,
                    }
                );
            foreach (var transition in machine.entryTransitions)
                Add(
                    graph,
                    new Node
                    {
                        Asset = transition,
                        Machine = machine,
                        Kind = "entry_transition",
                        Path = path,
                        Layer = layer,
                    }
                );
            foreach (var child in machine.stateMachines)
            {
                foreach (var transition in machine.GetStateMachineTransitions(child.stateMachine))
                    Add(
                        graph,
                        new Node
                        {
                            Asset = transition,
                            Machine = machine,
                            SourceMachine = child.stateMachine,
                            Kind = "state_machine_transition",
                            Path = path + "/" + child.stateMachine.name,
                            Layer = layer,
                        }
                    );
                Visit(graph, child.stateMachine, machine, path + "/" + child.stateMachine.name, layer, depth + 1, seen);
            }
        }

        private static void Add(Snapshot graph, Node node)
        {
            if (graph.Nodes.Count >= 2048 || node.Path.Length > 4096)
                throw new ArgumentException("Controller graph exceeds 2048 entries or path limits.");
            graph.Nodes.Add(node);
        }

        internal static object Page(JObject request)
        {
            Snapshot graph = Read(request);
            var ordered = graph.Nodes.OrderBy(node => node.Id, StringComparer.Ordinal).ToArray();
            return AnimationAssetAccess.Page(ordered.Length, index => ordered[index].Describe(), request, graph.Revision, "entries");
        }
    }
}
