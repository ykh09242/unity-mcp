using System;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

namespace IndependentExtension
{
    public sealed class ScenarioParticipant : IPlayScenarioStateProvider, IPlayScenarioResetParticipant
    {
        public bool IsResetComplete { get; private set; }

        public void BeginReset() => IsResetComplete = true;

        public bool TryRead(out PlayScenarioStateValue value)
        {
            value = PlayScenarioStateValue.FromBoolean(IsResetComplete);
            return true;
        }

        public IDisposable RegisterState() => PlayScenarioStateRegistry.Register("extension.state", this);

        public IDisposable RegisterReset() => PlayScenarioResetRegistry.Register("extension.reset", this);

        public IDisposable RegisterResources(ScriptableObject clone, PlayScenarioTarget target)
        {
            target.TargetId = "extension.target";
            PlayScenarioResourceTracker.RegisterScriptableObject(clone);
            return PlayScenarioResourceTracker.RegisterHandle(owner: "extension");
        }

        public IDisposable TrackSubscription() => PlayScenarioResourceTracker.RegisterSubscription();

        public void Publish()
        {
            using (var events = PlayScenarioEventRegistry.Register("extension.events", this))
                events.Emit("ready");
        }

        public static bool ExerciseGameState()
        {
            var participant = new ScenarioParticipant();
            IPlayScenarioResetParticipant reset = participant;
            IPlayScenarioStateProvider state = participant;
            reset.BeginReset();
            return state.TryRead(out var value) && value.BooleanValue;
        }
    }
}
