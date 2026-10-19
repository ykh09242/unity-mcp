Shader "Hidden/MCPForUnity/RecordingAspect"
{
    Properties
    {
        _MainTex ("Frame", 2D) = "white" {}
    }
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float4 _AspectScale;

            fixed4 frag(v2f_img input) : SV_Target
            {
                float2 uv = (input.uv - 0.5) * _AspectScale.xy + 0.5;
                if (any(uv < 0.0) || any(uv > 1.0))
                    return fixed4(0, 0, 0, 1);
                return tex2D(_MainTex, uv);
            }
            ENDCG
        }
    }
    Fallback Off
}
