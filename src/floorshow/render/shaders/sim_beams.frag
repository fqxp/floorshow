in vec3 v_world;
in vec3 v_color;
in vec3 v_dir;
in vec2 v_local;
in float v_along;
in float v_width;
in vec3 v_wallN;

uniform int u_mode;
uniform float u_width0;
uniform float u_beamGain;       // haze brightness, in display units
uniform float u_spotGain;       // wall brightness

out vec4 fragColor;

void main() {
    if (behindBall(u_eye, v_world)) discard;
    vec3 toEye = u_eye - v_world;
    float dist = length(toEye);
    float spread = u_width0 / v_width;
    vec3 e = v_color * spread * spread;  // irradiance inside the beam: the light spreads as it widens
    float atten = exp(-u_ext * (v_along + dist));
    if (u_mode == 0) {
        float x = v_local.x;
        float chord = sqrt(max(0.0, 1.0 - x * x));   // through a round beam, relative to its width
        float l = u_sigma * phase(dot(v_dir, toEye / dist)) * v_width * chord;
        fragColor = vec4(e * l * atten * u_beamGain, 1.0);
    } else {
        float r = length(v_local);
        float prof = 1.0 - smoothstep(0.7, 1.0, r);
        fragColor = vec4(e * prof * abs(dot(v_dir, v_wallN)) * atten * u_spotGain, 1.0);
    }
}
