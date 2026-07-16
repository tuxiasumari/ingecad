#version 330 core
// Thick line quads: each vertex carries the segment point and a unit
// perpendicular; u_half_world (half lineweight in world units, recomputed
// per frame from the zoom) expands the quad so thickness is constant in
// screen pixels — AutoCAD LWT display behavior.
layout(location = 0) in vec2 a_pos;
layout(location = 1) in vec2 a_normal;
layout(location = 2) in vec4 a_color;
uniform mat4 u_mvp;
uniform float u_half_world;
out vec4 v_color;

void main() {
    vec2 pos = a_pos + a_normal * u_half_world;
    gl_Position = u_mvp * vec4(pos, 0.0, 1.0);
    v_color = a_color;
}
