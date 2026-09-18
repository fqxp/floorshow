#define MAX_SHAPES 64

struct Shape {
    vec4 a;   // center.xyz, inner radius (rad)
    vec4 b;   // outer radius (rad), softness (rad), alpha, blend (0 over, 1 add, 2 multiply, 3 screen)
    vec4 c;   // color.rgb, world frame (0 = ball, 1 = image)
    vec4 t;   // texture layer (-1 none), mapping (0 shape, 1 latlon), scroll, unused
    vec4 s;   // texture scale x, y, unused, unused
};
layout(std140) uniform Shapes { Shape shapes[MAX_SHAPES]; };
uniform int u_numShapes;
uniform mat3 u_spin;                 // scene spin, applied in the ball frame
uniform sampler2DArray u_tex;

out vec4 fragColor;

const float PI = 3.14159265358979;

float annulusCoverage(float ang, float rin, float rout, float soft) {
    float inner = 1.0;
    float outer;
    if (soft < 1e-5) {
        if (rin > 0.0) inner = step(rin, ang);
        outer = rout >= PI - 1e-4 ? 1.0 : 1.0 - step(rout, ang);
    } else {
        // No fade at the centre of a filled circle: acos() >= 0 would leave a dark dot there.
        if (rin > 0.0) inner = smoothstep(rin - soft, rin + soft, ang);
        outer = rout >= PI - 1e-4 ? 1.0 : 1.0 - smoothstep(rout - soft, rout + soft, ang);
    }
    return inner * outer;
}

vec2 textureCoord(vec3 d, vec3 center, float ang, float rin, float rout, float mapping, vec2 scale, float scroll) {
    if (mapping < 0.5) {
        // shape-local: u = azimuth around the shape centre, v = position across the annulus
        vec3 ref = abs(center.y) < 0.99 ? vec3(0.0, 1.0, 0.0) : vec3(0.0, 0.0, 1.0);
        vec3 e1 = normalize(cross(ref, center));
        vec3 e2 = cross(center, e1);
        float az = atan(dot(d, e2), dot(d, e1));
        float v = rout > rin ? (ang - rin) / (rout - rin) : 0.0;
        return vec2(az / (2.0 * PI) * scale.x + scroll, v * scale.y);
    }
    float lat = asin(clamp(d.y, -1.0, 1.0));
    float lon = atan(d.x, d.z);
    return vec2((lon / (2.0 * PI) + 0.5) * scale.x + scroll, (lat / PI + 0.5) * scale.y);
}

void main() {
    vec3 dImg;
    if (!pixelToImageDir(gl_FragCoord.xy, dImg)) {
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);   // outside the ball: no light, so nothing spills onto the wall
        return;
    }
    vec3 dBall = u_spin * (u_imgToBall * dImg);

    vec3 col = vec3(0.0);
    for (int i = 0; i < u_numShapes; i++) {
        Shape s = shapes[i];
        vec3 d = s.c.w > 0.5 ? dImg : dBall;
        float ang = acos(clamp(dot(d, s.a.xyz), -1.0, 1.0));
        float cov = annulusCoverage(ang, s.a.w, s.b.x, s.b.y);
        if (cov <= 0.0) continue;

        vec3 src = s.c.rgb;
        if (s.t.x >= 0.0) {
            vec2 uv = textureCoord(d, s.a.xyz, ang, s.a.w, s.b.x, s.t.y, s.s.xy, s.t.z);
            src *= textureLod(u_tex, vec3(uv, s.t.x), 0.0).rgb;
        }
        float a = cov * s.b.z;
        int blend = int(s.b.w + 0.5);
        if (blend == 1)      col = col + src * a;
        else if (blend == 2) col = mix(col, col * src, a);
        else if (blend == 3) col = mix(col, 1.0 - (1.0 - col) * (1.0 - src), a);
        else                 col = mix(col, src, a);
    }
    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
