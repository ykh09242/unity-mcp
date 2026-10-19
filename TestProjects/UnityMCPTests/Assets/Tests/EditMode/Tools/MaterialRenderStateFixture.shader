Shader "Hidden/MCPForUnityTests/MaterialRenderState"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Value ("Value", Float) = 0
    }
    SubShader
    {
        Tags { "RenderType" = "Opaque" "Queue" = "Geometry" }
        Pass
        {
            Name "DISPLAY_NAME_IS_NOT_LIGHTMODE"
            Tags { "LightMode" = "FixtureForward" }
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_local _ FIXTURE_LOCAL
            #pragma multi_compile _ FIXTURE_OVERRIDABLE
            #include "UnityCG.cginc"
            float4 vert(float4 vertex : POSITION) : SV_POSITION { return UnityObjectToClipPos(vertex); }
            fixed4 frag() : SV_Target { return fixed4(1, 1, 1, 1); }
            ENDCG
        }
        Pass
        {
            Name "UNTAGGED_PASS"
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            float4 vert(float4 vertex : POSITION) : SV_POSITION { return UnityObjectToClipPos(vertex); }
            fixed4 frag() : SV_Target { return fixed4(1, 1, 1, 1); }
            ENDCG
        }
    }
}
