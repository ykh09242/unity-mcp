#if MCP_INPUT_SYSTEM
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using UnityEngine.Experimental.Input;
using UnityEngine.Experimental.Input.Plugins.Users;
using UnityEngine.InputSystem.Plugins.Users;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Users;

namespace MCPForUnity.Editor.Tools.Input
{
    public partial class RuntimeInputSystemSimulationBackend
    {
        private Behaviour _pairedPlayer;
        private static readonly Type PlayerType = InputSystemCompatibility.PlayerInputType;
        private static readonly PropertyInfo InputActive = PlayerType?.GetProperty("inputIsActive") ?? PlayerType?.GetProperty("active");
        private static readonly PropertyInfo PlayerIndex = PlayerType?.GetProperty("playerIndex");
        private static readonly PropertyInfo User = PlayerType?.GetProperty("user");
        private static readonly PropertyInfo Scheme = PlayerType?.GetProperty("currentControlScheme") ?? PlayerType?.GetProperty("controlScheme");
        private static readonly PropertyInfo Map = PlayerType?.GetProperty("currentActionMap");
        private static readonly PropertyInfo AutoSwitch = PlayerType?.GetProperty("neverAutoSwitchControlSchemes");
        private static readonly PropertyInfo Actions = PlayerType?.GetProperty("actions");

        public bool CanInspectPlayer => InputActive?.PropertyType == typeof(bool) && User?.PropertyType == typeof(InputUser);
        public bool CanPairPlayer =>
            CanInspectPlayer && Actions != null && AutoSwitch?.PropertyType == typeof(bool) && AutoSwitch.CanRead && AutoSwitch.CanWrite;

        private static int? Index(Behaviour player) => (int?)PlayerIndex?.GetValue(player);

        private InputUser _pairedUser;
        private bool _restoreAutoSwitch;

        private Behaviour RequirePlayer(GameObject target)
        {
            if (target == null || !target.activeInHierarchy || !target.scene.IsValid() || !target.scene.isLoaded)
                throw new ArgumentException("PlayerInput target must be an active scene GameObject.");
            if (!CanInspectPlayer)
                throw new ArgumentException("Installed Input System lacks the PlayerInput activity or user API.");
            var player = target.GetComponent(PlayerType) as Behaviour;
            if (player == null)
                throw new ArgumentException("target requires a PlayerInput component.");
            return player;
        }

        public object DiagnosePlayer(GameObject target) => PlayerDiagnostics(RequirePlayer(target));

        private Dictionary<string, object> PlayerDiagnostics(Behaviour player)
        {
            var user = (InputUser)User.GetValue(player);
            bool inputActive = (bool)InputActive.GetValue(player);
            bool active = player.isActiveAndEnabled && inputActive && user.valid;
            // PlayerInput.actions can lazily clone assets and install hooks. Observe only the
            // already-associated InputUser collection; diagnostics never call that lazy getter.
            IInputActionCollection actions = active ? user.actions : null;
            int resolved =
                actions == null
                    ? 0
                    : actions.Where(action => action.enabled).SelectMany(action => action.controls).Count(control => control.device == _gamepad);
            return new Dictionary<string, object>
            {
                ["player_index"] = Index(player),
                ["enabled"] = player.isActiveAndEnabled,
                ["input_active"] = inputActive,
                ["user_valid"] = user.valid,
                ["control_scheme"] = (string)Scheme?.GetValue(player),
                ["action_map"] = (Map?.GetValue(player) as InputActionMap)?.name,
                ["paired_device_ids"] = user.valid ? user.pairedDevices.Select(InputSystemCompatibility.DeviceId).ToArray() : Array.Empty<int?>(),
                ["owned_gamepad_id"] = InputSystemCompatibility.DeviceId(_gamepad),
                ["owned_gamepad_paired"] = _gamepad != null && user.valid && user.pairedDevices.Contains(_gamepad),
                ["resolved_owned_controls"] = resolved,
                ["action_inspection_available"] = actions != null,
                ["delivery_eligible"] = player.isActiveAndEnabled && inputActive && user.valid && resolved > 0,
                ["pairing_lease_selected"] = _pairedPlayer == player,
                ["note"] =
                    "Eligibility observes enabled action control resolution for this player only; callbacks, gameplay handling and successful delivery are not guaranteed.",
            };
        }

        public object PairPlayer(GameObject target, string device)
        {
            if (!Available)
                throw new ArgumentException(UnavailableReason);
            if (device != "gamepad")
                throw new ArgumentException("Only the MCP-owned gamepad can be explicitly paired.");
            Behaviour player = RequirePlayer(target);
            var user = (InputUser)User.GetValue(player);
            bool inputActive = (bool)InputActive.GetValue(player);
            if (!CanPairPlayer)
                throw new ArgumentException("Installed PlayerInput lacks the actions or writable auto-switch API needed to preserve routing.");
            if (!player.isActiveAndEnabled || !user.valid || !inputActive || Actions.GetValue(player) == null)
                throw new ArgumentException("Pairing requires an enabled PlayerInput with active actions and a valid InputUser.");
            if (_subscribed && Clock >= _deadline)
                ReleaseAll();
            if (_pairedPlayer != null && _pairedPlayer != player)
                throw new ArgumentException("Another PlayerInput owns this pairing lease; release_player first.");
            if (_pairedPlayer == player && _gamepad != null && _gamepad.added && _pairedUser.valid && user.id == _pairedUser.id)
                return DiagnosePlayer(target);
            if (_pairedPlayer == player)
                ReleasePlayerLease();
            if (_gamepad != null && _gamepad.added && _gamepadReleases.Count > 0)
                throw new ArgumentException("Release held gamepad controls before creating a PlayerInput pairing lease.");
            EnsureLease();
            _pairedPlayer = player;
            _pairedUser = user;
            try
            {
                _restoreAutoSwitch = !(bool)AutoSwitch.GetValue(player);
                if (_restoreAutoSwitch)
                    AutoSwitch.SetValue(player, true);
                Gamepad owned = EnsureGamepad();
                InputUser.PerformPairingWithDevice(owned, _pairedUser, InputUserPairingOptions.ForceNoPlatformUserAccountSelection);
                if (_pairedPlayer != player || _gamepad != owned || !owned.added)
                    throw new InvalidOperationException("PlayerInput pairing was interrupted; retry after cleanup.");
                return DiagnosePlayer(target);
            }
            catch
            {
                ReleasePlayerLease();
                throw;
            }
        }

        public object ReleasePlayer(GameObject target)
        {
            Behaviour player = RequirePlayer(target);
            bool released = _pairedPlayer == player;
            if (released)
                ReleasePlayerLease();
            var diagnostics = PlayerDiagnostics(player);
            diagnostics["pairing_released"] = released;
            return diagnostics;
        }

        private void ReleasePlayerLease()
        {
            if (_keyboard == null && _mouse == null && _touchscreen == null)
            {
                ReleaseAll();
                return;
            }
            RestorePlayerPairing();
            ReleaseGamepad();
        }

        private void RestorePlayerPairing()
        {
            Behaviour player = _pairedPlayer;
            InputUser user = _pairedUser;
            bool restore = _restoreAutoSwitch;
            _pairedPlayer = null;
            _pairedUser = default;
            _restoreAutoSwitch = false;
            if (user.valid && _gamepad != null && user.pairedDevices.Contains(_gamepad))
                user.UnpairDevice(_gamepad);
            if (restore && player != null && (bool)AutoSwitch.GetValue(player))
                AutoSwitch.SetValue(player, false);
        }
    }
}
#endif
