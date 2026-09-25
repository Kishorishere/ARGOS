# Generates Argos_Proposal_Deck.pptx - 10 slides, 16:9, no external assets.
# Run: python3 argos_deck_gen.py
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.oxml.ns import qn

# ---------- palette ----------
NAVY   = RGBColor(0x10, 0x2A, 0x43)   # dark background / titles
INK    = RGBColor(0x21, 0x2B, 0x36)   # body text
GRAY   = RGBColor(0x6B, 0x77, 0x84)   # secondary text
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)
ACCENT = RGBColor(0x2E, 0x86, 0xC1)   # title accent bar

BLUE_F, BLUE_L   = RGBColor(0xDD, 0xE9, 0xF7), RGBColor(0x2E, 0x5E, 0x8C)  # analysts
RED_F, RED_L     = RGBColor(0xFB, 0xE3, 0xE3), RGBColor(0xB0, 0x3A, 0x2E)  # debate
ORNG_F, ORNG_L   = RGBColor(0xFC, 0xEA, 0xD2), RGBColor(0xC0, 0x74, 0x22)  # risk
GRN_F, GRN_L     = RGBColor(0xE1, 0xF0, 0xE3), RGBColor(0x3F, 0x7D, 0x44)  # decision
GRY_F, GRY_L     = RGBColor(0xEF, 0xF1, 0xF3), RGBColor(0x8A, 0x93, 0x9B)  # io
CARD_L           = RGBColor(0x2E, 0x5E, 0x8C)

FONT = "Calibri"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]

# ---------- helpers ----------
def slide():
    return prs.slides.add_slide(BLANK)

def box(s, x, y, w, h, fill, line, text=None, size=14, bold=False,
        color=INK, align=PP_ALIGN.CENTER, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
        line_w=1.0, anchor=MSO_ANCHOR.MIDDLE):
    sp = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.fill.solid(); sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line; sp.line.width = Pt(line_w)
    sp.shadow.inherit = False
    try: sp.adjustments[0] = 0.12
    except Exception: pass
    tf = sp.text_frame; tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.06)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    tf.vertical_anchor = anchor
    if text is not None:
        p = tf.paragraphs[0]; p.alignment = align
        r = p.add_run(); r.text = text
        r.font.size = Pt(size); r.font.bold = bold
        r.font.color.rgb = color; r.font.name = FONT
    return sp

def txt(s, x, y, w, h, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame; tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.paragraphs[0].alignment = align
    return tf

def run(p, text, size=16, bold=False, color=INK, italic=False):
    r = p.add_run(); r.text = text
    r.font.size = Pt(size); r.font.bold = bold; r.font.italic = italic
    r.font.color.rgb = color; r.font.name = FONT
    return r

def para(tf, first=False):
    return tf.paragraphs[0] if first else tf.add_paragraph()

def bullets(tf, items, size=16, gap=6):
    """items: list of (level, head, rest). head is bolded when not empty."""
    first = True
    for lvl, head, rest in items:
        p = para(tf, first); first = False
        p.space_after = Pt(gap if lvl == 0 else 3)
        if lvl == 0:
            run(p, "\u2022  ", size, True, ACCENT)
        else:
            p.level = 1
            run(p, "      -  ", size - 2, False, GRAY)
        if head:
            run(p, head + (" " if rest else ""), size - (0 if lvl == 0 else 2), True,
                INK if lvl == 0 else GRAY)
        if rest:
            run(p, rest, size - (0 if lvl == 0 else 2), False,
                INK if lvl == 0 else GRAY)

def header(s, title, kicker=None):
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(0.42),
                             Inches(0.14), Inches(0.62))
    bar.fill.solid(); bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background(); bar.shadow.inherit = False
    tf = txt(s, 0.85, 0.30, 11.9, 1.0)
    if kicker:
        p = para(tf, True); run(p, kicker.upper(), 11, True, GRAY)
        p2 = para(tf);    run(p2, title, 27, True, NAVY)
        p2.space_before = Pt(2)
    else:
        p = para(tf, True); run(p, title, 28, True, NAVY)

def arrow(s, x1, y1, x2, y2, color=GRAY, w=1.5):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1),
                               Inches(x2), Inches(y2))
    c.line.color.rgb = color; c.line.width = Pt(w)
    c.shadow.inherit = False
    ln = c.line._get_or_add_ln()
    ln.append(ln.makeelement(qn('a:tailEnd'),
              {'type': 'triangle', 'w': 'med', 'len': 'med'}))
    return c

def label(s, x, y, w, text, color=GRAY, size=10, align=PP_ALIGN.CENTER, bold=False):
    tf = txt(s, x, y, w, 0.3, align=align)
    p = tf.paragraphs[0]; p.alignment = align
    run(p, text, size, bold, color)

# =====================================================================
# Slide 1 - Title
# =====================================================================
s = slide()
bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
bg.fill.solid(); bg.fill.fore_color.rgb = NAVY; bg.line.fill.background()
bg.shadow.inherit = False

tf = txt(s, 0.9, 1.55, 11.5, 1.6)
p = para(tf, True); run(p, "ARGOS", 60, True, WHITE)
p = para(tf); p.space_before = Pt(6)
run(p, "A Multi-Agent LLM Trading Research Desk", 26, False, RGBColor(0xBF, 0xD4, 0xE6))

ln = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.95), Inches(3.55),
                        Inches(2.2), Inches(0.045))
ln.fill.solid(); ln.fill.fore_color.rgb = ACCENT; ln.line.fill.background()
ln.shadow.inherit = False

tf = txt(s, 0.9, 3.85, 11.5, 1.1)
p = para(tf, True); run(p, "Proposal Defense: Product Requirements and Architecture", 17, False, WHITE)
p = para(tf); p.space_before = Pt(4)
run(p, "Kishor Timilsena   |   Fusemachines AI Fellowship 2026   |   September 2026", 14, False, RGBColor(0x9F, 0xB3, 0xC8))

# mini pipeline chips
chips = [("3 Analysts", BLUE_L), ("Bull / Bear", RED_L), ("Risk Veto", ORNG_L),
         ("Decision", GRN_L)]
cx = 0.95
for name, col in chips:
    wdt = 1.55 if name != "Bull / Bear" else 1.55
    box(s, cx, 5.35, wdt, 0.5, NAVY, col, name, 12, True, WHITE, line_w=1.25)
    cx += wdt + 0.28
    if name != "Decision":
        label(s, cx - 0.28, 5.40, 0.3, "\u203A", RGBColor(0x9F, 0xB3, 0xC8), 16, PP_ALIGN.CENTER, True)

tf = txt(s, 0.9, 6.55, 11.5, 0.5)
p = para(tf, True); run(p, "Decision support, not execution. The system advises; a human decides.",
                        13, True, RGBColor(0xBF, 0xD4, 0xE6))

# =====================================================================
# Slide 2 - Problem
# =====================================================================
s = slide(); header(s, "Why Build a Research Desk?", "The problem")

tf = txt(s, 0.6, 1.35, 12.1, 2.5)
bullets(tf, [
    (0, "", "Before a trade, a good trader checks the company's numbers, the news, the chart, the bull vs bear case, and a risk check that can say no."),
    (0, "", "What makes a desk good is not one brilliant analyst. It is how the roles interact: who sees what, who can overrule whom, what survives the argument."),
    (0, "", "LLMs can now play these roles. But published trading agents share two weaknesses."),
], size=17, gap=10)

box(s, 0.6, 4.05, 5.95, 2.95, RGBColor(0xF7, 0xFA, 0xFD), BLUE_L, None, line_w=1.5)
tf = s.shapes[-1].text_frame
tf.vertical_anchor = MSO_ANCHOR.TOP
tf.margin_left = tf.margin_right = Inches(0.18)
p = para(tf, True); p.space_before = Pt(6); p.space_after = Pt(6)
run(p, "Gap 1: One model plays every role", 17, True, NAVY)
for line in ["The same LLM reads the news, explains the chart, argues, and sizes the trade.",
             "The real price forecast is missing, or the LLM just re-explains indicators in words.",
             "Pattern-finding in numbers and judgment over messy evidence are different skills."]:
    p = para(tf); p.space_after = Pt(5)
    run(p, "\u2022  ", 14, True, ACCENT); run(p, line, 14, False, INK)

box(s, 6.8, 4.05, 5.95, 2.95, RGBColor(0xFD, 0xF6, 0xF4), RED_L, None, line_w=1.5)
tf = s.shapes[-1].text_frame
tf.vertical_anchor = MSO_ANCHOR.TOP
tf.margin_left = tf.margin_right = Inches(0.18)
p = para(tf, True); p.space_before = Pt(6); p.space_after = Pt(6)
run(p, "Gap 2: Nobody can say no", 17, True, NAVY)
for line in ["The step that picks the direction also picks the position size.",
             "No independent agent can reject the trade. Confidence is self-reported.",
             "A confident-sounding model is exactly when you need an outside check."]:
    p = para(tf); p.space_after = Pt(5)
    run(p, "\u2022  ", 14, True, RED_L); run(p, line, 14, False, INK)

# =====================================================================
# Slide 3 - What Argos Is
# =====================================================================
s = slide(); header(s, "What Argos Is", "The product")

tf = txt(s, 0.6, 1.35, 12.1, 4.3)
bullets(tf, [
    (0, "One recommendation per ticker per day:", "BUY / SELL / HOLD, a confidence score, a position cap, and the full written trail behind the call."),
    (0, "Decision support only.", "A human reads the brief and decides. The system never places orders."),
    (0, "Free tiers end to end.", "Two Gemini quota pools for the LLM calls, Kronos on free GPU sessions, yfinance for prices."),
    (0, "Built for one person.", "About 3 hours a day. Simple, debuggable code over clever architecture."),
    (0, "Small scope on purpose.", "1 to 3 tickers, one walk-forward window, one stress-test window."),
    (0, "Unit of value:", "a defensible decision trail, logged as structured JSON for every run."),
], size=17, gap=11)

bar = box(s, 0.6, 6.15, 12.15, 0.75, RGBColor(0xEF, 0xF1, 0xF3), None,
          "Non-goals: no broker connection, no portfolio building across tickers, no live infrastructure, no profit claims.",
          14, False, GRAY)

# =====================================================================
# Slide 4 - Three Design Commitments
# =====================================================================
s = slide(); header(s, "Three Design Commitments", "The idea")

cards = [
    ("1", "Two kinds of intelligence", BLUE_L, [
        "Five LLM roles handle language: filings, news, narrative.",
        "Kronos, a candlestick foundation model, handles the numbers.",
        "The desk reads its full forecast spread, not just the average."]),
    ("2", "A forced argument", RED_L, [
        "A Bull and a Bear argue in sequence.",
        "The Bear must quote and rebut the Bull's actual claims. No generic bear case.",
        "Disagreement is measured, never averaged away."]),
    ("3", "Belief is not permission", ORNG_L, [
        "The debate says what we believe. The Risk Manager says what we may do.",
        "Wide forecast bands tighten the cap, however confident the debate sounds.",
        "A veto short-circuits the graph straight to HOLD."]),
]
x = 0.6
for num, title, col, lines in cards:
    box(s, x, 1.5, 3.95, 5.3, RGBColor(0xFB, 0xFC, 0xFD), col, None, line_w=1.75)
    tf = s.shapes[-1].text_frame
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = tf.margin_right = Inches(0.2)
    p = para(tf, True); p.space_before = Pt(10); p.space_after = Pt(2)
    run(p, num, 26, True, col)
    p = para(tf); p.space_after = Pt(10)
    run(p, title, 18, True, NAVY)
    for line in lines:
        p = para(tf); p.space_after = Pt(7)
        run(p, "\u2022  ", 13.5, True, col); run(p, line, 13.5, False, INK)
    x += 4.11

# =====================================================================
# Slide 5 - Architecture diagram
# =====================================================================
s = slide(); header(s, "System Architecture", "How it works")

def node(x, y, w, h, text, fill, line, size=12):
    return box(s, x, y, w, h, fill, line, text, size, True, INK, line_w=1.4)

W = 2.55
start = node(5.39, 1.28, W, 0.44, "START", GRY_F, GRY_L, 11)
fund  = node(0.95, 2.10, W, 0.60, "Fundamentals\nAnalyst", BLUE_F, BLUE_L)
sent  = node(5.39, 2.10, W, 0.60, "Sentiment\nAnalyst", BLUE_F, BLUE_L)
tech  = node(9.83, 2.10, W, 0.60, "Technicals\n(Kronos narration)", BLUE_F, BLUE_L)
bull  = node(5.39, 3.12, W, 0.55, "Bull Advocate", RED_F, RED_L)
bear  = node(5.39, 3.95, W, 0.55, "Bear Advocate", RED_F, RED_L)
risk  = node(5.39, 4.78, W, 0.60, "Risk Manager\nveto / size cap", ORNG_F, ORNG_L)
hold  = node(1.30, 5.75, 3.10, 0.60, "VETO \u2192 HOLD\n(short-circuit)", GRN_F, GRN_L)
pm    = node(8.93, 5.75, 3.10, 0.60, "Portfolio Manager\nfinal decision", GRN_F, GRN_L)
endn  = node(4.02, 6.80, 5.30, 0.50, "END \u2192 one JSONL decision trail", GRY_F, GRY_L, 12)

def cxy(sp):  # center x, bottom y / top y
    return sp.left / 914400 + sp.width / 914400 * 0 + (sp.left + sp.width / 2) / 914400

def cx_(sp): return (sp.left + sp.width / 2) / 914400
def cy_(sp, edge):  # edge: 'top' or 'bottom'
    return sp.top / 914400 if edge == 'top' else (sp.top + sp.height) / 914400

arrow(s, cx_(start), cy_(start, 'bottom'), cx_(fund), cy_(fund, 'top'))
arrow(s, cx_(start), cy_(start, 'bottom'), cx_(sent), cy_(sent, 'top'))
arrow(s, cx_(start), cy_(start, 'bottom'), cx_(tech), cy_(tech, 'top'))
arrow(s, cx_(fund), cy_(fund, 'bottom'), cx_(bull) - 0.55, cy_(bull, 'top'))
arrow(s, cx_(sent), cy_(sent, 'bottom'), cx_(bull), cy_(bull, 'top'))
arrow(s, cx_(tech), cy_(tech, 'bottom'), cx_(bull) + 0.55, cy_(bull, 'top'))
arrow(s, cx_(bull), cy_(bull, 'bottom'), cx_(bear), cy_(bear, 'top'), color=RED_L, w=2.0)
label(s, cx_(bull) + 0.15, 3.70, 2.6, "must rebut his claims", RED_L, 10, PP_ALIGN.LEFT, True)
arrow(s, cx_(bear), cy_(bear, 'bottom'), cx_(risk), cy_(risk, 'top'))
arrow(s, cx_(risk), cy_(risk, 'bottom'), cx_(hold) + 1.0, cy_(hold, 'top'), color=ORNG_L, w=2.0)
label(s, 4.05, 5.42, 1.2, "VETO", ORNG_L, 11, PP_ALIGN.CENTER, True)
arrow(s, cx_(risk), cy_(risk, 'bottom'), cx_(pm) - 1.0, cy_(pm, 'top'), color=GRN_L, w=2.0)
label(s, 8.10, 5.42, 1.4, "approved", GRN_L, 11, PP_ALIGN.CENTER, True)
arrow(s, cx_(hold), cy_(hold, 'bottom'), cx_(hold), 6.80)
arrow(s, cx_(pm), cy_(pm, 'bottom'), cx_(pm), 6.80)

tf = txt(s, 0.6, 7.08, 12.1, 0.4)
p = para(tf, True); p.alignment = PP_ALIGN.CENTER
run(p, "Three analysts run in parallel  \u2022  the debate runs in order  \u2022  the Risk Manager can veto  \u2022  every output lands in one trail",
    12.5, False, GRAY)

# =====================================================================
# Slide 6 - Agent roster table
# =====================================================================
s = slide(); header(s, "Seven Agents, Typed Contracts", "The roster")

rows = [
    ("Agent", "Sees", "Contract"),
    ("Fundamentals", "Filings, earnings call, ratios", "Every number traceable to inputs. 150-word cap."),
    ("Sentiment", "Headlines, ratings, social volume", "Weighs recency and source quality. Neutral if data is thin."),
    ("Technicals", "Kronos forecast distribution", "Narration only. Describes the numbers, never invents causes."),
    ("Bull Advocate", "All three analyst signals", "Strongest buy case. Names its single best piece of evidence."),
    ("Bear Advocate", "Analyst signals + Bull's argument", "Must rebut the Bull's quoted claims, not give a generic case."),
    ("Risk Manager", "Debate + Kronos uncertainty width", "Veto flag + position cap. Wide bands mean a tighter cap."),
    ("Portfolio Manager", "Full trail + risk verdict", "Final call. Size clamped in code. Dissent kept on record."),
]
tbl = s.shapes.add_table(8, 3, Inches(0.6), Inches(1.45), Inches(12.15), Inches(5.5)).table
tbl.columns[0].width = Inches(2.5)
tbl.columns[1].width = Inches(4.05)
tbl.columns[2].width = Inches(5.6)
for r, (a, b, c) in enumerate(rows):
    for ci, val in enumerate((a, b, c)):
        cell = tbl.cell(r, ci)
        cell.margin_left, cell.margin_right = Inches(0.1), Inches(0.08)
        cell.margin_top = cell.margin_bottom = Inches(0.03)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = NAVY if r == 0 else (
            RGBColor(0xF7, 0xFA, 0xFD) if r % 2 else WHITE)
        tf = cell.text_frame; tf.word_wrap = True
        p = tf.paragraphs[0]
        run(p, val, 13 if r else 13.5, r == 0 or ci == 0,
            WHITE if r == 0 else (NAVY if ci == 0 else INK))

# =====================================================================
# Slide 7 - Kronos + Risk layer
# =====================================================================
s = slide(); header(s, "The Numbers Engine and the Veto", "Two core modules")

box(s, 0.6, 1.45, 5.95, 5.45, RGBColor(0xF7, 0xFA, 0xFD), BLUE_L, None, line_w=1.5)
tf = s.shapes[-1].text_frame; tf.vertical_anchor = MSO_ANCHOR.TOP
tf.margin_left = tf.margin_right = Inches(0.2)
p = para(tf, True); p.space_before = Pt(8); p.space_after = Pt(8)
run(p, "Kronos: the forecast channel", 17, True, NAVY)
for line in ["Pretrained on candlesticks from 45+ exchanges. OHLCV bars become tokens.",
             "Runs in batch: forecasts for the whole backtest window are precomputed and cached.",
             "Many sampled paths give a mean, a spread, and a p90-to-p10 band.",
             "The band width is the risk signal. The point forecast alone is not enough.",
             "Hard rule: the Technicals agent narrates the forecast as-is. Inventing causes is forbidden.",
             "If a trade fails because Kronos was wrong, the trail shows exactly that."]:
    p = para(tf); p.space_after = Pt(6)
    run(p, "\u2022  ", 13.5, True, BLUE_L); run(p, line, 13.5, False, INK)

box(s, 6.8, 1.45, 5.95, 5.45, RGBColor(0xFD, 0xF9, 0xF3), ORNG_L, None, line_w=1.5)
tf = s.shapes[-1].text_frame; tf.vertical_anchor = MSO_ANCHOR.TOP
tf.margin_left = tf.margin_right = Inches(0.2)
p = para(tf, True); p.space_before = Pt(8); p.space_after = Pt(8)
run(p, "Risk layer: belief vs permission", 17, True, NAVY)
for line in ["Verdict is two fields: veto yes/no, and a position cap from 0 to 1.",
             "Enforced three ways, strongest first: graph short-circuit, code clamp, prompt guidance.",
             "On a veto the Portfolio Manager is never called. Cheapest path to safety.",
             "Risk Manager lives on its own quota pool. A rate-limit storm cannot block the veto.",
             "One loader reads raw data, and it refuses any date newer than the as-of date. No lookahead, by construction.",
             "Every LLM output is a schema-validated object. No free-text parsing."]:
    p = para(tf); p.space_after = Pt(6)
    run(p, "\u2022  ", 13.5, True, ORNG_L); run(p, line, 13.5, False, INK)

# =====================================================================
# Slide 8 - Evaluation
# =====================================================================
s = slide(); header(s, "How We Prove It Works", "Evaluation")

def panel(x, w, title, col, lines):
    box(s, x, 1.45, w, 5.45, RGBColor(0xFB, 0xFC, 0xFD), col, None, line_w=1.5)
    tf = s.shapes[-1].text_frame; tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = tf.margin_right = Inches(0.2)
    p = para(tf, True); p.space_before = Pt(8); p.space_after = Pt(7)
    run(p, title, 16, True, NAVY)
    for head, rest in lines:
        p = para(tf); p.space_after = Pt(5.5)
        run(p, "\u2022  ", 13, True, col)
        if head: run(p, head + " ", 13, True, INK)
        if rest: run(p, rest, 13, False, INK)

panel(0.6, 5.95, "Protocol", CARD_L, [
    ("Walk-forward:", "one day at a time. Decide first, the outcome is revealed after. Only past data is ever visible."),
    ("Baselines:", "buy-and-hold. A single agent doing everything alone. Kronos trading its point forecast raw."),
    ("Ablations:", "no debate. No veto. Point forecast only. Shuffled confidence."),
    ("Stress test:", "a sharp multi-day drop window. Bands must widen, caps must tighten, drawdown must fall."),
])
panel(6.8, 5.95, "What we measure", CARD_L, [
    ("Decision quality:", "directional accuracy, F1 per action, decision-change rate."),
    ("Risk-adjusted:", "Sharpe and max drawdown on simulated, size-capped positions."),
    ("Calibration:", "Brier score and reliability curves of confidence vs outcome."),
    ("Forecast quality:", "MASE and WQL for Kronos, scored apart from trading outcomes."),
    ("Risk behavior:", "veto precision in the stress window, cap response to band width."),
    ("Auditability:", "trail completeness and schema validity, target 100 percent."),
])

# =====================================================================
# Slide 9 - Literature in one slide
# =====================================================================
s = slide(); header(s, "What the Literature Says", "One-page summary")

tf = txt(s, 0.6, 1.35, 12.1, 5.4)
items = [
    (0, "Finance basics:", "prices are close to efficient (Fama 1970), simple technical rules have fragile evidence (Brock 1992), news tone moves prices (Tetlock 2003). So: no profit claims, honest baselines."),
    (0, "Backtesting:", "overfitting is the main enemy (Lopez de Prado 2018). So: walk-forward only, no lookahead enforced in code, fixed baselines and ablations chosen in advance."),
    (0, "Forecasting models:", "PatchTST, one-fits-all transfer, Chronos and Lag-Llama led to Kronos (2025), the first open candlestick foundation model. Its sampled spread is exactly what a risk layer needs."),
    (0, "LLM trading agents:", "ChatGPT beats classic sentiment on headlines (Lopez-Lira 2023). FinMem adds memory. TradingAgents builds the desk. But one model plays every role, and risk only advises."),
    (0, "Debate:", "LLM debate improves factuality (Du 2023) and the format matters (Smit 2024). Groupthink is a known failure (Janis 1972), so Argos makes dissent structural and measures it."),
]
first = True
for _, head, rest in items:
    p = para(tf, first); first = False
    p.space_after = Pt(12)
    run(p, "\u2022  ", 15.5, True, ACCENT)
    run(p, head + " ", 15.5, True, NAVY)
    run(p, rest, 15.5, False, INK)

box(s, 0.6, 6.35, 12.15, 0.72, RGBColor(0xF0, 0xF6, 0xFB), ACCENT,
    "The gap Argos fills:  no published system wires a forecast distribution into a veto-first risk layer, or tests whether debate improves calibration on trading calls.",
    13.5, True, NAVY)

# =====================================================================
# Slide 10 - Hypotheses + plan
# =====================================================================
s = slide(); header(s, "Falsifiable Claims and the 12-Week Plan", "Closing")

box(s, 0.6, 1.40, 6.15, 4.95, RGBColor(0xF7, 0xFA, 0xFD), CARD_L, None, line_w=1.5)
tf = s.shapes[-1].text_frame; tf.vertical_anchor = MSO_ANCHOR.TOP
tf.margin_left = tf.margin_right = Inches(0.2)
p = para(tf, True); p.space_before = Pt(8); p.space_after = Pt(7)
run(p, "Six hypotheses, each with a test that can kill it", 15.5, True, NAVY)
hs = [
    ("H1", "Role split beats one agent on accuracy and auditability."),
    ("H2", "The debate improves calibration (Brier) without hurting accuracy."),
    ("H3", "The uncertainty-gated veto cuts drawdown at modest cost to upside."),
    ("H4", "Bull-Bear disagreement predicts volatility, not direction."),
    ("H5", "Kronos beats a momentum control on forecast error."),
    ("H6", "The debate changes real decisions. It is not decoration."),
]
for tag, line in hs:
    p = para(tf); p.space_after = Pt(6)
    run(p, tag + "  ", 13.5, True, ACCENT); run(p, line, 13.5, False, INK)

box(s, 7.0, 1.40, 5.75, 4.95, RGBColor(0xFB, 0xFC, 0xFD), CARD_L, None, line_w=1.5)
tf = s.shapes[-1].text_frame; tf.vertical_anchor = MSO_ANCHOR.TOP
tf.margin_left = tf.margin_right = Inches(0.2)
p = para(tf, True); p.space_before = Pt(8); p.space_after = Pt(7)
run(p, "Milestones (about 3 hours a day)", 15.5, True, NAVY)
ms = [
    ("Wk 1-2", "data pipeline, no-lookahead loader, tests"),
    ("Wk 3", "Kronos forecast cache for the full window"),
    ("Wk 4-5", "three analysts, schema-valid outputs"),
    ("Wk 6", "debate, transcripts checked by hand"),
    ("Wk 7", "risk layer, veto unit tests"),
    ("Wk 8", "full graph end to end, JSONL trail"),
    ("Wk 9-11", "walk-forward, baselines, ablations, stress"),
    ("Wk 12", "writeup and reproducibility pass"),
]
for tag, line in ms:
    p = para(tf); p.space_after = Pt(5.5)
    run(p, tag + "   ", 13.5, True, NAVY); run(p, line, 13.5, False, INK)

box(s, 0.6, 6.55, 12.15, 0.72, NAVY, None,
    "The system advises. A human decides.", 17, True, WHITE)

prs.save("Argos_Proposal_Deck.pptx")
print("Saved Argos_Proposal_Deck.pptx with", len(prs.slides._sldIdLst), "slides")
