using System;
using MCPForUnity.Editor.Services.PlayScenarios;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    public interface IInputSimulationBackend
    {
        bool Available { get; }
        string UnavailableReason { get; }
        object Status { get; }
        object Apply(InputSimulationRequest request);
        void ReleaseAll();
    }

    public interface IScenarioInputSimulationBackend : IInputSimulationBackend
    {
        long UpdateCount { get; }
        bool LeaseActive { get; }
    }

    public interface IUguiInputSimulationBackend
    {
        object Click(GameObject target);
    }

    /// <summary>Scenario clicks may wait before dispatch, never after an event has been sent.</summary>
    public interface IUguiScenarioClickBackend
    {
        bool TryClick(GameObject target, out object result, out string detail);
    }

    public interface IUguiScenarioRaycastClickBackend
    {
        bool TryRaycastClick(GameObject target, out object result, out string detail);
    }
}

namespace MCPForUnity.Runtime.PlayScenarios
{
    public static class PlayScenarioPlayerInput
    {
        public static MCPForUnity.Editor.Tools.Input.IUguiInputSimulationBackend Backend { get; set; }
        public static Func<MCPForUnity.Editor.Tools.Input.IScenarioInputSimulationBackend> CreateInputBackend { get; set; }
    }

    public interface IPlayScenarioInputHost
    {
        void MaintainInput();
        void ReleaseInput();
    }

    /// <summary>A host owns its virtual devices; a step is dispatched once and waits for real input updates.</summary>
    public sealed class PlayScenarioInputSession
    {
        private MCPForUnity.Editor.Tools.Input.IScenarioInputSimulationBackend backend;
        private long startedUpdate;

        public void Maintain()
        {
            if (backend != null && !backend.LeaseActive)
                backend.ReleaseAll();
        }

        public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
        {
            if (firstPoll)
            {
                var request = MCPForUnity.Editor.Tools.Input.InputSimulationRequest.ParseScenario(step.Input);
                if (request.Action == "release_all")
                {
                    Release();
                    return new PlayScenarioObservation(true, "Released scenario virtual devices.");
                }
                backend = backend ?? PlayScenarioPlayerInput.CreateInputBackend?.Invoke();
                RequireBackend();
                var dispatchBackend = backend;
                try
                {
                    dispatchBackend.Apply(request);
                }
                catch
                {
                    if (ReferenceEquals(backend, dispatchBackend))
                        Release();
                    else
                        dispatchBackend.ReleaseAll();
                    throw;
                }
                if (!ReferenceEquals(backend, dispatchBackend))
                {
                    // Device-added callbacks may cancel while an adapter is still assigning its new device.
                    dispatchBackend.ReleaseAll();
                    return new PlayScenarioObservation(false, "Input dispatch was interrupted and its devices were released.");
                }
                startedUpdate = dispatchBackend.UpdateCount;
            }
            RequireBackend();
            if (!backend.LeaseActive)
            {
                Release();
                throw new PlayScenarioException(
                    new PlayScenarioFailure
                    {
                        Code = "input_lease_expired",
                        Message = "Scenario virtual devices were released before the input step completed.",
                    }
                );
            }
            long updates = backend.UpdateCount - startedUpdate;
            return new PlayScenarioObservation(
                updates >= (step.WaitFrames ?? 1),
                "Completed input updates: " + updates + "; expected: " + (step.WaitFrames ?? 1) + "."
            );
        }

        private void RequireBackend()
        {
            if (backend != null && backend.Available)
                return;
            string reason =
                backend?.UnavailableReason ?? "Scenario raw input requires com.unity.inputsystem 1.7+ and Input System Package or Both active input handling.";
            Release();
            throw new PlayScenarioException(new PlayScenarioFailure { Code = "capability_unavailable", Message = reason });
        }

        public void Release()
        {
            var owned = backend;
            backend = null;
            owned?.ReleaseAll();
        }
    }

    public sealed class PlayScenarioClickResult
    {
        public string Message { get; }
        public object Data { get; }

        public PlayScenarioClickResult(string message, object data)
        {
            Message = message;
            Data = data;
        }
    }
}
