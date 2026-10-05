#version 300 es
// Filtro colore di AIOS (Impostazioni › Accessibilità): tritanopia (blu-giallo)
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Daltonizzazione: si simula come la vede chi ha la tritanopia (blu-giallo), e la differenza si sposta sui colori che distingue
void main() {
    vec4 c = texture(tex, v_texcoord);
    float L = 17.8824 * c.r + 43.5161 * c.g + 4.11935 * c.b;
    float M = 3.45565 * c.r + 27.1554 * c.g + 3.86714 * c.b;
    float S = 0.0299566 * c.r + 0.184309 * c.g + 1.46709 * c.b;
    float l = L; float m = M; float s = -0.395913 * L + 0.801109 * M;
    vec3 sim = vec3(0.0809444479 * l - 0.130504409 * m + 0.116721066 * s,
                    -0.0102485335 * l + 0.0540193266 * m - 0.113614708 * s,
                    -0.000365296938 * l - 0.00412161469 * m + 0.693511405 * s);
    vec3 err = c.rgb - sim;
    vec3 fix = vec3(0.0, 0.7 * err.r + err.g, 0.7 * err.r + err.b);
    fragColor = vec4(clamp(c.rgb + fix, 0.0, 1.0), c.a);
}
