#nullable disable
using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal static class ManageGameObjectCommon
    {
        internal static Vector3? ReadOptionalVector3(JObject parameters, string field)
        {
            JToken token = parameters[field];
            Vector3? value = VectorParsing.ParseVector3(token);
            if (!value.HasValue && token != null && token.Type != JTokenType.Null)
                throw new ArgumentException($"'{field}' must be a vector [x, y, z] or an object with x, y and z values.");
            return value;
        }

        internal static void ValidateComponentParameters(JObject parameters, bool validateRemovals, GameObject componentSource = null)
        {
            var additionDefinitions = new List<KeyValuePair<Type, JObject>>();
            Type transformType =
                componentSource != null ? componentSource.transform.GetType()
                : string.IsNullOrEmpty(parameters["prefabPath"]?.ToString()) ? typeof(Transform)
                : null;
            JToken additions = parameters["componentsToAdd"];
            if (additions != null && additions.Type != JTokenType.Null)
            {
                if (!(additions is JArray components))
                    throw new ArgumentException("'componentsToAdd' must be an array.");
                foreach (JToken component in components)
                {
                    JToken typeName = component is JObject entry ? entry["typeName"] : component;
                    if (typeName?.Type != JTokenType.String || string.IsNullOrWhiteSpace(typeName.ToString()))
                        throw new ArgumentException("Each 'componentsToAdd' entry must contain a nonempty component type name.");
                    string typeError = GameObjectComponentHelpers.ValidateComponentType(typeName.ToString(), false, out Type componentType);
                    if (typeError != null)
                        throw new ArgumentException(typeError);
                    if (
                        component is JObject definition
                        && definition["properties"] is JToken properties
                        && properties.Type != JTokenType.Null
                        && !(properties is JObject)
                    )
                        throw new ArgumentException("Component 'properties' must be an object.");
                    additionDefinitions.Add(new KeyValuePair<Type, JObject>(componentType, (component as JObject)?["properties"] as JObject));
                }
            }

            var presentTypes =
                componentSource != null
                    ? componentSource.GetComponents<Component>().Where(component => component != null).Select(component => component.GetType()).ToList()
                    : new List<Type> { typeof(Transform) };
            JToken removals = parameters["componentsToRemove"];
            if (validateRemovals && removals != null && removals.Type != JTokenType.Null)
            {
                if (!(removals is JArray components))
                    throw new ArgumentException("'componentsToRemove' must be an array.");
                foreach (JToken component in components)
                {
                    if (component.Type != JTokenType.String || string.IsNullOrWhiteSpace(component.ToString()))
                        throw new ArgumentException("Each 'componentsToRemove' entry must be a nonempty component type name.");
                    string typeError = GameObjectComponentHelpers.ValidateComponentType(component.ToString(), true, out Type removalType);
                    if (typeError != null)
                        throw new ArgumentException(typeError);
                    if (componentSource != null)
                    {
                        int index = presentTypes.FindIndex(removalType.IsAssignableFrom);
                        if (index < 0)
                            throw new ArgumentException($"Component '{component}' not found on '{componentSource.name}' to remove.");
                        presentTypes.RemoveAt(index);
                    }
                }
            }
            string finalName = parameters["name"]?.ToString() ?? parameters["new_name"]?.ToString() ?? parameters["newName"]?.ToString();
            if (string.IsNullOrEmpty(finalName))
                finalName = componentSource != null ? componentSource.name : null;
            foreach (var addition in additionDefinitions)
            {
                string planError = GameObjectComponentHelpers.ValidateAdditionPlan(presentTypes, new[] { addition.Key });
                if (planError != null)
                    throw new ArgumentException(planError);
                string propertyError = GameObjectComponentHelpers.ValidateAdditionProperties(
                    addition.Key,
                    addition.Value,
                    transformType,
                    out _,
                    (type, value) => GameObjectComponentHelpers.IsPlannedTargetReference(type, value, finalName, componentSource, presentTypes),
                    componentSource
                );
                if (propertyError != null)
                    throw new ArgumentException(propertyError);
            }

            JToken componentProperties = parameters["componentProperties"];
            if (componentProperties == null || componentProperties.Type == JTokenType.Null)
                return;
            if (!(componentProperties is JObject propertyMap))
                throw new ArgumentException("'componentProperties' must be an object.");
            foreach (JProperty component in propertyMap.Properties())
                if (string.IsNullOrWhiteSpace(component.Name) || !(component.Value is JObject))
                    throw new ArgumentException("Each 'componentProperties' entry must map a nonempty component type name to an object.");
        }

        internal static GameObject FindObjectInternal(JToken targetToken, string searchMethod, JObject findParams = null)
        {
            bool findAll = findParams?["findAll"]?.ReadScalar<bool?>() ?? false;

            if (targetToken?.Type == JTokenType.Integer || (searchMethod == "by_id" && int.TryParse(targetToken?.ToString(), out _)))
            {
                findAll = false;
            }

            List<GameObject> results = FindObjectsInternal(targetToken, searchMethod, findAll, findParams);
            return results.Count > 0 ? results[0] : null;
        }

        internal static List<GameObject> FindObjectsInternal(JToken targetToken, string searchMethod, bool findAll, JObject findParams = null)
        {
            List<GameObject> results = new List<GameObject>();
            string searchTerm = findParams?["searchTerm"]?.ToString() ?? targetToken?.ToString();
            bool searchInChildren = findParams?["searchInChildren"]?.ReadScalar<bool?>() ?? false;
            bool searchInactive = findParams?["searchInactive"]?.ReadScalar<bool?>() ?? false;

            if (string.IsNullOrEmpty(searchMethod))
            {
                if (targetToken?.Type == JTokenType.Integer || int.TryParse(searchTerm, out _))
                    searchMethod = "by_id";
                else if (!string.IsNullOrEmpty(searchTerm) && searchTerm.Contains('/'))
                    searchMethod = "by_path";
                else
                    searchMethod = "by_name";
            }

            GameObject rootSearchObject = null;
            if (searchInChildren && targetToken != null)
            {
                rootSearchObject = FindObjectInternal(targetToken, "by_id_or_name_or_path");
                if (rootSearchObject == null)
                {
                    McpLog.Warn($"[ManageGameObject.Find] Root object '{targetToken}' for child search not found.");
                    return results;
                }
            }

            switch (searchMethod)
            {
                case "by_id":
                    if (int.TryParse(searchTerm, out int instanceId))
                    {
                        var allObjects = GetAllSceneObjects(searchInactive);
                        GameObject obj = allObjects.FirstOrDefault(go => go.GetInstanceIDCompat() == instanceId);
                        if (obj != null)
                            results.Add(obj);
                    }
                    break;

                case "by_name":
                    var searchPoolName = rootSearchObject
                        ? rootSearchObject.GetComponentsInChildren<Transform>(searchInactive).Select(t => t.gameObject)
                        : GetAllSceneObjects(searchInactive);
                    AddMatches(results, searchPoolName.Where(go => go.name == searchTerm), findAll);
                    break;

                case "by_path":
                    if (rootSearchObject != null)
                    {
                        Transform foundTransform = rootSearchObject.transform.Find(searchTerm);
                        if (foundTransform != null)
                            results.Add(foundTransform.gameObject);
                    }
                    else
                    {
                        var prefabStage = PrefabStageUtility.GetCurrentPrefabStage();
                        if (prefabStage != null || searchInactive)
                        {
                            // In Prefab Stage, GameObject.Find() doesn't work, need to search manually
                            var allObjects = GetAllSceneObjects(searchInactive);
                            foreach (var go in allObjects)
                            {
                                if (GameObjectLookup.MatchesPath(go, searchTerm))
                                {
                                    results.Add(go);
                                }
                            }
                        }
                        else
                        {
                            var found = GameObject.Find(searchTerm);
                            if (found != null)
                                results.Add(found);
                        }
                    }
                    break;

                case "by_tag":
                    var searchPoolTag = rootSearchObject
                        ? rootSearchObject.GetComponentsInChildren<Transform>(searchInactive).Select(t => t.gameObject)
                        : GetAllSceneObjects(searchInactive);
                    AddMatches(results, searchPoolTag.Where(go => go.CompareTag(searchTerm)), findAll);
                    break;

                case "by_layer":
                    var searchPoolLayer = rootSearchObject
                        ? rootSearchObject.GetComponentsInChildren<Transform>(searchInactive).Select(t => t.gameObject)
                        : GetAllSceneObjects(searchInactive);
                    if (int.TryParse(searchTerm, out int layerIndex))
                    {
                        AddMatches(results, searchPoolLayer.Where(go => go.layer == layerIndex), findAll);
                    }
                    else
                    {
                        int namedLayer = LayerMask.NameToLayer(searchTerm);
                        if (namedLayer != -1)
                            AddMatches(results, searchPoolLayer.Where(go => go.layer == namedLayer), findAll);
                    }
                    break;

                case "by_component":
                    Type componentType = FindType(searchTerm);
                    if (componentType != null)
                    {
                        IEnumerable<GameObject> searchPoolComp;
                        if (rootSearchObject)
                        {
                            searchPoolComp = rootSearchObject.GetComponentsInChildren(componentType, searchInactive).Select(c => (c as Component).gameObject);
                        }
                        else
                        {
                            searchPoolComp = UnityFindObjectsCompat.FindAll(componentType, searchInactive).Cast<Component>().Select(c => c.gameObject);
                        }
                        AddMatches(results, searchPoolComp.Where(go => go != null), findAll);
                    }
                    else
                    {
                        McpLog.Warn($"[ManageGameObject.Find] Component type not found: {searchTerm}");
                    }
                    break;

                case "by_id_or_name_or_path":
                    if (int.TryParse(searchTerm, out int id))
                    {
                        var allObjectsId = GetAllSceneObjects(true);
                        GameObject objById = allObjectsId.FirstOrDefault(go => go.GetInstanceIDCompat() == id);
                        if (objById != null)
                        {
                            results.Add(objById);
                            break;
                        }
                    }

                    // Try path search - in Prefab Stage, GameObject.Find() doesn't work
                    var allObjectsForPath = GetAllSceneObjects(true);
                    GameObject objByPath = allObjectsForPath.FirstOrDefault(go =>
                    {
                        return GameObjectLookup.MatchesPath(go, searchTerm);
                    });
                    if (objByPath != null)
                    {
                        results.Add(objByPath);
                        break;
                    }

                    var allObjectsName = GetAllSceneObjects(true);
                    results.AddRange(allObjectsName.Where(go => go.name == searchTerm));
                    break;

                default:
                    McpLog.Warn($"[ManageGameObject.Find] Unknown search method: {searchMethod}");
                    break;
            }

            if (!findAll && results.Count > 1)
            {
                return new List<GameObject> { results[0] };
            }

            return results.Distinct().ToList();
        }

        internal static void AddMatches(List<GameObject> results, IEnumerable<GameObject> matches, bool findAll)
        {
            results.AddRange(findAll ? matches : matches.Take(1));
        }

        private static IEnumerable<GameObject> GetAllSceneObjects(bool includeInactive)
        {
            // Delegate to GameObjectLookup to avoid code duplication and ensure consistent behavior
            return GameObjectLookup.GetAllSceneObjects(includeInactive);
        }

        private static Type FindType(string typeName)
        {
            if (ComponentResolver.TryResolve(typeName, out Type resolvedType, out string error))
            {
                return resolvedType;
            }

            if (!string.IsNullOrEmpty(error))
            {
                McpLog.Warn($"[FindType] {error}");
            }

            return null;
        }
    }
}
