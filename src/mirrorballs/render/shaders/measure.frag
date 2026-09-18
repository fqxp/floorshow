#version 330
// Averages the scene target into a small grid: each output pixel is the mean of taps x taps samples
// spread evenly over its cell. Unlike a mip chain this is exact for any (non power-of-two) frame size.
uniform sampler2D u_src;
uniform int u_taps;
uniform float u_grid;
out vec4 fragColor;

void main() {
    vec2 cell = floor(gl_FragCoord.xy);
    vec3 acc = vec3(0.0);
    for (int j = 0; j < u_taps; j++) {
        for (int i = 0; i < u_taps; i++) {
            vec2 uv = (cell + (vec2(float(i), float(j)) + 0.5) / float(u_taps)) / u_grid;
            acc += texture(u_src, uv).rgb;
        }
    }
    fragColor = vec4(acc / float(u_taps * u_taps), 1.0);
}
