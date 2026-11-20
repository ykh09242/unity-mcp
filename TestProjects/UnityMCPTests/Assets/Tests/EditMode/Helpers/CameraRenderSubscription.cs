using System;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnityTests.Editor.Helpers
{
    internal sealed class CameraRenderSubscription : IDisposable
    {
        internal enum Stage
        {
            BeforeCulling,
            BeforeRendering,
            AfterRendering,
        }

        private Action unsubscribe;

        internal CameraRenderSubscription(Stage stage, Action<Camera> callback)
        {
#if !UNITY_7000_0_OR_NEWER
            if (GraphicsSettings.currentRenderPipeline == null)
            {
                Camera.CameraCallback legacy = callback.Invoke;
                switch (stage)
                {
                    case Stage.BeforeCulling:
                        Camera.onPreCull += legacy;
                        unsubscribe = () => Camera.onPreCull -= legacy;
                        break;
                    case Stage.BeforeRendering:
                        Camera.onPreRender += legacy;
                        unsubscribe = () => Camera.onPreRender -= legacy;
                        break;
                    case Stage.AfterRendering:
                        Camera.onPostRender += legacy;
                        unsubscribe = () => Camera.onPostRender -= legacy;
                        break;
                }
                return;
            }
#endif
            Action<ScriptableRenderContext, Camera> pipelineCallback = (_, camera) => callback(camera);
            if (stage == Stage.AfterRendering)
            {
                RenderPipelineManager.endCameraRendering += pipelineCallback;
                unsubscribe = () => RenderPipelineManager.endCameraRendering -= pipelineCallback;
            }
            else
            {
                RenderPipelineManager.beginCameraRendering += pipelineCallback;
                unsubscribe = () => RenderPipelineManager.beginCameraRendering -= pipelineCallback;
            }
        }

        public void Dispose()
        {
            unsubscribe?.Invoke();
            unsubscribe = null;
        }
    }
}
