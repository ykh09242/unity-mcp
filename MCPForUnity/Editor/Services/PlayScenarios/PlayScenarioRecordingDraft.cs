using System;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded observed actions, never inferred timing or expected game outcomes.</summary>
    public sealed class PlayScenarioRecordingDraft
    {
        private readonly JArray steps = new JArray();
        private readonly JArray diagnostics = new JArray();
        private string scene;
        public string Id { get; } = Guid.NewGuid().ToString("N");
        public string Name { get; }
        public int MaxEvents { get; }
        public string Status { get; private set; } = "recording";
        public string Reason { get; private set; }
        public bool Saved { get; private set; }
        public bool Recording => Status == "recording";
        public string CurrentScene => scene;

        public PlayScenarioRecordingDraft(string name, string initialScene, int maxEvents = 31)
        {
            PlayScenarioDefinition.ValidateName(name);
            if (maxEvents < 1 || maxEvents > 31)
                throw new ArgumentException("max_events must be an integer between 1 and 31.");
            Name = name;
            MaxEvents = maxEvents;
            scene = initialScene;
            steps.Add(
                new JObject
                {
                    ["name"] = "Recorded initial scene",
                    ["action"] = "load_scene",
                    ["scene"] = scene,
                }
            );
            Definition();
        }

        public PlayScenarioDefinition Definition()
        {
            JObject json = Json();
            if (Encoding.UTF8.GetByteCount(json.ToString(Formatting.None)) > 60 * 1024)
                throw new ArgumentException("The recording draft exceeded its 60 KiB authoring budget.");
            return PlayScenarioDefinition.Parse(json);
        }

        private JObject Json() =>
            new JObject
            {
                ["name"] = Name,
                ["draft"] = true,
                ["tags"] = new JArray("recorded-draft"),
                ["steps"] = steps.DeepClone(),
            };

        public void Diagnose(string message)
        {
            if (string.IsNullOrEmpty(message))
                return;
            string bounded = message.Length > 512 ? message.Substring(0, 512) : message;
            if (diagnostics.Count < 32 && !diagnostics.Any(value => (string)value == bounded))
                diagnostics.Add(bounded);
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
            if (target.Scene != scene)
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
            step[target.TargetId == null ? "target" : "target_id"] = target.TargetId ?? target.Target;
            return Append(step);
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
                ["limitations"] = new JArray(
                    "Only supported uGUI Button pointer clicks in the active scene are captured. Keyboard, raw input, drag, scroll, UI Toolkit and programmatic onClick callbacks require manual authoring.",
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
