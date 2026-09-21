uniform sampler2D u_a;          // outgoing scene
uniform sampler2D u_b;          // incoming (or only) scene
uniform float u_mix;            // transition progress 0..1 (1 = only B)
uniform int u_trans;            // 0 cut, 1 crossfade, 2 wipe_lat, 3 iris, 4 flash
uniform float u_master;         // brightness incl. blackout fade

uniform vec4 u_lines;           // calibration lines L, R, T, B as fractions (top-down); x<0 = off
uniform vec4 u_lineLevels;      // brightness of each line (last-moved one brighter)
uniform int u_grid;             // verification grid on/off
uniform float u_gridSpin;       // degrees the grid is turned about the pole axis (step 4's spinning ball)
uniform vec2 u_cross;           // pole crosshair centre (x, y as fractions, top-down); x < 0 = off
uniform vec4 u_rows;            // tile rows: mode (0 off, 1 zebra, 2 single row), pitch deg, offset deg, zebra limit deg

out vec4 fragColor;

const float PI = 3.14159265358979;

float lineCoverage(float distPx) { return 1.0 - smoothstep(0.5, 1.5, distPx); }

float gridLine(float v) {       // v in cell units; 1 px wide via derivatives
    float w = max(fwidth(v), 1e-4);
    return 1.0 - smoothstep(0.5, 1.5, abs(fract(v + 0.5) - 0.5) / w);
}

void main() {
    vec2 uv = gl_FragCoord.xy / u_resolution;
    vec3 a = texture(u_a, uv).rgb;
    vec3 b = texture(u_b, uv).rgb;
    vec3 col = b;

    if (u_mix < 1.0 && u_trans != 0) {
        float p = u_mix;
        vec3 dImg;
        bool inside = pixelToImageDir(gl_FragCoord.xy, dImg);
        vec3 dBall = u_imgToBall * dImg;
        if (u_trans == 1) {
            col = mix(a, b, p);
        } else if (u_trans == 2) {                       // wipe from north pole to south pole
            float lat = asin(clamp(dBall.y, -1.0, 1.0));
            float edge = mix(PI / 2.0 + 0.05, -PI / 2.0 - 0.05, p);
            col = mix(a, b, smoothstep(edge - 0.05, edge + 0.05, lat));
        } else if (u_trans == 3) {                       // iris opening from the centre of the ball
            float ang = acos(clamp(dImg.z, -1.0, 1.0));
            float edge = p * (PI / 2.0 + 0.1);
            col = mix(a, b, 1.0 - smoothstep(edge - 0.05, edge + 0.05, ang));
        } else {                                          // flash through white
            col = p < 0.5 ? mix(a, vec3(1.0), p * 2.0) : mix(vec3(1.0), b, (p - 0.5) * 2.0);
        }
        if (!inside) col = vec3(0.0);
    }
    col *= u_master;

    // Calibration overlay: lines are drawn straight onto the projector image.
    if (u_lines.x >= 0.0) {
        vec2 px = gl_FragCoord.xy;
        float l = 0.0;
        l = max(l, lineCoverage(abs(px.x - u_lines.x * u_resolution.x)) * u_lineLevels.x);
        l = max(l, lineCoverage(abs(px.x - u_lines.y * u_resolution.x)) * u_lineLevels.y);
        l = max(l, lineCoverage(abs(px.y - (1.0 - u_lines.z) * u_resolution.y)) * u_lineLevels.z);
        l = max(l, lineCoverage(abs(px.y - (1.0 - u_lines.w) * u_resolution.y)) * u_lineLevels.w);
        col = max(col, vec3(l));
    }
    if (u_grid != 0) {
        vec3 dImg;
        if (pixelToImageDir(gl_FragCoord.xy, dImg)) {
            vec3 d = u_imgToBall * dImg;
            float lat = degrees(asin(clamp(d.y, -1.0, 1.0)));
            float lon = degrees(atan(d.x, d.z)) - u_gridSpin;
            float g = max(gridLine(lat / 30.0), gridLine(lon / 30.0)) * 0.55;
            float equator = 1.0 - smoothstep(0.5, 1.5, abs(lat) / max(fwidth(lat), 1e-4));
            vec2 q = vec2((uv.x - u_cal.x) / u_cal.z, (uv.y - u_cal.y) / u_cal.w);
            float rim = lineCoverage((1.0 - length(q)) * min(u_cal.z * u_resolution.x, u_cal.w * u_resolution.y));
            col = max(col, vec3(0.0, g, g));
            col = max(col, vec3(equator, equator, 0.0));
            col = max(col, vec3(rim * 0.9));
        }
    }
    if (u_rows.x > 0.5) {
        vec3 dImg;
        if (pixelToImageDir(gl_FragCoord.xy, dImg)) {
            vec3 d = u_imgToBall * dImg;
            float lat = degrees(asin(clamp(d.y, -1.0, 1.0)));
            float pos = (lat - u_rows.z) / u_rows.y;      // integer = a row boundary
            // Signed distance to the nearest stripe edge (in row units, positive inside a lit stripe), and a
            // ramp centred on the edge and one pixel wide, so edges do not shift where latitude changes fast.
            float lit;
            if (u_rows.x < 1.5) {                          // zebra: every other row, within the limit
                float v = pos * 0.5;                        // one stripe = two rows
                float sd = 0.25 - abs(fract(v - 0.25 + 0.5) - 0.5);
                float hw = 0.5 * max(fwidth(v), 1e-4);
                lit = smoothstep(-hw, hw, sd) * step(abs(lat), u_rows.w);
            } else {                                        // one row: [offset, offset + pitch)
                float sd = 0.5 - abs(pos - 0.5);
                float hw = 0.5 * max(fwidth(pos), 1e-4);
                lit = smoothstep(-hw, hw, sd);
            }
            col = max(col, vec3(lit));
        }
    }
    if (u_cross.x >= 0.0) {
        vec2 c = vec2(u_cross.x * u_resolution.x, (1.0 - u_cross.y) * u_resolution.y);
        vec2 dp = gl_FragCoord.xy - c;
        float arm = 16.0;
        float h = lineCoverage(abs(dp.y)) * step(abs(dp.x), arm);
        float v = lineCoverage(abs(dp.x)) * step(abs(dp.y), arm);
        float ring = lineCoverage(abs(length(dp) - 9.0));
        col = max(col, vec3(max(max(h, v), ring)));
    }
    fragColor = vec4(col, 1.0);
}
