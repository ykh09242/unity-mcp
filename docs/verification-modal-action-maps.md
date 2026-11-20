# Observe input shared by a menu and gameplay

Run the optional Unity Test Framework suite `modal-action-maps`, or select the EditMode filter `MCPForUnityTests.EditMode.Tools.Input.ModalActionMapTests` in a prepared project with the Input System enabled. Each test enters Play Mode. The MCP simulation backend injects Enter on a virtual keyboard or the south button on a virtual gamepad.

Both `UI/Submit` and `Gameplay/Fire` bind the same control. With both maps active while the menu is open, a single input confirms the menu and spends one round of ammunition. Disabling the gameplay map while the menu is open preserves ammunition. Closing the menu disables its map and restores gameplay input. The next press must spend exactly one round in both controls. The NUnit output records the active maps, actual performed actions, device layouts and IDs, control paths, confirmations and remaining ammunition for each press.
