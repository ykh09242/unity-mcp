using System;
using System.Globalization;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.Helpers;
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

    public interface IUguiScenarioFocusBackend
    {
        bool TryGetCurrentFocus(out GameObject selected);
    }
}

namespace MCPForUnity.Runtime.PlayScenarios
{
    public static class PlayScenarioPlayerInput
    {
        public static MCPForUnity.Editor.Tools.Input.IUguiInputSimulationBackend Backend { get; set; }
        public static Func<MCPForUnity.Editor.Tools.Input.IScenarioInputSimulationBackend> CreateInputBackend { get; set; }

        internal static PlayScenarioObservation ObserveFocus(
            PlayScenarioStep step,
            GameObject target,
            MCPForUnity.Editor.Tools.Input.IUguiInputSimulationBackend backend
        )
        {
            string selector = step.TargetId ?? step.Target;
            const string capability = "wait_ui_focus requires the optional uGUI focus backend.";
            if (!(backend is MCPForUnity.Editor.Tools.Input.IUguiScenarioFocusBackend focus))
                throw new PlayScenarioException(
                    new PlayScenarioFailure
                    {
                        Code = "capability_unavailable",
                        Target = selector,
                        Message = capability,
                    }
                );
            bool available = focus.TryGetCurrentFocus(out GameObject selected);
            string actual =
                !available ? "no active EventSystem"
                : selected == null ? "no selected GameObject"
                : "GameObject '"
                    + PlayScenarioEngine.Bounded(selected.name, 192)
                    + "' (instance ID "
                    + selected.GetInstanceIDCompat().ToString(CultureInfo.InvariantCulture)
                    + ") in scene '"
                    + PlayScenarioEngine.Bounded(selected.scene.path, 512)
                    + "'";
            bool ready = selected != null && selected == target;
            string detail = ready ? "uGUI focus reached " + actual + "." : "Current uGUI focus: " + actual + ".";
            return new PlayScenarioObservation(
                ready,
                detail,
                ready
                    ? null
                    : new PlayScenarioFailure
                    {
                        Code = "condition_unmet",
                        Target = selector,
                        Expected = PlayScenarioEngine.Bounded(target.scene.path, 512) + " / " + PlayScenarioEngine.Bounded(selector, 500),
                        Actual = actual,
                        Message = detail,
                    }
            );
        }
    }

    public interface IPlayScenarioInputHost
    {
        void MaintainInput();
        void ReleaseInput();
    }

    public sealed class PlayScenarioInputReleaseException : Exception
    {
        internal PlayScenarioInputReleaseException(Exception inner)
            : base("Input release outcome is unknown: " + inner.Message, inner) { }
    }

    /// <summary>A host owns its virtual devices; a step is dispatched once and waits for real input updates.</summary>
    public sealed class PlayScenarioInputSession
    {
        private MCPForUnity.Editor.Tools.Input.IScenarioInputSimulationBackend backend;
        private long startedUpdate;
        private Exception releaseFailure;

        public void Maintain()
        {
            if (backend != null && !backend.LeaseActive)
                backend.ReleaseAll();
        }

        public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
        {
            if (releaseFailure != null)
                throw new InvalidOperationException("Previous input release has an unknown outcome.", releaseFailure);
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
                    else if (releaseFailure == null)
                        ReleaseOwned(dispatchBackend);
                    throw;
                }
                if (!ReferenceEquals(backend, dispatchBackend))
                {
                    if (releaseFailure != null)
                        throw new InvalidOperationException("Previous input release has an unknown outcome.", releaseFailure);
                    // Device-added callbacks may cancel while an adapter is still assigning its new device.
                    ReleaseOwned(dispatchBackend);
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
            string reason = backend?.UnavailableReason
                ?? "Scenario raw replay requires an enabled com.unity.inputsystem backend, Input System Package/Both handling and an Input System consumer. Legacy raw replay into UnityEngine.Input is unavailable; changing handling alone does not redirect those reads. Review recorded-legacy-input drafts and use target-based click_ui for uGUI. A project-defined input adapter requires explicit project-code integration.";
            Release();
            throw new PlayScenarioException(new PlayScenarioFailure { Code = "capability_unavailable", Message = reason });
        }

        public void Release()
        {
            if (releaseFailure != null)
                throw new InvalidOperationException("Previous input release has an unknown outcome: " + releaseFailure.Message, releaseFailure);
            var owned = backend;
            backend = null;
            ReleaseOwned(owned);
        }

        private void ReleaseOwned(MCPForUnity.Editor.Tools.Input.IScenarioInputSimulationBackend owned)
        {
            try
            {
                owned?.ReleaseAll();
            }
            catch (Exception error)
            {
                releaseFailure = error;
                throw new PlayScenarioInputReleaseException(error);
            }
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
