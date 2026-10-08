"""Deterministic checks for the local UCSS branding and motion enhancement."""

import re
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_ucss_logo_is_a_valid_png_with_expected_dimensions():
    logo = ROOT / "static" / "img" / "ucss-logo.png"
    data = logo.read_bytes()

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[12:16] == b"IHDR"
    assert struct.unpack(">II", data[16:24]) == (277, 88)


def test_anonymous_layout_uses_local_logo_with_accessible_alt(client):
    html = client.get("/auth/login").get_data(as_text=True)

    assert re.search(r'src="/static/img/ucss-logo\.png\?v=2"', html)
    assert 'alt="Universidad Católica Sedes Sapientiae (UCSS)"' in html
    assert "https://www.ucss.edu.pe/templates/gk_gamenews/images/logo.png" not in html


def test_observed_palette_is_shared_by_tailwind_css_and_dashboard_chart():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    dashboard = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")

    for color in ("#00a2c5", "#0d97a4", "#154462"):
        assert color in base
        assert color in css
    assert '"#00a2c5"' in dashboard and '"#0d97a4"' in dashboard


def test_motion_has_reduced_motion_override_and_bounded_stagger():
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "@keyframes page-enter" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transform: none !important" in css
    assert re.search(r"nth-child\(n \+ 6\).*175ms", css)


def test_branding_has_visible_logo_lockup_and_non_white_shell():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "brand-logo-panel" in base
    assert "brand-product" in base
    assert ".brand-logo {" in css and "width: 11rem" in css
    assert "width: 8.25rem" in css
    assert ".app-shell" in css and "radial-gradient" in css
    assert "--color-ucss-navy" in css and "--color-ucss-teal" in css
    assert ".kpi-card--lost" in css and ".kpi-card--returned" in css


def test_institutional_header_and_mobile_menu_keep_accessible_contrast(client):
    # The login screen hides the global header; the 404 page still renders the full base layout.
    html = client.get("/no-such-page").get_data(as_text=True)
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert re.search(r'<header class="app-header\b', html)
    assert re.search(r'<body class="app-shell\b', html)
    assert re.search(r'<img class="brand-logo"[^>]*ucss-logo\.png\?v=2', html)
    assert 'aria-controls="nav-menu" aria-expanded="false"' in html
    assert ".app-header :focus-visible" in css
    assert ".app-nav-menu" in css and "top: 100%" in css
    assert "--color-ucss-cyan: #00a2c5" in css
    assert "--color-ucss-teal: #0d97a4" in css
    reduced_motion = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".card:hover," in reduced_motion
    assert ".app-nav-link:hover," in reduced_motion


# --- U2: split login/register screens ----------------------------------------


def _css():
    return (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_split_auth_layout_is_two_columns_from_900px_and_stacked_below():
    css = _css()

    assert re.search(r"\.auth-split\s*\{[^}]*display:\s*grid", css)
    assert re.search(
        r"@media \(min-width: 900px\)\s*\{[^@]*\.auth-split\s*\{[^}]*grid-template-columns:\s*repeat\(2", css
    )
    assert re.search(r"\.auth-split\s*\{[^}]*overflow:\s*hidden", css)


def test_ucss_logo_is_large_on_desktop_and_smaller_on_mobile():
    css = _css()

    assert re.search(r"\.auth-brand__logo\s*\{[^}]*width:\s*20rem", css)
    assert re.search(r"\.auth-brand__logo\s*\{[^}]*max-width:\s*100%", css)
    mobile = css.split("@media (max-width: 899px)", 1)[1]
    assert re.search(r"\.auth-brand__logo\s*\{[^}]*width:\s*11rem", mobile)


def test_auth_card_uses_turquoise_palette_with_rounded_ring_and_light_button():
    css = _css()
    card = re.search(r"\.auth-card\s*\{([^}]*)\}", css).group(1)

    assert "linear-gradient(165deg, #0a707b 0%, #08515c 100%)" in card
    assert "#0b2536" not in card
    assert "border-radius: 1.5rem" in card
    assert "box-shadow" in card
    submit = re.search(r"\.auth-card \.auth-submit\s*\{([^}]*)\}", css).group(1)
    assert re.search(r"background:\s*#fff;", submit)
    assert re.search(r"color:\s*#08515c;", submit)
    assert ".auth-card .form-input" in css
    assert "border-bottom" in re.search(r"\.auth-card \.form-input\s*\{([^}]*)\}", css).group(1)


def test_auth_spheres_are_pure_css_and_do_not_capture_pointer_events():
    css = _css()
    spheres = re.search(r"\.auth-spheres\s*\{([^}]*)\}", css).group(1)
    sphere = re.search(r"\.auth-sphere\s*\{([^}]*)\}", css).group(1)

    assert "pointer-events: none" in spheres
    assert "radial-gradient" in sphere
    assert "border-radius: 50%" in sphere


def test_auth_motion_is_subtle_and_disabled_under_reduced_motion():
    css = _css()

    assert "@keyframes auth-card-enter" in css
    assert re.search(r"\.auth-card\s*\{[^}]*animation:\s*auth-card-enter\s+(2[4-9]\d|3[0-5]\d)ms", css)
    assert re.search(r"\.auth-card \.auth-submit:hover\s*\{[^}]*translateY", css)
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".auth-card," in reduced
    assert ".auth-card .auth-submit:hover" in reduced
    assert "animation: none !important" in reduced and "transform: none !important" in reduced


def test_auth_card_focus_ring_and_error_color_are_visible_on_dark():
    css = _css()

    assert re.search(r"\.auth-card :focus-visible\s*\{[^}]*outline:\s*2px solid #bff6fb", css)
    assert re.search(r"\.auth-card \.form-error\s*\{[^}]*color:\s*#ffe0e0", css)
    assert ":-webkit-autofill" in css


def test_base_layout_exposes_overridable_blocks_with_previous_defaults():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    for block in ("body_class", "header", "flash", "main_class", "footer"):
        assert "{% block " + block + " %}" in base, block
    for template in ("login.html", "register.html"):
        source = (ROOT / "templates" / template).read_text(encoding="utf-8")
        assert "{% block header %}{% endblock %}" in source
        assert "{% block footer %}{% endblock %}" in source


# --- Turquoise auth card: WCAG contrast read from app.css -----------------------


def _linear(channel):
    channel /= 255
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(color):
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def _contrast(foreground, background):
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _blend(foreground, background, alpha):
    mixed = (
        round(alpha * int(foreground[i : i + 2], 16) + (1 - alpha) * int(background[i : i + 2], 16))
        for i in (1, 3, 5)
    )
    return "#%02x%02x%02x" % tuple(mixed)


def _declared(css, selector, prop):
    block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css).group(1)
    value = re.search(r"(?<![-\w])" + prop + r":\s*#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", block).group(1).lower()
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    return "#" + value


def test_auth_card_text_and_ui_colors_meet_wcag_on_the_turquoise_gradient():
    css = _css()
    card = re.search(r"\.auth-card\s*\{([^}]*)\}", css).group(1)
    light, dark = re.search(r"linear-gradient\(165deg,\s*(#[0-9a-f]{6}) 0%,\s*(#[0-9a-f]{6}) 100%\)", card).groups()
    assert _luminance(light) > _luminance(dark)  # the lighter end is the worst case

    muted = _declared(css, ".auth-card__subtitle", "color")
    link = _declared(css, ".auth-card__switch a", "color")
    error = _declared(css, ".auth-card .form-error", "color")
    help_text = _declared(css, ".auth-card .form-help", "color")
    placeholder = _declared(css, ".auth-card .form-input::placeholder", "color")
    toggle = _declared(css, ".auth-card .password-toggle", "color")
    focus = re.search(r"\.auth-card :focus-visible\s*\{[^}]*outline:\s*2px solid (#[0-9a-f]{6})", css).group(1)
    error_border = _declared(css, ".auth-card .form-input-error", "border-bottom-color")
    button_bg = _declared(css, ".auth-card .auth-submit", "background")
    button_text = _declared(css, ".auth-card .auth-submit", "color")
    hover_bg = _declared(css, ".auth-card .auth-submit:hover", "background")

    underline_alpha = float(
        re.search(
            r"\.auth-card \.form-input\s*\{[^}]*border-bottom:\s*1px solid rgba\(255, 255, 255, ([\d.]+)\)", css
        ).group(1)
    )

    for background in (light, dark):
        for name, color in (
            ("white", "#ffffff"),
            ("muted", muted),
            ("link", link),
            ("error", error),
            ("help", help_text),
            ("placeholder", placeholder),
            ("toggle", toggle),
        ):
            assert _contrast(color, background) >= 4.5, (name, color, background)
        for name, color in (("focus", focus), ("error border", error_border)):
            assert _contrast(color, background) >= 3, (name, color, background)
        # Input underline is translucent white over the card, drawn as a UI boundary.
        assert _contrast(_blend("#ffffff", background, underline_alpha), background) >= 3

    # Date badge: white 10% overlay lightens the card slightly; text must still pass.
    badge = _blend("#ffffff", light, 0.1)
    weekday = _declared(css, ".auth-date__weekday", "color")
    assert _contrast("#ffffff", badge) >= 4.5 and _contrast(weekday, badge) >= 4.5

    # Button: text on its own background, and button shape against the card.
    for background in (button_bg, hover_bg):
        assert _contrast(button_text, background) >= 4.5, background
        assert _contrast(background, light) >= 3 and _contrast(background, dark) >= 3

    autofill = re.search(r"box-shadow:\s*0 0 0 1000px (#[0-9a-f]{6}) inset", css).group(1)
    assert _luminance(dark) <= _luminance(autofill) <= _luminance(light)
    assert _contrast("#ffffff", autofill) >= 4.5
