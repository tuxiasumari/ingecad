#version 330 core
// World-space 2D vertices with per-vertex color; u_mvp maps world -> clip.
layout(location = 0) in vec2 a_pos;
layout(location = 1) in vec3 a_color;
uniform mat4 u_mvp;
out vec3 v_color;

void main() {
    gl_Position = u_mvp * vec4(a_pos, 0.0, 1.0);
    v_color = a_color;
}
