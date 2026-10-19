using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    // The four CLI shapes deliberately retain their native defaults and initial GO response.
    // This is not a generic batch executor and does not change ordinary create semantics.
    internal static class UguiCliCreation
    {
        internal static Func<object> Prepare(JObject request)
        {
            string kind = RequiredString(request, "element_type", 16);
            if (!new[] { "canvas", "text", "button", "image" }.Contains(kind))
                throw new ArgumentException("CLI creation supports canvas, text, button or image.");
            var allowed = new HashSet<string> { "action", "element_type", "name" };
            if (kind == "canvas")
                allowed.Add("render_mode");
            else
                allowed.Add("parent");
            if (kind == "text" || kind == "button")
                allowed.Add("text");
            if (kind == "text")
                allowed.Add("position");
            if (kind == "image")
                allowed.Add("sprite");
            if (request.Properties().Any(property => !allowed.Contains(property.Name)))
                throw new ArgumentException("Unsupported field for this CLI creation shape.");
            if (request["action"]?.Type != JTokenType.String || request.Value<string>("action") != "create_cli")
                throw new ArgumentException("Expected the exact create_cli action.");

            var toolParams = new ToolParams(request);
            JToken nameToken = request["name"];
            if (nameToken == null || (nameToken.Type != JTokenType.String && nameToken.Type != JTokenType.Date))
                throw new ArgumentException("name must be a string.");
            string name = toolParams.Get("name");
            ManageUGUI.ValidateCreationName(name);
            BoundedUnicode(name, "name", 4096);
            GameObject parent = kind == "canvas" ? null : ManageUGUI.Resolve(request["parent"], false);
            if (parent != null && (!(parent.transform is RectTransform) || parent.GetComponentInParent<Canvas>(true) == null))
                throw new ArgumentException("A CLI UI element requires a RectTransform parent beneath a Canvas.");

            RenderMode mode = RenderMode.ScreenSpaceOverlay;
            if (kind == "canvas" && request["render_mode"] != null)
            {
                string value = RequiredString(request, "render_mode", 32);
                if (!new[] { "ScreenSpaceOverlay", "ScreenSpaceCamera", "WorldSpace" }.Contains(value))
                    throw new ArgumentException("Unsupported Canvas render_mode.");
                mode = (RenderMode)Enum.Parse(typeof(RenderMode), value);
            }
            string content = kind == "text" || kind == "button" ? RequiredString(request, "text", 65536, true) : null;
            Vector2 position = Vector2.zero;
            if (kind == "text" && request["position"] != null)
                position = Position(request["position"]);

            Type image = kind == "image" || kind == "button" ? ManageUGUI.RequireType("UnityEngine.UI.Image") : null;
            Type button = kind == "button" ? ManageUGUI.RequireType("UnityEngine.UI.Button") : null;
            Type text = kind == "text" || kind == "button" ? ManageUGUI.RequireType("TMPro.TextMeshProUGUI") : null;
            Type scaler = kind == "canvas" ? ManageUGUI.RequireType("UnityEngine.UI.CanvasScaler") : null;
            Type raycaster = kind == "canvas" ? ManageUGUI.RequireType("UnityEngine.UI.GraphicRaycaster") : null;
            UnityEngine.Object font = text == null ? null : ManageUGUI.DefaultFont();
            PropertyInfo fontProperty = text == null ? null : Property(text, "font", font.GetType());
            PropertyInfo textProperty = text == null ? null : Property(text, "text", typeof(string));
            bool hasSprite = kind == "image" && request["sprite"] != null;
            Sprite sprite = hasSprite ? ManageUGUI.ResolveImageSprite(request["sprite"]) : null;
            PropertyInfo spriteProperty = hasSprite ? Property(image, "sprite", typeof(Sprite)) : null;

            return () =>
            {
                if (kind == "canvas")
                    return CreateCanvas(name, mode, scaler, raycaster);
                // Reuse the old root construction, serialization and world-preserving parenting.
                var input = new JObject { ["name"] = name };
                if (parent != null)
                    input["parent"] = parent.GetInstanceIDCompat();
                if (kind == "text")
                    input["position"] = new JArray(position.x, position.y, 0);
                var initial = CreateRoot(input, out GameObject root);
                if (image != null)
                {
                    Component visual = Add(root, image);
                    if (hasSprite)
                    {
                        Undo.RecordObject(visual, "Set CLI Image sprite");
                        spriteProperty.SetValue(visual, sprite);
                    }
                }
                if (button != null)
                    Add(root, button);
                if (kind == "text")
                {
                    SetText(Add(root, text), fontProperty, textProperty, font, content);
                    var rect = root.GetComponent<RectTransform>();
                    if (rect == null)
                        throw new InvalidOperationException("TMP creation did not produce a RectTransform.");
                    Undo.RecordObject(rect, "Set CLI text position");
                    rect.anchoredPosition = position;
                }
                else if (kind == "button")
                {
                    CreateRoot(new JObject { ["name"] = name + "_Label", ["parent"] = root.GetInstanceIDCompat() }, out GameObject label);
                    SetText(Add(label, text), fontProperty, textProperty, font, content);
                }
                // The returned data intentionally describes the initial root, like the old CLI.
                // It is not emitted until all registered changes have completed successfully.
                return initial;
            };
        }

        private static SuccessResponse CreateCanvas(string name, RenderMode mode, Type scaler, Type raycaster)
        {
            GameObject created = null;
            bool registered = false;
            try
            {
                created = new GameObject(name);
                var initial = new SuccessResponse(
                    $"GameObject '{created.name}' created successfully in scene.",
                    GameObjectSerializer.GetGameObjectData(created)
                );
                bool active = created.activeSelf;
                created.SetActive(false);
                var canvas = created.AddComponent<Canvas>();
                if (canvas == null)
                    throw new InvalidOperationException("Required CLI Canvas could not be added.");
                if (scaler == null || created.AddComponent(scaler) == null)
                    throw new InvalidOperationException("Required CLI CanvasScaler could not be added.");
                if (raycaster == null || created.AddComponent(raycaster) == null)
                    throw new InvalidOperationException("Required CLI GraphicRaycaster could not be added.");
                created.SetActive(active);
                canvas.renderMode = mode;
                // Capture the completed new graph once, without component or property Undo records.
                Undo.RegisterCreatedObjectUndo(created, $"Create GameObject '{created.name}'");
                registered = true;
                Selection.activeGameObject = created;
                return initial;
            }
            catch (Exception primaryError)
            {
                if (!registered && created != null)
                {
                    try
                    {
                        UnityEngine.Object.DestroyImmediate(created);
                    }
                    catch (Exception cleanupError)
                    {
                        throw new AggregateException("CLI Canvas construction and owned-root cleanup failed.", primaryError, cleanupError);
                    }
                }
                throw;
            }
        }

        private static SuccessResponse CreateRoot(JObject input, out GameObject root)
        {
            object result = GameObjectCreate.Handle(input);
            if (!(result is SuccessResponse success))
                throw new InvalidOperationException(result is ErrorResponse failure ? failure.Error : "CLI root creation failed.");
            var data = JObject.FromObject(success.Data);
            if (data["instanceID"]?.Type != JTokenType.Integer)
                throw new InvalidOperationException("CLI root creation returned no integer identity.");
            root = GameObjectLookup.FindById(data.Value<int>("instanceID"));
            if (root == null)
                throw new InvalidOperationException("Created CLI root is no longer available.");
            return success;
        }

        private static Component Add(GameObject root, Type type)
        {
            Component value = Undo.AddComponent(root, type);
            if (value == null)
                throw new InvalidOperationException("Required CLI component could not be added: " + type.FullName);
            return value;
        }

        private static void SetText(Component component, PropertyInfo fontProperty, PropertyInfo textProperty, UnityEngine.Object font, string content)
        {
            Undo.RecordObject(component, "Set CLI text");
            fontProperty.SetValue(component, font);
            textProperty.SetValue(component, content);
        }

        private static PropertyInfo Property(Type type, string name, Type assignedType)
        {
            PropertyInfo property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public);
            if (property == null || !property.CanWrite || property.GetIndexParameters().Length != 0 || !property.PropertyType.IsAssignableFrom(assignedType))
                throw new ArgumentException("Required CLI component property is unavailable: " + type.FullName + "." + name);
            return property;
        }

        private static string RequiredString(JObject input, string key, int limit, bool allowEmpty = false)
        {
            if (input[key]?.Type != JTokenType.String)
                throw new ArgumentException(key + " must be a string.");
            string value = input.Value<string>(key);
            if (!allowEmpty && value.Length == 0)
                throw new ArgumentException(key + " must not be empty.");
            BoundedUnicode(value, key, limit);
            return value;
        }

        private static void BoundedUnicode(string value, string key, int limit)
        {
            if (value.Length > limit)
                throw new ArgumentException(key + " exceeds its CLI creation bound.");
            new UTF8Encoding(false, true).GetByteCount(value);
        }

        private static Vector2 Position(JToken token)
        {
            if (!(token is JArray values) || values.Count != 2)
                throw new ArgumentException("position must contain exactly two finite coordinates.");
            var result = Vector2.zero;
            for (int axis = 0; axis < 2; axis++)
            {
                if (values[axis].Type != JTokenType.Integer && values[axis].Type != JTokenType.Float)
                    throw new ArgumentException("position must contain exactly two finite coordinates.");
                double value = values[axis].Value<double>();
                float narrowed = (float)value;
                if (double.IsNaN(value) || double.IsInfinity(value) || float.IsNaN(narrowed) || float.IsInfinity(narrowed))
                    throw new ArgumentException("position must be finite in Unity coordinates.");
                result[axis] = narrowed;
            }
            return result;
        }
    }
}
