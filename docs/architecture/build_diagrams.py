"""Draws the architecture diagrams in docs/architecture as SVG, in the style of cloud architecture
diagrams: product logos on tiles, short labels, labelled group frames, straight orthogonal lines.

    python docs/architecture/build_diagrams.py

Logos: Simple Icons (CC0), in icons/*.path. Open Policy Agent has no Simple Icons logo and is
drawn as a shield; people, warehouse, cog and check are simple line icons drawn here.
The font (Google Sans Flex, OFL) is embedded so the diagrams render the same everywhere.
"""

import base64
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
FONT = (
    HERE.parent.parent
    / "infra/keycloak/themes/palletwise/login/resources/fonts/google-sans-flex-latin-wght-normal.woff2"
)

INK, MUTED, LINE, BG = "#0f1f3d", "#5a6b88", "#7f93b5", "#ffffff"
TILE, TILE_EDGE, ACCENT = "#f4f7fc", "#d5deec", "#0662cc"
BRAND = {
    "react": "#58C4DC", "vite": "#646CFF", "threedotjs": "#000000", "typescript": "#3178C6",
    "fastapi": "#009688", "python": "#3776AB", "postgresql": "#4169E1", "keycloak": "#4D4D4D",
    "traefikproxy": "#24A1C1", "googlegemini": "#8E75B2", "docker": "#2496ED",
    "opentelemetry": "#F5A800",
}  # fmt: skip
FILLED = {"opa": ("M12 1 3 5v6c0 5.5 3.8 10.7 9 12 5.2-1.3 9-6.5 9-12V5z", "#7D9199")}
STROKED = {
    "people": '<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2"/>'
    '<path d="M16 3.1a4 4 0 0 1 0 7.8M22 21v-2a4 4 0 0 0-3-3.9"/>',
    "warehouse": '<path d="M2 21V8l10-5 10 5v13"/><path d="M6 21v-9h12v9M6 15h12M6 18h12"/>',
    "check": '<circle cx="12" cy="12" r="9.5"/><path d="m7.5 12.5 3 3 6-6.5"/>',
    "cog": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1'
    'M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    "cloud": '<path d="M17.5 19H7a5 5 0 1 1 1-9.9A6 6 0 0 1 19.4 11 4 4 0 0 1 17.5 19z"/>',
}  # fmt: skip

T = 104  # tile size
LOGO = 56


class Diagram:
    def __init__(self, width: int, height: int, title: str, subtitle: str) -> None:
        self.w, self.h = width, height
        self.back = [
            f'<rect width="{width}" height="{height}" fill="{BG}"/>',
            f'<text x="56" y="78" font-size="36" font-weight="650" fill="{INK}">{escape(title)}</text>',
            f'<text x="56" y="116" font-size="19" fill="{MUTED}">{escape(subtitle)}</text>',
        ]
        self.lines: list[str] = []  # under the tiles
        self.front: list[str] = []  # tiles and their text
        self.labels: list[str] = []  # line labels, over everything

    def glyph(self, name: str, x: float, y: float, size: float, colour: str | None = None) -> str:
        s = size / 24
        if name in STROKED:
            return (
                f'<g transform="translate({x} {y}) scale({s})" fill="none" stroke="{colour or INK}" '
                f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{STROKED[name]}</g>'
            )
        if name in FILLED:
            path, fill = FILLED[name]
        else:
            path, fill = (ICONS / f"{name}.path").read_text().strip(), BRAND[name]
        return f'<path transform="translate({x} {y}) scale({s})" d="{path}" fill="{colour or fill}"/>'

    def group(self, x: int, y: int, w: int, h: int, label: str, icon: str, dashed: bool = False) -> None:
        dash = ' stroke-dasharray="8 6"' if dashed else ""
        self.back.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="none" stroke="#b9c6dc" '
            f'stroke-width="1.6"{dash}/>'
            f'<path d="M{x} {y + 44}V{y + 16}a16 16 0 0 1 16-16H{x + 44}V{y + 44}z" fill="{INK}"/>'
            + self.glyph(icon, x + 10, y + 10, 24, "#ffffff")
            + f'<text x="{x + 58}" y="{y + 29}" font-size="18" font-weight="600" fill="{INK}">'
            f"{escape(label)}</text>"
        )

    def node(self, cx: float, cy: float, icon: str, label: str, sub: str = "", strong: bool = False,
             step: int | None = None) -> None:  # fmt: skip
        x, y = cx - T / 2, cy - T / 2
        edge, width = (ACCENT, 2.4) if strong else (TILE_EDGE, 1.4)
        self.front.append(
            f'<rect x="{x}" y="{y}" width="{T}" height="{T}" rx="22" fill="{TILE}" stroke="{edge}" '
            f'stroke-width="{width}"/>'
            + self.glyph(icon, cx - LOGO / 2, cy - LOGO / 2, LOGO)
            + f'<text x="{cx}" y="{cy + T / 2 + 32}" font-size="21" font-weight="600" fill="{INK}" '
            f'text-anchor="middle">{escape(label)}</text>'
        )
        for i, line in enumerate(sub.split("\n") if sub else []):
            self.front.append(
                f'<text x="{cx}" y="{cy + T / 2 + 58 + i * 22}" font-size="16.5" fill="{MUTED}" '
                f'text-anchor="middle">{escape(line)}</text>'
            )
        if step is not None:
            self.front.append(
                f'<circle cx="{x + 4}" cy="{y + 4}" r="17" fill="{ACCENT}" stroke="#ffffff" '
                f'stroke-width="3"/>'
                f'<text x="{x + 4}" y="{y + 10}" font-size="17" font-weight="700" fill="#ffffff" '
                f'text-anchor="middle">{step}</text>'
            )

    def pill(self, cx: float, y: float, text: str) -> None:
        w = 16 + len(text) * 9.4
        self.front.append(
            f'<rect x="{cx - w / 2}" y="{y}" width="{w}" height="32" rx="16" fill="#e8f0fc"/>'
            f'<text x="{cx}" y="{y + 22}" font-size="15.5" '
            'font-family="ui-monospace,Menlo,Consolas,monospace" '
            f'fill="{ACCENT}" text-anchor="middle">{escape(text)}</text>'
        )

    def arrow(self, points: list[tuple[float, float]], label: str = "", at: int = 0, both: bool = False,
              dashed: bool = False, side: str = "above") -> None:  # fmt: skip
        """Straight segments through the waypoints; the label sits on segment `at`."""
        d = "M" + " L".join(f"{x} {y}" for x, y in points)
        extra = (' stroke-dasharray="7 6"' if dashed else "") + (' marker-start="url(#tail)"' if both else "")
        self.lines.append(
            f'<path d="{d}" fill="none" stroke="{LINE}" stroke-width="2" stroke-linejoin="round" '
            f'marker-end="url(#head)"{extra}/>'
        )
        if label:
            (x1, y1), (x2, y2) = points[at], points[at + 1]
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            if y1 == y2:
                lx, ly, anchor = mx, (my - 12 if side == "above" else my + 26), "middle"
            else:
                lx, ly, anchor = mx + 12, my + 6, "start"
            self.labels.append(
                f'<text x="{lx}" y="{ly}" font-size="16" font-weight="500" fill="{INK}" '
                f'text-anchor="{anchor}" '
                f'paint-order="stroke" stroke="{BG}" stroke-width="7" stroke-linejoin="round">'
                f"{escape(label)}</text>"
            )

    def save(self, name: str) -> None:
        font = base64.b64encode(FONT.read_bytes()).decode()
        defs = (
            "<defs><style>"
            f'@font-face{{font-family:GSF;src:url(data:font/woff2;base64,{font}) format("woff2");'
            "font-weight:100 1000}"
            'text{font-family:GSF,"Google Sans Flex",Helvetica,Arial,sans-serif}'
            "</style>"
            f'<marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="11" markerHeight="11" '
            f'markerUnits="userSpaceOnUse" orient="auto"><path d="M0 1 L10 5 L0 9z" fill="{LINE}"/></marker>'
            f'<marker id="tail" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="11" markerHeight="11" '
            f'markerUnits="userSpaceOnUse" orient="auto"><path d="M10 1 L0 5 L10 9z" fill="{LINE}"/></marker>'
            "</defs>"
        )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" '
            f'height="{self.h}">'
            + defs
            + "".join(self.back + self.lines + self.front + self.labels)
            + "</svg>\n"
        )
        (HERE / name).write_text(svg, encoding="utf-8")


def system() -> None:
    d = Diagram(1600, 1010, "PalletWise system architecture",
                "One Docker stack. The browser holds no tokens; "
                "the model reaches data only through tools.")  # fmt: skip
    r1, r2, r3 = 340, 580, 840
    h = T / 2
    d.group(40, 160, 250, 810, "Browser", "people")
    d.group(330, 160, 990, 810, "Docker network", "cog")
    d.group(1360, 160, 200, 810, "Google Cloud", "cloud", dashed=True)

    d.node(165, r1, "people", "Manager, Admin", "sign in, ask, act")
    d.node(165, r2, "react", "Web app", "3D map, copilot")
    d.node(460, r2, "traefikproxy", "Traefik", "edge, port 8080")
    d.node(720, r2, "fastapi", "API", "BFF, tools.executor", strong=True)
    d.node(460, r1, "opentelemetry", "Telemetry", "one trace a question")
    d.node(980, r1, "keycloak", "Keycloak", "sign-in, token exchange")
    d.node(1200, r1, "opa", "OPA", "every call decided")
    d.node(720, r3, "postgresql", "PostgreSQL 17", "RLS, semantic layer", strong=True)
    d.node(980, r3, "cog", "Workers", "connector, evaluator")
    d.node(1200, r3, "warehouse", "Simulated WMS", "outbox of records")
    d.node(1460, r2, "googlegemini", "Gemini", "picks tools, words")

    d.arrow([(165, r1 + h + 74), (165, r2 - h)])
    d.arrow([(165 + h, r2), (460 - h, r2)], "HTTPS, SSE", both=True)
    d.arrow([(460 + h, r2), (720 - h, r2)], "/api", both=True)
    d.arrow([(720 + h, r2), (1460 - h, r2)], "tool choice, wording")
    d.arrow([(720, r2 + h + 74), (720, r3 - h)], "semantic functions only")
    d.arrow([(980 - h, r3), (720 + h, r3)], "own roles")
    d.arrow([(1200 - h, r3), (980 + h, r3)], "records")
    d.arrow([(700, r2 - h), (700, 232), (1200, 232), (1200, r1 - h)], "policy", at=1)
    d.arrow([(740, r2 - h), (740, r1), (980 - h, r1)], "OIDC", at=1)
    d.arrow([(680, r2 - h), (680, r1), (460 + h, r1)], "spans", at=1, dashed=True)
    d.save("containers.svg")


def question_flow() -> None:
    d = Diagram(1600, 470, "From a question to highlighted locations",
                "The answer, the table and the highlights come from one result, "
                "so they cannot disagree.")  # fmt: skip
    steps = [
        ("react", "Ask", "plain words\nor a chip"),
        ("keycloak", "Check", "session, budget,\nagent active"),
        ("googlegemini", "Route", "fast path, or the\nmodel picks a tool"),
        ("opa", "Execute", "policy, kill switch,\naudit with agent id"),
        ("postgresql", "Compute", "semantic layer:\nrows, highlights"),
        ("check", "Verify", "every number\nchecked vs rows"),
        ("threedotjs", "Highlight", "map lights the\nresult's bins"),
    ]
    x0, gap, cy = 130, 223, 262
    for i, (icon, label, sub) in enumerate(steps):
        d.node(x0 + i * gap, cy, icon, label, sub, strong=i in (4, 6), step=i + 1)
        if i < len(steps) - 1:
            d.arrow([(x0 + i * gap + T / 2, cy), (x0 + (i + 1) * gap - T / 2, cy)])
    d.save("question-flow.svg")


def data_flow() -> None:
    d = Diagram(1600, 520, "Data path, one database role per step",
                "Swap the simulator for a real WMS adapter; "
                "nothing after the connector changes.")  # fmt: skip
    steps = [
        ("warehouse", "WMS or simulator", "source of truth", "sim_owner"),
        ("postgresql", "Outbox", "ordered records", "sim_outbox_reader"),
        ("python", "Connector", "keeps invariants", "pw_ingest"),
        ("postgresql", "Canonical model", "forced RLS", "owner only"),
        ("postgresql", "Semantic layer", "every calculation", "pw_agent_read"),
        ("react", "Map, copilot, inbox", "one source", "pw_app"),
    ]
    x0, gap, cy = 160, 256, 262
    d.group(40, 160, 240, 320, "Replaceable", "warehouse", dashed=True)
    for i, (icon, label, sub, role) in enumerate(steps):
        cx = x0 + i * gap
        d.node(cx, cy, icon, label, sub, strong=i == 4)
        d.pill(cx, cy + 126, role)
        if i < len(steps) - 1:
            d.arrow([(cx + T / 2, cy), (cx + gap - T / 2, cy)])
    d.save("data-flow.svg")


if __name__ == "__main__":
    system()
    question_flow()
    data_flow()
