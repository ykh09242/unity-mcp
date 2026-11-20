using System;
using System.Globalization;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

namespace VerifiedWorkflows
{
    public sealed class PhysicsTriggerProbe : MonoBehaviour
    {
        public const int TriggerLayer = 8;
        public const int MoverLayer = 9;
        public const string TriggerLayerName = "FixtureTrigger";
        public const string MoverLayerName = "FixtureMover";
        public const string SettingsId = "fixture.physics.settings";
        public const string MatrixId = "fixture.physics.layer_pair_ignored";
        public const string CountId = "fixture.physics.trigger_enter_count";
        public const string ExpectedSettings =
            "layers=8:FixtureTrigger,9:FixtureMover; trigger=True; kinematic=True; gravity=False; pair_ignored=False; "
            + "simulation=Script; positions=-3,0,3; step_seconds=0.02";

        private const float StepSeconds = 0.02f;
        private static readonly int[] Positions = { -3, 0, 3 };

        public Rigidbody Mover;

        private readonly StateProvider settings = new();
        private readonly StateProvider matrix = new();
        private readonly StateProvider count = new();
        private IDisposable settingsRegistration;
        private IDisposable matrixRegistration;
        private IDisposable countRegistration;
        private int entered;

        private void OnEnable()
        {
            settingsRegistration = PlayScenarioStateRegistry.Register(SettingsId, settings);
            matrixRegistration = PlayScenarioStateRegistry.Register(MatrixId, matrix);
            countRegistration = PlayScenarioStateRegistry.Register(CountId, count);
        }

        private void OnDisable()
        {
            countRegistration?.Dispose();
            matrixRegistration?.Dispose();
            settingsRegistration?.Dispose();
        }

        private void Start()
        {
            var trigger = GetComponent<Collider>();
            var moverCollider = Mover.GetComponent<Collider>();
            int triggerLayer = gameObject.layer;
            int moverLayer = Mover.gameObject.layer;
            var previous = Physics.simulationMode;
            try
            {
                Physics.simulationMode = SimulationMode.Script;
                string inputs = string.Format(
                    CultureInfo.InvariantCulture,
                    "layers={0}:{1},{2}:{3}; trigger={4}; kinematic={5}; gravity={6}; pair_ignored={7}; simulation={8}; positions={9}; step_seconds={10}",
                    triggerLayer,
                    LayerMask.LayerToName(triggerLayer),
                    moverLayer,
                    LayerMask.LayerToName(moverLayer),
                    trigger.isTrigger,
                    Mover.isKinematic,
                    Mover.useGravity,
                    Physics.GetIgnoreCollision(trigger, moverCollider),
                    Physics.simulationMode,
                    string.Join(",", Positions),
                    StepSeconds
                );
                settings.Value = PlayScenarioStateValue.FromString(inputs);
                matrix.Value = PlayScenarioStateValue.FromInteger(Physics.GetIgnoreLayerCollision(triggerLayer, moverLayer) ? 1 : 0);
                foreach (int x in Positions)
                {
                    Mover.position = new Vector3(x, 0, 0);
                    Physics.SyncTransforms();
                    Physics.Simulate(StepSeconds);
                }
                count.Value = PlayScenarioStateValue.FromInteger(entered);
            }
            finally
            {
                Physics.simulationMode = previous;
            }
        }

        private void OnTriggerEnter(Collider other)
        {
            if (other.attachedRigidbody == Mover)
                entered++;
        }

        private sealed class StateProvider : IPlayScenarioStateProvider
        {
            internal PlayScenarioStateValue? Value;

            public bool TryRead(out PlayScenarioStateValue value)
            {
                value = Value.GetValueOrDefault();
                return Value.HasValue;
            }
        }
    }
}
