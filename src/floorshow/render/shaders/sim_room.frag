// Full screen: the room and the ball, plus the beams and the projector's beam drawn before; tone-mapped.
uniform sampler2D u_beams;       // beams and spots (HDR)
uniform sampler2D u_fog;         // the projector's beam (HDR, half resolution)
uniform vec2 u_res;
uniform mat4 u_invViewProj;
uniform mat3 u_roomToBall;       // room -> the turning ball's own frame
uniform vec2 u_tile;             // row pitch, row offset (degrees)
uniform float u_wallGain;        // the projector's light landing on a wall directly
uniform float u_ambient;         // the room's own dim light, so you keep your bearings while moving round
uniform float u_chainR;          // the chain the ball hangs from (radius)
uniform int u_showBeam;
uniform int u_showBall;
uniform int u_showRoom;

out vec4 fragColor;

// First hit of the ray with the chain: a thin vertical cylinder from the top of the ball to the ceiling, or -1.
float chainHit(vec3 o, vec3 d) {
    float a = dot(d.xz, d.xz);
    if (a < 1e-8) return -1.0;
    float b = dot(o.xz, d.xz);
    float h = b * b - a * (dot(o.xz, o.xz) - u_chainR * u_chainR);
    if (h < 0.0) return -1.0;
    float t = (-b - sqrt(h)) / a;
    float y = o.y + d.y * t;
    return t > 0.0 && y > u_radius * 0.98 && y < u_boxMax.y ? t : -1.0;
}

// The room in its ambient light: floor lightest, ceiling darkest, a 1 m grid on the floor, darker into the corners.
vec3 ambientWall(vec3 p, int axis, vec3 rd) {
    float face = axis == 1 ? (rd.y < 0.0 ? 1.0 : 0.55) : 0.75;
    vec3 a = abs(p) / max(abs(u_boxMin), abs(u_boxMax));
    float corner = 1.0 - 0.45 * max(max(a.x * a.y, a.y * a.z), a.x * a.z);
    float grid = 0.0;
    if (axis == 1 && rd.y < 0.0) {
        vec2 g = abs(fract(p.xz + 0.5) - 0.5) / max(fwidth(p.xz), vec2(1e-4));
        grid = 1.0 - smoothstep(0.5, 1.5, min(g.x, g.y));
    }
    return vec3(u_ambient * face * corner * (1.0 + 0.6 * grid));
}

void main() {
    vec2 ndc = gl_FragCoord.xy / u_res * 2.0 - 1.0;
    vec4 far = u_invViewProj * vec4(ndc, 1.0, 1.0);
    vec3 rd = normalize(far.xyz / far.w - u_eye);
    vec3 toProj = toProjector();
    int axis;
    float tWall = boxExit(u_eye, rd, axis);
    float tBall = sphereHit(u_eye, rd, u_radius);
    vec3 col = vec3(0.0);

    if (tBall > 0.0) {
        if (u_showBall != 0) {
            vec3 n = (u_eye + rd * tBall) / u_radius;
            // Which tile: rows of latitude, as many tiles in a row as fit around it (render/sim.py facet_layout).
            vec3 b = u_roomToBall * n;
            float lat = degrees(asin(clamp(b.y, -1.0, 1.0)));
            float lon = mod(degrees(atan(b.x, b.z)), 360.0);
            float k = floor((lat - u_tile.y) / u_tile.x + 0.5);
            float latC = u_tile.y + k * u_tile.x;
            float cnt = max(1.0, floor(360.0 * cos(radians(latC)) / u_tile.x + 0.5));
            float w = 360.0 / cnt;
            float lonC = (floor(lon / w) + 0.5) * w;
            float fx = abs(fract(lon / w) - 0.5);
            float fy = abs((lat - latC) / u_tile.x);
            float seam = smoothstep(0.40, 0.5, max(fx, fy));
            vec3 nb = vec3(cos(radians(latC)) * sin(radians(lonC)), sin(radians(latC)), cos(radians(latC)) * cos(radians(lonC)));
            vec3 nf = transpose(u_roomToBall) * nb;   // the tile's normal in the room
            vec3 ni = u_roomToImg * nf;
            vec3 light = vec3(0.0);
            if (ni.z > 0.0) {
                vec2 uv = vec2(u_cal.x + ni.x * u_cal.z, u_cal.y + ni.y * u_cal.w);
                light = texture(u_proj, uv).rgb;
            }
            // A mirror shows what it reflects: a glint when it sends the projector's light at the eye,
            // a faint glow otherwise (the glass edges and dust).
            float glint = pow(max(dot(reflect(rd, nf), toProj), 0.0), 400.0);
            col = (vec3(0.03) + light * (0.15 + 4.0 * glint)) * (1.0 - 0.8 * seam);
        }
    } else if (u_showRoom != 0) {
        vec3 p = u_eye + rd * tWall;
        vec3 n = vec3(0.0);
        n[axis] = -sign(rd[axis]);
        vec3 direct = projectorLight(p) * max(dot(n, toProj), 0.0);
        col = ambientWall(p, axis, rd) + direct * u_wallGain;
    }

    vec2 uv = gl_FragCoord.xy / u_res;
    if (u_showBeam != 0) col += texture(u_fog, uv).rgb;
    col += texture(u_beams, uv).rgb;

    // The chain, drawn over the light: it is thin enough that what passes in front of it does not matter.
    float tChain = u_showBall != 0 ? chainHit(u_eye, rd) : -1.0;
    if (tChain > 0.0 && (tBall < 0.0 || tChain < tBall)) {
        vec3 p = u_eye + rd * tChain;
        vec3 n = vec3(p.x, 0.0, p.z) / u_chainR;
        float lit = 0.45 + 0.55 * max(dot(n, normalize(vec3(0.4, 0.0, 0.9))), 0.0);
        float link = 0.55 + 0.45 * abs(sin(p.y * 3.14159 / 0.012));   // links about 12 mm long
        col = vec3(0.5) * lit * link * max(u_ambient * 4.0, 0.08) + projectorLight(p) * 0.3;
    }
    fragColor = vec4(vec3(1.0) - exp(-col), 1.0);
}
