#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System.Collections;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public class PlayerInputReadOnlyDiagnosticsTests
    {
        private static object Raw(PlayerInput player, string name)
        {
            FieldInfo field = typeof(PlayerInput).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(field, "The selected Input System 1.20.1 fixture requires its actual lifecycle evidence field: " + name);
            return field.GetValue(player);
        }

        [UnityTest]
        public IEnumerator DisabledSharedAssetDiagnosticsPreserveIdentityInitializationAndHooks()
        {
            Assert.IsNotNull(
                typeof(RuntimeInputSystemSimulationBackend).GetMethod("DiagnosePlayer"),
                "PlayerInput diagnostics must be implemented before read-only regression acceptance."
            );
            yield return new EnterPlayMode();
            var input = new InputTestFixture();
            input.Setup();
            var backend = new RuntimeInputSystemSimulationBackend();
            PlayerInput active = null;
            PlayerInput disabled = null;
            InputActionAsset asset = null;
            try
            {
                MethodInfo diagnose = typeof(RuntimeInputSystemSimulationBackend).GetMethod("DiagnosePlayer");
                Assert.IsNotNull(diagnose, "PlayerInput diagnostics must be implemented before read-only regression acceptance.");
                asset = ScriptableObject.CreateInstance<InputActionAsset>();
                asset.AddActionMap("Gameplay").AddAction("Move", InputActionType.Value, "<Gamepad>/leftStick");
                var first = new GameObject("OwnedReadOnlyActivePlayer");
                first.SetActive(false);
                active = first.AddComponent<PlayerInput>();
                active.notificationBehavior = PlayerNotifications.InvokeCSharpEvents;
                active.actions = asset;
                active.defaultActionMap = "Gameplay";
                active.neverAutoSwitchControlSchemes = true;
                first.SetActive(true);
                Assert.IsTrue((bool)Raw(active, "m_ActionsInitialized"));
                Assert.AreSame(asset, Raw(active, "m_Actions"));
                var second = new GameObject("OwnedReadOnlyDisabledPlayer");
                second.SetActive(false);
                disabled = second.AddComponent<PlayerInput>();
                disabled.enabled = false;
                disabled.notificationBehavior = PlayerNotifications.InvokeCSharpEvents;
                disabled.actions = asset;
                disabled.defaultActionMap = "Gameplay";
                second.SetActive(true);
                Assert.IsTrue(second.activeInHierarchy);
                Assert.IsFalse(disabled.isActiveAndEnabled);
                Assert.IsFalse((bool)Raw(disabled, "m_ActionsInitialized"));
                Assert.AreSame(asset, Raw(disabled, "m_Actions"));
                object beforeHook = Raw(disabled, "m_ActionTriggeredDelegate");
                object activeHook = Raw(active, "m_ActionTriggeredDelegate");
                Assert.IsNull(beforeHook, "The disabled component must have no installed notification hook before observation.");
                int assets = Resources.FindObjectsOfTypeAll<InputActionAsset>().Length;
                int[] devices = active.devices.Select(device => device.deviceId).ToArray();

                JObject result = JObject.FromObject(diagnose.Invoke(backend, new object[] { second }));

                Assert.AreSame(asset, Raw(disabled, "m_Actions"), "Read-only diagnosis must not clone or replace the disabled player's shared actions.");
                Assert.IsFalse((bool)Raw(disabled, "m_ActionsInitialized"), "Read-only diagnosis must not initialize disabled actions.");
                Assert.AreSame(beforeHook, Raw(disabled, "m_ActionTriggeredDelegate"), "Read-only diagnosis must not install notification hooks.");
                Assert.AreSame(activeHook, Raw(active, "m_ActionTriggeredDelegate"));
                Assert.AreEqual(assets, Resources.FindObjectsOfTypeAll<InputActionAsset>().Length, "No clone asset may be allocated by diagnosis.");
                CollectionAssert.AreEquivalent(devices, active.devices.Select(device => device.deviceId));
                Assert.AreEqual(0, (int)result["resolved_owned_controls"]);
                Assert.IsFalse((bool)result["delivery_eligible"]);
                Assert.IsFalse((bool)result["action_inspection_available"]);
            }
            finally
            {
                backend.ReleaseAll();
                InputActionAsset disabledAsset = disabled == null ? null : Raw(disabled, "m_Actions") as InputActionAsset;
                InputActionAsset activeAsset = active == null ? null : Raw(active, "m_Actions") as InputActionAsset;
                if (disabled != null)
                    Object.DestroyImmediate(disabled.gameObject);
                if (active != null)
                    Object.DestroyImmediate(active.gameObject);
                if (disabledAsset != null && disabledAsset != asset)
                    Object.DestroyImmediate(disabledAsset);
                if (activeAsset != null && activeAsset != asset && activeAsset != disabledAsset)
                    Object.DestroyImmediate(activeAsset);
                if (asset != null)
                    Object.DestroyImmediate(asset);
                input.TearDown();
            }
            yield return new ExitPlayMode();
        }

        [UnityTearDown]
        public IEnumerator RestoreEditMode()
        {
            if (Application.isPlaying)
                yield return new ExitPlayMode();
        }
    }
}
#endif
