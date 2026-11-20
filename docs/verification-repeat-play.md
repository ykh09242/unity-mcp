# Observe duplicate gameplay subscriptions across Play sessions

Run the EditMode Unity Test Framework filter `MCPForUnityTests.Editor.Services.RepeatPlaySubscriptionTests` in the licensed Editor matrix. The test enables scene reload, disables domain reload, and enters and exits Play twice for each control. The domain identifier must remain stable.

One coin event credits ten units to a fresh wallet. In the broken control the previous listener survives stopping Play, so one coin credits ten on the first entry and twenty on the second. The repair unregisters the listener on leaving Play. One coin then credits ten on both entries. Inspect the NUnit test output for the per-entry listener count, actual callback count, wallet balance and domain identifier. Teardown releases the test-owned listener and restores the prior Enter Play Mode options.
