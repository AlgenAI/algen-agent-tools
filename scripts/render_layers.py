"""Generate the README's layer diagram (light and dark) in the Algen palette.

Run ``python scripts/render_layers.py docs/assets`` after changing the layers or connectors.
"""

import sys
from xml.sax.saxutils import escape

THEMES = {
    "light": dict(
        bg0="#FDFCF9",
        bg1="#F4EFE3",
        grid="#755C0A",
        grid_op=".07",
        ink="#0C0C0B",
        accent="#755C0A",
        muted="#4A4C40",
        faint="#6B6E60",
        card="#FFFFFF",
        card_op=".72",
        card_line="#D9CFB8",
        chip="#FDFCF9",
        chip_ink="#14130F",
        g0="#E9C86D",
        g1="#D4A017",
        g2="#956E18",
        badge_ink="#1A1813",
    ),
    "dark": dict(
        bg0="#0C0C0B",
        bg1="#2A2418",
        grid="#E9C86D",
        grid_op=".06",
        ink="#F5F0E8",
        accent="#E9C86D",
        muted="#C9C2B4",
        faint="#A29C90",
        card="#1A1813",
        card_op=".78",
        card_line="#3A3326",
        chip="#14130F",
        chip_ink="#F5F0E8",
        g0="#F3D98B",
        g1="#D4A017",
        g2="#B8860B",
        badge_ink="#0F0E0B",
    ),
}
SANS = "Inter,ui-sans-serif,system-ui,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,monospace"
W, H = 1440, 830
LAYERS = [
    (
        "DOMAIN PACKS",
        "your-product-pack",
        ["Product-specific tools and decisions.", "What to do with what was read."],
        ["candidate screening", "payroll rules", "text-to-SQL", "HR policies", "+ yours"],
        "domain",
    ),
    (
        "CONNECTORS",
        "algen-agent-tools",
        [
            "Generic, typed toolkits, one optional extra each.",
            "How a system is read, and what it means.",
        ],
        [
            "gmail",
            "outlook",
            "imap",
            "postgres",
            "mysql",
            "documents",
            "github · mcp",
            "telegram",
            "webhook",
        ],
        "tools",
    ),
    (
        "CORE",
        "algen-agent-runtime",
        ["Contracts, governance and execution.", "How every tool call runs, safely."],
        [
            "@tool · presets",
            "Toolkit",
            "plugins",
            "approvals",
            "policies · budgets",
            "audit · OTel",
            "describe",
        ],
        "core",
    ),
]


def svg(theme: str) -> str:
    c = THEMES[theme]
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">',
        '<title id="title">Where code belongs: core, connectors and domain packs</title>',
        '<desc id="desc">Three layers. Domain packs hold product-specific tools and build on '
        "algen-agent-tools, which holds generic connectors such as Gmail, IMAP, Postgres and "
        "documents, and builds on algen-agent-runtime, the core that owns contracts, "
        "approvals, policies and audit.</desc>",
        "<defs>",
        f'<linearGradient id="bg" x1="0" y1="0" x2="{W}" y2="{H}" gradientUnits="userSpaceOnUse">'
        f'<stop stop-color="{c["bg0"]}"/><stop offset="1" stop-color="{c["bg1"]}"/></linearGradient>',
        f'<linearGradient id="g" x1="0" y1="0" x2="{W}" y2="{H}" gradientUnits="userSpaceOnUse">'
        f'<stop stop-color="{c["g0"]}"/><stop offset=".5" stop-color="{c["g1"]}"/>'
        f'<stop offset="1" stop-color="{c["g2"]}"/></linearGradient>',
        f'<pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">'
        f'<path d="M32 0H0v32" fill="none" stroke="{c["grid"]}" stroke-opacity="{c["grid_op"]}"/>'
        "</pattern>",
        '<filter id="lift" x="-5%" y="-20%" width="110%" height="150%">'
        '<feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#2A2418" '
        'flood-opacity=".12"/></filter>',
        "</defs>",
        f'<rect width="{W}" height="{H}" rx="28" fill="url(#bg)"/>',
        f'<rect width="{W}" height="{H}" rx="28" fill="url(#grid)"/>',
        f'<text x="72" y="84" fill="{c["ink"]}" font-family="{SANS}" font-size="36" '
        f'font-weight="780" letter-spacing="-1">Where code belongs</text>',
        f'<text x="72" y="118" fill="{c["muted"]}" font-family="{SANS}" font-size="19">'
        "Each layer builds only on the public API of the layer below it.</text>",
    ]
    top, height, gap = 150, 176, 34
    for index, (kicker, package, lines, chips, kind) in enumerate(LAYERS):
        y = top + index * (height + gap)
        highlight = kind == "tools"
        stroke = "url(#g)" if highlight else c["card_line"]
        width = 4 if highlight else 2
        dash = ' stroke-dasharray="10 9"' if kind == "domain" else ""
        out.append(
            f'<rect x="72" y="{y}" width="{W - 144}" height="{height}" rx="24" '
            f'fill="{c["card"]}" fill-opacity="{c["card_op"]}" stroke="{stroke}" '
            f'stroke-width="{width}"{dash} filter="url(#lift)"/>'
        )
        out.append(
            f'<text x="112" y="{y + 46}" fill="{c["accent"]}" font-family="{SANS}" '
            f'font-size="14" font-weight="700" letter-spacing="2.4">{kicker}</text>'
        )
        out.append(
            f'<text x="112" y="{y + 84}" fill="{c["ink"]}" font-family="{MONO}" '
            f'font-size="25" font-weight="700">{escape(package)}</text>'
        )
        for number, line in enumerate(lines):
            out.append(
                f'<text x="112" y="{y + 118 + number * 26}" fill="{c["muted"]}" '
                f'font-family="{SANS}" font-size="17">{escape(line)}</text>'
            )
        if highlight:
            out.append(
                f'<rect x="{112 + len(package) * 15.2 + 18}" y="{y + 62}" width="96" height="30" '
                f'rx="15" fill="url(#g)"/>'
            )
            out.append(
                f'<text x="{112 + len(package) * 15.2 + 66}" y="{y + 82}" fill="{c["badge_ink"]}" '
                f'font-family="{SANS}" font-size="14" font-weight="750" '
                f'text-anchor="middle">this repo</text>'
            )
        x0 = x = 640
        rows: list[list[tuple[str, float]]] = [[]]
        for chip in chips:
            w = len(chip) * 10.6 + 40
            if x + w > W - 112:
                rows.append([])
                x = x0
            rows[-1].append((chip, w))
            x += w + 14
        block = len(rows) * 46 + (len(rows) - 1) * 14
        cy = y + (height - block) / 2
        for row_chips in rows:
            x = x0
            for chip, w in row_chips:
                yours = chip == "+ yours"
                chip_stroke = c["faint"] if yours else "url(#g)"
                extra = ' stroke-dasharray="7 7"' if yours else ""
                out.append(
                    f'<rect x="{x:.0f}" y="{cy:.0f}" width="{w:.0f}" height="46" rx="15" '
                    f'fill="{c["chip"]}" stroke="{chip_stroke}" stroke-width="2.5"{extra}/>'
                )
                ink = c["faint"] if yours else c["chip_ink"]
                out.append(
                    f'<text x="{x + w / 2:.0f}" y="{cy + 29:.0f}" fill="{ink}" font-family="{MONO}" '
                    f'font-size="18" font-weight="600" text-anchor="middle">{escape(chip)}</text>'
                )
                x += w + 14
            cy += 60
        if index < len(LAYERS) - 1:
            ay = y + height
            out.append(
                f'<path d="M360 {ay + 6}V{ay + gap - 8}" stroke="url(#g)" stroke-width="4" '
                f'stroke-linecap="round"/>'
            )
            out.append(
                f'<path d="M350 {ay + gap - 16}l10 10 10-10" fill="none" stroke="url(#g)" '
                f'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
            )
            out.append(
                f'<text x="384" y="{ay + gap / 2 + 6}" fill="{c["faint"]}" font-family="{SANS}" '
                f'font-size="14" font-weight="600" letter-spacing="1.6">BUILDS ON</text>'
            )
    fy = top + 3 * height + 2 * gap + 52
    parts = [
        ("How a system is read", "Tools"),
        ("What to do with it", "your domain pack"),
        ("How it runs safely", "Runtime"),
    ]
    x = 72
    out.append(f'<text x="{x}" y="{fy}" font-family="{SANS}" font-size="17">')
    for number, (question, owner) in enumerate(parts):
        sep = "   ·   " if number else ""
        out.append(
            f'<tspan fill="{c["faint"]}">{sep}{escape(question)} → </tspan>'
            f'<tspan fill="{c["accent"]}" font-weight="700">{escape(owner)}</tspan>'
        )
    out.append("</text></svg>")
    return "\n".join(out) + "\n"


for theme in ("light", "dark"):
    with open(f"{sys.argv[1]}/layers-{theme}.svg", "w") as handle:
        handle.write(svg(theme))
