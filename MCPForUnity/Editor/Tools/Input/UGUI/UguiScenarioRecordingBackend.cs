using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;

namespace MCPForUnity.Editor.Tools.Input
{
    [InitializeOnLoad]
    public sealed class UguiScenarioRecordingBackend : IPlayScenarioRecordingBackend
    {
        static UguiScenarioRecordingBackend() => PlayScenarioRecordingService.Backend = new UguiScenarioRecordingBackend();

        public IPlayScenarioRecordingSession Begin(Action<PlayScenarioRecordedTarget> click, Action<string> diagnose)
        {
            var session = new Session(click, diagnose);
            try
            {
                session.Refresh();
                return session;
            }
            catch
            {
                session.Dispose();
                throw;
            }
        }

        private sealed class Session : IPlayScenarioRecordingSession
        {
            private readonly Action<PlayScenarioRecordedTarget> click;
            private readonly Action<string> diagnose;
            private readonly Dictionary<Button, UguiScenarioRecordingObserver> observers = new Dictionary<Button, UguiScenarioRecordingObserver>();
            private readonly List<Button> removed = new List<Button>();
            private readonly List<Button> buttons = new List<Button>();
            private readonly List<GameObject> roots = new List<GameObject>();
            private readonly List<GameObject> scratchRoots = new List<GameObject>();
            private bool disposed;

            public Session(Action<PlayScenarioRecordedTarget> click, Action<string> diagnose)
            {
                this.click = click;
                this.diagnose = diagnose;
            }

            public void Refresh()
            {
                if (disposed)
                    return;
                removed.Clear();
                foreach (var entry in observers)
                    if (
                        entry.Key == null
                        || entry.Value == null
                        || !entry.Value.HasButton
                        || !entry.Key.gameObject.scene.isLoaded
                        || EditorSceneManager.IsPreviewScene(entry.Key.gameObject.scene)
                    )
                    {
                        Remove(entry.Value);
                        removed.Add(entry.Key);
                    }
                foreach (Button button in removed)
                    observers.Remove(button);
                int supported = 0;
                try
                {
                    SceneObjectRoots.CopyTo(roots, scratchRoots);
                    foreach (GameObject root in roots)
                    {
                        if (root == null)
                            continue;
                        root.GetComponentsInChildren(true, buttons);
                        foreach (Button button in buttons)
                        {
                            if (button == null || !button.gameObject.scene.IsValid() || !button.gameObject.scene.isLoaded)
                                continue;
                            if (++supported > 512)
                            {
                                diagnose("The recorder supports at most 512 loaded Buttons. Additional Buttons were not observed.");
                                return;
                            }
                            if (observers.ContainsKey(button))
                                continue;
                            if (observers.Count >= 512)
                            {
                                diagnose("The recorder's 512 retained observer limit was reached. Additional Buttons were not observed.");
                                return;
                            }
                            if (button.GetComponents<Button>().Length != 1)
                            {
                                diagnose("Multiple Button components share one GameObject. Its pointer interactions were not recorded.");
                                continue;
                            }
                            // Do not add EventTrigger: its extra interfaces change drag/scroll event routing.
                            UguiScenarioRecordingObserver observer = button.gameObject.AddComponent<UguiScenarioRecordingObserver>();
                            if (observer == null)
                                throw new InvalidOperationException("Unity could not attach the runtime uGUI recording observer.");
                            observer.hideFlags = HideFlags.HideInInspector | HideFlags.DontSave;
                            observer.Bind(button, target => click(PlayScenarioRecordingService.DescribeTarget(target)));
                            observers.Add(button, observer);
                            // Observe before an onClick callback can synchronously destroy the source scene.
                            // Moving only this temporary component preserves all original component ordering.
                            int remaining = button.GetComponents<Component>().Length;
                            while (remaining-- > 0 && Array.IndexOf(button.GetComponents<Component>(), observer) > 1)
                                if (!UnityEditorInternal.ComponentUtility.MoveComponentUp(observer))
                                    break;
                        }
                    }
                }
                finally
                {
                    roots.Clear();
                    scratchRoots.Clear();
                    buttons.Clear();
                    removed.Clear();
                }
            }

            public void Dispose()
            {
                if (disposed)
                    return;
                disposed = true;
                foreach (UguiScenarioRecordingObserver observer in observers.Values)
                    Remove(observer);
                observers.Clear();
                roots.Clear();
                scratchRoots.Clear();
                buttons.Clear();
                removed.Clear();
            }

            private static void Remove(UguiScenarioRecordingObserver observer)
            {
                if (observer == null)
                    return;
                observer.Release();
                if (Application.isPlaying)
                    UnityEngine.Object.Destroy(observer);
                else
                    UnityEngine.Object.DestroyImmediate(observer);
            }
        }
    }
}
