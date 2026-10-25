using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded observed actions, never inferred timing or expected game outcomes.</summary>
    public sealed class PlayScenarioRecordingDraft
    {
        private readonly JArray steps = new JArray();
        private readonly JArray diagnostics = new JArray();
        private JObject inputObservation;

        private sealed class HeldInput
        {
            internal JObject Step;
            internal long Started;
        }

        private readonly Dictionary<string, HeldInput> held = new Dictionary<string, HeldInput>(StringComparer.Ordinal);
        private long? inputFrame;
        private JObject previousInput;
        private long previousInputFrame;
        private string scene;
        private readonly string sceneScope;
        private readonly JArray setup = new JArray();
        public string Id { get; } = Guid.NewGuid().ToString("N");
        public string Name { get; }
        public int MaxEvents { get; }
        public string Status { get; private set; } = "recording";
        public string Reason { get; private set; }
        public bool Saved { get; private set; }
        public bool Recording => Status == "recording";
        public string CurrentScene => scene;
        public bool RawInputReplayAvailable => inputObservation?.Value<bool?>("raw_replay_available") != false;

        public PlayScenarioRecordingDraft(string name, string initialScene, int maxEvents = 31, string sceneScope = "active", string[] initialScenes = null)
        {
            PlayScenarioDefinition.ValidateName(name);
            if (maxEvents < 1 || maxEvents >= PlayScenarioDefinition.MaximumSteps)
                throw new ArgumentException("max_events must be an integer between 1 and 127.");
            if (sceneScope != "active" && sceneScope != "all_loaded")
                throw new ArgumentException("scene_scope must be active or all_loaded.");
            this.sceneScope = sceneScope;
            if (sceneScope == "all_loaded")
            {
                if (
                    initialScenes == null
                    || initialScenes.Length < 1
                    || initialScenes.Length > PlayScenarioDefinition.MaximumAuxiliarySteps
                    || initialScenes.Distinct(StringComparer.Ordinal).Count() != initialScenes.Length
                    || !initialScenes.Contains(initialScene, StringComparer.Ordinal)
                )
                    throw new ArgumentException("all_loaded requires one to thirty-two unique initial scenes including the active scene.");
                foreach (string path in new[] { initialScene }.Concat(initialScenes.Where(path => path != initialScene)))
                {
                    var load = new JObject
                    {
                        ["name"] = "Recorded initial context " + setup.Count,
                        ["action"] = "load_scene",
                        ["scene"] = path,
                    };
                    if (setup.Count > 0)
                        load["load_mode"] = "additive";
                    setup.Add(load);
                }
            }
            Name = name;
            MaxEvents = maxEvents;
            scene = initialScene;
            steps.Add(
                new JObject
                {
                    ["name"] = "Recorded initial scene",
                    ["action"] = sceneScope == "active" ? "load_scene" : "wait_scene",
                    ["scene"] = scene,
                }
            );
            Definition();
        }

        public PlayScenarioDefinition Definition()
        {
            JObject json = Json();
            if (Encoding.UTF8.GetByteCount(json.ToString(Formatting.None)) > PlayScenarioDefinition.MaximumDefinitionBytes)
                throw new ArgumentException("The recording draft exceeded its 1 MiB authoring budget.");
            return PlayScenarioDefinition.Parse(json);
        }

        private JObject Json()
        {
            var result = new JObject
            {
                ["name"] = Name,
                ["draft"] = true,
                ["tags"] =
                    inputObservation?.Value<string>("backend") == "legacy_input_manager"
                        ? new JArray("recorded-draft", "recorded-legacy-input")
                        : new JArray("recorded-draft"),
                ["steps"] = steps.DeepClone(),
            };
            if (sceneScope == "all_loaded")
                result["setup_steps"] = setup.DeepClone();
            return result;
        }

        public void Diagnose(string message)
        {
            if (string.IsNullOrEmpty(message))
                return;
            string bounded = message.Length > 512 ? message.Substring(0, 512) : message;
            if (diagnostics.Count < 32 && !diagnostics.Any(value => (string)value == bounded))
                diagnostics.Add(bounded);
        }

        public void DescribeInputObservation(JObject capabilities) => inputObservation = capabilities == null ? null : (JObject)capabilities.DeepClone();

        /// <summary>Advances only the actual game-input update counter supplied by the observer.</summary>
        public bool InputFrameObserved(long frame)
        {
            if (!Recording)
                return false;
            if (frame < 0 || (inputFrame.HasValue && frame < inputFrame.Value))
            {
                Diagnose("The observed input update counter moved backwards; recording stopped without guessing timing.");
                Finish("interrupted", "input_clock_changed");
                return false;
            }
            if (
                held.Values.Any(value => frame - value.Started > InputSimulationRequest.MaxFrames)
                || (previousInput != null && frame - previousInputFrame > InputSimulationRequest.MaxFrames)
            )
            {
                Diagnose("Observed input hold/spacing exceeded the executable 36000 input-update bound; recording stopped without clamping timing.");
                Finish("interrupted", "input_frame_limit");
                return false;
            }
            foreach (HeldInput value in held.Values)
                value.Step["input"]["frames"] = Math.Max(1, frame - value.Started);
            if (previousInput != null)
                previousInput["wait_frames"] = Math.Max(1, frame - previousInputFrame);
            inputFrame = frame;
            return true;
        }

        /// <summary>Records a validated observed state transition, never a requested/inferred hold duration.</summary>
        public bool InputObserved(JObject observed, long frame)
        {
            if (!Recording)
                return false;
            InputSimulationRequest request;
            try
            {
                if (observed == null || observed.Property("frames") != null)
                    throw new ArgumentException("Observed input cannot supply an unobserved frames duration.");
                request = InputSimulationRequest.ParseScenario(observed);
                if (request.Action != "key" && request.Action != "mouse" && request.Action != "touch" && request.Action != "wheel")
                    throw new ArgumentException("Only observed key, mouse, touch and wheel input is supported by this recorder.");
            }
            catch (ArgumentException error)
            {
                Diagnose("An unsupported observed input was omitted: " + error.Message);
                return false;
            }
            string identity =
                request.Action == "key" ? "key:" + request.Key
                : request.Action == "mouse" ? "mouse:" + request.Button
                : request.Action == "touch" ? "touch:" + request.TouchId
                : null;
            if (!InputFrameObserved(frame))
                return false;
            if (identity != null && request.State == "press" && held.ContainsKey(identity))
            {
                Diagnose("A repeated observed press was omitted; the existing hold was not restarted.");
                return false;
            }
            if (identity != null && (request.State == "release" || (request.Action == "touch" && request.State == "move")) && !held.ContainsKey(identity))
            {
                Diagnose("An observed release/move without a recorded press was omitted; no earlier input was invented.");
                return false;
            }
            if (identity != null && request.State == "press" && held.Count >= 16)
            {
                Diagnose("The recorder supports at most sixteen simultaneously observed held controls.");
                Finish("interrupted", "held_input_limit");
                return false;
            }
            if (previousInput != null && frame == previousInputFrame)
                Diagnose(
                    "Events in the same input update retain arrival order, but executable replay requires at least one update per input step; review simultaneous timing."
                );
            var input = (JObject)observed.DeepClone();
            if (request.State == "press" && identity != null)
                input["frames"] = 1;
            var step = new JObject
            {
                ["name"] = "Recorded input " + steps.Count,
                ["action"] = "input",
                ["input"] = input,
                ["wait_frames"] = 1,
            };
            HeldInput released = null;
            if (identity != null && request.State == "press")
                held[identity] = new HeldInput { Step = step, Started = frame };
            else if (identity != null && request.State == "release")
            {
                released = held[identity];
                held.Remove(identity);
            }
            if (!Append(step))
            {
                if (identity != null && request.State == "press")
                    held.Remove(identity);
                else if (released != null)
                    held[identity] = released;
                return false;
            }
            previousInput = step;
            previousInputFrame = frame;
            return true;
        }

        public bool Click(PlayScenarioRecordedTarget target)
        {
            if (!Recording)
                return false;
            if (target == null || !string.IsNullOrEmpty(target.Error))
            {
                Diagnose(target?.Error ?? "A pointer click had no reliable target identity.");
                return false;
            }
            if (sceneScope == "active" && target.Scene != scene)
            {
                Diagnose("A click outside the recorded active scene was omitted; additive-scene interactions require manual authoring.");
                return false;
            }
            var step = new JObject
            {
                ["name"] = "Recorded click " + steps.Count,
                ["action"] = "click_ui",
                ["click_mode"] = "raycast",
            };
            if (sceneScope == "all_loaded")
                step["scene"] = target.Scene;
            step[target.TargetId == null ? "target" : "target_id"] = target.TargetId ?? target.Target;
            return Append(step);
        }

        public bool SceneObserved(string path, string state)
        {
            if (sceneScope != "all_loaded")
                return state == "active" && SceneChanged(path);
            if (!Recording)
                return false;
            if (state != "active" && state != "loaded" && state != "unloaded")
                throw new ArgumentException("Invalid observed scene state.");
            if (state == "active" && path == scene)
                return false;
            var step = new JObject
            {
                ["name"] = "Observed scene " + steps.Count,
                ["action"] = "wait_scene",
                ["scene"] = path,
                ["scene_state"] = state,
            };
            if (!Append(step))
                return false;
            if (state == "active")
                scene = path;
            return true;
        }

        public bool SceneChanged(string observedScene)
        {
            if (!Recording || observedScene == scene)
                return false;
            var step = new JObject
            {
                ["name"] = "Observed scene " + steps.Count,
                ["action"] = "wait_scene",
                ["scene"] = observedScene,
            };
            if (!Append(step))
                return false;
            scene = observedScene;
            return true;
        }

        public bool Checkpoint(string name)
        {
            PlayScenarioDefinition.ValidateName(name);
            if (!Recording)
                throw new InvalidOperationException("A checkpoint requires an active recording.");
            if (steps.OfType<JObject>().Any(step => (string)step["screenshot"]?["name"] == name))
                throw new ArgumentException("Checkpoint names must be unique within the recording.");
            return Append(
                new JObject
                {
                    ["name"] = "Requested checkpoint " + name,
                    ["action"] = "wait_scene",
                    ["scene"] = scene,
                    ["screenshot"] = new JObject
                    {
                        ["name"] = name,
                        ["width"] = 1280,
                        ["height"] = 720,
                    },
                }
            );
        }

        private bool Append(JObject step)
        {
            if (!Recording)
                return false;
            if ((string)step["action"] != "input")
                previousInput = null;
            steps.Add(step);
            try
            {
                Definition();
            }
            catch (ArgumentException error)
            {
                steps.RemoveAt(steps.Count - 1);
                Diagnose("An unsupported observed event was omitted: " + error.Message);
                Finish("interrupted", "invalid_observed_event");
                return false;
            }
            if (steps.Count - 1 >= MaxEvents)
            {
                Diagnose("The recording reached max_events. Remaining interactions were not recorded.");
                Finish("limit_reached", "max_events");
            }
            return true;
        }

        public void Finish(string status, string reason)
        {
            if (!Recording)
                return;
            if (held.Count > 0)
                Diagnose(
                    "Recording ended while input remained held. Only the observed duration is retained; no later release or outcome was invented. Review this partial gesture."
                );
            Status = status;
            Reason = reason;
        }

        public void MarkSaved()
        {
            if (Status == "cancelled")
                throw new InvalidOperationException("A cancelled recording cannot be saved.");
            Status = "stopped";
            Saved = true;
        }

        public void Cancel()
        {
            if (Saved)
                throw new InvalidOperationException("A saved recording cannot be cancelled.");
            Status = "cancelled";
            Reason = "requested";
        }

        public JObject Snapshot() =>
            new JObject
            {
                ["recording_id"] = Id,
                ["status"] = Status,
                ["reason"] = Reason,
                ["saved"] = Saved,
                ["requires_review"] = true,
                ["event_count"] = steps.Count - 1,
                ["max_events"] = MaxEvents,
                ["diagnostics"] = diagnostics.DeepClone(),
                ["input_observation"] = inputObservation == null ? JValue.CreateNull() : inputObservation.DeepClone(),
                ["last_observed_input_update"] = inputFrame.HasValue ? new JValue(inputFrame.Value) : JValue.CreateNull(),
                ["limitations"] = new JArray(
                    sceneScope == "active"
                        ? "Supported uGUI Button pointer clicks and available key/mouse/touch/wheel observation are recorded. input_observation identifies the actual backend and raw replay limits. UI Toolkit, programmatic callbacks and gamepad require manual authoring."
                        : "Supported uGUI Button pointer clicks carry exact scene selectors. Initial loaded context is explicit setup requiring review; observed transitions are waits, never inferred loading/unloading effects.",
                    "Raw positions are observed screen pixels, not stable UI identities. Holds and spacing count the observer's game updates; same-update ordering is retained but exact simultaneous replay and cross-backend units need review.",
                    "Objects created between observer scans may be missed. Scene changes preserve observed order, not inferred waits or readiness conditions.",
                    "Review selectors, scene readiness, state expectations and reset policy before clearing draft. Checkpoints request future screenshots and do not create image assertions."
                ),
                ["scenario"] = Json(),
            };
    }

    public sealed class PlayScenarioRecordedTarget
    {
        public string Scene;
        public string Target;
        public string TargetId;
        public string Error;
    }
}
