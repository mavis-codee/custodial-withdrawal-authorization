"""Typeset the Chinese research paper as an embedded-font, single-column A4 PDF."""
from pathlib import Path
from xml.sax.saxutils import escape
import hashlib
import json
import re

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Flowable,
    Paragraph, Spacer, PageBreak, Table, TableStyle, KeepTogether)
from reportlab.platypus import paragraph as para_engine

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / "output/pdf/Custodial_Withdrawal_Authorization_Paper"
OUT.mkdir(parents=True, exist_ok=True)
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)
FONT_FILES = {"Song": "PaperSerif-Regular.ttf", "Hei": "PaperSans-Semibold.ttf",
              "Roman": "Tinos-Regular.ttf", "RomanBold": "Tinos-Bold.ttf",
              "RomanItalic": "Tinos-Italic.ttf", "Code": "SourceCodePro-Regular.ttf",
              "Math": "DejaVuSans.ttf"}
CMAP = {}
for name, filename in FONT_FILES.items():
    pdfmetrics.registerFont(TTFont(name, str(ROOT / "fonts" / filename)))
    CMAP[name] = set(pdfmetrics.getFont(name).face.charWidths)
pdfmetrics.registerFontFamily("Roman", normal="Roman", bold="RomanBold", italic="RomanItalic", boldItalic="RomanBold")
pdfmetrics.registerFontFamily("Song", normal="Song", bold="Hei", italic="Song", boldItalic="Hei")
pdfmetrics.registerFontFamily("Hei", normal="Hei", bold="Hei", italic="Hei", boldItalic="Hei")
W, H = A4
LEFT = RIGHT = 63.8
TOP, BOTTOM = 52.5, 53.5
WIDTH = W - LEFT - RIGHT
INK = colors.HexColor("#111111")
GRAY = colors.HexColor("#555555")
TRACK = []


def rich(text, role="body"):
    out = []
    for piece in re.split(r"(\*\*.*?\*\*|`[^`]+`)", text):
        bold = piece.startswith("**") and piece.endswith("**")
        code = piece.startswith("`") and piece.endswith("`")
        value = piece[2:-2] if bold else piece[1:-1] if code else piece
        groups = []
        for ch in value:
            if ch in "\r\n":
                groups.append((None, "<br/>"))
                continue
            latin = ord(ch) < 0x2E80
            primary = "Code" if code else "RomanBold" if bold or role == "heading" else "Roman"
            candidates = [primary, "Math", "Hei" if bold or role == "heading" else "Song"] if latin else ["Hei" if bold or role == "heading" else "Song", "Math"]
            name = next((n for n in candidates if ord(ch) in CMAP[n]), None)
            if name is None:
                raise ValueError(f"Missing glyph {ch!r} U+{ord(ch):04X}")
            if groups and groups[-1][0] == name:
                groups[-1] = name, groups[-1][1] + escape(ch)
            else:
                groups.append((name, escape(ch)))
        out.extend(s if n is None else f'<font name="{n}">{s}</font>' for n, s in groups)
    return "".join(out)


class CJKParagraph(Paragraph):
    """Keep Latin words intact where possible, and apply Chinese punctuation rules."""
    def breakLinesCJK(self, maxWidths):
        if hasattr(self, "blPara") and getattr(self, "_splitpara", 0):
            return self.blPara
        widths = list(maxWidths) if isinstance(maxWidths, (list, tuple)) else [maxWidths]
        para_engine._handleBulletWidth(self.bulletText, self.style, widths)
        chars = []
        for frag in self.frags:
            if frag.text:
                chars.extend(para_engine.cjkU(ch, frag, "utf8") for ch in frag.text)
            else:
                chars.append(para_engine.cjkU("", frag, "utf8"))
        cannot_start = set("，。、；：？！）】》”’!?％%)]},.:;")
        cannot_end = set("（【《“‘([{")
        def latin(ch):
            return bool(ch) and ch.isascii() and (ch.isalnum() or ch in "_-.$")
        def width_of(ch, width):
            value = ch.width
            if hasattr(value, "normalizedValue"):
                value._normalizer = width
                value = value.normalizedValue(width)
            return value
        lines, start, count = [], 0, len(chars)
        while start < count:
            width = widths[min(len(lines), len(widths) - 1)]
            used, end, explicit = 0, start, False
            while end < count:
                ch = chars[end]
                if hasattr(ch.frag, "lineBreak"):
                    end += 1
                    explicit = True
                    break
                cw = width_of(ch, width)
                if used + cw > width + 1e-6 and end > start:
                    break
                used += cw
                end += 1
            cut = end
            if not explicit and cut < count:
                if cut > start and latin(chars[cut - 1]) and latin(chars[cut]):
                    k = cut
                    while k > start and latin(chars[k - 1]):
                        k -= 1
                    if k > start:
                        cut = k
                while cut > start + 1 and (str(chars[cut]) in cannot_start or str(chars[cut - 1]) in cannot_end):
                    cut -= 1
                if cut < count and cut > start and latin(chars[cut - 1]) and latin(chars[cut]):
                    k = cut
                    while k > start and latin(chars[k - 1]):
                        k -= 1
                    if k > start:
                        cut = k
            if cut <= start:
                cut = start + 1
            segment = chars[start:cut]
            used = sum(width_of(ch, width) for ch in segment)
            lines.append(para_engine.makeCJKParaLine(segment, width, used, width - used, explicit, False))
            start = cut
        return para_engine.ParaLines(kind=1, lines=lines)


class P(CJKParagraph):
    def __init__(self, text, style, plain=None, bookmark=None, **kw):
        self.plain = plain
        self.bookmark = bookmark
        super().__init__(text, style, **kw)
    def split(self, *args):
        parts = super().split(*args)
        for part in parts:
            part.plain = self.plain
            part.bookmark = self.bookmark
        return parts
    def draw(self):
        x, y = self.canv.absolutePosition(0, 0)
        TRACK.append({"page": self.canv.getPageNumber(), "x": x, "y": y,
                      "width": self.width, "height": self.height, "text": self.plain})
        super().draw()


def style(name, **kw):
    base = dict(fontName="Song", fontSize=11, leading=17.6, alignment=TA_JUSTIFY,
                wordWrap="CJK", textColor=INK, firstLineIndent=22,
                spaceAfter=4.8, allowWidows=0, allowOrphans=0)
    base.update(kw)
    return ParagraphStyle(name, **base)


ST = {
    "body": style("body"),
    "title": style("title", fontName="Hei", fontSize=18, leading=27, firstLineIndent=0,
                   alignment=TA_CENTER, spaceAfter=7),
    "subtitle": style("subtitle", fontSize=11.3, leading=18, firstLineIndent=0,
                      alignment=TA_CENTER, spaceAfter=12),
    "meta": style("meta", fontSize=10, leading=15, firstLineIndent=0,
                  alignment=TA_CENTER, spaceAfter=3),
    "abstract": style("abstract", fontSize=10.2, leading=16.4, firstLineIndent=20.4, spaceAfter=7),
    "english": style("english", fontName="Roman", fontSize=10.3, leading=14.6,
                     firstLineIndent=0, alignment=TA_JUSTIFY, wordWrap=None, spaceAfter=7),
    "keywords": style("keywords", fontSize=9.8, leading=15.4, firstLineIndent=0, spaceAfter=8),
    "h1": style("h1", fontName="Hei", fontSize=13, leading=21, firstLineIndent=0,
                spaceBefore=13, spaceAfter=7, keepWithNext=True, alignment=TA_LEFT),
    "h2": style("h2", fontName="Hei", fontSize=11.2, leading=18.2, firstLineIndent=0,
                spaceBefore=9, spaceAfter=4.5, keepWithNext=True, alignment=TA_LEFT),
    "abshead": style("abshead", fontName="Hei", fontSize=10.7, leading=17,
                     firstLineIndent=0, spaceBefore=8, spaceAfter=4, keepWithNext=True),
    "caption": style("caption", fontSize=9.5, leading=15, firstLineIndent=0,
                     alignment=TA_CENTER, spaceBefore=5, spaceAfter=7),
    "cell": style("cell", fontSize=9.5, leading=14.4, firstLineIndent=0, spaceAfter=0, alignment=TA_LEFT),
    "ref": style("ref", fontSize=9.4, leading=14, leftIndent=19, firstLineIndent=-19,
                 alignment=TA_LEFT, spaceAfter=5),
    "equation": style("equation", fontSize=10.7, leading=19, firstLineIndent=0,
                      alignment=TA_CENTER, spaceBefore=6, spaceAfter=9),
}


def paragraph(text, key="body", bookmark=None):
    role = "heading" if key in {"title", "h1", "h2", "abshead"} else "body"
    value = rich(text, role)
    if key == "body":
        value = value.replace("V_U", "V<sub>U</sub>").replace("V_L", "V<sub>L</sub>")
    if key in {"meta", "ref"}:
        parts, cursor = [], 0
        for match in re.finditer(r"https?://\S+|[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
            parts.append(rich(text[cursor:match.start()], role))
            uri = match.group()
            href = uri if uri.startswith("http") else "mailto:" + uri
            parts.append(f'<link href="{escape(href)}" color="#111111">{rich(uri, role)}</link>')
            cursor = match.end()
        parts.append(rich(text[cursor:], role))
        value = "".join(parts)
    return P(value, ST[key], plain=re.sub(r"\*\*|`", "", text), bookmark=bookmark)


EQUATIONS = {
    "1": ('<i>x</i> = (domain, c, v, u, a, q, d, n, e, p)',
          "x = (domain, c, v, u, a, q, d, n, e, p)"),
    "2": (rich("∀ Q ⊆ S, |Q| ≥ t ⇒ Q ∩ H ≠ ∅"), "∀ Q ⊆ S, |Q| ≥ t ⇒ Q ∩ H ≠ ∅"),
    "3": ('Accept(<i>x</i>,<i>s</i>) = V<sub>U</sub>(<i>x</i>) '+rich("∧")+' V<sub>L</sub>(<i>x</i>,<i>s</i>) '+rich("∧")+' Fresh(<i>x</i>,<i>s</i>)<br/>'+rich("∧")+' Effects(<i>x</i>) '+rich("∧")+' Preserve(<i>s</i>,<i>x</i>)',
          "Accept(x,s) = VU(x) ∧ VL(x,s) ∧ Fresh(x,s) ∧ Effects(x) ∧ Preserve(s,x)"),
    "4": ('Execute(<i>x</i>,<i>s</i>) '+rich("⇒")+' Authorized<sub>U</sub>(<i>x</i>)<br/>'+rich("∧")+' Entitled<sub>L</sub>(<i>x</i>,<i>s</i>) '+rich("∧")+' Unused(<i>u</i>,<i>n</i>,<i>c</i>,<i>v</i>)',
          "Execute(x,s) ⇒ AuthorizedU(x) ∧ EntitledL(x,s) ∧ Unused(u,n,c,v)"),
}


def equation(number):
    html, plain = EQUATIONS[number]
    p = P(f'<font name="Roman">{html}</font>', ST["equation"], plain=plain)
    num = paragraph(f"({number})", "caption")
    t = Table([[p, num]], colWidths=[WIDTH - 28, 28])
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Roman"),
                          ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                          ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                          ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


ALGORITHM = [
    "Input: intent x, customer proof, entitlement proof, state s",
    "1  Verify the customer key and the complete intent fields",
    "2  Verify the entitlement and the authenticated ledger state",
    "3  Check domain, chain, vault, expiry and unused sequence",
    "4  Decode and restrict every asset effect of the operation",
    "5  Reject changes that create an unrestricted exit path",
    "6  Atomically:",
    "       consume sequence; deduct entitlement; transfer asset",
    "7  Commit the new state and the execution receipt",
    "Failure: do not transfer protected assets",
]


class Algorithm(Flowable):
    def __init__(self):
        super().__init__()
        self.width = WIDTH
        self.height = 151
    def draw(self):
        c = self.canv
        c.setStrokeColor(INK)
        c.setLineWidth(.55)
        c.line(0, self.height - 2, WIDTH, self.height - 2)
        c.line(0, 0, WIDTH, 0)
        c.setFont("Code", 8.8)
        c.setFillColor(INK)
        y = self.height - 17
        for line in ALGORITHM:
            if pdfmetrics.stringWidth(line, "Code", 8.8) > WIDTH - 12:
                raise ValueError("Algorithm line too wide")
            c.drawString(6, y, line)
            y -= 13.8


class Architecture(Flowable):
    def __init__(self):
        super().__init__()
        self.width, self.height = WIDTH, 218
    def draw(self):
        c = self.canv
        c.setStrokeColor(INK)
        c.setFillColor(INK)
        c.setLineWidth(.6)
        def box(x, y, w, h, lines):
            c.rect(x, y, w, h, stroke=1, fill=0)
            for i, line in enumerate(lines):
                p = paragraph(line, "cell")
                p.style = ParagraphStyle("diagram", parent=ST["cell"], alignment=TA_CENTER, fontSize=9.4, leading=14)
                p.wrap(w - 10, 35)
                p.drawOn(c, x + 5, y + h - 8 - (i + 1) * 14)
        def arrow(x1, y1, x2, y2):
            import math
            c.line(x1, y1, x2, y2)
            a = math.atan2(y2 - y1, x2 - x1)
            p = c.beginPath()
            p.moveTo(x2, y2)
            p.lineTo(x2 - 6 * math.cos(a - .40), y2 - 6 * math.sin(a - .40))
            p.lineTo(x2 - 6 * math.cos(a + .40), y2 - 6 * math.sin(a + .40))
            p.close()
            c.drawPath(p, stroke=0, fill=1)
        box(5, 164, 190, 43, ["客户独立授权 U", "明确绑定资产 地址 金额 网络"])
        box(WIDTH - 195, 164, 190, 43, ["交易所业务与签名 B S", "构建 排队 中继 平台签名"])
        box((WIDTH - 240) / 2, 73, 240, 57, ["资产执行守卫 G", "核验意图 权益 单次状态 实际效果", "平台权限不能单独取消约束"])
        box(5, 14, 182, 35, ["可验证权益状态 L"])
        box(WIDTH - 160, 14, 155, 35, ["指定链与接收地址"])
        arrow(143, 164, 193, 131)
        arrow(WIDTH - 143, 164, WIDTH - 193, 131)
        arrow(159, 49, 184, 73)
        arrow(WIDTH - 184, 73, WIDTH - 120, 49)
        p = paragraph("治理 恢复 与资金调拨必须保持同一资产边界", "caption")
        p.wrap(WIDTH, 20)
        p.drawOn(c, 0, -8)


class EmbeddedCanvas(canvas.Canvas):
    def __init__(self, *a, **kw):
        kw["initialFontName"] = "Roman"
        super().__init__(*a, **kw)


class PaperDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, P) and flowable.bookmark:
            key = flowable.bookmark
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.plain, key, level=0, closed=False)


def page_marks(c, doc):
    c.saveState()
    c.setFillColor(GRAY)
    c.setFont("Roman", 9)
    c.drawCentredString(W / 2, 29, str(doc.page))
    if doc.page > 1:
        c.setFont("Song", 8)
        c.drawString(LEFT, H - 29, "托管提币授权的抗合谋与抗胁迫研究")
        c.setFont("Roman", 8)
        c.drawRightString(W - RIGHT, H - 29, "Koji Tamaki")
    c.restoreState()


def make_table(lines):
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    rows = [r for r in rows if not all(re.fullmatch(r"[:\- ]+", c) for c in r)]
    count = len(rows[0])
    if count == 4:
        widths = [WIDTH * x for x in (.44, .20, .17, .19)]
    elif rows[0][0] == "中文术语":
        widths = [WIDTH * x for x in (.22, .31, .47)]
    else:
        widths = [WIDTH * x for x in (.21, .37, .42)]
    parsed = []
    for ri, row in enumerate(rows):
        cells = []
        for ci, value in enumerate(row):
            p = paragraph("**" + value + "**" if ri == 0 else value, "cell")
            if count == 4 and ci > 0:
                p.style = ParagraphStyle("numeric", parent=ST["cell"], alignment=TA_CENTER)
            cells.append(p)
        parsed.append(cells)
    t = Table(parsed, colWidths=widths, repeatRows=1, hAlign="CENTER")
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Roman"),
                          ("LINEABOVE", (0, 0), (-1, 0), .8, INK),
                          ("LINEBELOW", (0, 0), (-1, 0), .45, INK),
                          ("LINEBELOW", (0, -1), (-1, -1), .8, INK),
                          ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                          ("LEFTPADDING", (0, 0), (-1, -1), 5),
                          ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                          ("TOPPADDING", (0, 0), (-1, -1), 6),
                          ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def build():
    source = (ROOT / "paper.md").read_text(encoding="utf-8")
    lines = source.splitlines()
    story, index, body_started, references, abstract = [], 0, False, False, "meta"
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("# "):
            title = line[2:].replace("的抗合谋", "的\n抗合谋")
            story.append(paragraph(title, "title"))
        elif line.startswith("## "):
            heading = line[3:]
            if not body_started and not heading.startswith("1 "):
                story.append(paragraph(heading, "subtitle"))
            else:
                if not body_started:
                    story.append(PageBreak())
                    body_started = True
                references = heading == "参考文献"
                story.append(paragraph(heading, "h1", "sec-" + str(index)))
        elif line.startswith("### "):
            heading = line[4:]
            if not body_started:
                abstract = "en" if heading == "Abstract" else "zh"
                story.append(paragraph(heading, "abshead"))
            else:
                story.append(paragraph(heading, "h2"))
        elif line.startswith("$$"):
            number = re.search(r"\\tag\{(\d+)\}", line).group(1)
            story.append(equation(number))
        elif line == ":::figure architecture":
            story.append(Architecture())
        elif line == ":::algorithm withdrawal":
            story.extend([paragraph("算法 1 受约束提款的概念执行流程", "caption"), Algorithm(), Spacer(1, 8)])
        elif line.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            following = index
            while following < len(lines) and not lines[following].strip():
                following += 1
            block = [Spacer(1, 6), make_table(table_lines), Spacer(1, 3)]
            if following < len(lines) and lines[following].strip().startswith("**表 "):
                block.append(paragraph(lines[following].strip(), "caption"))
                index = following + 1
            story.append(KeepTogether(block))
            continue
        elif line.startswith("**图 ") or line.startswith("**表 "):
            story.append(paragraph(line, "caption"))
        elif line.startswith("**关键词") or line.startswith("**Keywords"):
            story.append(paragraph(line, "keywords"))
        elif references:
            match = re.match(r"\[(\d+)\]", line)
            rp = paragraph(line, "ref")
            html = rp.text
            if match:
                html = f'<a name="ref-{match.group(1)}"/>' + html
            story.append(P(html, ST["ref"], plain=line))
        elif not body_started and abstract == "meta":
            story.append(paragraph(line, "meta"))
        elif not body_started:
            story.append(paragraph(line, "english" if abstract == "en" else "abstract"))
        else:
            story.append(paragraph(line))
        index += 1
    result = OUT / "Custodial_Withdrawal_Authorization_zh.pdf"
    doc = PaperDoc(str(result), pagesize=A4, leftMargin=LEFT, rightMargin=RIGHT,
                   topMargin=TOP, bottomMargin=BOTTOM,
                   title="中心化交易所提币授权的抗合谋与抗胁迫机制研究", author="Koji Tamaki",
                   subject="独立用户意图 全路径资产约束 内部合谋与胁迫 有界模型检验")
    doc.addPageTemplates(PageTemplate(id="paper", frames=[Frame(LEFT, BOTTOM, WIDTH, H - TOP - BOTTOM,
                           leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=page_marks))
    doc.build(story, canvasmaker=EmbeddedCanvas)
    (ROOT / "layout.json").write_text(json.dumps(TRACK, ensure_ascii=False, indent=2), encoding="utf-8")
    # Produce a standalone diagram for the editable Markdown source.
    fig = canvas.Canvas(str(ASSETS / "architecture.pdf"), pagesize=(WIDTH + 20, 240), initialFontName="Roman")
    Architecture().drawOn(fig, 10, 15)
    fig.save()
    print(result)
    print("PDF_SHA256", hashlib.sha256(result.read_bytes()).hexdigest())


if __name__ == "__main__":
    build()
